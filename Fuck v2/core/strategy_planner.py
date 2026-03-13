"""
strategy_planner.py
Goal-to-execution strategy engine for the Marlow platform.

What this does:
  - Takes an active goal and calls ALDRIC to generate a structured step plan
  - Stores the plan in the DB against the goal ID
  - Tracks step completion as the operator checks them off
  - Surfaces stalled steps in the session brief and weekly report
  - Feeds plan progress into ALDRIC's decision_context so he knows
    what's executing vs what's stalled

DB tables used:
  - goals (existing) — reads goal title, description, target_date
  - goal_plans (new) — stores the generated plan per goal
  - goal_steps (new) — individual steps with status tracking

This is not autonomous. The operator triggers plan generation.
ALDRIC generates the plan. The operator executes and marks steps.
The system tracks and surfaces what's stalling.
"""

import json
from datetime import datetime, timedelta
from core.database import DatabaseManager
from core.marlow_logger import log_error, log_warning, log_info


def _row_get(row, key, default=None):
    try:
        v = row[key]
        return v if v is not None else default
    except (KeyError, IndexError, TypeError):
        return default


# ── Schema init ───────────────────────────────────────────────────────────────

def _ensure_strategy_tables(db: DatabaseManager) -> None:
    """
    Creates goal_plans and goal_steps tables if they don't exist.
    Called once at startup — safe on existing DBs.
    """
    try:
        db.conn.execute("""
            CREATE TABLE IF NOT EXISTS goal_plans (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id     INTEGER NOT NULL,
                created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
                generated_by TEXT DEFAULT 'ALDRIC',
                raw_plan    TEXT,
                step_count  INTEGER DEFAULT 0,
                is_active   INTEGER DEFAULT 1
            )
        """)
        db.conn.execute("""
            CREATE TABLE IF NOT EXISTS goal_steps (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id     INTEGER NOT NULL,
                goal_id     INTEGER NOT NULL,
                step_number INTEGER NOT NULL,
                title       TEXT NOT NULL,
                description TEXT,
                status      TEXT DEFAULT 'pending',
                created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
                completed_at TEXT,
                notes       TEXT
            )
        """)
        db.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_goal_steps_plan "
            "ON goal_steps(plan_id, step_number)"
        )
        db.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_goal_steps_goal "
            "ON goal_steps(goal_id, status)"
        )
        db.conn.commit()
        log_info("StrategyPlanner", "Tables verified.")
    except Exception as e:
        log_error("StrategyPlanner", "_ensure_strategy_tables", e)


# ── Plan generation ───────────────────────────────────────────────────────────

