"""
streak_tracker.py
Execution consistency tracking for the Marlow platform.

Tracks one thing: did the operator do the thing they said they'd do, or not.
Not mood. Not energy. Not effort. Just execution — binary.

A streak is defined as consecutive days with at least one goal step completed
OR a morning/evening sync logged with output >= threshold.

Two types of streaks tracked:
  1. Active streak: current consecutive execution days
  2. Best streak: longest run ever recorded
  3. Per-goal execution rate: % of days with plan steps completed since plan created

The 90-day view of this single metric is more predictive than any mood score.

DB table: execution_streaks (daily execution log)
"""

from datetime import datetime, timedelta, date
from core.database import DatabaseManager
from core.marlow_logger import log_error, log_info


_OUTPUT_THRESHOLD = 5   # Output score >= this counts as an execution day


def _ensure_tables(db: DatabaseManager) -> None:
    try:
        db.conn.execute("""
            CREATE TABLE IF NOT EXISTS execution_days (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                day          TEXT UNIQUE NOT NULL,
                executed     INTEGER NOT NULL DEFAULT 0,
                output_score REAL,
                steps_done   INTEGER DEFAULT 0,
                source       TEXT
            )
        """)
        db.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_execution_days_day "
            "ON execution_days(day)"
        )
        db.conn.commit()
    except Exception as e:
        log_error("StreakTracker", "_ensure_tables", e)


# ── Daily execution assessment ────────────────────────────────────────────────

def assess_today(db: DatabaseManager) -> dict:
    """
    Assesses whether today counts as an execution day.
    Checks:
      1. Any goal step marked complete today
      2. Any sync log with output score >= threshold
    Saves result to execution_days table.
    Returns dict: {day, executed, output_score, steps_done}
    """
    _ensure_tables(db)
    today = date.today().isoformat()

    # Check goal steps completed today
    steps_done = 0
    try:
        steps_done = db.conn.execute("""
            SELECT COUNT(*) FROM goal_steps
            WHERE status = 'complete'
            AND date(completed_at) = ?
        """, (today,)).fetchone()[0]
    except Exception:
        pass

    # Check output score from syncs today
    output_score = None
    try:
        import re
        rows = db.conn.execute("""
            SELECT content FROM logs
            WHERE date(timestamp) = ?
            ORDER BY id DESC
        """, (today,)).fetchall()
        for row in rows:
            content = row["content"] if row["content"] else ""
            m = re.search(r"output[:\s]+(\d+(?:\.\d+)?)", content, re.IGNORECASE)
            if m:
                output_score = float(m.group(1))
                break
    except Exception:
        pass

    executed = 1 if (steps_done >= 1 or (output_score is not None and output_score >= _OUTPUT_THRESHOLD)) else 0

    try:
        db.conn.execute("""
            INSERT INTO execution_days (day, executed, output_score, steps_done, source)
            VALUES (?, ?, ?, ?, 'auto')
            ON CONFLICT(day) DO UPDATE SET
                executed     = MAX(executed, excluded.executed),
                output_score = COALESCE(excluded.output_score, output_score),
                steps_done   = MAX(steps_done, excluded.steps_done)
        """, (today, executed, output_score, steps_done))
        db.conn.commit()
    except Exception as e:
        log_error("StreakTracker", "assess_today", e)

    return {"day": today, "executed": executed, "output_score": output_score, "steps_done": steps_done}


# ── Streak calculation ─────────────────────────────────────────────────────────

