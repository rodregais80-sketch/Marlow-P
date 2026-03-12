"""
weekly_premortem.py
Forward-facing weekly pre-mortem for the Marlow platform.

What this does:
  Before the week starts, ALDRIC looks at:
    - Active goal steps due or pending
    - Historical energy patterns for this day of week
    - Current physiological state (last sync)
    - Contradiction map (what conditions predict broken commitments)
    - Streak data (current momentum)

  Then writes ONE paragraph: here is what is most likely to derail this week
  and why. Not motivational. Probabilistic. Uses your own data against you
  in the best possible way.

Fires automatically:
  - At startup on Monday (or first session of the week)
  - Or on demand from the Weekly Report menu

1 Groq call. Result saved to DB. Not regenerated until next week.

DB: auto_reports table with report_type = 'premortem'
"""

import re
from datetime import datetime, timedelta, date
from core.database import DatabaseManager
from core.groq_client import chat_completion as groq_chat, is_available as groq_available
from core.marlow_logger import log_error, log_info, log_warning


def _get_day_of_week_pattern(db: DatabaseManager, day_name: str) -> dict:
    """
    Pulls historical average energy and output for a given day of week.
    day_name: 'Monday', 'Tuesday', etc.
    Returns dict with avg_energy, avg_output, sample_count.
    """
    try:
        rows = db.conn.execute("""
            SELECT content FROM logs
            WHERE strftime('%w', timestamp) = ?
            ORDER BY timestamp DESC
            LIMIT 60
        """, (str(["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"].index(day_name)),)
        ).fetchall()

        energies = []
        outputs  = []
        for row in rows:
            content = row["content"] if row["content"] else ""
            e = re.search(r"energy[:\s]+(\d+(?:\.\d+)?)", content, re.IGNORECASE)
            o = re.search(r"output[:\s]+(\d+(?:\.\d+)?)", content, re.IGNORECASE)
            if e: energies.append(float(e.group(1)))
            if o: outputs.append(float(o.group(1)))

        return {
            "avg_energy": round(sum(energies)/len(energies), 1) if energies else None,
            "avg_output": round(sum(outputs)/len(outputs), 1)   if outputs  else None,
            "sample_count": len(rows)
        }
    except Exception as e:
        log_error("WeeklyPremortem", "_get_day_of_week_pattern", e)
        return {}


def _get_pending_steps_this_week(db: DatabaseManager) -> list:
    """
    Returns active goal plan steps that are pending, for ALDRIC's context.
    """
    try:
        rows = db.conn.execute("""
            SELECT gs.title, gs.step_number, g.title as goal_title, gs.created_at
            FROM goal_steps gs
            JOIN goal_plans gp ON gs.plan_id = gp.id
            JOIN goals g ON gs.goal_id = g.id
            WHERE gs.status = 'pending'
            AND gp.is_active = 1
            AND g.status = 'active'
            ORDER BY g.id, gs.step_number ASC
            LIMIT 10
        """).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _last_premortem_date(db: DatabaseManager) -> str | None:
    """Returns the date of the last premortem generation, or None."""
    try:
        row = db.conn.execute("""
            SELECT generated_at FROM auto_reports
            WHERE report_type = 'premortem'
            ORDER BY id DESC LIMIT 1
        """).fetchone()
        return row["generated_at"][:10] if row else None
    except Exception:
        return None


def should_run_premortem(db: DatabaseManager) -> bool:
    """
    Returns True if a new pre-mortem should be generated.
    Fires on Monday or if 7+ days since last one.
    """
    today     = date.today()
    last_date = _last_premortem_date(db)

    if last_date is None:
        return True  # Never run

    last = date.fromisoformat(last_date)
    days_since = (today - last).days

    # Always fire on Monday if more than 5 days since last
    if today.weekday() == 0 and days_since >= 5:
        return True

    # Also fire if somehow 10+ days have passed
    if days_since >= 10:
        return True

    return False


