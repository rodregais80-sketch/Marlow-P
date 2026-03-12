"""
decision_tracker.py — NEW FILE
Decision quality retrospective tracking and goal momentum scoring.

Tier 1: Decision quality retrospective — log a decision, rate it 30 days later.
         After enough data, the system maps what state produces good vs bad decisions.

Tier 1: Goal momentum scoring — watches logs for goal-related activity and
         produces a live momentum score per goal without manual check-ins.

The retrospective model learns the operator's personal decision fingerprint:
what their internal state looks like when they make their best decisions vs worst.
That map is more persuasive than any external argument because it is theirs.
"""

import re
from datetime import datetime, timedelta, timezone
from collections import defaultdict


def _row_get(row, key, default=None):
    try:
        v = row[key]
        return v if v is not None else default
    except (KeyError, IndexError, TypeError):
        return default


def _avg(lst):
    return round(sum(lst) / len(lst), 2) if lst else None


# ─────────────────────────────────────────────────────────────────────────────
# DATABASE SCHEMA ADDITIONS
# These tables are added in database.py _initialize_schema().
# Defined here for reference:
#
# decision_log:
#   id, timestamp, decision_text, state_energy, state_mood, state_fog,
#   state_impulse, state_sleep, outcome_score (NULL until reviewed),
#   outcome_notes, review_due_at, reviewed_at
#
# goal_momentum:
#   id, goal_id, computed_at, momentum_score, mention_count,
#   action_count, stall_flag
# ─────────────────────────────────────────────────────────────────────────────


