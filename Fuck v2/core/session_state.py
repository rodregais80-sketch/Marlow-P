"""
session_state.py
Session-level state container for Marlow Platform 3.

Holds all data that is static within a session — fetched once at startup,
reused everywhere. Eliminates repeated DB reads for the same unchanged data.

Problem it solves:
  db.get_static_profile()    — called in execute_task, build_context_for_persona
                               (once per persona per council run), generate_session_brief.
                               That's 4-6 identical reads per session for data that never changes.
  db.get_life_history()      — same pattern.
  db.get_active_goals()      — called in weekly report, task mode, goal momentum scorer,
                               build_context_for_persona. Never changes mid-session.
  get_monthly_pattern_as_context() — called in build_trend_report, generate_weekly_report,
                               generate_monthly_pattern. One DB read is sufficient.
  GoalMomentumScorer         — instantiated and run in session_brief, execute_task,
                               weekly_report, and decision_context. All same session data.
  _get_tiered_ctx()          — fetched per active persona per council run. If operator
                               asks two questions in a row, runs again for all personas
                               even though tiered history didn't change.

Design:
  SessionState is a simple dataclass-style object.
  It is initialized once in main() and passed to run_council(), execute_task(),
  generate_session_brief() where needed.
  Invalidation hooks allow specific fields to be refreshed when new data is logged.
  build_context_for_persona() in context_builder.py checks session_state first.

Backward compatibility:
  Everything that uses these values has fallback logic — if session_state is None
  or a field is empty, the existing DB call path runs as before.
  This makes the upgrade non-breaking.
"""

from datetime import datetime