def generate_weekly_premortem(db: DatabaseManager) -> str:
    """
    Generates the weekly pre-mortem using ALDRIC's prompt.
    1 Groq call. Saves to auto_reports. Returns the content.
    """
    if not groq_available():
        return ""

    today     = date.today()
    day_names = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    week_days = [day_names[(today.weekday() + i) % 7] for i in range(7)]

    # Day-of-week energy/output patterns
    day_patterns = {}
    for d in week_days[:5]:  # Weekdays only
        day_patterns[d] = _get_day_of_week_pattern(db, d)

    # Pending steps
    pending_steps = _get_pending_steps_this_week(db)

    # Last sync data
    last_sync_content = ""
    try:
        row = db.conn.execute(
            "SELECT content, sync_type FROM logs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row:
            last_sync_content = f"{row['sync_type']}: {row['content'][:400]}"
    except Exception:
        pass

    # Contradiction map summary
    contradiction_summary = ""
    try:
        from core.contradiction_engine import build_contradiction_context
        contradiction_summary = build_contradiction_context(db, max_chars=300)
    except Exception:
        pass

    # Streak data
    streak_summary = ""
    try:
        from core.streak_tracker import build_streak_context
        streak_summary = build_streak_context(db, max_chars=200)
    except Exception:
        pass

    # Format day patterns
    day_pattern_str = ""
    for d, p in day_patterns.items():
        if p.get("avg_energy"):
            day_pattern_str += f"  {d}: avg energy {p['avg_energy']}/10"
            if p.get("avg_output"):
                day_pattern_str += f", avg output {p['avg_output']}/10"
            day_pattern_str += f" ({p.get('sample_count', 0)} sessions)\n"

    # Format pending steps
    steps_str = ""
    for s in pending_steps:
        steps_str += f"  [{s['goal_title']}] Step {s['step_number']}: {s['title']}\n"

    prompt = f"""You are ALDRIC. Today is {today.strftime('%A, %B %d')}.

Your job is to write a pre-mortem for the week ahead. Not motivation. Not encouragement.
A pre-mortem: what is most likely to go wrong this week, and why, based on the data below.

HISTORICAL DAY-OF-WEEK PATTERNS (this operator's own data):
{day_pattern_str if day_pattern_str else "  Insufficient historical data for day patterns."}

PENDING EXECUTION STEPS THIS WEEK:
{steps_str if steps_str else "  No active plan steps pending."}

LAST SYNC STATE:
{last_sync_content if last_sync_content else "  No recent sync data."}

{contradiction_summary}

{streak_summary}

Write exactly ONE paragraph. 4-6 sentences.

Identify: the single most likely failure point this week. Name the specific day or condition
where the data says risk is highest. State the mechanism — why that day/condition historically
produces poor execution. Then state one structural change that would prevent it.

Not generic advice. Specific to this data. ALDRIC voice — cold, precise, probabilistic.
No opener phrases. Start with the risk."""

    try:
        result = groq_chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=250
        )

        if result and not result.startswith("["):
            now          = datetime.now()
            week_start   = today.isoformat()
            week_end     = (today + timedelta(days=6)).isoformat()
            try:
                db.conn.execute("""
                    INSERT INTO auto_reports (report_type, period_start, period_end, report_content)
                    VALUES ('premortem', ?, ?, ?)
                """, (week_start, week_end, result))
                db.conn.commit()
                log_info("WeeklyPremortem", f"Pre-mortem generated for week of {week_start}")
            except Exception as e:
                log_error("WeeklyPremortem", "save to auto_reports", e)

        return result

    except Exception as e:
        log_error("WeeklyPremortem", "generate_weekly_premortem", e)
        return ""


def get_latest_premortem(db: DatabaseManager) -> dict | None:
    """Returns the most recent pre-mortem as a dict {content, generated_at}."""
    try:
        row = db.conn.execute("""
            SELECT report_content, generated_at, period_start
            FROM auto_reports
            WHERE report_type = 'premortem'
            ORDER BY id DESC LIMIT 1
        """).fetchone()
        if row:
            return {
                "content":      row["report_content"],
                "generated_at": row["generated_at"],
                "period_start": row["period_start"]
            }
        return None
    except Exception as e:
        log_error("WeeklyPremortem", "get_latest_premortem", e)
        return None
