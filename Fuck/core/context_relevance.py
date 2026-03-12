"""
context_relevance.py
Conditional context loading for Marlow Platform 3.

Each context block has a relevance check — a fast SQLite query that determines
whether the block is worth computing this session.

The principle: don't compute what isn't relevant to what's actually happening.

Substance context when no substances logged in 14+ days = empty block, wasted compute.
Contradiction context when no pending intentions exist = empty block, wasted compute.
Streak context when no active goals = empty block, wasted compute.

This runs before run_council() assembles context. Each flag is a boolean.
run_council() reads the flags and skips blocks that aren't active.

All checks are pure SQLite reads — no computation, no Groq.
Worst case: 6 queries, each returning a COUNT or MAX. Under 5ms total.

Substance tiering:
  ACTIVE   (use in last 14 days)    → full correlation engine
  RECENT   (use in last 14-30 days) → cached summary only, no recompute
  INACTIVE (no use in 30+ days)     → substance_ctx silent, zero cost
"""

from datetime import datetime, timedelta


# ── Substance keywords scanned across sync logs ───────────────────────────────
# Matches fuel, taboo, chaos_activity fields from syncs and journal entries.
# Intentionally broad — catches partial mentions.
_SUBSTANCE_KEYWORDS = [
    "alcohol", "beer", "wine", "whisky", "whiskey", "vodka", "drinking", "drunk",
    "weed", "cannabis", "marijuana", "smoke", "smoked", "smoking",
    "cocaine", "coke", "blow",
    "meth", "methamphetamine", "crystal",
    "mdma", "molly", "ecstasy",
    "xanax", "benzo", "valium",
    "adderall", "vyvanse", "ritalin",
    "shrooms", "mushrooms", "psilocybin",
    "pills", "prescription",
    "high", "stoned", "wired", "coming down", "comedown", "crashed after",
    "withdrawal", "withdrawing", "cravings", "craving"
]


def _days_since(db, table: str, timestamp_col: str = "timestamp") -> int:
    """
    Returns days since the most recent entry in a table.
    Returns 9999 if no entries exist.
    """
    try:
        row = db.conn.execute(
            f"SELECT MAX({timestamp_col}) FROM {table}"
        ).fetchone()
        if not row or not row[0]:
            return 9999
        last_dt = datetime.fromisoformat(str(row[0])[:19])
        return (datetime.now() - last_dt).days
    except Exception:
        return 9999


def _substance_activity_window(db) -> str:
    """
    Checks last 30 days of log content for substance keywords.
    Returns:
        "active"   — substance keyword found in last 14 days
        "recent"   — keyword found in 14-30 days, nothing in last 14
        "inactive" — no keyword found in last 30 days
    """
    try:
        cutoff_14 = (datetime.now() - timedelta(days=14)).isoformat()
        cutoff_30 = (datetime.now() - timedelta(days=30)).isoformat()

        # Pull last 30 days of log content
        rows = db.conn.execute(
            "SELECT timestamp, content FROM logs WHERE timestamp >= ? ORDER BY timestamp DESC",
            (cutoff_30,)
        ).fetchall()

        if not rows:
            return "inactive"

        found_14  = False
        found_30  = False

        for row in rows:
            ts      = str(row[0])
            content = str(row[1] or "").lower()
            is_14   = ts >= cutoff_14

            for keyword in _SUBSTANCE_KEYWORDS:
                if keyword in content:
                    if is_14:
                        found_14 = True
                    else:
                        found_30 = True
                    break  # one keyword per row is enough

        if found_14:
            return "active"
        elif found_30:
            return "recent"
        else:
            return "inactive"

    except Exception:
        # On failure, assume active — safer to over-include than miss
        return "active"


def _has_pending_intentions(db) -> bool:
    """
    Returns True if there are stated intentions in the contradiction engine
    that have not yet been assessed (pending follow-through check).
    """
    try:
        count = db.conn.execute(
            """
            SELECT COUNT(*) FROM stated_intentions
            WHERE assessed = 0
              AND datetime(created_at) <= datetime('now', '-4 days')
            """
        ).fetchone()
        return (count[0] if count else 0) > 0
    except Exception:
        # Table may not exist yet — not an error
        return False


def _has_active_goals(db) -> bool:
    """
    Returns True if there is at least one goal with status = 'active'.
    """
    try:
        count = db.conn.execute(
            "SELECT COUNT(*) FROM goals WHERE status = 'active'"
        ).fetchone()
        return (count[0] if count else 0) > 0
    except Exception:
        return False


