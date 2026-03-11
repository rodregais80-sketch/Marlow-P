"""
memory_consolidator.py
Tiered memory compression system for the Marlow platform.

Architecture:
  Tier 1 — Raw (0-30 days):     Full sync logs. No compression. Existing behavior.
  Tier 2 — Weekly (30-90 days): Algorithmic compression. No Groq cost.
  Tier 3 — Monthly (90d-1yr):   Groq-generated narrative summaries per persona.
  Tier 4 — Annual (1yr+):       Groq-generated arc summaries per persona.

Each persona compresses only data relevant to its domain:
  ALDRIC  — decisions, goals, strategic moves, outcomes
  SEREN   — emotional events, mental health patterns, relationship events
  ORYN    — substance use, sleep, biological patterns, physical health events
  MORRO   — impulse events, risky decisions, near-misses, avoidance patterns

Manual pin system:
  Any persona can have specific memories manually pinned by the operator.
  Pinned memories always surface regardless of age tier.

Startup behavior:
  maybe_consolidate_memory(db) fires once on startup.
  Checks if any compression pass is due. Silent if not.
  Weekly pass runs if last run was 7+ days ago.
  Monthly pass runs if last run was 30+ days ago.
  Annual pass runs if last run was 365+ days ago.
"""

import re
from datetime import datetime, timedelta
from core.database import DatabaseManager
from core.groq_client import chat_completion as groq_chat, is_available as groq_available

# ── Domain filters per persona ────────────────────────────────────────────────
# Defines what each persona cares about when compressing historical data.

PERSONA_DOMAINS = {
    "ALDRIC": {
        "label": "strategic/economic",
        "focus": (
            "strategic decisions, goal progress, business moves, financial events, "
            "outcomes of past choices, momentum patterns, leverage points, "
            "periods of high execution vs stagnation"
        ),
        "metric_fields": ["energy", "mood"],
        "keywords": [
            "goal", "decision", "plan", "strategy", "money", "business",
            "invest", "build", "execute", "fail", "succeed", "progress",
            "work", "project", "income", "opportunity", "risk", "outcome"
        ]
    },
    "SEREN": {
        "label": "emotional/psychological",
        "focus": (
            "emotional state patterns, mental health events, relationship changes, "
            "crisis points, what helped vs what didn't, support system changes, "
            "periods of clarity vs darkness, grief, connection, isolation"
        ),
        "metric_fields": ["mood", "mental_fog"],
        "keywords": [
            "feel", "felt", "emotion", "sad", "happy", "anxious", "depressed",
            "angry", "hurt", "lonely", "connected", "relationship", "friend",
            "family", "breakdown", "cry", "grief", "loss", "love", "conflict",
            "therapy", "support", "mental", "mood", "low", "dark", "hope"
        ]
    },
    "ORYN": {
        "label": "biological/physiological",
        "focus": (
            "substance use patterns and outcomes, sleep quality over time, "
            "energy baselines, fog levels, physical health events, "
            "what substances were used and their next-day effects, "
            "biological turning points, crash patterns, recovery periods"
        ),
        "metric_fields": ["energy", "sleep_hours", "mental_fog", "impulse_drive"],
        "keywords": [
            "sleep", "energy", "fog", "substance", "drink", "smoke", "weed",
            "cannabis", "alcohol", "caffeine", "pill", "drug", "sober", "clean",
            "tired", "exhausted", "wired", "crash", "sick", "headache",
            "body", "health", "physical", "exercise", "gym", "food", "eat"
        ]
    },
    "MORRO": {
        "label": "shadow/impulse",
        "focus": (
            "impulse events, reckless decisions, things being avoided, "
            "near-misses, patterns of self-sabotage, moments of shadow behavior, "
            "what the operator knew was wrong but did anyway, avoidance patterns"
        ),
        "metric_fields": ["impulse_drive"],
        "keywords": [
            "impulse", "urge", "risk", "impulsive", "sabotage", "avoid",
            "procrastinate", "escape", "numb", "ignore", "reckless", "stupid",
            "regret", "waste", "distract", "addict", "binge", "lose control",
            "shouldn't", "knew better", "self-destruct", "spiral"
        ]
    }
}

# ── DB setup ──────────────────────────────────────────────────────────────────

