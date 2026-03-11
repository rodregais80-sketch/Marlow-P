"""
memory.py
Cross-persona memory retrieval and context block construction.

Gap 5 fix: Pattern detection replaced from exact string matching (which never
produced output on AI-generated free text) to word-overlap similarity grouping.
Summaries with >= 40% shared significant words are grouped as the same pattern.

New in this version:
- build_memory_block() now accepts optional persona_name parameter
- When persona_name is provided, tiered compressed historical memory is
  appended AFTER the raw logs block via memory_consolidator.get_tiered_context_for_persona()
- This gives each persona full temporal awareness: raw (0-30d), weekly (30-90d),
  monthly (90d-1yr), annual (1yr+), and pinned memories (always)
- When persona_name is None, behavior is identical to previous version
"""

from core.database import DatabaseManager


# ─────────────────────────────────────────────────────────────────────────────
# PATTERN DETECTION — word overlap similarity
# ─────────────────────────────────────────────────────────────────────────────

# Words that carry no semantic signal — excluded from overlap comparison
_STOP_WORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "is", "was", "are", "were", "be", "been", "being", "have",
    "has", "had", "do", "does", "did", "will", "would", "could", "should",
    "may", "might", "shall", "can", "i", "you", "he", "she", "it", "we",
    "they", "this", "that", "these", "those", "my", "your", "his", "her",
    "its", "our", "their", "from", "by", "about", "up", "out", "no", "not",
    "so", "if", "as", "into", "through", "during", "before", "after",
    "between", "each", "more", "also", "just", "than", "then", "when",
    "where", "who", "which", "what", "how", "all", "any", "both", "few",
    "very", "still", "own", "same", "than", "too", "very", "s", "t",
    "responded", "input", "length", "session", "logged", "chat", "noted"
}

SIMILARITY_THRESHOLD = 0.40   # 40% word overlap = same pattern
MIN_WORD_LENGTH      = 3       # Ignore very short words in overlap calc


def _extract_keywords(text: str) -> set:
    """Extracts significant words from a summary string."""
    words = text.lower().replace("_", " ").split()
    return {
        w.strip(".,;:!?\"'()[]{}") for w in words
        if len(w) >= MIN_WORD_LENGTH and w not in _STOP_WORDS
    }


def _similarity(a: str, b: str) -> float:
    """Jaccard similarity between keyword sets of two strings."""
    ka = _extract_keywords(a)
    kb = _extract_keywords(b)
    if not ka or not kb:
        return 0.0
    intersection = ka & kb
    union        = ka | kb
    return len(intersection) / len(union)


def _cluster_summaries(summaries: list) -> list:
    """
    Groups a list of summary strings by semantic similarity.
    Returns a list of (representative_summary, count) tuples, sorted by count desc.
    Replaces the broken exact-match pattern_count approach in database.py.
    """
    clusters = []  # list of [representative, count, member_keywords]

    for summary in summaries:
        if not summary:
            continue
        matched = False
        for cluster in clusters:
            if _similarity(summary, cluster[0]) >= SIMILARITY_THRESHOLD:
                cluster[1] += 1
                matched = True
                break
        if not matched:
            clusters.append([summary, 1])

    clusters.sort(key=lambda x: -x[1])
    return [(c[0], c[1]) for c in clusters]


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def get_cross_persona_memory(
    db: DatabaseManager,
    personas: list,
    per_person_limit: int = 5
) -> str:
    """
    Pulls recent memory flags from all personas.
    Injected into each persona's prompt so they know what others have flagged.

    Gap 1 fix: personas list is now passed in correctly from run_council()
    rather than being called with an empty list [].

    Gap 5 fix: pattern_summary now uses word-overlap clustering rather than
    exact string matching, which previously produced no output on AI-generated text.
    """
    memory_lines = []

    for persona in personas:
        name = persona["name"]
        memories = db.get_persona_memory(name, limit=per_person_limit)

        for m in memories:
            memory_lines.append(
                f"[{name} flagged — Risk {m['risk_score']}/10]: {m['summary']}"
            )

        # Use word-overlap clustering instead of exact match
        all_summaries = db.get_persona_memory(name, limit=50)
        summary_texts = [m["summary"] for m in all_summaries if m["summary"]]
        clusters      = _cluster_summaries(summary_texts)
        recurring     = [(s, c) for s, c in clusters if c >= 2]

        if recurring:
            pattern_lines = [f"  [{name} recurring x{c}]: {s[:100]}" for s, c in recurring[:5]]
            memory_lines.append(f"[{name} patterns]:\n" + "\n".join(pattern_lines))

    if not memory_lines:
        return "No recent cross-persona flags."

    return "\n".join(memory_lines)


def build_memory_block(
    db: DatabaseManager,
    personas: list,
    logs_limit: int = 7,
    persona_name: str = None
) -> str:
    """
    Builds the full memory context block to inject into prompts.

    Gap 1 fix: personas list must be the actual PERSONAS list, not [].
    This is now guaranteed because run_council() passes the full personas
    list when calling build_shared_context().

    New: If persona_name is provided, appends tiered compressed historical
    memory from memory_consolidator. This extends the persona's awareness
    beyond the last 30 days all the way back to the start of the system.
    Each persona gets only the compressed history relevant to their domain.

    Parameters:
        db           : DatabaseManager instance
        personas     : Full PERSONAS list
        logs_limit   : How many raw recent logs to include (last 30 days)
        persona_name : Optional. If set, injects tiered compressed history.
    """
    cross_memory = get_cross_persona_memory(db, personas)
    recent_logs  = db.get_recent_logs(limit=logs_limit)

    log_lines = []
    for log in reversed(recent_logs):
        log_lines.append(
            f"[{log['timestamp']} — {log['sync_type'].upper()}]\n{log['content']}"
        )

    logs_block = "\n\n".join(log_lines) if log_lines else "No recent sync logs."

    base_block = (
        f"--- CROSS-PERSONA MEMORY FLAGS ---\n"
        f"{cross_memory}\n\n"
        f"--- RECENT SYNC HISTORY (last {logs_limit} entries) ---\n"
        f"{logs_block}"
    )

    # ── Tiered compressed history injection ───────────────────────────────
    # Only fires if a specific persona_name is passed in.
    # Returns historical context from 30 days ago all the way back to year 1.
    # If no compressed data exists yet (system is young), returns empty string
    # and the block is unchanged from previous behavior.
    if persona_name:
        try:
            from core.memory_consolidator import get_tiered_context_for_persona
            tiered_ctx = get_tiered_context_for_persona(db, persona_name)
            if tiered_ctx:
                base_block = base_block + "\n\n" + tiered_ctx
        except Exception:
            # Silently degrade — tiered history is additive, not critical
            pass

    return base_block
