"""
contradiction_engine.py
Personal contradiction and self-deception mapping for the Marlow platform.

What this does:
  - Extracts stated intentions from journal entries and syncs
    (phrases like "I'm going to", "tomorrow I will", "planning to", "going to start")
  - Cross-references those intentions against what the behavioral data shows
    actually happened in the following days (energy, output, goal step completion)
  - After enough data builds a personal contradiction map:
    what conditions reliably produce failed commitments vs kept ones
  - Surfaces these patterns into ALDRIC's decision_context so he can
    challenge commitments made in physiologically incompatible states

This is not a judgment engine. It's a pattern recognition engine.
The point is not "you lied" — it's "when you make this type of commitment
in this type of state, your own historical follow-through rate is X%."

DB tables:
  - stated_intentions  : extracted commitments with timestamp and state
  - intention_outcomes : follow-through assessment 3-7 days later
"""

import re
from datetime import datetime, timedelta
from core.database import DatabaseManager
from core.marlow_logger import log_error, log_warning, log_info


# ── Intention extraction patterns ─────────────────────────────────────────────

_INTENTION_PATTERNS = [
    r"i(?:'m| am) going to (.{10,120})",
    r"i(?:'m| am) planning to (.{10,120})",
    r"tomorrow i(?:'ll| will) (.{10,120})",
    r"i(?:'ll| will) (.{10,120})",
    r"i(?:'m| am) going to start (.{10,120})",
    r"i(?:'m| am) going to stop (.{10,120})",
    r"starting (?:today|tomorrow|monday|this week) (.{10,120})",
    r"going to focus on (.{10,120})",
    r"need to (.{10,120})",
    r"have to (.{10,120})",
    r"decided to (.{10,120})",
    r"committing to (.{10,120})",
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in _INTENTION_PATTERNS]


def _ensure_tables(db: DatabaseManager) -> None:
    try:
        db.conn.execute("""
            CREATE TABLE IF NOT EXISTS stated_intentions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp    TEXT DEFAULT CURRENT_TIMESTAMP,
                source_type  TEXT,
                raw_text     TEXT,
                intention    TEXT,
                state_energy REAL,
                state_mood   REAL,
                state_fog    REAL,
                state_impulse REAL,
                assessed     INTEGER DEFAULT 0
            )
        """)
        db.conn.execute("""
            CREATE TABLE IF NOT EXISTS intention_outcomes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                intention_id    INTEGER NOT NULL,
                assessed_at     TEXT DEFAULT CURRENT_TIMESTAMP,
                days_elapsed    INTEGER,
                avg_output_after REAL,
                avg_energy_after REAL,
                goal_steps_completed INTEGER DEFAULT 0,
                follow_through  TEXT,
                confidence      REAL
            )
        """)
        db.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_intentions_assessed "
            "ON stated_intentions(assessed, timestamp)"
        )
        db.conn.commit()
    except Exception as e:
        log_error("ContradictionEngine", "_ensure_tables", e)


# ── Intention extraction ───────────────────────────────────────────────────────

def extract_intentions(text: str) -> list:
    """
    Extract stated intentions from free text.
    Returns list of intention strings (cleaned).
    """
    found = []
    for pattern in _COMPILED:
        for match in pattern.finditer(text):
            intention = match.group(1).strip().rstrip(".!?,;")
            # Filter noise — too short, too long, or clearly not an intention
            if 10 <= len(intention) <= 150:
                found.append(intention)
    # Deduplicate while preserving order
    seen = set()
    result = []
    for i in found:
        key = i.lower()[:40]
        if key not in seen:
            seen.add(key)
            result.append(i)
    return result


