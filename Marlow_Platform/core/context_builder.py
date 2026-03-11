"""
context_builder.py — NEW FILE
Builds the shared context data structure ONCE per council session.
Delivers persona-tiered context blocks to reduce irrelevant token spend.
Enforces character limits to protect model context windows from overflow.

Improvements addressed:
- Improvement 12: Tiered context delivery per persona domain
- Improvement 18: Context built once per session, not rebuilt on every parallel call
- Gap 1 (partial): memory_block now passed in with actual personas list (resolved in council_engine)
- Gap 7 (partial): Character limit guards protecting context window integrity
"""

# ─────────────────────────────────────────────
# CHARACTER LIMITS — hard ceilings per block
# Protects against silent context window overflow in llama-3.1-8b-instant
# ─────────────────────────────────────────────
MAX_PROFILE_CHARS  = 800
MAX_HISTORY_CHARS  = 2000
MAX_GOALS_CHARS    = 800
MAX_TREND_CHARS    = 1500
MAX_MEMORY_CHARS   = 2000

# ─────────────────────────────────────────────
# PERSONA HISTORY FIELD TIERS
# Each persona only receives the history fields they can act on.
# Reduces token spend on data a persona has no domain over.
# ─────────────────────────────────────────────
PERSONA_HISTORY_FIELDS = {
    "ALDRIC": [
        "background",
        "current_strengths",
        "goals_longterm",
        "additional_context",
    ],
    "SEREN": [
        "background",
        "current_struggles",
        "relationship_status",
        "support_network",
        "mental_health_history",
        "substance_history",
        "additional_context",
    ],
    "MORRO": [
        "background",
    ],
    "ORYN": [
        "background",
        "mental_health_history",
        "substance_history",
        "current_struggles",
        "significant_events",
        "current_strengths",
    ],
}

# Which personas receive behavioral trends in their context
PERSONA_NEEDS_TRENDS = {
    "ALDRIC": True,
    "SEREN":  True,
    "MORRO":  False,   # MORRO needs minimal context — reacts to impulse, not data
    "ORYN":   True,
}

# Which personas receive the cross-session memory block
PERSONA_NEEDS_MEMORY = {
    "ALDRIC": True,
    "SEREN":  True,
    "MORRO":  False,
    "ORYN":   True,
}

HISTORY_FIELD_LABELS = {
    "background":            "Background",
    "significant_events":    "Significant Events",
    "current_struggles":     "Current Struggles",
    "current_strengths":     "Current Strengths",
    "relationship_status":   "Relationship Status",
    "support_network":       "Support Network",
    "mental_health_history": "Mental Health History",
    "substance_history":     "Substance History",
    "goals_longterm":        "Long-Term Goals",
    "additional_context":    "Additional Context",
}


def build_shared_context(
    db,
    trend_report: str,
    memory_block: str
) -> dict:
    """
    Builds the full shared context dict ONCE per council session.

    Accepts pre-computed trend_report and memory_block to avoid redundant
    DB calls across parallel persona execution (Improvement 18).

    Returns a dict consumed by build_context_for_persona().
    """
    from datetime import datetime

    profile = db.get_static_profile()
    history = db.get_life_history()
    goals   = db.get_goals_as_context()
    now     = datetime.now().strftime("%A, %B %d, %Y - %I:%M %p")

    # ── Profile block ──────────────────────────────────────────────────────
    profile_block = "No static profile on file yet."
    if profile:
        profile_block = (
            f"Name: {profile['name'] or ''}\n"
            f"Age: {profile['age'] or ''}\n"
            f"Location: {profile['location'] or ''}\n"
            f"Occupation: {profile['occupation'] or ''}\n"
            f"Primary Goal: {profile['primary_goal'] or ''}\n"
            f"Biggest Challenge: {profile['biggest_challenge'] or ''}\n"
            f"Preferred Support Style: {profile['support_style'] or ''}\n"
            f"Additional Context: {profile['additional_context'] or ''}"
        )

    # ── History block ──────────────────────────────────────────────────────
    history_raw        = {}
    history_full_block = "No life history on file yet."
    if history:
        history_raw = dict(history)
        lines = []
        for field, label in HISTORY_FIELD_LABELS.items():
            val = history_raw.get(field, "") or ""
            if val:
                lines.append(f"{label}: {val}")
        history_full_block = "\n".join(lines)

    return {
        "now":                now,
        "profile_block":      profile_block[:MAX_PROFILE_CHARS],
        "history_full_block": history_full_block[:MAX_HISTORY_CHARS],
        "history_raw":        history_raw,
        "goals_block":        (goals or "")[:MAX_GOALS_CHARS],
        "trend_report":       (trend_report or "")[:MAX_TREND_CHARS],
        "memory_block":       (memory_block or "")[:MAX_MEMORY_CHARS],
    }


def build_context_for_persona(persona_name: str, shared: dict) -> str:
    """
    Returns a trimmed, persona-relevant context string.

    Each persona receives only the context they can act on.
    This prevents token waste and improves response precision.

    Parameters:
        persona_name : One of ALDRIC / SEREN / MORRO / ORYN
        shared       : Dict returned by build_shared_context()
    """
    parts = []

    # All personas receive the base profile
    if shared.get("profile_block"):
        parts.append(f"--- USER PROFILE ---\n{shared['profile_block']}")

    # History — tiered per persona domain
    history_raw = shared.get("history_raw", {})
    if history_raw:
        fields = PERSONA_HISTORY_FIELDS.get(persona_name)
        if fields is None:
            # Unknown persona gets full history
            history_text = shared.get("history_full_block", "")
        else:
            lines = []
            for f in fields:
                val = history_raw.get(f, "") or ""
                if val:
                    lines.append(f"{HISTORY_FIELD_LABELS.get(f, f)}: {val}")
            history_text = "\n".join(lines)

        if history_text:
            parts.append(
                f"--- LIFE HISTORY ---\n{history_text[:MAX_HISTORY_CHARS]}"
            )

    # Goals — all except MORRO
    if persona_name != "MORRO" and shared.get("goals_block"):
        parts.append(f"--- ACTIVE GOALS ---\n{shared['goals_block']}")

    # Behavioral trends — ALDRIC, SEREN, ORYN only
    if PERSONA_NEEDS_TRENDS.get(persona_name, True) and shared.get("trend_report"):
        parts.append(f"--- BEHAVIORAL TRENDS ---\n{shared['trend_report']}")

    # Memory and recent logs — ALDRIC, SEREN, ORYN only
    if PERSONA_NEEDS_MEMORY.get(persona_name, True) and shared.get("memory_block"):
        parts.append(f"--- MEMORY & RECENT LOGS ---\n{shared['memory_block']}")

    return "\n\n".join(parts)