def _ensure_tables(db: DatabaseManager):
    """Creates compressed_memories and pinned_memories tables if they don't exist."""
    db.conn.execute("""
        CREATE TABLE IF NOT EXISTS compressed_memories (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            persona_name  TEXT    NOT NULL,
            tier          TEXT    NOT NULL,
            period_start  TEXT    NOT NULL,
            period_end    TEXT    NOT NULL,
            content       TEXT    NOT NULL,
            generated_at  TEXT    NOT NULL
        )
    """)
    db.conn.execute("""
        CREATE TABLE IF NOT EXISTS pinned_memories (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            persona_name  TEXT    NOT NULL,
            content       TEXT    NOT NULL,
            pinned_at     TEXT    NOT NULL,
            pinned_by     TEXT    NOT NULL DEFAULT 'manual'
        )
    """)
    db.conn.execute("""
        CREATE TABLE IF NOT EXISTS consolidation_log (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            tier          TEXT    NOT NULL,
            ran_at        TEXT    NOT NULL
        )
    """)
    db.conn.commit()


def _last_consolidation(db: DatabaseManager, tier: str):
    """Returns datetime of last consolidation run for a given tier, or None."""
    _ensure_tables(db)
    row = db.conn.execute(
        "SELECT ran_at FROM consolidation_log WHERE tier = ? ORDER BY id DESC LIMIT 1",
        (tier,)
    ).fetchone()
    if not row:
        return None
    try:
        return datetime.fromisoformat(row[0])
    except Exception:
        return None


def _log_consolidation(db: DatabaseManager, tier: str):
    """Records that a consolidation pass ran now."""
    db.conn.execute(
        "INSERT INTO consolidation_log (tier, ran_at) VALUES (?, ?)",
        (tier, datetime.now().isoformat())
    )
    db.conn.commit()