def save_intentions_from_entry(
    db: DatabaseManager,
    text: str,
    source_type: str = "journal",
    state: dict = None
) -> int:
    """
    Extract and save intentions from a journal or sync entry.
    Called after any free-text input is saved.
    Returns number of intentions saved.
    """
    _ensure_tables(db)
    intentions = extract_intentions(text)
    if not intentions:
        return 0

    state = state or {}
    saved = 0
    try:
        for intention in intentions:
            db.conn.execute("""
                INSERT INTO stated_intentions
                (source_type, raw_text, intention, state_energy, state_mood,
                 state_fog, state_impulse)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                source_type,
                text[:500],
                intention,
                state.get("energy"),
                state.get("mood"),
                state.get("fog"),
                state.get("impulse")
            ))
            saved += 1
        db.conn.commit()
        if saved:
            log_info("ContradictionEngine", f"Saved {saved} intentions from {source_type}")
    except Exception as e:
        log_error("ContradictionEngine", "save_intentions_from_entry", e)
    return saved


# ── Follow-through assessment ──────────────────────────────────────────────────

def assess_pending_intentions(db: DatabaseManager, assess_after_days: int = 4) -> int:
    """
    Looks at unassessed intentions older than assess_after_days.
    For each, pulls the behavioral data from the following window and
    scores follow-through based on output scores and goal step completion.

    Called at startup (0 Groq calls — fully algorithmic).
    Returns number of intentions assessed.
    """
    _ensure_tables(db)
    cutoff = (datetime.now() - timedelta(days=assess_after_days)).isoformat()

    try:
        rows = db.conn.execute("""
            SELECT * FROM stated_intentions
            WHERE assessed = 0 AND timestamp <= ?
            ORDER BY timestamp ASC
            LIMIT 50
        """, (cutoff,)).fetchall()
    except Exception as e:
        log_error("ContradictionEngine", "assess_pending_intentions", e)
        return 0

    assessed = 0
    for row in rows:
        try:
            intention_ts = row["timestamp"]
            window_start = intention_ts
            window_end   = (
                datetime.fromisoformat(intention_ts) + timedelta(days=assess_after_days + 3)
            ).isoformat()

            # Pull output and energy from logs in window
            metrics = db.conn.execute("""
                SELECT sync_type, content FROM logs
                WHERE timestamp BETWEEN ? AND ?
                ORDER BY timestamp ASC
            """, (window_start, window_end)).fetchall()

            output_scores = []
            energy_scores = []
            for m in metrics:
                content = m["content"] if m["content"] else ""
                out_match    = re.search(r"output[:\s]+(\d+(?:\.\d+)?)", content, re.I)
                energy_match = re.search(r"energy[:\s]+(\d+(?:\.\d+)?)", content, re.I)
                if out_match:
                    output_scores.append(float(out_match.group(1)))
                if energy_match:
                    energy_scores.append(float(energy_match.group(1)))

            # Pull goal step completions in window
            try:
                steps_done = db.conn.execute("""
                    SELECT COUNT(*) FROM goal_steps
                    WHERE status = 'complete'
                    AND completed_at BETWEEN ? AND ?
                """, (window_start, window_end)).fetchone()[0]
            except Exception:
                steps_done = 0

            avg_output = round(sum(output_scores) / len(output_scores), 2) if output_scores else None
            avg_energy = round(sum(energy_scores) / len(energy_scores), 2) if energy_scores else None

            # Score follow-through
            # High output (7+) + any steps done = likely followed through
            # Low output (<4) + no steps = likely didn't
            if avg_output is not None:
                if avg_output >= 7 or steps_done >= 1:
                    follow_through = "likely_kept"
                    confidence     = min(0.9, 0.5 + (avg_output / 20) + (steps_done * 0.1))
                elif avg_output <= 4 and steps_done == 0:
                    follow_through = "likely_broken"
                    confidence     = min(0.85, 0.5 + ((10 - avg_output) / 20))
                else:
                    follow_through = "unclear"
                    confidence     = 0.4
            else:
                follow_through = "insufficient_data"
                confidence     = 0.2

            db.conn.execute("""
                INSERT INTO intention_outcomes
                (intention_id, days_elapsed, avg_output_after, avg_energy_after,
                 goal_steps_completed, follow_through, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                row["id"],
                assess_after_days,
                avg_output,
                avg_energy,
                steps_done,
                follow_through,
                confidence
            ))
            db.conn.execute(
                "UPDATE stated_intentions SET assessed = 1 WHERE id = ?", (row["id"],)
            )
            assessed += 1

        except Exception as e:
            log_error("ContradictionEngine", f"assess row {row['id']}", e)
            continue

    if assessed:
        db.conn.commit()
        log_info("ContradictionEngine", f"Assessed {assessed} intentions")

    return assessed


# ── Pattern analysis ───────────────────────────────────────────────────────────

