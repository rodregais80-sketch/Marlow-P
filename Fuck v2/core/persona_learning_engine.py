"""
persona_learning_engine.py
Adaptive persona learning for Marlow Platform 3.

The problem this solves:
  Personas give advice. You rate decision outcomes 30 days later.
  That feedback signal is currently stored in decision_log and never used again.
  This engine reads rated decisions and extracts lessons that are then injected
  back into persona prompts — so ALDRIC's advice actually shifts based on what
  worked and what didn't specifically for this operator.

How it works:
  1. When a decision is rated (outcome_score saved), generate_persona_lessons()
     is called with the rated decision data.
  2. One Groq call extracts lessons per relevant persona based on:
     - What the decision was
     - What the operator's state was at decision time (energy, mood, impulse, fog, sleep)
     - What the outcome score was
     - What the gap between expectation and reality looks like
  3. Lessons are stored in persona_learning table.
  4. get_persona_lessons_context() retrieves the last N lessons per persona
     and formats them for prompt injection.
  5. run_council() passes this to each persona via their system prompt block.

Feedback signal design:
  We only learn from RATED decisions — decisions the operator actually reviewed.
  Unrated decisions are noise. They're intentions, not outcomes.
  The rating score (1-10) combined with the state at decision time is the signal.
  Low score + high energy + high impulse = "don't make big moves when manic"
  High score + moderate energy + low fog = "clear-headed moderate state produces best outcomes"

Groq call budget:
  1 call per rated decision. Fires in background after rating is submitted.
  Not called during council sessions. No rate limit impact on normal usage.

Lesson injection:
  Last 5 lessons per persona injected as a compact block.
  Max ~200 chars per lesson × 5 = ~1000 chars max per persona.
  Injected after tiered_history in persona prompt — lowest priority, highest specificity.
"""

from datetime import datetime


# ── Table management ──────────────────────────────────────────────────────────

def _ensure_learning_table(db) -> None:
    """Creates persona_learning table if it doesn't exist."""
    db.conn.execute("""
        CREATE TABLE IF NOT EXISTS persona_learning (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            persona       TEXT NOT NULL,
            lesson        TEXT NOT NULL,
            decision_id   INTEGER,
            outcome_score INTEGER,
            state_energy  REAL,
            state_mood    REAL,
            state_impulse REAL,
            state_fog     REAL,
            state_sleep   REAL,
            confidence    REAL DEFAULT 0.7,
            timestamp     DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_persona_learning_persona
        ON persona_learning (persona, timestamp)
    """)
    db.conn.commit()


# ── Lesson generation ─────────────────────────────────────────────────────────