def generate_plan_for_goal(goal: dict, db: DatabaseManager, persona_prompt: str = None) -> dict:
    """
    Calls Groq (ALDRIC persona) to generate a structured step plan for a goal.
    Stores the plan and steps in the DB.

    Args:
        goal           : Row from the goals table (sqlite3.Row or dict).
        db             : DatabaseManager instance.
        persona_prompt : Optional system prompt override. Uses ALDRIC's prompt by default.

    Returns:
        dict with keys: plan_id, steps (list of dicts), raw_plan (str), error (str or None)
    """
    _ensure_strategy_tables(db)

    goal_id    = _row_get(goal, "id")
    title      = _row_get(goal, "title", "")
    description= _row_get(goal, "description", "")
    target     = _row_get(goal, "target_date", "")
    progress   = _row_get(goal, "progress_note", "")

    if not goal_id or not title:
        return {"plan_id": None, "steps": [], "raw_plan": "", "error": "Invalid goal data"}

    # Build ALDRIC's planning prompt
    context_parts = [f"Goal: {title}"]
    if description:
        context_parts.append(f"Description: {description}")
    if target:
        context_parts.append(f"Target date: {target}")
    if progress:
        context_parts.append(f"Progress so far: {progress}")

    goal_context = "\n".join(context_parts)

    system = persona_prompt or """You are ALDRIC. You think in executable systems, not intentions.

Your job is to generate a structured execution plan for a goal.
You produce plans that are ruthlessly specific and sequenced correctly.
Each step must be something that can actually be done — not an aspiration."""

    user_msg = f"""Generate a structured execution plan for this goal:

{goal_context}

Return ONLY valid JSON in this exact format. No preamble, no explanation, no markdown:

{{
  "plan_summary": "One sentence describing the strategic approach",
  "steps": [
    {{
      "step_number": 1,
      "title": "Short action title",
      "description": "Specific, concrete description of what to do and how to know it's done"
    }},
    {{
      "step_number": 2,
      "title": "Short action title",
      "description": "Specific, concrete description"
    }}
  ]
}}

Rules:
- 4 to 8 steps maximum
- Each step must be discrete and completable in under 2 weeks
- Sequence matters — steps build on each other
- No vague steps like "research the market" — be specific about what research and what output
- The last step should be the milestone that confirms the goal is achieved"""

    try:
        from core.groq_client import chat_completion, is_available
        if not is_available():
            return {"plan_id": None, "steps": [], "raw_plan": "", "error": "Groq unavailable"}

        raw = chat_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg}
            ],
            temperature=0.4,
            max_tokens=800
        )

        # Strip markdown fences if model added them
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[1:])
        if cleaned.endswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[:-1])
        cleaned = cleaned.strip()

        plan_data = json.loads(cleaned)
        steps     = plan_data.get("steps", [])
        summary   = plan_data.get("plan_summary", "")

        if not steps:
            return {"plan_id": None, "steps": [], "raw_plan": raw, "error": "No steps returned"}

        # Deactivate any existing plans for this goal
        db.conn.execute(
            "UPDATE goal_plans SET is_active = 0 WHERE goal_id = ?", (goal_id,)
        )

        # Save the new plan
        cursor = db.conn.execute("""
            INSERT INTO goal_plans (goal_id, raw_plan, step_count, generated_by)
            VALUES (?, ?, ?, 'ALDRIC')
        """, (goal_id, raw, len(steps)))
        plan_id = cursor.lastrowid

        # Save individual steps
        for step in steps:
            db.conn.execute("""
                INSERT INTO goal_steps
                (plan_id, goal_id, step_number, title, description, status)
                VALUES (?, ?, ?, ?, ?, 'pending')
            """, (
                plan_id,
                goal_id,
                step.get("step_number", 0),
                step.get("title", "")[:200],
                step.get("description", "")[:500]
            ))

        db.conn.commit()
        log_info("StrategyPlanner", f"Plan generated for goal {goal_id}: {len(steps)} steps")

        return {
            "plan_id": plan_id,
            "steps":   steps,
            "summary": summary,
            "raw_plan": raw,
            "error":   None
        }

    except json.JSONDecodeError as e:
        log_error("StrategyPlanner", "generate_plan_for_goal", e, f"goal_id={goal_id}")
        return {"plan_id": None, "steps": [], "raw_plan": raw if 'raw' in dir() else "", "error": f"JSON parse failed: {e}"}
    except Exception as e:
        log_error("StrategyPlanner", "generate_plan_for_goal", e, f"goal_id={goal_id}")
        return {"plan_id": None, "steps": [], "raw_plan": "", "error": str(e)}


# ── Step management ───────────────────────────────────────────────────────────

def get_active_plan(db: DatabaseManager, goal_id: int) -> dict:
    """
    Returns the active plan for a goal with all its steps.

    Returns:
        dict: {plan_id, summary, steps: [{step_number, title, description, status, notes}]}
        or None if no active plan exists.
    """
    _ensure_strategy_tables(db)
    try:
        plan = db.conn.execute(
            "SELECT * FROM goal_plans WHERE goal_id = ? AND is_active = 1 ORDER BY id DESC LIMIT 1",
            (goal_id,)
        ).fetchone()
        if not plan:
            return None

        steps = db.conn.execute(
            "SELECT * FROM goal_steps WHERE plan_id = ? ORDER BY step_number ASC",
            (plan["id"],)
        ).fetchall()

        return {
            "plan_id":    plan["id"],
            "goal_id":    goal_id,
            "created_at": plan["created_at"],
            "steps": [
                {
                    "id":          s["id"],
                    "step_number": s["step_number"],
                    "title":       s["title"],
                    "description": s["description"],
                    "status":      s["status"],
                    "notes":       s["notes"],
                    "completed_at":s["completed_at"]
                }
                for s in steps
            ]
        }
    except Exception as e:
        log_error("StrategyPlanner", "get_active_plan", e)
        return None