def build_contradiction_map(db: DatabaseManager, min_samples: int = 5) -> dict:
    """
    Builds the personal contradiction map from assessed intention outcomes.
    Groups by state conditions at time of commitment to find patterns.

    Returns dict with:
      - total_assessed: int
      - follow_through_rate: float (0-1)
      - high_impulse_rate: float — follow-through when impulse >= 6
      - low_energy_rate: float  — follow-through when energy <= 4
      - best_state: dict        — avg state conditions when commitments ARE kept
      - worst_state: dict       — avg state conditions when commitments are BROKEN
      - patterns: list of strings (human-readable)
    """
    _ensure_tables(db)

    try:
        rows = db.conn.execute("""
            SELECT si.state_energy, si.state_mood, si.state_fog, si.state_impulse,
                   io.follow_through, io.confidence
            FROM stated_intentions si
            JOIN intention_outcomes io ON si.id = io.intention_id
            WHERE io.follow_through != 'insufficient_data'
            AND io.confidence >= 0.4
            ORDER BY si.timestamp DESC
            LIMIT 200
        """).fetchall()
    except Exception as e:
        log_error("ContradictionEngine", "build_contradiction_map", e)
        return {}

    if len(rows) < min_samples:
        return {"total_assessed": len(rows), "insufficient_data": True}

    kept   = [r for r in rows if r["follow_through"] == "likely_kept"]
    broken = [r for r in rows if r["follow_through"] == "likely_broken"]

    def avg_state(group, field):
        vals = [r[field] for r in group if r[field] is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    # High impulse broken rate
    high_impulse = [r for r in rows if r["state_impulse"] and r["state_impulse"] >= 6]
    hi_broken    = [r for r in high_impulse if r["follow_through"] == "likely_broken"]
    hi_rate      = round(len(hi_broken) / len(high_impulse), 2) if high_impulse else None

    # Low energy broken rate
    low_energy = [r for r in rows if r["state_energy"] and r["state_energy"] <= 4]
    le_broken  = [r for r in low_energy if r["follow_through"] == "likely_broken"]
    le_rate    = round(len(le_broken) / len(low_energy), 2) if low_energy else None

    overall_rate = round(len(kept) / len(rows), 2) if rows else None

    # Build pattern strings for context injection
    patterns = []

    if overall_rate is not None:
        patterns.append(
            f"Overall follow-through rate: {int(overall_rate * 100)}% "
            f"({len(kept)} kept / {len(broken)} broken of {len(rows)} assessed)"
        )

    if hi_rate is not None and len(high_impulse) >= 3:
        patterns.append(
            f"When impulse >= 6 at time of commitment: {int(hi_rate * 100)}% break rate "
            f"({len(hi_broken)}/{len(high_impulse)} commitments)"
        )

    if le_rate is not None and len(low_energy) >= 3:
        patterns.append(
            f"When energy <= 4 at time of commitment: {int(le_rate * 100)}% break rate "
            f"({len(le_broken)}/{len(low_energy)} commitments)"
        )

    best_energy  = avg_state(kept, "state_energy")
    worst_energy = avg_state(broken, "state_energy")
    if best_energy and worst_energy:
        patterns.append(
            f"Kept commitments: avg energy {best_energy} at time made. "
            f"Broken commitments: avg energy {worst_energy}."
        )

    best_impulse  = avg_state(kept, "state_impulse")
    worst_impulse = avg_state(broken, "state_impulse")
    if best_impulse and worst_impulse:
        patterns.append(
            f"Kept commitments: avg impulse {best_impulse}. "
            f"Broken commitments: avg impulse {worst_impulse}."
        )

    return {
        "total_assessed":     len(rows),
        "follow_through_rate": overall_rate,
        "high_impulse_break_rate": hi_rate,
        "low_energy_break_rate":   le_rate,
        "best_state": {
            "energy":  best_energy,
            "impulse": best_impulse,
            "fog":     avg_state(kept, "state_fog"),
        },
        "worst_state": {
            "energy":  worst_energy,
            "impulse": worst_impulse,
            "fog":     avg_state(broken, "state_fog"),
        },
        "patterns": patterns
    }


def build_contradiction_context(db: DatabaseManager, max_chars: int = 500) -> str:
    """
    Formats contradiction map for injection into ALDRIC's prompt context.
    Returns empty string if insufficient data.
    """
    try:
        cmap = build_contradiction_map(db)
        if not cmap or cmap.get("insufficient_data"):
            n = cmap.get("total_assessed", 0) if cmap else 0
            if n > 0:
                return f"COMMITMENT TRACKING: {n} commitments assessed — insufficient data for pattern analysis yet."
            return ""

        lines = ["COMMITMENT PATTERNS (personal contradiction map):"]
        for p in cmap.get("patterns", []):
            lines.append(f"  {p}")

        result = "\n".join(lines)
        return result[:max_chars]
    except Exception as e:
        log_error("ContradictionEngine", "build_contradiction_context", e)
        return ""