def get_streak_data(db: DatabaseManager, lookback_days: int = 90) -> dict:
    """
    Calculates current streak, best streak, and execution rate over lookback window.

    Returns:
        current_streak : int — consecutive execution days ending today or yesterday
        best_streak    : int — longest consecutive run ever
        execution_rate : float — % of days with execution in lookback window
        total_days     : int — days tracked
        executed_days  : int — days with execution
        last_executed  : str — date of most recent execution day
        gap_days       : int — days since last execution (0 if today)
    """
    _ensure_tables(db)

    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()

    try:
        rows = db.conn.execute("""
            SELECT day, executed FROM execution_days
            WHERE day >= ?
            ORDER BY day DESC
        """, (cutoff,)).fetchall()
    except Exception as e:
        log_error("StreakTracker", "get_streak_data", e)
        return {}

    if not rows:
        return {
            "current_streak": 0, "best_streak": 0, "execution_rate": 0.0,
            "total_days": 0, "executed_days": 0, "last_executed": None, "gap_days": None
        }

    day_map = {r["day"]: r["executed"] for r in rows}
    today   = date.today()

    # Current streak — walk back from today
    current_streak = 0
    check_date = today
    while True:
        ds = check_date.isoformat()
        if day_map.get(ds) == 1:
            current_streak += 1
            check_date -= timedelta(days=1)
        elif ds not in day_map:
            # Day not recorded — skip one gap, allow for days not yet synced
            check_date -= timedelta(days=1)
            if check_date.isoformat() not in day_map or day_map.get(check_date.isoformat()) != 1:
                break
        else:
            break

    # Best streak — all time
    all_rows = []
    try:
        all_rows = db.conn.execute(
            "SELECT day, executed FROM execution_days ORDER BY day ASC"
        ).fetchall()
    except Exception:
        pass

    best_streak = 0
    run = 0
    prev_date = None
    for r in all_rows:
        d = date.fromisoformat(r["day"])
        if r["executed"] == 1:
            if prev_date and (d - prev_date).days == 1:
                run += 1
            else:
                run = 1
            best_streak = max(best_streak, run)
            prev_date = d
        else:
            run = 0
            prev_date = d

    # Execution rate over lookback
    total_days    = len(rows)
    executed_days = sum(1 for r in rows if r["executed"] == 1)
    rate          = round(executed_days / total_days, 3) if total_days else 0.0

    # Last executed day and gap
    last_executed = next((r["day"] for r in rows if r["executed"] == 1), None)
    gap_days = None
    if last_executed:
        gap_days = (today - date.fromisoformat(last_executed)).days

    return {
        "current_streak":  current_streak,
        "best_streak":     best_streak,
        "execution_rate":  rate,
        "total_days":      total_days,
        "executed_days":   executed_days,
        "last_executed":   last_executed,
        "gap_days":        gap_days
    }


def get_streak_history(db: DatabaseManager, days: int = 30) -> list:
    """
    Returns a list of (day, executed) tuples for the last N days.
    Used for display in the Goals/streak menu.
    """
    _ensure_tables(db)
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    try:
        rows = db.conn.execute("""
            SELECT day, executed, output_score, steps_done
            FROM execution_days
            WHERE day >= ?
            ORDER BY day ASC
        """, (cutoff,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        log_error("StreakTracker", "get_streak_history", e)
        return []


# ── Context for personas ───────────────────────────────────────────────────────

def build_streak_context(db: DatabaseManager, max_chars: int = 300) -> str:
    """
    Builds a compact streak context block for injection into ALDRIC's prompt.
    """
    try:
        assess_today(db)
        data = get_streak_data(db, lookback_days=90)
        if not data or data.get("total_days", 0) < 3:
            return ""

        rate_pct = int(data["execution_rate"] * 100)
        lines    = ["EXECUTION STREAK:"]
        lines.append(f"  Current streak: {data['current_streak']} day(s)")
        lines.append(f"  Best streak: {data['best_streak']} day(s)")
        lines.append(f"  90-day execution rate: {rate_pct}% ({data['executed_days']}/{data['total_days']} days)")

        if data.get("gap_days") and data["gap_days"] > 0:
            lines.append(f"  Last execution: {data['gap_days']} day(s) ago")

        result = "\n".join(lines)
        return result[:max_chars]
    except Exception as e:
        log_error("StreakTracker", "build_streak_context", e)
        return ""


def format_streak_display(db: DatabaseManager) -> str:
    """
    Formats streak data for terminal display in the Goals menu.
    Shows a 30-day calendar bar and key stats.
    """
    _ensure_tables(db)
    assess_today(db)
    data    = get_streak_data(db, lookback_days=90)
    history = get_streak_history(db, days=30)

    if not data:
        return "  No execution data yet. Complete a goal step or log an evening sync with output score."

    lines = []

    # 30-day bar
    bar = ""
    today = date.today()
    day_map = {h["day"]: h["executed"] for h in history}
    for i in range(29, -1, -1):
        d  = (today - timedelta(days=i)).isoformat()
        ex = day_map.get(d)
        if ex == 1:
            bar += "█"
        elif ex == 0:
            bar += "░"
        else:
            bar += "·"

    lines.append(f"  30-day execution: {bar}")
    lines.append(f"  (█ = executed  ░ = tracked, not executed  · = not logged)")
    lines.append("")
    lines.append(f"  Current streak : {data['current_streak']} day(s)")
    lines.append(f"  Best streak    : {data['best_streak']} day(s)")
    rate_pct = int(data["execution_rate"] * 100)
    lines.append(f"  90-day rate    : {rate_pct}%  ({data['executed_days']} of {data['total_days']} days)")
    if data.get("gap_days") is not None and data["gap_days"] > 1:
        lines.append(f"  Last executed  : {data['gap_days']} day(s) ago")

    return "\n".join(lines)