def mark_step_complete(db: DatabaseManager, step_id: int, notes: str = "") -> bool:
    """Marks a step as completed with optional notes."""
    _ensure_strategy_tables(db)
    try:
        db.conn.execute("""
            UPDATE goal_steps
            SET status = 'complete', completed_at = ?, notes = ?
            WHERE id = ?
        """, (datetime.now().isoformat(), notes, step_id))
        db.conn.commit()
        log_info("StrategyPlanner", f"Step {step_id} marked complete")
        return True
    except Exception as e:
        log_error("StrategyPlanner", "mark_step_complete", e)
        return False


def mark_step_skipped(db: DatabaseManager, step_id: int, notes: str = "") -> bool:
    """Marks a step as skipped."""
    _ensure_strategy_tables(db)
    try:
        db.conn.execute(
            "UPDATE goal_steps SET status = 'skipped', notes = ? WHERE id = ?",
            (notes, step_id)
        )
        db.conn.commit()
        return True
    except Exception as e:
        log_error("StrategyPlanner", "mark_step_skipped", e)
        return False


def get_stalled_steps(db: DatabaseManager, stall_days: int = 7) -> list:
    """
    Returns steps that are still pending and were created more than stall_days ago.
    Used by session brief and weekly report to surface stalled execution.
    """
    _ensure_strategy_tables(db)
    try:
        cutoff = (datetime.now() - timedelta(days=stall_days)).isoformat()
        rows   = db.conn.execute("""
            SELECT gs.*, gp.goal_id, g.title as goal_title
            FROM goal_steps gs
            JOIN goal_plans gp ON gs.plan_id = gp.id
            JOIN goals g ON gs.goal_id = g.id
            WHERE gs.status = 'pending'
            AND gs.created_at <= ?
            AND gp.is_active = 1
            AND g.status = 'active'
            ORDER BY gs.goal_id, gs.step_number ASC
        """, (cutoff,)).fetchall()
        return rows
    except Exception as e:
        log_error("StrategyPlanner", "get_stalled_steps", e)
        return []


# ── Context for personas ──────────────────────────────────────────────────────

def build_plan_context(db: DatabaseManager, max_chars: int = 600) -> str:
    """
    Builds a context block of active plans + step status for ALDRIC's prompt.
    Called by council_engine alongside decision_context.

    Returns a formatted string showing which steps are pending, complete, stalled.
    """
    _ensure_strategy_tables(db)
    try:
        # Get all active goals with active plans
        rows = db.conn.execute("""
            SELECT g.id, g.title, g.target_date,
                   COUNT(CASE WHEN gs.status = 'pending'  THEN 1 END) as pending,
                   COUNT(CASE WHEN gs.status = 'complete' THEN 1 END) as complete,
                   COUNT(CASE WHEN gs.status = 'skipped'  THEN 1 END) as skipped,
                   COUNT(*) as total
            FROM goals g
            JOIN goal_plans gp ON g.id = gp.goal_id AND gp.is_active = 1
            JOIN goal_steps gs ON gs.plan_id = gp.id
            WHERE g.status = 'active'
            GROUP BY g.id
            ORDER BY g.created_at ASC
        """).fetchall()

        if not rows:
            return ""

        stalled = get_stalled_steps(db, stall_days=7)
        stalled_step_ids = {s["id"] for s in stalled}

        lines = ["EXECUTION PLANS:"]
        for r in rows:
            pct  = round((r["complete"] / r["total"]) * 100) if r["total"] else 0
            line = f"  [{r['title'][:40]}] {r['complete']}/{r['total']} steps complete ({pct}%)"
            if r["target_date"]:
                line += f" | target: {r['target_date']}"
            lines.append(line)

            # Show the next pending step
            next_step = db.conn.execute("""
                SELECT gs.* FROM goal_steps gs
                JOIN goal_plans gp ON gs.plan_id = gp.id
                WHERE gp.goal_id = ? AND gp.is_active = 1 AND gs.status = 'pending'
                ORDER BY gs.step_number ASC LIMIT 1
            """, (r["id"],)).fetchone()

            if next_step:
                stall_flag = " ⚠ STALLED" if next_step["id"] in stalled_step_ids else ""
                lines.append(f"    Next: Step {next_step['step_number']} — {next_step['title'][:60]}{stall_flag}")

        result = "\n".join(lines)
        return result[:max_chars]

    except Exception as e:
        log_error("StrategyPlanner", "build_plan_context", e)
        return ""
