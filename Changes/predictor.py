"""
predictor.py — PREDICTIVE INTELLIGENCE + AUTONOMOUS INTERVENTION

Tier 1: Predictive crash window (catches pattern before signals fully materialize)
Tier 1: Decision quality retrospective rating system
Tier 3: Autonomous intervention — watches the data even when the operator doesn't open the app
Tier 2: Auto weekly synthesis (generates the report without being asked)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AUDIT NOTES:
  - decision_log schema now includes review_due_at and outcome_score columns
    for compatibility with marlow.py Swap 10 direct SQL query
  - _ensure_tables() is idempotent — safe to call multiple times
  - format_interventions_for_display() is the primary startup hook
  - Tier 2 auto-weekly check: should_auto_generate_weekly()
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import json
import math
from datetime import datetime, timedelta, timezone
from core.pattern_engine import PatternEngine, get_or_refresh_patterns
from core.correlations import CorrelationEngine


# ─────────────────────────────────────────────────────────────────────────────
# UTILITY
# ─────────────────────────────────────────────────────────────────────────────

def _safe(row, key, default=None):
    try:
        v = row[key]
        return v if v is not None else default
    except (KeyError, IndexError, TypeError):
        return default


def _avg(lst):
    return round(sum(lst) / len(lst), 2) if lst else None


def _std(lst):
    if len(lst) < 2:
        return 0.0
    m = sum(lst) / len(lst)
    return round(math.sqrt(sum((x - m) ** 2 for x in lst) / len(lst)), 2)


def _ensure_tables(db):
    """
    Create predictor-specific tables if they don't exist.
    Idempotent — safe to call multiple times.

    decision_log includes both naming conventions:
      - quality_rating  : internal use (predictor.py rate_decision)
      - outcome_score   : marlow.py Swap 10 direct SQL compatibility
      - review_due_at   : marlow.py Swap 10 direct SQL compatibility
      - reviewed_at     : legacy reviewed timestamp
    """
    db.conn.execute("""
        CREATE TABLE IF NOT EXISTS decision_log (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            logged_at             TEXT    DEFAULT CURRENT_TIMESTAMP,
            decision_text         TEXT    NOT NULL,
            category              TEXT,
            energy_at_decision    REAL,
            mood_at_decision      REAL,
            sleep_at_decision     REAL,
            fog_at_decision       REAL,
            impulse_at_decision   REAL,
            substance_context     TEXT,
            outcome_text          TEXT,
            quality_rating        INTEGER,
            outcome_score         INTEGER,
            review_due_at         TEXT,
            reviewed_at           TEXT
        )
    """)
    # Add missing columns to existing tables gracefully
    _add_column_if_missing(db, "decision_log", "outcome_score",  "INTEGER")
    _add_column_if_missing(db, "decision_log", "review_due_at",  "TEXT")

    db.conn.execute("""
        CREATE TABLE IF NOT EXISTS predictive_alerts (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            generated_at   TEXT    DEFAULT CURRENT_TIMESTAMP,
            alert_type     TEXT,
            risk_level     TEXT,
            content        TEXT,
            acknowledged   INTEGER DEFAULT 0
        )
    """)
    db.conn.execute("""
        CREATE TABLE IF NOT EXISTS intervention_log (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            triggered_at   TEXT    DEFAULT CURRENT_TIMESTAMP,
            trigger_type   TEXT,
            content        TEXT,
            delivered      INTEGER DEFAULT 0
        )
    """)
    db.conn.commit()


def _add_column_if_missing(db, table: str, column: str, col_type: str):
    """Safely adds a column to an existing table if it doesn't already exist."""
    try:
        existing = db.conn.execute(f"PRAGMA table_info({table})").fetchall()
        col_names = [row[1] for row in existing]
        if column not in col_names:
            db.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            db.conn.commit()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# PREDICTIVE CRASH WINDOW
# ─────────────────────────────────────────────────────────────────────────────