class SessionState:
    """
    Holds session-static data fetched once at startup.
    Mutable fields can be invalidated and refreshed when new data is logged.
    """

    def __init__(self, db):
        self.db            = db
        self._initialized  = False
        self._init_time    = None

        # Session-static fields — fetched once, never change mid-session
        # unless explicitly invalidated
        self.profile        = None   # db.get_static_profile()
        self.life_history   = None   # db.get_life_history()
        self.active_goals   = []     # db.get_active_goals()
        self.goals_context  = ""     # db.get_goals_as_context()
        self.monthly_pattern = ""    # get_monthly_pattern_as_context()
        self.momentum_ctx   = ""     # GoalMomentumScorer.build_momentum_context()

        # Per-persona tiered history — fetched once per persona per session
        # key: persona name, value: compressed history string
        self.tiered_histories: dict = {}

        # Context flags from context_relevance.py
        # Refreshed at session start and after each sync
        self.context_flags: dict = {}

        # Timestamp of last data write (sync, journal, goal update)
        # Used to decide whether metric-invalidated caches need refresh
        self._last_data_write: str = ""

    # ── Initialization ────────────────────────────────────────────────────────

    def initialize(self) -> None:
        """
        Fetches all session-static data from DB.
        Called once in main() after db is ready.
        Graceful: any individual fetch failure leaves that field empty/None.
        """
        self._init_time = datetime.now()

        try:
            self.profile = self.db.get_static_profile()
        except Exception:
            self.profile = None

        try:
            self.life_history = self.db.get_life_history()
        except Exception:
            self.life_history = None

        try:
            self.active_goals = list(self.db.get_active_goals() or [])
        except Exception:
            self.active_goals = []

        try:
            self.goals_context = self.db.get_goals_as_context() or ""
        except Exception:
            self.goals_context = ""

        try:
            from core.council_engine import get_monthly_pattern_as_context
            self.monthly_pattern = get_monthly_pattern_as_context(self.db)
        except Exception:
            self.monthly_pattern = ""

        try:
            from core.decision_tracker import GoalMomentumScorer
            scorer            = GoalMomentumScorer(self.db)
            self.momentum_ctx = scorer.build_momentum_context()
        except Exception:
            self.momentum_ctx = ""

        try:
            from core.context_relevance import get_active_context_flags
            self.context_flags = get_active_context_flags(self.db)
        except Exception:
            self.context_flags = {}

        self._initialized = True

    def load_tiered_histories(self, active_persona_names: list, get_tiered_fn) -> None:
        """
        Fetches tiered compressed history for each active persona.
        Called once per council session start — result reused for all personas.
        Skips personas already loaded this session.

        Args:
            active_persona_names: list of persona names active this council run
            get_tiered_fn: _get_tiered_ctx function from memory_consolidator
        """
        for name in active_persona_names:
            if name not in self.tiered_histories:
                try:
                    self.tiered_histories[name] = get_tiered_fn(self.db, name)
                except Exception:
                    self.tiered_histories[name] = ""

    # ── Invalidation hooks ────────────────────────────────────────────────────
    # Called after data-writing operations so cached values stay accurate.

    def invalidate_after_sync(self) -> None:
        """
        Call after any sync is saved (morning/midday/evening).
        Refreshes context flags since substance window and trend data may have changed.
        Does NOT re-fetch static data (profile, history, goals) — those don't change on sync.
        """
        self._last_data_write = datetime.now().isoformat()
        try:
            from core.context_relevance import get_active_context_flags
            self.context_flags = get_active_context_flags(self.db)
        except Exception:
            pass

    def invalidate_after_goal_change(self) -> None:
        """
        Call after a goal is added, updated, or status-changed.
        Refreshes goals, momentum, and streak flag.
        """
        self._last_data_write = datetime.now().isoformat()
        try:
            self.active_goals  = list(self.db.get_active_goals() or [])
            self.goals_context = self.db.get_goals_as_context() or ""
        except Exception:
            pass
        try:
            from core.decision_tracker import GoalMomentumScorer
            scorer            = GoalMomentumScorer(self.db)
            self.momentum_ctx = scorer.build_momentum_context()
        except Exception:
            pass
        try:
            from core.context_relevance import get_active_context_flags
            self.context_flags = get_active_context_flags(self.db)
        except Exception:
            pass

    def invalidate_after_journal(self) -> None:
        """
        Call after a journal or vent entry is saved.
        Refreshes contradiction flags since new intentions may have been extracted.
        """
        self._last_data_write = datetime.now().isoformat()
        try:
            from core.context_relevance import get_active_context_flags
            self.context_flags = get_active_context_flags(self.db)
        except Exception:
            pass

    # ── Accessors ─────────────────────────────────────────────────────────────

    def get_profile_name(self, default: str = "Operator") -> str:
        if self.profile:
            try:
                return self.profile["name"] or default
            except (KeyError, TypeError):
                pass
        return default

    def get_profile_dict(self) -> dict:
        """Returns profile as a plain dict, or empty dict."""
        if self.profile is None:
            return {}
        try:
            return dict(self.profile)
        except Exception:
            return {}

    def get_history_dict(self) -> dict:
        """Returns life history as a plain dict, or empty dict."""
        if self.life_history is None:
            return {}
        try:
            return dict(self.life_history)
        except Exception:
            return {}

    def get_tiered_history(self, persona_name: str) -> str:
        """Returns tiered history for a persona, or empty string."""
        return self.tiered_histories.get(persona_name, "")

    def get_flag(self, flag_name: str, default: bool = False):
        """Returns a context relevance flag by name."""
        return self.context_flags.get(flag_name, default)

    def is_ready(self) -> bool:
        """Returns True if session state has been initialized."""
        return self._initialized

    def summary(self) -> str:
        """
        Returns a one-line summary of what was loaded.
        Used for debug/startup confirmation.
        """
        if not self._initialized:
            return "SessionState: not initialized"
        profile_name   = self.get_profile_name("unknown")
        goal_count     = len(self.active_goals)
        tiered_count   = len(self.tiered_histories)
        flags_summary  = ""
        if self.context_flags:
            try:
                from core.context_relevance import format_flags_for_log
                flags_summary = " | " + format_flags_for_log(self.context_flags)
            except Exception:
                pass
        return (
            f"SessionState loaded: profile={profile_name}, goals={goal_count}, "
            f"tiered_personas={tiered_count}{flags_summary}"
        )