class DecisionTracker:
    """
    Logs decisions with their physiological/emotional state context.
    Allows retrospective rating. Builds a personal decision quality map
    that tells the operator what internal conditions produce their best outcomes.
    """

    def __init__(self, db):
        self.db = db

    def log_decision(self, decision_text: str, state: dict) -> int:
        """
        Logs a decision with current state. State dict should contain
        energy, mood, fog, impulse, sleep from current metrics.
        Returns the decision_id for future retrospective rating.
        """
        review_due = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        try:
            cursor = self.db.conn.cursor()
            cursor.execute("""
                INSERT INTO decision_log
                (timestamp, decision_text, state_energy, state_mood, state_fog,
                 state_impulse, state_sleep, review_due_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.now(timezone.utc).isoformat(),
                decision_text,
                state.get("energy"), state.get("mood"), state.get("fog"),
                state.get("impulse"), state.get("sleep"),
                review_due
            ))
            self.db.conn.commit()
            return cursor.lastrowid
        except Exception as e:
            self.db.conn.rollback()
            print(f"[DB ERROR] log_decision: {e}")
            return None

    def rate_decision(self, decision_id: int, outcome_score: int, outcome_notes: str = ""):
        """
        Retrospective rating of a decision outcome.
        outcome_score: 1-10 (1 = disastrous, 10 = perfect)
        """
        try:
            self.db.conn.execute("""
                UPDATE decision_log
                SET outcome_score = ?, outcome_notes = ?, reviewed_at = ?
                WHERE id = ?
            """, (
                outcome_score,
                outcome_notes,
                datetime.now(timezone.utc).isoformat(),
                decision_id
            ))
            self.db.conn.commit()
        except Exception as e:
            self.db.conn.rollback()
            print(f"[DB ERROR] rate_decision: {e}")

    def get_pending_reviews(self) -> list:
        """Returns decisions that are due for retrospective rating."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            return self.db.conn.execute("""
                SELECT * FROM decision_log
                WHERE outcome_score IS NULL AND review_due_at <= ?
                ORDER BY review_due_at ASC
            """, (now,)).fetchall()
        except Exception:
            return []

    def get_all_decisions(self, limit: int = 50) -> list:
        try:
            return self.db.conn.execute(
                "SELECT * FROM decision_log ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            ).fetchall()
        except Exception:
            return []

    def build_decision_quality_map(self) -> dict:
        """
        Analyzes rated decisions to build the personal decision fingerprint.

        Maps which state conditions (energy, mood, fog, impulse, sleep)
        are statistically associated with good vs bad decision outcomes.

        Returns a dict with the operator's personal decision quality profile.
        """
        try:
            rated = self.db.conn.execute("""
                SELECT * FROM decision_log
                WHERE outcome_score IS NOT NULL
                ORDER BY timestamp DESC
            """).fetchall()
        except Exception:
            return {"sufficient_data": False}

        if len(rated) < 5:
            return {
                "sufficient_data": False,
                "rated_count":     len(rated),
                "needed":          5
            }

        good_decisions = [d for d in rated if _row_get(d, "outcome_score", 0) >= 7]
        bad_decisions  = [d for d in rated if _row_get(d, "outcome_score", 0) <= 4]

        def state_avg(decisions, field):
            vals = [
                _row_get(d, field)
                for d in decisions
                if _row_get(d, field) is not None
            ]
            return _avg(vals)

        good_state = {}
        bad_state  = {}
        for field in ["state_energy", "state_mood", "state_fog", "state_impulse", "state_sleep"]:
            clean = field.replace("state_", "")
            good_state[clean] = state_avg(good_decisions, field)
            bad_state[clean]  = state_avg(bad_decisions, field)

        # Build the insight
        insights = []
        if good_state.get("energy") and bad_state.get("energy"):
            diff = (good_state["energy"] or 0) - (bad_state["energy"] or 0)
            if abs(diff) >= 1.0:
                insights.append(
                    f"Your good decisions average energy {good_state['energy']:.1f} vs "
                    f"{bad_state['energy']:.1f} for bad ones — "
                    f"{'higher energy correlates with better outcomes' if diff > 0 else 'lower energy surprisingly correlates with better outcomes'}"
                )

        if good_state.get("impulse") and bad_state.get("impulse"):
            diff = (bad_state["impulse"] or 0) - (good_state["impulse"] or 0)
            if abs(diff) >= 1.0:
                insights.append(
                    f"Bad decisions average impulse {bad_state['impulse']:.1f} vs "
                    f"{good_state['impulse']:.1f} for good ones — "
                    f"{'impulse drive is inversely correlated with decision quality in your data' if diff > 0 else 'higher impulse surprisingly correlates with better outcomes'}"
                )

        if good_state.get("sleep") and bad_state.get("sleep"):
            diff = (good_state["sleep"] or 0) - (bad_state["sleep"] or 0)
            if abs(diff) >= 0.75:
                insights.append(
                    f"Good decisions averaged {good_state['sleep']:.1f}h sleep vs "
                    f"{bad_state['sleep']:.1f}h for bad ones"
                )

        return {
            "sufficient_data":  True,
            "total_rated":      len(rated),
            "good_count":       len(good_decisions),
            "bad_count":        len(bad_decisions),
            "good_state_avg":   good_state,
            "bad_state_avg":    bad_state,
            "insights":         insights
        }

    def get_decisions_due_context(self) -> str:
        """Returns a short string noting pending reviews for injection into prompts."""
        pending = self.get_pending_reviews()
        if not pending:
            return ""
        return (
            f"[{len(pending)} decision(s) are due for retrospective rating — "
            f"the system is waiting for outcome data to refine your decision quality map]"
        )


# ─────────────────────────────────────────────────────────────────────────────
# GOAL MOMENTUM SCORING
# ─────────────────────────────────────────────────────────────────────────────

class GoalMomentumScorer:
    """
    Watches sync logs and journal entries for goal-related activity.
    Produces a momentum score per goal without requiring manual check-ins.

    A goal with zero mention in 7 days is flagged automatically.
    A goal being mentioned daily gets surfaced as the operator's actual driver.
    """

    # Action words that indicate active progress (not just mention)
    ACTION_WORDS = [
        "called", "emailed", "built", "finished", "completed", "launched",
        "posted", "sent", "pitched", "worked on", "started", "booked",
        "scheduled", "signed", "closed", "delivered", "published", "shipped",
        "coded", "wrote", "created", "made", "ran", "executed"
    ]

    def __init__(self, db):
        self.db = db

    def score_all_goals(self, days: int = 14) -> list:
        """
        Scores all active goals by their presence in recent behavioral logs.
        Returns list of goal dicts with momentum scores.
        """
        try:
            goals = self.db.get_active_goals()
        except Exception:
            return []

        if not goals:
            return []

        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        try:
            logs     = self.db.conn.execute(
                "SELECT timestamp, content FROM logs WHERE timestamp >= ? ORDER BY timestamp DESC",
                (since,)
            ).fetchall()
            journals = self.db.conn.execute(
                "SELECT timestamp, content FROM journals WHERE timestamp >= ? ORDER BY timestamp DESC",
                (since,)
            ).fetchall()
        except Exception:
            logs, journals = [], []

        all_entries = []
        for l in logs:
            all_entries.append({
                "timestamp": l["timestamp"] or "",
                "text":      l["content"] or ""
            })
        for j in journals:
            all_entries.append({
                "timestamp": j["timestamp"] or "",
                "text":      j["content"] or ""
            })

        all_text = " ".join(e["text"] for e in all_entries).lower()

        scored = []
        for goal in goals:
            title    = (goal["title"] or "").lower()
            keywords = [w for w in title.split() if len(w) > 3]
            if not keywords:
                keywords = title.split()

            # Count mentions
            mention_count = sum(all_text.count(kw) for kw in keywords)

            # Count action-associated mentions
            action_count = 0
            for entry in all_entries:
                entry_text = entry["text"].lower()
                has_keyword = any(kw in entry_text for kw in keywords)
                has_action  = any(aw in entry_text for aw in self.ACTION_WORDS)
                if has_keyword and has_action:
                    action_count += 1

            # Days since last mention
            last_mention_days = None
            for entry in all_entries:
                if any(kw in entry["text"].lower() for kw in keywords):
                    try:
                        ts = datetime.fromisoformat(entry["timestamp"])
                        last_mention_days = (datetime.now(timezone.utc) - ts.replace(tzinfo=timezone.utc)).days
                        break
                    except Exception:
                        pass

            # Momentum score: 0-100
            raw_score     = min((mention_count * 5) + (action_count * 15), 70)
            recency_bonus = max(0, 30 - ((last_mention_days or 30) * 2))
            momentum      = min(raw_score + recency_bonus, 100)

            stall_flag = (last_mention_days is None or last_mention_days > 7)

            scored.append({
                "goal_id":           goal["id"],
                "title":             goal["title"],
                "momentum_score":    momentum,
                "mention_count":     mention_count,
                "action_count":      action_count,
                "last_mention_days": last_mention_days,
                "stall_flag":        stall_flag,
                "label": (
                    "ACTIVE"   if momentum >= 60 else
                    "BUILDING" if momentum >= 30 else
                    "STALLING" if momentum >= 10 else
                    "DORMANT"
                )
            })

        scored.sort(key=lambda x: -x["momentum_score"])
        return scored

    def build_momentum_context(self, days: int = 14, max_chars: int = 600) -> str:
        """Formats goal momentum scores for persona prompt injection."""
        scored = self.score_all_goals(days=days)
        if not scored:
            return "No active goals to score."

        lines = ["--- GOAL MOMENTUM SCORES ---"]
        for g in scored:
            stall = " ⚠ STALLING — not appearing in logs" if g["stall_flag"] else ""
            lines.append(
                f"[{g['label']}] {g['title']}: {g['momentum_score']}/100"
                f" (mentions: {g['mention_count']}, actions: {g['action_count']}){stall}"
            )

        return "\n".join(lines)[:max_chars]

    def save_scores_to_db(self):
        """Persists current momentum scores to goal_momentum table."""
        scored = self.score_all_goals()
        now    = datetime.now(timezone.utc).isoformat()
        for g in scored:
            try:
                self.db.conn.execute("""
                    INSERT INTO goal_momentum
                    (goal_id, computed_at, momentum_score, mention_count, action_count, stall_flag)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    g["goal_id"], now, g["momentum_score"],
                    g["mention_count"], g["action_count"],
                    1 if g["stall_flag"] else 0
                ))
            except Exception:
                pass
        try:
            self.db.conn.commit()
        except Exception:
            pass


def build_decision_context(db, max_chars: int = 600) -> str:
    """
    Builds decision quality context for persona prompt injection.
    Combines decision map insights and pending reviews.
    """
    tracker = DecisionTracker(db)
    dmap    = tracker.build_decision_quality_map()

    lines = []

    if dmap.get("sufficient_data"):
        lines.append("--- DECISION QUALITY MAP (from your rated history) ---")
        for insight in dmap.get("insights", []):
            lines.append(f"  {insight}")
        lines.append(
            f"Based on {dmap['total_rated']} rated decisions: "
            f"{dmap['good_count']} good, {dmap['bad_count']} bad."
        )

    pending = tracker.get_decisions_due_context()
    if pending:
        lines.append(pending)

    return "\n".join(lines)[:max_chars] if lines else ""