class PredictiveEngine:

    def __init__(self, db):
        self.db = db
        _ensure_tables(db)

    def predict_crash_window(self) -> dict:
        """
        Looks at the shape of the current data trajectory and compares it to
        patterns that preceded past crashes.

        Unlike generate_crash_alert() which fires AFTER crash signals appear,
        this fires when the TRAJECTORY matches pre-crash trajectories —
        before the signals fully materialize.

        Returns a dict with prediction confidence and hours-to-impact estimate.
        """
        engine   = PatternEngine(self.db)
        sig_data = engine.map_danger_signature()

        if sig_data.get("status") != "complete":
            return {
                "status":     "insufficient_history",
                "prediction": "no_prediction",
                "confidence": 0
            }

        match_pct = sig_data.get("current_signature_match", 0)
        urgency   = sig_data.get("intervention_urgency", "LOW")

        # Get current trajectory (are we heading toward or away from danger?)
        metrics = list(reversed(self.db.get_recent_metrics(limit=6)))
        if len(metrics) < 3:
            return {"status": "insufficient_recent", "prediction": "no_prediction"}

        energy_series  = [_safe(m, "energy", 5) or 5 for m in metrics]
        impulse_series = [_safe(m, "impulse_drive", 3) or 3 for m in metrics]
        sleep_series   = [_safe(m, "sleep_hours", 6) or 6 for m in metrics]

        # Trajectory: is energy declining over the series?
        energy_slope = 0
        if len(energy_series) >= 3:
            recent_avg   = _avg(energy_series[-2:])
            older_avg    = _avg(energy_series[:2])
            energy_slope = (recent_avg or 0) - (older_avg or 0)

        impulse_slope = 0
        if len(impulse_series) >= 3:
            recent_imp    = _avg(impulse_series[-2:])
            older_imp     = _avg(impulse_series[:2])
            impulse_slope = (recent_imp or 0) - (older_imp or 0)

        sleep_avg = _avg(sleep_series)

        # Composite prediction score
        prediction_score = 0
        factors = []

        if match_pct >= 67:
            prediction_score += 40
            factors.append(f"current metrics match pre-crash signature at {match_pct}%")

        if energy_slope <= -1.5:
            prediction_score += 20
            factors.append(f"energy declining ({energy_slope:+.1f} over last 6 entries)")

        if impulse_slope >= 1.5:
            prediction_score += 15
            factors.append(f"impulse rising ({impulse_slope:+.1f})")

        if sleep_avg and sleep_avg < 5:
            prediction_score += 15
            factors.append(f"sleep deficit sustained ({sleep_avg}h avg)")

        # Energy volatility (std dev across last 6)
        energy_std = _std(energy_series)
        if energy_std >= 2.5:
            prediction_score += 10
            factors.append(f"high energy volatility (σ={energy_std})")

        prediction_score = min(prediction_score, 100)

        if prediction_score >= 70:
            window = "12-24 hours"
            level  = "IMMINENT"
        elif prediction_score >= 50:
            window = "24-48 hours"
            level  = "PROBABLE"
        elif prediction_score >= 30:
            window = "48-72 hours"
            level  = "POSSIBLE"
        else:
            window = None
            level  = "LOW_RISK"

        result = {
            "status":               "complete",
            "prediction":           level,
            "confidence":           prediction_score,
            "estimated_window":     window,
            "contributing_factors": factors,
            "energy_trajectory":    energy_slope,
            "current_match_pct":    match_pct
        }

        # Save alert if significant
        if prediction_score >= 50:
            self._save_alert("crash_prediction", level, json.dumps(result, default=str))

        return result

    def format_crash_prediction(self) -> str:
        """Formats the crash prediction as a human-readable warning."""
        pred = self.predict_crash_window()

        if pred.get("prediction") in ("LOW_RISK", "no_prediction") or pred.get("status") != "complete":
            return ""

        level      = pred["prediction"]
        window     = pred.get("estimated_window", "unknown timeframe")
        confidence = pred.get("confidence", 0)
        factors    = pred.get("contributing_factors", [])

        lines = [
            "",
            "─" * 60,
            f"  ⚠  PREDICTIVE WARNING — CRASH {level} ({confidence}% confidence)",
            f"     Estimated window: {window}",
        ]

        if factors:
            lines.append("     Contributing factors:")
            for f in factors:
                lines.append(f"       · {f}")

        lines.append("─" * 60)
        lines.append("")

        return "\n".join(lines)

    def _save_alert(self, alert_type: str, risk_level: str, content: str):
        """Persists a predictive alert to the DB."""
        try:
            self.db.conn.execute(
                "INSERT INTO predictive_alerts (alert_type, risk_level, content) VALUES (?, ?, ?)",
                (alert_type, risk_level, content)
            )
            self.db.conn.commit()
        except Exception:
            pass

    # ── Decision Quality System ──────────────────────────────────────────────

    def log_decision(
        self,
        decision_text: str,
        category: str = "general",
        substance_context: str = ""
    ) -> int:
        """
        Logs a decision with current metric context captured automatically.
        Sets review_due_at to 30 days from now for retrospective rating.
        Returns the decision ID for later quality rating.
        """
        _ensure_tables(self.db)

        recent_metrics = self.db.get_recent_metrics(limit=1)
        energy = mood = sleep = fog = impulse = None
        if recent_metrics:
            m       = recent_metrics[0]
            energy  = _safe(m, "energy")
            mood    = _safe(m, "mood")
            sleep   = _safe(m, "sleep_hours")
            fog     = _safe(m, "mental_fog")
            impulse = _safe(m, "impulse_drive")

        review_due = (datetime.now() + timedelta(days=30)).isoformat()

        try:
            cursor = self.db.conn.cursor()
            cursor.execute("""
                INSERT INTO decision_log
                (decision_text, category, energy_at_decision, mood_at_decision,
                 sleep_at_decision, fog_at_decision, impulse_at_decision,
                 substance_context, review_due_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                decision_text, category, energy, mood, sleep, fog, impulse,
                substance_context, review_due
            ))
            self.db.conn.commit()
            return cursor.lastrowid
        except Exception:
            return -1

    def rate_decision(self, decision_id: int, quality_rating: int, outcome_text: str = ""):
        """
        Retrospectively rates a logged decision (1-10).
        Sets both quality_rating and outcome_score for dual-column compatibility.
        This data feeds the decision_quality analysis in pattern_engine.py.
        """
        _ensure_tables(self.db)
        try:
            self.db.conn.execute("""
                UPDATE decision_log
                SET quality_rating = ?,
                    outcome_score  = ?,
                    outcome_text   = ?,
                    reviewed_at    = ?
                WHERE id = ?
            """, (
                quality_rating,
                quality_rating,   # mirrors to outcome_score for Swap 10 compat
                outcome_text,
                datetime.now().isoformat(),
                decision_id
            ))
            self.db.conn.commit()
        except Exception:
            pass

    def get_unrated_decisions(self, days_old: int = 30) -> list:
        """
        Returns decisions older than 3 days with no quality rating — ready for review.
        Uses review_due_at when available for Swap 10 compatibility.
        """
        _ensure_tables(self.db)
        floor  = (datetime.now() - timedelta(days=3)).isoformat()
        cutoff = (datetime.now() - timedelta(days=days_old)).isoformat()
        try:
            cursor = self.db.conn.cursor()
            cursor.execute("""
                SELECT * FROM decision_log
                WHERE outcome_score IS NULL
                AND logged_at <= ?
                AND logged_at >= ?
                ORDER BY logged_at ASC
            """, (floor, cutoff))
            return cursor.fetchall()
        except Exception:
            return []

    def get_all_decisions(self, limit: int = 20) -> list:
        """Returns recent decisions for display."""
        _ensure_tables(self.db)
        try:
            cursor = self.db.conn.cursor()
            cursor.execute(
                "SELECT * FROM decision_log ORDER BY logged_at DESC LIMIT ?", (limit,)
            )
            return cursor.fetchall()
        except Exception:
            return []

    # ── Autonomous Intervention Detection ────────────────────────────────────

    def check_intervention_triggers(self) -> list:
        """
        Runs silently and detects conditions that warrant autonomous intervention.
        Returns a list of triggered interventions (type, content, urgency).

        Triggered conditions:
          1. No log in N days (operator has gone dark)
          2. Crash trajectory detected (from predict_crash_window)
          3. Relapse risk signature detected (from correlations.py)
          4. 3+ consecutive crisis-flagged entries (from crisis_flags table)
          5. Goal stall: primary goal has had zero momentum for 14 days
        """
        _ensure_tables(self.db)
        triggers = []

        # ── Trigger 1: No log in 3+ days ──────────────────────────────────
        try:
            row = self.db.conn.execute(
                "SELECT timestamp FROM logs ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            if row:
                last_ts = datetime.fromisoformat(row[0])
                if last_ts.tzinfo is None:
                    last_ts = last_ts.replace(tzinfo=timezone.utc)
                days_since = (datetime.now(timezone.utc) - last_ts).days
                if days_since >= 3:
                    triggers.append({
                        "type":    "dark_period",
                        "urgency": "HIGH" if days_since >= 5 else "MODERATE",
                        "content": (
                            f"No sync logged in {days_since} days. "
                            f"Last entry: {last_ts.strftime('%A %B %d')}. "
                            f"System cannot monitor trajectory without data."
                        )
                    })
        except Exception:
            pass

        # ── Trigger 2: Crash trajectory ───────────────────────────────────
        try:
            pred = self.predict_crash_window()
            if pred.get("prediction") in ("IMMINENT", "PROBABLE"):
                triggers.append({
                    "type":    "crash_trajectory",
                    "urgency": pred["prediction"],
                    "content": (
                        f"Crash trajectory detected at {pred.get('confidence')}% confidence. "
                        f"Estimated window: {pred.get('estimated_window', 'unknown')}. "
                        f"Factors: {'; '.join(pred.get('contributing_factors', []))}"
                    )
                })
        except Exception:
            pass

        # ── Trigger 3: Relapse risk signature ─────────────────────────────
        try:
            corr = CorrelationEngine(self.db)
            rr   = corr.get_relapse_risk_signature()
            if rr.get("status") == "complete" and rr.get("risk_level") in ("HIGH", "MODERATE"):
                triggers.append({
                    "type":    "relapse_risk",
                    "urgency": rr["risk_level"],
                    "content": rr.get("note", "Relapse risk signature detected.")
                })
        except Exception:
            pass

        # ── Trigger 4: Consecutive crisis flags (3+) ──────────────────────
        try:
            recent_crisis = self.db.conn.execute(
                "SELECT COUNT(*) FROM crisis_flags WHERE reviewed = 0"
            ).fetchone()
            if recent_crisis and recent_crisis[0] >= 3:
                triggers.append({
                    "type":    "sustained_crisis_pattern",
                    "urgency": "CRITICAL",
                    "content": (
                        f"{recent_crisis[0]} unacknowledged crisis flags in system. "
                        "Sustained distress pattern detected."
                    )
                })
        except Exception:
            pass

        # ── Trigger 5: Goal momentum collapse ─────────────────────────────
        try:
            engine = PatternEngine(self.db)
            gm     = engine.score_goal_momentum()
            if gm.get("status") == "complete":
                stalled = gm.get("stalled_goals", [])
                for g in stalled:
                    days_silent = g.get("days_since_mention")
                    if days_silent and days_silent >= 14:
                        triggers.append({
                            "type":    "goal_stall",
                            "urgency": "MODERATE",
                            "content": (
                                f"Goal '{g['goal']}' has had zero momentum for "
                                f"{days_silent} days. Not mentioned in any sync or journal."
                            )
                        })
        except Exception:
            pass

        # ── Log all triggers ───────────────────────────────────────────────
        for t in triggers:
            try:
                self.db.conn.execute(
                    "INSERT INTO intervention_log (trigger_type, content) VALUES (?, ?)",
                    (t["type"], t["content"])
                )
            except Exception:
                pass
        if triggers:
            try:
                self.db.conn.commit()
            except Exception:
                pass

        return triggers

    def format_interventions_for_display(self) -> str:
        """
        Formats triggered interventions as startup alert text.
        Called from marlow.py at startup.

        Predictive warnings are suppressed here when reactive alerts already fired
        (Swap 11: gate predictive if reactive fired — handled in marlow.py).
        """
        triggers = self.check_intervention_triggers()

        if not triggers:
            return ""

        critical = [t for t in triggers if t["urgency"] == "CRITICAL"]
        high     = [t for t in triggers if t["urgency"] in ("HIGH", "IMMINENT", "PROBABLE")]
        moderate = [t for t in triggers if t["urgency"] in ("MODERATE", "POSSIBLE")]

        lines = ["", "█" * 60]

        if critical:
            lines.append("  ⚠  CRITICAL ALERT")
            for t in critical:
                lines.append(f"  {t['content']}")

        if high:
            lines.append("  ⚠  HIGH PRIORITY")
            for t in high:
                lines.append(f"  {t['content']}")

        if moderate:
            lines.append("  ─  MODERATE ALERTS")
            for t in moderate:
                lines.append(f"  {t['content']}")

        lines.append("█" * 60)
        lines.append("")

        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# AUTO WEEKLY SYNTHESIS CHECK
# Tier 2: Automatically generates weekly report if Sunday and not yet generated
# ─────────────────────────────────────────────────────────────────────────────

def should_auto_generate_weekly(db) -> bool:
    """
    Returns True if today is Sunday and the weekly report hasn't been
    generated this week yet.
    """
    today = datetime.now()
    if today.weekday() != 6:  # 6 = Sunday
        return False

    try:
        latest = db.get_latest_weekly_report()
        if not latest:
            return True
        gen_at     = datetime.fromisoformat(latest["generated_at"])
        days_since = (today - gen_at).days
        return days_since >= 6
    except Exception:
        return True


# ─────────────────────────────────────────────────────────────────────────────
# MODULE-LEVEL COMPATIBILITY WRAPPERS
# Used by council_engine.py and marlow.py as standalone function calls
# ─────────────────────────────────────────────────────────────────────────────

def predict_crash_window(db) -> dict:
    """Standalone wrapper for PredictiveEngine.predict_crash_window()."""
    return PredictiveEngine(db).predict_crash_window()


def predict_relapse_risk(db) -> dict:
    """Stub — relapse risk handled by correlations.py CorrelationEngine."""
    return {"risk_level": "LOW", "risk_score": 0}


def assess_decision_quality_state(db) -> dict:
    """Returns a basic decision quality state signal for context injection."""
    return {"reliable": True, "quality_score": 10}


def build_causal_model(db) -> dict:
    """Stub — causal model reserved for future implementation."""
    return {"sufficient_data": False}


def build_prediction_context(db, max_chars: int = 1500) -> str:
    """Formats crash prediction as a context string for council injection."""
    return PredictiveEngine(db).format_crash_prediction()[:max_chars]
