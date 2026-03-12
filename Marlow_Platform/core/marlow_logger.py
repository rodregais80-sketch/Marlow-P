"""
marlow_logger.py
Structured subsystem failure logging for the Marlow platform.

Replaces the silent exception pattern throughout the codebase.
All subsystem degradation, Groq failures, DB errors, and context
build failures are written to marlow_errors.log in the project root.

Usage:
    from core.marlow_logger import log_error, log_warning, log_info, get_logger

    log_error("PatternEngine", "synthesize_master_insights", e)
    log_warning("ContextBuilder", "recent syncs unavailable — continuing with reduced context")
    log_info("MemoryConsolidator", "Weekly compression ran — 12 entries compressed")

The log is append-only. It rotates at 5MB to prevent disk bloat.
Startup prints a summary only if errors were logged in the last 24 hours.
"""

import os
import logging
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from logging.handlers import RotatingFileHandler

# ── Config ────────────────────────────────────────────────────────────────────

_LOG_FILE     = Path(__file__).resolve().parent.parent / "marlow_errors.log"
_MAX_BYTES    = 5 * 1024 * 1024   # 5MB before rotation
_BACKUP_COUNT = 2                  # Keep 2 rotated logs max
_LOGGER_NAME  = "marlow"

# ── Logger init ───────────────────────────────────────────────────────────────

def _build_logger() -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)

    if logger.handlers:
        return logger  # Already initialized — don't add duplicate handlers

    logger.setLevel(logging.DEBUG)

    # File handler — rotating, structured format
    try:
        fh = RotatingFileHandler(
            str(_LOG_FILE),
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8"
        )
        fh.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        fh.setFormatter(formatter)
        logger.addHandler(fh)
    except Exception as e:
        # If log file can't be created (permissions etc), fall back to stderr silently
        import sys
        sh = logging.StreamHandler(sys.stderr)
        sh.setLevel(logging.WARNING)
        logger.addHandler(sh)

    return logger


_logger = _build_logger()


# ── Public API ────────────────────────────────────────────────────────────────

def log_error(subsystem: str, function: str, exception: Exception, extra: str = "") -> None:
    """
    Log a subsystem failure with full traceback.
    Use when a critical function raised an exception.

    Args:
        subsystem  : Module name. e.g. "PatternEngine", "MemoryConsolidator"
        function   : Function or method name. e.g. "synthesize_master_insights"
        exception  : The caught exception object.
        extra      : Optional additional context string.
    """
    tb  = traceback.format_exc()
    msg = f"[{subsystem}] {function} FAILED: {type(exception).__name__}: {exception}"
    if extra:
        msg += f" | {extra}"
    _logger.error(msg)
    if tb and "NoneType" not in tb:
        _logger.debug(f"[{subsystem}] Traceback:\n{tb.strip()}")


def log_warning(subsystem: str, message: str) -> None:
    """
    Log a non-fatal degradation.
    Use when a subsystem ran but produced reduced/partial output.

    Args:
        subsystem : Module name.
        message   : Description of what degraded and what fallback was used.
    """
    _logger.warning(f"[{subsystem}] {message}")


def log_info(subsystem: str, message: str) -> None:
    """
    Log a significant positive event.
    Use for memory consolidation runs, report generation, config loads, etc.

    Args:
        subsystem : Module name.
        message   : What happened.
    """
    _logger.info(f"[{subsystem}] {message}")


def log_groq(operation: str, model: str, tokens_used: int = None, elapsed_ms: int = None) -> None:
    """
    Log a successful Groq API call with usage data.

    Args:
        operation  : What the call was for. e.g. "ALDRIC persona", "synthesis", "classify_intent"
        model      : Model string used.
        tokens_used: Total tokens if available.
        elapsed_ms : Round-trip time in milliseconds if available.
    """
    parts = [f"[Groq] {operation} | model={model}"]
    if tokens_used is not None:
        parts.append(f"tokens={tokens_used}")
    if elapsed_ms is not None:
        parts.append(f"elapsed={elapsed_ms}ms")
    _logger.info(" | ".join(parts))


def log_context_health(health_dict: dict) -> None:
    """
    Log the context health table from run_council().
    Writes one line per degraded layer.

    Args:
        health_dict: Dict of {layer_name: status_string}
    """
    degraded = {k: v for k, v in health_dict.items() if v != "OK"}
    if not degraded:
        return
    for layer, status in degraded.items():
        _logger.warning(f"[ContextHealth] {layer}: {status}")


# ── Startup error summary ─────────────────────────────────────────────────────

def get_recent_error_count(hours: int = 24) -> int:
    """
    Returns the number of ERROR-level entries in the log file in the last N hours.
    Used at startup to decide whether to show an error summary badge.
    """
    try:
        if not _LOG_FILE.exists():
            return 0
        cutoff   = datetime.now() - timedelta(hours=hours)
        count    = 0
        with open(str(_LOG_FILE), "r", encoding="utf-8") as f:
            for line in f:
                if "| ERROR" not in line:
                    continue
                try:
                    ts_str = line.split("|")[0].strip()
                    ts     = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                    if ts >= cutoff:
                        count += 1
                except Exception:
                    continue
        return count
    except Exception:
        return 0


def get_log_path() -> str:
    """Returns the absolute path to the active log file."""
    return str(_LOG_FILE)