def _has_pending_decisions(db) -> bool:
    """
    Returns True if there are decisions logged that are either:
    - Pending retrospective rating (outcome_score IS NULL, review_due_at passed)
    - Logged in last 7 days (recent enough to be relevant for context)
    """
    try:
        # Pending reviews
        pending = db.conn.execute(
            """
            SELECT COUNT(*) FROM decision_log
            WHERE outcome_score IS NULL
              AND review_due_at <= ?
            """,
            (datetime.now().isoformat(),)
        ).fetchone()
        if pending and pending[0] > 0:
            return True

        # Recent decisions (last 7 days)
        recent = db.conn.execute(
            """
            SELECT COUNT(*) FROM decision_log
            WHERE timestamp >= ?
            """,
            ((datetime.now() - timedelta(days=7)).isoformat(),)
        ).fetchone()
        return (recent[0] if recent else 0) > 0

    except Exception:
        return False


def _has_sufficient_pattern_data(db, min_entries: int = 5) -> bool:
    """
    Returns True if there are enough log entries for PatternEngine to produce
    meaningful output. Below 5 entries the engine runs but produces noise.
    """
    try:
        count = db.conn.execute(
            "SELECT COUNT(*) FROM logs"
        ).fetchone()
        return (count[0] if count else 0) >= min_entries
    except Exception:
        return True  # assume sufficient, don't skip pattern engine by default


def _has_new_data_since_cache(db, cache_key: str) -> bool:
    """
    Returns True if a new log or metric has been saved since the cache entry
    for this key was computed.

    Used to invalidate trend_report and session_brief when new data was logged
    rather than relying purely on TTL.
    """
    try:
        # Get cache timestamp
        cache_row = db.conn.execute(
            """
            SELECT computed_at FROM context_cache
            WHERE cache_key = ?
              AND datetime(expires_at) > datetime('now')
            ORDER BY id DESC LIMIT 1
            """,
            (cache_key,)
        ).fetchone()

        if not cache_row:
            return True  # no cache entry — treat as new data

        cached_at = cache_row[0]

        # Check if any log or metric was saved after the cache was computed
        new_log = db.conn.execute(
            "SELECT COUNT(*) FROM logs WHERE timestamp > ?",
            (cached_at,)
        ).fetchone()
        if new_log and new_log[0] > 0:
            return True

        new_metric = db.conn.execute(
            "SELECT COUNT(*) FROM metrics WHERE timestamp > ?",
            (cached_at,)
        ).fetchone()
        return (new_metric[0] if new_metric else 0) > 0

    except Exception:
        return True  # on failure, treat as new data — safer to recompute


def get_active_context_flags(db) -> dict:
    """
    Main entry point. Runs all relevance checks and returns a flag dict.

    Called once per council session at the start of run_council().
    All checks are fast SQLite reads — no Groq, no computation.

    Returns dict with keys:
        substance_window:        "active" | "recent" | "inactive"
        substance_active:        True if full engine should run
        substance_use_cache:     True if cached summary sufficient (recent window)
        substance_skip:          True if substance_ctx should be silent entirely
        contradictions_pending:  True if contradiction context worth building
        streak_active:           True if streak context worth building
        decisions_pending:       True if decision context worth building
        pattern_sufficient_data: True if PatternEngine has enough data
        trend_needs_refresh:     True if trend report cache is stale or missing
        brief_needs_refresh:     True if session brief cache is stale or new sync logged
    """
    substance_window = _substance_activity_window(db)

    flags = {
        "substance_window":        substance_window,
        "substance_active":        substance_window == "active",
        "substance_use_cache":     substance_window == "recent",
        "substance_skip":          substance_window == "inactive",
        "contradictions_pending":  _has_pending_intentions(db),
        "streak_active":           _has_active_goals(db),
        "decisions_pending":       _has_pending_decisions(db),
        "pattern_sufficient_data": _has_sufficient_pattern_data(db),
        "trend_needs_refresh":     _has_new_data_since_cache(db, "trend_report"),
        "brief_needs_refresh":     _has_new_data_since_cache(db, "session_brief"),
    }

    return flags


def format_flags_for_log(flags: dict) -> str:
    """
    Formats the context flags dict as a readable one-liner for logging/debug.
    """
    active = []
    skipped = []

    if flags.get("substance_active"):
        active.append("substance(full)")
    elif flags.get("substance_use_cache"):
        active.append("substance(cache)")
    else:
        skipped.append("substance")

    if flags.get("contradictions_pending"):
        active.append("contradictions")
    else:
        skipped.append("contradictions")

    if flags.get("streak_active"):
        active.append("streak")
    else:
        skipped.append("streak")

    if flags.get("decisions_pending"):
        active.append("decisions")
    else:
        skipped.append("decisions")

    return f"active=[{', '.join(active)}] skipped=[{', '.join(skipped)}]"
