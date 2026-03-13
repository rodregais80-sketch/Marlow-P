"""
background_runner.py
Background intelligence daemon for Marlow Platform 3.

Runs as a daemon thread while marlow.py is open.
Performs pre-computation of heavy context blocks during idle periods
so council sessions start faster.

What runs in the background:
  - PatternEngine full synthesis → writes to context_cache
  - PredictiveEngine context → writes to context_cache
  - assess_pending_intentions() — contradiction engine follow-through check
  - assess_today() — streak tracker daily assessment
  - clear_context_cache() — expire stale cache entries

What NEVER runs in the background:
  - Any Groq call during an active council session (rate limit protection)
  - Memory consolidation (has its own startup schedule)
  - Weekly/monthly report generation (event-driven, not time-driven)

Rate limit protection:
  _council_active flag is set True when council starts, False when synthesis ends.
  Background thread checks this flag before any operation that would hit Groq.
  In practice, background Groq calls are rare — pattern and prediction engines
  are pure computation. The flag mainly protects against future additions.

Interval:
  Default cycle every 2 hours (7200 seconds).
  First pass fires 60 seconds after startup to pre-warm cache without slowing boot.
  Configurable via BACKGROUND_INTERVAL_SECONDS.

Thread safety:
  All DB writes use the same connection as the main thread via db.conn.
  SQLite in WAL mode (already configured) handles concurrent reads safely.
  We avoid concurrent writes by checking _council_active before any write.
  The thread is daemon=True — it dies when the main process exits.
"""

import threading
import time
from datetime import datetime

# ── Shared state ──────────────────────────────────────────────────────────────
# These flags are read/written by both the main thread and the background thread.
# Simple booleans — no lock needed for reads, Python GIL protects single assignments.

_council_active      = False   # True while run_council() is executing
_background_running  = False   # True once the daemon is started
_last_cycle_time     = None    # datetime of last successful background cycle
_stop_event          = threading.Event()  # signals the thread to stop cleanly

BACKGROUND_INTERVAL_SECONDS = 7200   # 2 hours
FIRST_PASS_DELAY_SECONDS     = 60    # 1 minute after startup before first pass


# ── Council activity flag setters ─────────────────────────────────────────────

def set_council_active(active: bool) -> None:
    """
    Called by run_council() at start (True) and after synthesis completes (False).
    Background thread checks this before heavy operations.
    """
    global _council_active
    _council_active = active


def is_council_active() -> bool:
    return _council_active


# ── Background cycle ──────────────────────────────────────────────────────────

def _run_background_cycle(db) -> list:
    """
    Executes one background intelligence cycle.
    Returns list of action strings for logging.

    Checks _council_active before each step — aborts remaining steps if
    the council starts mid-cycle.
    """
    global _last_cycle_time
    actions = []

    # Step 1: Expire stale cache entries (always safe — no Groq)
    try:
        db.clear_context_cache()
        actions.append("Cache: expired stale entries")
    except Exception as e:
        actions.append(f"Cache: clear failed ({str(e)[:40]})")

    # Step 2: Pre-compute pattern context if cache is cold or stale
    if not _council_active:
        try:
            cached = db.get_context_cache("pattern_ctx")
            if not cached:
                from core.pattern_engine import PatternEngine
                engine       = PatternEngine(db)
                pattern_data = engine.synthesize_master_insights()
                pattern_ctx  = engine.format_insights_for_context(pattern_data)[:1200]
                if pattern_ctx:
                    db.set_context_cache("pattern_ctx", pattern_ctx, ttl_minutes=120)
                    actions.append("Pattern: pre-computed and cached (120min TTL)")
            else:
                actions.append("Pattern: cache warm, skipped")
        except Exception as e:
            actions.append(f"Pattern: failed ({str(e)[:40]})")
    else:
        actions.append("Pattern: skipped — council active")

    # Step 3: Pre-compute prediction context
    if not _council_active:
        try:
            cached = db.get_context_cache("prediction_ctx")
            if not cached:
                from core.predictor import build_prediction_context
                prediction_ctx = build_prediction_context(db, max_chars=1000)
                if prediction_ctx:
                    db.set_context_cache("prediction_ctx", prediction_ctx, ttl_minutes=120)
                    actions.append("Prediction: pre-computed and cached (120min TTL)")
            else:
                actions.append("Prediction: cache warm, skipped")
        except Exception as e:
            actions.append(f"Prediction: failed ({str(e)[:40]})")
    else:
        actions.append("Prediction: skipped — council active")

    # Step 4: Assess pending intentions (0 Groq — pure SQLite)
    if not _council_active:
        try:
            from core.contradiction_engine import assess_pending_intentions
            assess_pending_intentions(db)
            actions.append("Contradiction: pending intentions assessed")
        except Exception as e:
            actions.append(f"Contradiction: failed ({str(e)[:40]})")

    # Step 5: Streak tracker day assessment (0 Groq — pure SQLite)
    if not _council_active:
        try:
            from core.streak_tracker import assess_today
            assess_today(db)
            actions.append("Streak: today assessed")
        except Exception as e:
            actions.append(f"Streak: failed ({str(e)[:40]})")

    _last_cycle_time = datetime.now()
    return actions


def _background_thread_fn(db, logger_fn=None) -> None:
    """
    Main background thread function.
    Waits for first-pass delay, then cycles on BACKGROUND_INTERVAL_SECONDS.
    Stops cleanly when _stop_event is set.
    """
    # First pass delay — don't compete with startup sequence
    stopped = _stop_event.wait(timeout=FIRST_PASS_DELAY_SECONDS)
    if stopped:
        return

    while not _stop_event.is_set():
        try:
            actions = _run_background_cycle(db)
            if logger_fn and actions:
                for action in actions:
                    try:
                        logger_fn("BackgroundRunner", action)
                    except Exception:
                        pass
        except Exception:
            pass  # Background thread must never crash

        # Wait for next cycle or until stop signal
        _stop_event.wait(timeout=BACKGROUND_INTERVAL_SECONDS)


# ── Public API ────────────────────────────────────────────────────────────────

def start_background_runner(db, logger_fn=None) -> bool:
    """
    Starts the background intelligence daemon.
    Safe to call multiple times — will not start a second thread if already running.

    Args:
        db: DatabaseManager instance (shared with main thread)
        logger_fn: optional logging callable (e.g. log_info from marlow_logger)
                   signature: logger_fn(component: str, message: str)

    Returns:
        True if started successfully, False if already running or failed.
    """
    global _background_running

    if _background_running:
        return False

    try:
        _stop_event.clear()
        thread = threading.Thread(
            target=_background_thread_fn,
            args=(db, logger_fn),
            daemon=True,
            name="MarlowBackground"
        )
        thread.start()
        _background_running = True
        return True
    except Exception:
        return False


def stop_background_runner() -> None:
    """
    Signals the background thread to stop cleanly.
    Called on clean exit. Not required — daemon thread dies with the process anyway.
    """
    global _background_running
    _stop_event.set()
    _background_running = False


def get_last_cycle_time() -> str:
    """
    Returns the timestamp of the last completed background cycle, or empty string.
    """
    if _last_cycle_time:
        return _last_cycle_time.strftime("%I:%M %p")
    return ""


def force_cycle(db) -> list:
    """
    Forces an immediate background cycle outside the normal schedule.
    Useful after a long idle period or on manual trigger.
    Returns list of action strings.
    """
    return _run_background_cycle(db)