def _save_compressed(
    db: DatabaseManager,
    persona_name: str,
    tier: str,
    period_start: str,
    period_end: str,
    content: str
):
    """Saves a compressed memory block to the DB."""
    db.conn.execute(
        """INSERT INTO compressed_memories
           (persona_name, tier, period_start, period_end, content, generated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (persona_name, tier, period_start, period_end, content, datetime.now().isoformat())
    )
    db.conn.commit()


def _get_compressed(
    db: DatabaseManager,
    persona_name: str,
    tier: str,
    since_date: datetime = None
) -> list:
    """Retrieves compressed memory entries for a persona, optionally filtered by date."""
    _ensure_tables(db)
    if since_date:
        rows = db.conn.execute(
            """SELECT period_start, period_end, content, generated_at
               FROM compressed_memories
               WHERE persona_name = ? AND tier = ? AND period_end >= ?
               ORDER BY period_start ASC""",
            (persona_name, tier, since_date.isoformat())
        ).fetchall()
    else:
        rows = db.conn.execute(
            """SELECT period_start, period_end, content, generated_at
               FROM compressed_memories
               WHERE persona_name = ? AND tier = ?
               ORDER BY period_start ASC""",
            (persona_name, tier)
        ).fetchall()
    return rows


def _already_compressed(db: DatabaseManager, persona_name: str, tier: str, period_start: str) -> bool:
    """Returns True if this period has already been compressed for this persona."""
    row = db.conn.execute(
        """SELECT id FROM compressed_memories
           WHERE persona_name = ? AND tier = ? AND period_start = ?""",
        (persona_name, tier, period_start)
    ).fetchone()
    return row is not None


# ── Domain relevance scoring ──────────────────────────────────────────────────

def _score_relevance(text: str, persona_name: str) -> int:
    """
    Returns a relevance score for a piece of text against a persona's domain.
    Higher = more relevant. Used to filter what gets included in compression.
    """
    if not text:
        return 0
    domain = PERSONA_DOMAINS.get(persona_name, {})
    keywords = domain.get("keywords", [])
    text_lower = text.lower()
    return sum(1 for kw in keywords if kw in text_lower)


# ── Raw data retrieval helpers ────────────────────────────────────────────────

def _get_metrics_for_period(db: DatabaseManager, start: datetime, end: datetime) -> list:
    """Pulls all sync metrics between two dates."""
    try:
        rows = db.conn.execute(
            """SELECT * FROM daily_metrics
               WHERE timestamp >= ? AND timestamp < ?
               ORDER BY timestamp ASC""",
            (start.isoformat(), end.isoformat())
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _get_journals_for_period(db: DatabaseManager, start: datetime, end: datetime) -> list:
    """Pulls all journal entries between two dates."""
    try:
        rows = db.conn.execute(
            """SELECT * FROM journal_entries
               WHERE timestamp >= ? AND timestamp < ?
               ORDER BY timestamp ASC""",
            (start.isoformat(), end.isoformat())
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _get_logs_for_period(db: DatabaseManager, start: datetime, end: datetime) -> list:
    """Pulls all sync logs between two dates."""
    try:
        rows = db.conn.execute(
            """SELECT * FROM activity_logs
               WHERE timestamp >= ? AND timestamp < ?
               ORDER BY timestamp ASC""",
            (start.isoformat(), end.isoformat())
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _get_decisions_for_period(db: DatabaseManager, start: datetime, end: datetime) -> list:
    """Pulls decision log entries for a period."""
    try:
        rows = db.conn.execute(
            """SELECT * FROM decision_log
               WHERE logged_at >= ? AND logged_at < ?
               ORDER BY logged_at ASC""",
            (start.isoformat(), end.isoformat())
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ── Tier 2: Weekly algorithmic compression ────────────────────────────────────

def _compress_week_algorithmic(
    db: DatabaseManager,
    week_start: datetime,
    week_end: datetime
) -> dict:
    """
    Compresses one week of data algorithmically per persona.
    No Groq call. Pure signal extraction.

    Returns a dict: {persona_name: compressed_string}
    """
    metrics  = _get_metrics_for_period(db, week_start, week_end)
    journals = _get_journals_for_period(db, week_start, week_end)
    logs     = _get_logs_for_period(db, week_start, week_end)
    decisions = _get_decisions_for_period(db, week_start, week_end)

    period_label = f"{week_start.strftime('%b %d')}–{week_end.strftime('%b %d, %Y')}"
    results = {}

    for persona_name, domain in PERSONA_DOMAINS.items():
        metric_fields = domain.get("metric_fields", [])

        # ── Metric averages for this persona's domain fields ──────────────
        metric_lines = []
        for field in metric_fields:
            vals = [
                m.get(field) for m in metrics
                if m.get(field) is not None
            ]
            if vals:
                avg = round(sum(vals) / len(vals), 1)
                mn  = min(vals)
                mx  = max(vals)
                metric_lines.append(f"{field.replace('_', ' ').title()}: avg {avg}/10 (range {mn}–{mx})")

        # ── Journal relevance filter ───────────────────────────────────────
        relevant_journals = []
        for j in journals:
            content = j.get("content", "") or j.get("entry", "") or ""
            score = _score_relevance(content, persona_name)
            if score > 0:
                snippet = content[:200].replace("\n", " ")
                relevant_journals.append(snippet)

        # ── Log relevance filter ──────────────────────────────────────────
        relevant_logs = []
        for log in logs:
            content = log.get("content", "") or ""
            score = _score_relevance(content, persona_name)
            if score > 0:
                snippet = content[:150].replace("\n", " ")
                relevant_logs.append(snippet)

        # ── Decisions (ALDRIC and MORRO only) ─────────────────────────────
        decision_lines = []
        if persona_name in ("ALDRIC", "MORRO") and decisions:
            for d in decisions[:3]:
                desc    = (d.get("decision") or d.get("description") or "")[:100]
                outcome = d.get("outcome_rating")
                if desc:
                    outcome_str = f" [rated {outcome}/10]" if outcome else " [unrated]"
                    decision_lines.append(f"Decision: {desc}{outcome_str}")

        # ── Assemble compression block ─────────────────────────────────────
        if not metric_lines and not relevant_journals and not relevant_logs and not decision_lines:
            results[persona_name] = f"[Week of {period_label}] No significant {domain['label']} data."
            continue

        parts = [f"[Week of {period_label}]"]

        if metric_lines:
            parts.append("Metrics: " + " | ".join(metric_lines))

        if decision_lines:
            parts.append("\n".join(decision_lines))

        if relevant_journals:
            parts.append("Journal signals: " + " // ".join(relevant_journals[:3]))

        if relevant_logs:
            parts.append("Sync signals: " + " // ".join(relevant_logs[:2]))

        results[persona_name] = "\n".join(parts)

    return results


# ── Tier 3: Monthly Groq compression ─────────────────────────────────────────

def _compress_month_groq(
    db: DatabaseManager,
    month_start: datetime,
    month_end: datetime
) -> dict:
    """
    Compresses one month of data using Groq per persona.
    Pulls weekly summaries for that month and synthesizes them into a narrative.

    Returns a dict: {persona_name: compressed_string}
    """
    period_label = month_start.strftime("%B %Y")
    results = {}

    for persona_name, domain in PERSONA_DOMAINS.items():
        # Pull weekly summaries from that month
        weekly_rows = _get_compressed(db, persona_name, "weekly", since_date=month_start)
        weekly_in_range = [
            r for r in weekly_rows
            if r[0] >= month_start.isoformat() and r[0] < month_end.isoformat()
        ]

        if not weekly_in_range:
            # Fall back to raw data if no weekly summaries exist
            metrics  = _get_metrics_for_period(db, month_start, month_end)
            journals = _get_journals_for_period(db, month_start, month_end)
            raw_text = ""
            if metrics:
                field = domain["metric_fields"][0] if domain["metric_fields"] else "energy"
                vals = [m.get(field) for m in metrics if m.get(field) is not None]
                if vals:
                    raw_text += f"Average {field}: {round(sum(vals)/len(vals), 1)}/10 over {len(vals)} syncs. "
            for j in journals[:5]:
                content = j.get("content", "") or j.get("entry", "") or ""
                if _score_relevance(content, persona_name) > 0:
                    raw_text += content[:200] + " "
            if not raw_text:
                results[persona_name] = f"[{period_label}] No significant {domain['label']} data recorded."
                continue
            source_text = raw_text[:2000]
        else:
            source_text = "\n\n".join([r[2] for r in weekly_in_range])

        if not groq_available():
            # Fallback: concatenate weekly summaries without synthesis
            results[persona_name] = f"[{period_label}]\n{source_text[:600]}"
            continue

        prompt = f"""You are the {persona_name} persona from the Marlow intelligence system.
Your domain: {domain['focus']}

You are compressing one month of data ({period_label}) into a compact memory block.
Your goal: preserve only what is signal-level important for your domain.
Discard noise. Keep turning points, patterns, and key events.

Source data (weekly summaries or raw logs):
{source_text}

Write a compressed monthly memory block in 3-6 sentences.
Focus strictly on your domain: {domain['label']}.
Be specific. Use concrete language. No filler. No hedging.
Format: plain paragraph, no headers, no bullet points."""

        try:
            response = groq_chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=300
            )
            results[persona_name] = f"[{period_label}]\n{response.strip()}"
        except Exception as e:
            results[persona_name] = f"[{period_label}] Compression failed: {str(e)[:60]}"

    return results


# ── Tier 4: Annual Groq compression ──────────────────────────────────────────

def _compress_year_groq(
    db: DatabaseManager,
    year_start: datetime,
    year_end: datetime
) -> dict:
    """
    Compresses one year of data using Groq per persona.
    Pulls monthly summaries and synthesizes into a high-level arc.

    Returns a dict: {persona_name: compressed_string}
    """
    year_label = year_start.strftime("%Y")
    results = {}

    for persona_name, domain in PERSONA_DOMAINS.items():
        monthly_rows = _get_compressed(db, persona_name, "monthly", since_date=year_start)
        monthly_in_range = [
            r for r in monthly_rows
            if r[0] >= year_start.isoformat() and r[0] < year_end.isoformat()
        ]

        if not monthly_in_range:
            results[persona_name] = f"[{year_label}] Insufficient monthly summaries to generate annual arc."
            continue

        source_text = "\n\n".join([r[2] for r in monthly_in_range])

        if not groq_available():
            results[persona_name] = f"[{year_label}]\n{source_text[:800]}"
            continue

        prompt = f"""You are the {persona_name} persona from the Marlow intelligence system.
Your domain: {domain['focus']}

You are compressing an entire year ({year_label}) into a single memory arc.
This will be the only record the system retains for this year beyond quarterly markers.

Monthly summaries for the year:
{source_text}

Write a 4-8 sentence annual arc that captures:
- The dominant {domain['label']} pattern of this year
- Key turning points or phase shifts
- How the year started vs how it ended
- What this year means for understanding the operator's trajectory

Be honest. Be specific. No filler. This is the permanent record."""

        try:
            response = groq_chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.35,
                max_tokens=400
            )
            results[persona_name] = f"[{year_label} Annual Arc]\n{response.strip()}"
        except Exception as e:
            results[persona_name] = f"[{year_label}] Annual compression failed: {str(e)[:60]}"

    return results


# ── Main consolidation pass ───────────────────────────────────────────────────

def maybe_consolidate_memory(db: DatabaseManager) -> list:
    """
    Startup check. Fires compression passes if any tier is due.
    Returns a list of action strings taken (for debug/logging).
    Silent if nothing is due.

    Call this once at startup in marlow.py after db is initialized.
    """
    _ensure_tables(db)
    actions = []
    now     = datetime.now()

    # ── Weekly pass ───────────────────────────────────────────────────────
    # Runs if last weekly pass was 7+ days ago.
    # Compresses all complete weeks older than 30 days that haven't been compressed yet.
    last_weekly = _last_consolidation(db, "weekly")
    should_run_weekly = (
        last_weekly is None or
        (now - last_weekly).days >= 7
    )

    if should_run_weekly:
        cutoff = now - timedelta(days=30)
        # Find the earliest data we have
        try:
            oldest_row = db.conn.execute(
                "SELECT MIN(timestamp) FROM daily_metrics"
            ).fetchone()
            oldest_ts = oldest_row[0] if oldest_row and oldest_row[0] else None
        except Exception:
            oldest_ts = None

        if oldest_ts:
            try:
                oldest_dt = datetime.fromisoformat(oldest_ts[:19])
            except Exception:
                oldest_dt = now - timedelta(days=90)

            # Walk through weeks from oldest to cutoff
            week_cursor = oldest_dt.replace(hour=0, minute=0, second=0, microsecond=0)
            # Align to Monday
            week_cursor -= timedelta(days=week_cursor.weekday())
            weeks_compressed = 0

            while week_cursor + timedelta(days=7) < cutoff:
                week_end = week_cursor + timedelta(days=7)
                period_start_str = week_cursor.isoformat()

                # Check if any persona needs this week compressed
                needs_compression = any(
                    not _already_compressed(db, pname, "weekly", period_start_str)
                    for pname in PERSONA_DOMAINS
                )

                if needs_compression:
                    compressed = _compress_week_algorithmic(db, week_cursor, week_end)
                    for persona_name, content in compressed.items():
                        if not _already_compressed(db, persona_name, "weekly", period_start_str):
                            _save_compressed(
                                db, persona_name, "weekly",
                                period_start_str, week_end.isoformat(), content
                            )
                    weeks_compressed += 1

                week_cursor = week_end

            if weeks_compressed > 0:
                actions.append(f"Weekly compression: {weeks_compressed} week(s) processed")

        _log_consolidation(db, "weekly")

    # ── Monthly pass ──────────────────────────────────────────────────────
    # Runs if last monthly pass was 30+ days ago.
    # Compresses all complete months older than 90 days.
    last_monthly = _last_consolidation(db, "monthly")
    should_run_monthly = (
        last_monthly is None or
        (now - last_monthly).days >= 30
    )

    if should_run_monthly:
        cutoff = now - timedelta(days=90)
        month_cursor = (now - timedelta(days=365)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        months_compressed = 0

        while True:
            # Next month
            if month_cursor.month == 12:
                month_end = month_cursor.replace(year=month_cursor.year + 1, month=1)
            else:
                month_end = month_cursor.replace(month=month_cursor.month + 1)

            if month_end > cutoff:
                break

            period_start_str = month_cursor.isoformat()
            needs_compression = any(
                not _already_compressed(db, pname, "monthly", period_start_str)
                for pname in PERSONA_DOMAINS
            )

            if needs_compression:
                compressed = _compress_month_groq(db, month_cursor, month_end)
                for persona_name, content in compressed.items():
                    if not _already_compressed(db, persona_name, "monthly", period_start_str):
                        _save_compressed(
                            db, persona_name, "monthly",
                            period_start_str, month_end.isoformat(), content
                        )
                months_compressed += 1

            month_cursor = month_end

        if months_compressed > 0:
            actions.append(f"Monthly compression: {months_compressed} month(s) processed ({months_compressed * 4} Groq calls used)")

        _log_consolidation(db, "monthly")

    # ── Annual pass ───────────────────────────────────────────────────────
    # Runs if last annual pass was 365+ days ago.
    # Compresses complete years older than 365 days.
    last_annual = _last_consolidation(db, "annual")
    should_run_annual = (
        last_annual is None or
        (now - last_annual).days >= 365
    )

    if should_run_annual:
        cutoff = now - timedelta(days=365)
        years_compressed = 0

        # Check back up to 10 years
        for y in range(10):
            year_start = (now - timedelta(days=365 * (y + 2))).replace(
                month=1, day=1, hour=0, minute=0, second=0, microsecond=0
            )
            year_end = year_start.replace(year=year_start.year + 1)

            if year_end > cutoff:
                continue

            period_start_str = year_start.isoformat()
            needs_compression = any(
                not _already_compressed(db, pname, "annual", period_start_str)
                for pname in PERSONA_DOMAINS
            )

            if needs_compression:
                compressed = _compress_year_groq(db, year_start, year_end)
                for persona_name, content in compressed.items():
                    if not _already_compressed(db, persona_name, "annual", period_start_str):
                        _save_compressed(
                            db, persona_name, "annual",
                            period_start_str, year_end.isoformat(), content
                        )
                years_compressed += 1

        if years_compressed > 0:
            actions.append(f"Annual compression: {years_compressed} year(s) archived ({years_compressed * 4} Groq calls used)")

        _log_consolidation(db, "annual")

    return actions


# ── Tiered context retrieval ──────────────────────────────────────────────────

def get_tiered_context_for_persona(db: DatabaseManager, persona_name: str) -> str:
    """
    Returns the full compressed historical memory for a persona, tiered by age.

    What gets returned:
      - Annual arcs: everything older than 1 year
      - Monthly summaries: 90 days to 1 year
      - Weekly summaries: 30 to 90 days
      - Raw logs: last 30 days (handled by existing memory.py — not included here)
      - Pinned memories: always included regardless of age

    This is injected AFTER the raw memory block (which covers last 30 days).
    Together they give the persona full temporal range.
    """
    _ensure_tables(db)
    now    = datetime.now()
    parts  = []

    # ── Pinned memories (always) ───────────────────────────────────────────
    pinned = get_pinned_memories(db, persona_name)
    if pinned:
        pin_lines = []
        for p in pinned:
            pin_lines.append(f"  ★ {p['content']} [pinned {p['pinned_at'][:10]}]")
        parts.append("--- PINNED MEMORIES (permanent) ---\n" + "\n".join(pin_lines))

    # ── Annual arcs (1yr+) ────────────────────────────────────────────────
    annual_cutoff = now - timedelta(days=365)
    annual_rows   = _get_compressed(db, persona_name, "annual")
    annual_in_range = [r for r in annual_rows if r[1] < annual_cutoff.isoformat()]

    if annual_in_range:
        annual_blocks = [r[2] for r in annual_in_range]
        parts.append("--- ANNUAL ARCS ---\n" + "\n\n".join(annual_blocks))

    # ── Monthly summaries (90d–1yr) ───────────────────────────────────────
    monthly_start  = now - timedelta(days=365)
    monthly_end    = now - timedelta(days=90)
    monthly_rows   = _get_compressed(db, persona_name, "monthly")
    monthly_in_range = [
        r for r in monthly_rows
        if r[0] >= monthly_start.isoformat() and r[1] <= monthly_end.isoformat()
    ]

    if monthly_in_range:
        monthly_blocks = [r[2] for r in monthly_in_range]
        parts.append(
            "--- MONTHLY SUMMARIES (90 days – 1 year ago) ---\n" +
            "\n\n".join(monthly_blocks)
        )

    # ── Weekly summaries (30–90 days) ─────────────────────────────────────
    weekly_start = now - timedelta(days=90)
    weekly_end   = now - timedelta(days=30)
    weekly_rows  = _get_compressed(db, persona_name, "weekly")
    weekly_in_range = [
        r for r in weekly_rows
        if r[0] >= weekly_start.isoformat() and r[1] <= weekly_end.isoformat()
    ]

    if weekly_in_range:
        weekly_blocks = [r[2] for r in weekly_in_range]
        parts.append(
            "--- WEEKLY SUMMARIES (30–90 days ago) ---\n" +
            "\n\n".join(weekly_blocks)
        )

    if not parts:
        return ""

    header = f"--- COMPRESSED HISTORICAL MEMORY: {persona_name} ---"
    return header + "\n\n" + "\n\n".join(parts)


# ── Pinned memory management ──────────────────────────────────────────────────

def pin_memory(
    db: DatabaseManager,
    persona_name: str,
    content: str,
    pinned_by: str = "manual"
):
    """
    Pins a memory for a specific persona.
    Pinned memories always appear in context regardless of age.

    Parameters:
        persona_name : ALDRIC / SEREN / ORYN / MORRO
        content      : The memory text to pin
        pinned_by    : "manual" (operator) or "auto" (system)
    """
    _ensure_tables(db)
    db.conn.execute(
        "INSERT INTO pinned_memories (persona_name, content, pinned_at, pinned_by) VALUES (?, ?, ?, ?)",
        (persona_name, content.strip(), datetime.now().isoformat(), pinned_by)
    )
    db.conn.commit()


def unpin_memory(db: DatabaseManager, pin_id: int):
    """Removes a pinned memory by ID."""
    _ensure_tables(db)
    db.conn.execute("DELETE FROM pinned_memories WHERE id = ?", (pin_id,))
    db.conn.commit()


def get_pinned_memories(db: DatabaseManager, persona_name: str = None) -> list:
    """
    Returns pinned memories. If persona_name is given, filters to that persona only.
    Returns list of dicts: {id, persona_name, content, pinned_at, pinned_by}
    """
    _ensure_tables(db)
    if persona_name:
        rows = db.conn.execute(
            "SELECT id, persona_name, content, pinned_at, pinned_by FROM pinned_memories WHERE persona_name = ? ORDER BY pinned_at DESC",
            (persona_name,)
        ).fetchall()
    else:
        rows = db.conn.execute(
            "SELECT id, persona_name, content, pinned_at, pinned_by FROM pinned_memories ORDER BY persona_name, pinned_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def list_all_pinned(db: DatabaseManager) -> str:
    """Returns a formatted string of all pinned memories for display."""
    _ensure_tables(db)
    pins = get_pinned_memories(db)
    if not pins:
        return "No pinned memories."

    lines = []
    current_persona = None
    for p in pins:
        if p["persona_name"] != current_persona:
            current_persona = p["persona_name"]
            lines.append(f"\n  ── {current_persona} ──")
        lines.append(f"  [{p['id']}] {p['content'][:120]} ({p['pinned_by']}, {p['pinned_at'][:10]})")

    return "\n".join(lines)


def run_pin_menu(db: DatabaseManager):
    """
    Interactive CLI menu for managing pinned memories.
    Called from marlow.py menu.
    """
    _ensure_tables(db)
    persona_names = list(PERSONA_DOMAINS.keys())

    while True:
        print("\n  ┌─ PINNED MEMORY MANAGER ──────────────────────────────┐")
        print("  │  1. View all pinned memories")
        print("  │  2. Pin a new memory")
        print("  │  3. Remove a pinned memory")
        print("  │  4. Back")
        print("  └──────────────────────────────────────────────────────┘")

        choice = input("\n  > ").strip()

        if choice == "1":
            print("\n" + list_all_pinned(db))

        elif choice == "2":
            print("\n  Which persona should remember this?")
            for i, name in enumerate(persona_names, 1):
                domain = PERSONA_DOMAINS[name]["label"]
                print(f"  {i}. {name} ({domain})")
            p_choice = input("  > ").strip()
            if p_choice.isdigit() and 1 <= int(p_choice) <= len(persona_names):
                persona = persona_names[int(p_choice) - 1]
                print(f"\n  What should {persona} remember? (will always surface in context)")
                content = input("  > ").strip()
                if content:
                    pin_memory(db, persona, content, pinned_by="manual")
                    print(f"\n  ✓ Pinned to {persona}.")
                else:
                    print("  Nothing entered.")
            else:
                print("  Invalid choice.")

        elif choice == "3":
            print("\n" + list_all_pinned(db))
            pin_id = input("\n  Enter ID to remove: ").strip()
            if pin_id.isdigit():
                unpin_memory(db, int(pin_id))
                print("  ✓ Removed.")
            else:
                print("  Invalid ID.")

        elif choice == "4":
            break
        else:
            print("  Invalid.")