def generate_persona_lessons(db, decision_id: int, groq_chat_fn) -> bool:
    """
    Generates persona-specific lessons from a rated decision.
    Called once after outcome_score is saved for a decision.

    Args:
        db: DatabaseManager instance
        decision_id: ID of the rated decision in decision_log
        groq_chat_fn: groq_chat callable from groq_client

    Returns:
        True if lessons were generated and saved, False on failure.
    """
    _ensure_learning_table(db)

    # Fetch the rated decision
    try:
        row = db.conn.execute(
            """
            SELECT decision_text, outcome_score, state_energy, state_mood,
                   state_impulse, state_fog, state_sleep, timestamp, outcome_notes
            FROM decision_log
            WHERE id = ? AND outcome_score IS NOT NULL
            """,
            (decision_id,)
        ).fetchone()
    except Exception:
        return False

    if not row:
        return False

    decision_text  = row[0] or ""
    outcome_score  = row[1]
    state_energy   = row[2]
    state_mood     = row[3]
    state_impulse  = row[4]
    state_fog      = row[5]
    state_sleep    = row[6]
    decision_ts    = row[7]
    outcome_notes  = row[8] or ""

    # Don't regenerate if lessons already exist for this decision
    try:
        existing = db.conn.execute(
            "SELECT COUNT(*) FROM persona_learning WHERE decision_id = ?",
            (decision_id,)
        ).fetchone()
        if existing and existing[0] > 0:
            return True
    except Exception:
        pass

    # Build state context string
    state_parts = []
    if state_energy  is not None: state_parts.append(f"energy={state_energy}/10")
    if state_mood    is not None: state_parts.append(f"mood={state_mood}/10")
    if state_impulse is not None: state_parts.append(f"impulse={state_impulse}/10")
    if state_fog     is not None: state_parts.append(f"fog={state_fog}/10")
    if state_sleep   is not None: state_parts.append(f"sleep={state_sleep}hrs")
    state_str = ", ".join(state_parts) if state_parts else "no state data recorded"

    # Score interpretation
    if outcome_score >= 8:
        score_label = "excellent outcome"
    elif outcome_score >= 6:
        score_label = "good outcome"
    elif outcome_score >= 4:
        score_label = "mixed outcome"
    elif outcome_score >= 2:
        score_label = "poor outcome"
    else:
        score_label = "bad outcome / mistake"

    prompt = f"""You are analyzing a rated decision to extract learning signals for a personal advisory council.

DECISION:
{decision_text}

OPERATOR STATE AT TIME OF DECISION:
{state_str}

OUTCOME:
Rated {outcome_score}/10 — {score_label}
{f"Notes: {outcome_notes}" if outcome_notes else ""}

Extract one specific lesson for EACH of these personas based on this decision and its outcome.
Each lesson must be:
- Specific to this operator's state pattern and what happened
- Under 120 characters
- Written as a direct operating principle, not a generic platitude
- Honest — if the decision was bad, say what the actual failure pattern was

Format your response as JSON exactly like this:
{{
  "ALDRIC": "lesson for ALDRIC here",
  "SEREN": "lesson for SEREN here",
  "ORYN": "lesson for ORYN here"
}}

Rules:
- ALDRIC lesson: strategic/financial angle — what does this reveal about decision quality at this state?
- SEREN lesson: emotional/psychological angle — what does this reveal about emotional state and outcomes?
- ORYN lesson: biological angle — what does this reveal about physiological state and decision quality?
- No lesson for MORRO — he already knows
- If a persona's domain is not relevant to this decision, write: "insufficient data for this decision type"
- Return ONLY the JSON object. No preamble. No markdown. No explanation."""

    try:
        import time as _time
        _time.sleep(0.5)
        raw = groq_chat_fn(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=300,
            timeout=20
        )
    except Exception:
        return False

    # Parse JSON response
    try:
        import json
        raw = raw.strip()
        # Strip any markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        lessons = json.loads(raw.strip())
    except Exception:
        return False

    # Store lessons per persona
    saved = 0
    for persona in ["ALDRIC", "SEREN", "ORYN"]:
        lesson = lessons.get(persona, "")
        if not lesson or "insufficient data" in lesson.lower():
            continue
        try:
            db.conn.execute(
                """
                INSERT INTO persona_learning
                    (persona, lesson, decision_id, outcome_score,
                     state_energy, state_mood, state_impulse, state_fog, state_sleep)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    persona, lesson[:300], decision_id, outcome_score,
                    state_energy, state_mood, state_impulse, state_fog, state_sleep
                )
            )
            saved += 1
        except Exception:
            pass

    if saved > 0:
        db.conn.commit()

    return saved > 0


# ── Lesson retrieval ──────────────────────────────────────────────────────────

def get_persona_lessons(db, persona_name: str, limit: int = 5) -> list:
    """
    Retrieves the most recent lessons for a persona.

    Args:
        db: DatabaseManager instance
        persona_name: ALDRIC, SEREN, or ORYN
        limit: max lessons to return

    Returns:
        List of dicts with keys: lesson, outcome_score, timestamp, state_*
    """
    _ensure_learning_table(db)
    try:
        rows = db.conn.execute(
            """
            SELECT lesson, outcome_score, state_energy, state_mood,
                   state_impulse, timestamp
            FROM persona_learning
            WHERE persona = ?
            ORDER BY id DESC LIMIT ?
            """,
            (persona_name, limit)
        ).fetchall()

        return [
            {
                "lesson":        row[0],
                "outcome_score": row[1],
                "state_energy":  row[2],
                "state_mood":    row[3],
                "state_impulse": row[4],
                "timestamp":     row[5]
            }
            for row in rows
        ]
    except Exception:
        return []


def get_persona_lessons_context(db, persona_name: str, limit: int = 5) -> str:
    """
    Formats persona lessons as a compact context block for prompt injection.

    Args:
        db: DatabaseManager instance
        persona_name: ALDRIC, SEREN, or ORYN
        limit: max lessons to include

    Returns:
        Formatted string, or empty string if no lessons exist.
    """
    lessons = get_persona_lessons(db, persona_name, limit)
    if not lessons:
        return ""

    lines = [f"--- LEARNED PATTERNS ({persona_name} — last {len(lessons)} rated decisions) ---"]
    for l in lessons:
        score_str = f"[outcome {l['outcome_score']}/10]" if l["outcome_score"] else ""
        ts_str    = l["timestamp"][:10] if l["timestamp"] else ""
        lines.append(f"• [{ts_str}] {score_str} {l['lesson']}")

    return "\n".join(lines)


def get_all_persona_learning_summary(db) -> str:
    """
    Builds a cross-persona learning summary for MARLOW synthesis context.
    Shows the most impactful lessons (highest and lowest outcome scores).

    Returns:
        Formatted string summarizing what the system has learned, or empty string.
    """
    _ensure_learning_table(db)
    try:
        # Get total lesson count
        total = db.conn.execute(
            "SELECT COUNT(*) FROM persona_learning"
        ).fetchone()
        if not total or total[0] == 0:
            return ""

        # Best state pattern (avg outcome_score grouped by energy range)
        best = db.conn.execute(
            """
            SELECT
                CASE
                    WHEN state_energy BETWEEN 6 AND 8 THEN 'moderate-high energy (6-8)'
                    WHEN state_energy BETWEEN 3 AND 5 THEN 'moderate energy (3-5)'
                    WHEN state_energy >= 9            THEN 'very high energy (9-10)'
                    WHEN state_energy <= 2            THEN 'very low energy (1-2)'
                    ELSE 'unknown energy'
                END as energy_band,
                ROUND(AVG(outcome_score), 1) as avg_score,
                COUNT(*) as n
            FROM persona_learning
            WHERE state_energy IS NOT NULL AND outcome_score IS NOT NULL
            GROUP BY energy_band
            ORDER BY avg_score DESC LIMIT 1
            """
        ).fetchone()

        lines = [f"System has learned from {total[0]} rated decision(s)."]
        if best and best[1]:
            lines.append(f"Best outcome pattern: {best[0]} → avg {best[1]}/10 ({best[2]} decisions)")

        return "\n".join(lines)

    except Exception:
        return ""


# ── Integration hook ──────────────────────────────────────────────────────────

def trigger_learning_from_rating(db, decision_id: int, groq_chat_fn) -> None:
    """
    Entry point called from run_decision_log() after outcome_score is saved.
    Runs lesson generation in a background thread to avoid blocking the CLI.

    Args:
        db: DatabaseManager instance
        decision_id: ID of the just-rated decision
        groq_chat_fn: groq_chat callable
    """
    import threading

    def _run():
        try:
            generate_persona_lessons(db, decision_id, groq_chat_fn)
        except Exception:
            pass

    thread = threading.Thread(target=_run, daemon=True, name="MarlowLearning")
    thread.start()


# ── Lesson viewer ─────────────────────────────────────────────────────────────

def format_learning_report(db) -> str:
    """
    Builds a readable learning report for all personas.
    Displayed in the Decision Log menu when enough lessons exist.

    Returns:
        Formatted string report, or empty string if no lessons.
    """
    _ensure_learning_table(db)
    try:
        total = db.conn.execute(
            "SELECT COUNT(*) FROM persona_learning"
        ).fetchone()
        if not total or total[0] == 0:
            return ""

        lines = ["PERSONA LEARNING HISTORY", "─" * 40]

        for persona in ["ALDRIC", "SEREN", "ORYN"]:
            lessons = get_persona_lessons(db, persona, limit=3)
            if not lessons:
                continue
            lines.append(f"\n{persona}:")
            for l in lessons:
                score_str = f"  ({l['outcome_score']}/10)" if l["outcome_score"] else ""
                ts_str    = l["timestamp"][:10] if l["timestamp"] else ""
                lines.append(f"  [{ts_str}]{score_str} {l['lesson']}")

        # Overall pattern summary
        summary = get_all_persona_learning_summary(db)
        if summary:
            lines.append("")
            lines.append("PATTERN SUMMARY:")
            lines.append(summary)

        return "\n".join(lines)

    except Exception:
        return ""
