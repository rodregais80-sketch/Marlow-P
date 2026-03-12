"""
persona_chat.py — REWRITTEN
Real Groq integration. Previous version was a non-functional placeholder
that returned hardcoded fake responses and did not call any LLM.

This module now re-exports PersonaChat from core.database — which is the
real, fully-functional implementation with Groq + Ollama fallback. This
approach eliminates code duplication while fixing the broken import chain
that existed when persona_menu.py tried to import from this file.

All persona_menu.py and any other module that imports PersonaChat from
core.persona_chat will now receive the real class.

marlow.py already imports PersonaChat from core.database directly and
is unaffected by this change.
"""

# ── Real PersonaChat — import from canonical source ───────────────────────────
# The authoritative PersonaChat implementation lives in core/database.py.
# It has: Groq API calls, Ollama fallback, conversation history, persona
# memory logging, goal momentum injection, and cross-session context building.
# We surface it here so any import of core.persona_chat resolves correctly.

from core.database import PersonaChat  # noqa: F401 — intentional re-export

__all__ = ["PersonaChat"]
