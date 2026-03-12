"""
database.py
SQLite persistence layer for the Marlow platform.

Changes from v2:
- Added decision_log table (Tier 1 decision quality retrospective)
- Added goal_momentum table (Tier 1 goal momentum scoring)
- Added auto_reports table (Tier 2 automatic weekly synthesis)
- All v2 tables, methods, and PersonaChat class preserved in full
"""

import sqlite3
import threading
import uuid
import random
from datetime import datetime, timedelta, timezone

_OFFLINE_MESSAGES_CHAT = {
    "ALDRIC": [
        "ALDRIC got up mid-thought and hasn't come back yet. The whiteboard still has your name on it.",
        "ALDRIC is unreachable. Last seen muttering about leverage points and walking in circles.",
        "ALDRIC closed his notebook and walked out without explanation. Classic.",
        "ALDRIC is currently somewhere doing math that nobody asked for. He'll be back when it's done."
    ],
    "SEREN": [
        "SEREN stepped out. She left the light on for you.",
        "SEREN is away. She said she'd be back but didn't say when. She rarely does.",
        "SEREN went quiet. Not the bad kind of quiet. The kind where she's paying attention to something important.",
        "SEREN is unavailable right now. She heard you though. She always does."
    ],
    "MORRO": [
        "MORRO has nothing to say right now. That's either very good or very bad.",
        "MORRO went dark. He does that.",
        "MORRO is not here. Whatever he was going to say, you can probably guess.",
        "MORRO stepped out mid-sentence. He does this when he thinks the honest answer would cause problems."
    ],
    "ORYN": [
        "ORYN is unavailable. He's comparing your last three sleep scores to a case study from 1987 Finland. He's very excited about it.",
        "ORYN got distracted by your cortisol data. He says it will only take a minute. It will not take a minute.",
        "ORYN stepped away to verify something in your metrics. He looked concerned but wouldn't say why.",
        "ORYN is reading a 400 page study about sleep. He thinks this is a completely normal thing to do right now."
    ]
}

thread_local = threading.local()


class DatabaseManager:
    def __init__(self, db_path="vault.db"):
        self.db_path = db_path
        self._get_conn()
        self._initialize_schema()

    def _get_conn(self):
        if not hasattr(thread_local, "conn") or thread_local.conn is None:
            thread_local.conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False
            )
            thread_local.conn.row_factory = sqlite3.Row
            thread_local.conn.execute("PRAGMA journal_mode=WAL;")
            thread_local.conn.execute("PRAGMA synchronous=NORMAL;")
            thread_local.conn.execute("PRAGMA cache_size=-64000;")
        return thread_local.conn

    @property
    def conn(self):
        return self._get_conn()

    @staticmethod
    def current_utc_timestamp() -> str:
        return datetime.now(timezone.utc).isoformat()

    def safe_execute(self, query: str, params: tuple = ()):
        try:
            cursor = self.conn.cursor()
            cursor.execute(query, params)
            self.conn.commit()
            return cursor
        except sqlite3.Error as e:
            self.conn.rollback()
            print(f"[DB ERROR] {e} | Query: {query[:80]}")
            return None

    def _initialize_schema(self):
        cursor = self.conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS static_profile (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                name TEXT, age INTEGER, location TEXT, occupation TEXT,
                primary_goal TEXT, biggest_challenge TEXT,
                support_style TEXT, additional_context TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS life_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                background TEXT, significant_events TEXT,
                current_struggles TEXT, current_strengths TEXT,
                relationship_status TEXT, support_network TEXT,
                mental_health_history TEXT, substance_history TEXT,
                goals_longterm TEXT, additional_context TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                sync_type TEXT, content TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                sync_type TEXT, energy INTEGER, mood INTEGER,
                mental_fog INTEGER, impulse_drive INTEGER,
                intensity INTEGER, sleep_hours REAL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS persona_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                persona_name TEXT, summary TEXT,
                risk_score INTEGER, confidence_score INTEGER, decision TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS journals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                content TEXT, intent_type TEXT,
                council_responded INTEGER DEFAULT 0, council_response TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS council_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                user_input TEXT, intent_type TEXT, full_response TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS crisis_flags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                content TEXT, confidence REAL, notes TEXT,
                reviewed INTEGER DEFAULT 0
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                title TEXT NOT NULL, description TEXT, status TEXT DEFAULT 'active',
                progress_note TEXT, target_date TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS weekly_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                generated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                week_start TEXT, week_end TEXT, report_content TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                role TEXT, content TEXT, intent_type TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS safe_space_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mood_checkins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                score INTEGER, label TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS monthly_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                generated_at TEXT NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                pattern_content TEXT NOT NULL
            )
        """)

        # ── NEW: Decision quality retrospective (Tier 1) ──────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS decision_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                decision_text TEXT NOT NULL,
                state_energy INTEGER,
                state_mood INTEGER,
                state_fog INTEGER,
                state_impulse INTEGER,
                state_sleep REAL,
                outcome_score INTEGER,
                outcome_notes TEXT,
                review_due_at TEXT,
                reviewed_at TEXT
            )
        """)

        # ── NEW: Goal momentum scores (Tier 1) ────────────────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS goal_momentum (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id INTEGER,
                computed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                momentum_score INTEGER,
                mention_count INTEGER,
                action_count INTEGER,
                stall_flag INTEGER DEFAULT 0
            )
        """)

        # ── NEW: Auto-generated weekly reports (Tier 2) ───────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS auto_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                generated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                report_type TEXT,
                period_start TEXT,
                period_end TEXT,
                content TEXT,
                delivered INTEGER DEFAULT 0
            )
        """)

        self.conn.commit()

    # ── Static profile ────────────────────────────────────────────────────────

    def get_static_profile(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM static_profile ORDER BY id DESC LIMIT 1")
        return cursor.fetchone()

    def save_static_profile(self, data: dict):
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT INTO static_profile
                (name, age, location, occupation, primary_goal,
                 biggest_challenge, support_style, additional_context)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data.get("name"), data.get("age"), data.get("location"),
                data.get("occupation"), data.get("primary_goal"),
                data.get("biggest_challenge"), data.get("support_style"),
                data.get("additional_context")
            ))
            self.conn.commit()
        except sqlite3.Error as e:
            self.conn.rollback()
            print(f"[DB ERROR] save_static_profile: {e}")

    def cleanup_old_profiles(self, keep: int = 5):
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                DELETE FROM static_profile WHERE id NOT IN (
                    SELECT id FROM static_profile ORDER BY id DESC LIMIT ?
                )
            """, (keep,))
            self.conn.commit()
        except sqlite3.Error as e:
            self.conn.rollback()

    def cleanup_old_histories(self, keep: int = 5):
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                DELETE FROM life_history WHERE id NOT IN (
                    SELECT id FROM life_history ORDER BY id DESC LIMIT ?
                )
            """, (keep,))
            self.conn.commit()
        except sqlite3.Error as e:
            self.conn.rollback()

    # ── Life history ──────────────────────────────────────────────────────────

    def get_life_history(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM life_history ORDER BY id DESC LIMIT 1")
        return cursor.fetchone()

    def save_life_history(self, data: dict):
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT INTO life_history
                (background, significant_events, current_struggles, current_strengths,
                 relationship_status, support_network, mental_health_history,
                 substance_history, goals_longterm, additional_context)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data.get("background"), data.get("significant_events"),
                data.get("current_struggles"), data.get("current_strengths"),
                data.get("relationship_status"), data.get("support_network"),
                data.get("mental_health_history"), data.get("substance_history"),
                data.get("goals_longterm"), data.get("additional_context")
            ))
            self.conn.commit()
        except sqlite3.Error as e:
            self.conn.rollback()
            print(f"[DB ERROR] save_life_history: {e}")

    # ── Logs and metrics ──────────────────────────────────────────────────────

    def save_metrics(self, sync_type: str, metrics: dict):
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT INTO metrics
                (timestamp, sync_type, energy, mood, mental_fog,
                 impulse_drive, intensity, sleep_hours)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                self.current_utc_timestamp(), sync_type,
                metrics.get("energy"), metrics.get("mood"),
                metrics.get("mental_fog"), metrics.get("impulse_drive"),
                metrics.get("intensity"), metrics.get("sleep_hours")
            ))
            self.conn.commit()
        except sqlite3.Error as e:
            self.conn.rollback()
            print(f"[DB ERROR] save_metrics: {e}")

    def save_log(self, sync_type: str, content: str):
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT INTO logs (timestamp, sync_type, content) VALUES (?, ?, ?)",
                (self.current_utc_timestamp(), sync_type, content)
            )
            self.conn.commit()
        except sqlite3.Error as e:
            self.conn.rollback()
            print(f"[DB ERROR] save_log: {e}")

    def get_recent_logs(self, limit=7):
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM logs ORDER BY timestamp DESC LIMIT ?", (limit,))
        return cursor.fetchall()

    def get_recent_metrics(self, limit=30):
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM metrics ORDER BY timestamp DESC LIMIT ?", (limit,))
        return cursor.fetchall()

    def get_logs_for_week(self, week_start: str, week_end: str):
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM logs WHERE timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp ASC
        """, (week_start, week_end))
        return cursor.fetchall()

    def get_metrics_for_week(self, week_start: str, week_end: str):
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM metrics WHERE timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp ASC
        """, (week_start, week_end))
        return cursor.fetchall()

    # ── Persona memory ────────────────────────────────────────────────────────

    def save_persona_memory(self, persona_name: str, summary: str,
                             risk_score: int, confidence_score: int, decision: str):
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT INTO persona_memory
                (timestamp, persona_name, summary, risk_score, confidence_score, decision)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (self.current_utc_timestamp(), persona_name, summary,
                  risk_score, confidence_score, decision))
            self.conn.commit()
        except sqlite3.Error as e:
            self.conn.rollback()
            print(f"[DB ERROR] save_persona_memory: {e}")

    def get_persona_memory(self, persona_name: str, limit: int = 10):
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM persona_memory WHERE persona_name = ?
            ORDER BY timestamp DESC LIMIT ?
        """, (persona_name, limit))
        return cursor.fetchall()

    def get_persona_memory_extended(self, persona_name: str, limit: int = 50, since_days: int = None):
        cursor = self.conn.cursor()
        query  = "SELECT * FROM persona_memory WHERE persona_name = ?"
        params = [persona_name]
        if since_days is not None:
            since_date = datetime.now(timezone.utc) - timedelta(days=since_days)
            query     += " AND timestamp >= ?"
            params.append(since_date.isoformat())
        query  += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        cursor.execute(query, params)
        memories = cursor.fetchall()

        pattern_count = {}
        for m in memories:
            key = m["summary"]
            if key:
                pattern_count[key] = pattern_count.get(key, 0) + 1

        pattern_summary = "\n".join(
            f"[Pattern x{count}] {summary}"
            for summary, count in sorted(pattern_count.items(), key=lambda x: -x[1])
            if count > 1
        )
        return memories, pattern_summary or None

    def get_all_persona_patterns(self, persona_name: str, min_repeats: int = 2) -> list:
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT summary, COUNT(*) as count FROM persona_memory
            WHERE persona_name = ? AND summary IS NOT NULL
            GROUP BY summary HAVING count >= ?
            ORDER BY count DESC
        """, (persona_name, min_repeats))
        return cursor.fetchall()

    # ── Crisis flags ──────────────────────────────────────────────────────────

    def save_crisis_flag(self, content: str, confidence: float, notes: str):
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT INTO crisis_flags (timestamp, content, confidence, notes) VALUES (?, ?, ?, ?)",
                (self.current_utc_timestamp(), content, confidence, notes)
            )
            self.conn.commit()
        except sqlite3.Error as e:
            self.conn.rollback()
            print(f"[DB ERROR] save_crisis_flag: {e}")

    def get_crisis_flags(self, reviewed: bool = False, limit: int = 20):
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM crisis_flags WHERE reviewed = ? ORDER BY timestamp DESC LIMIT ?",
            (1 if reviewed else 0, limit)
        )
        return cursor.fetchall()

    # ── Journals ──────────────────────────────────────────────────────────────

    def is_duplicate_journal(self, content: str, window_seconds: int = 60) -> bool:
        try:
            since = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT id FROM journals WHERE content = ? AND timestamp >= ? LIMIT 1
            """, (content, since.isoformat()))
            return cursor.fetchone() is not None
        except sqlite3.Error:
            return False

    def save_journal(self, content: str, intent_type: str):
        if self.is_duplicate_journal(content, window_seconds=60):
            try:
                cursor = self.conn.cursor()
                cursor.execute(
                    "SELECT id FROM journals WHERE content = ? ORDER BY id DESC LIMIT 1",
                    (content,)
                )
                row = cursor.fetchone()
                return row["id"] if row else None
            except Exception:
                return None
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT INTO journals (timestamp, content, intent_type) VALUES (?, ?, ?)",
                (self.current_utc_timestamp(), content, intent_type)
            )
            self.conn.commit()
            return cursor.lastrowid
        except sqlite3.Error as e:
            self.conn.rollback()
            print(f"[DB ERROR] save_journal: {e}")
            return None

    def update_journal_response(self, journal_id: int, response: str):
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                UPDATE journals SET council_responded = 1, council_response = ?
                WHERE id = ?
            """, (response, journal_id))
            self.conn.commit()
        except sqlite3.Error as e:
            self.conn.rollback()
            print(f"[DB ERROR] update_journal_response: {e}")

    def get_recent_journals(self, limit=3):
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM journals ORDER BY timestamp DESC LIMIT ?", (limit,))
        return cursor.fetchall()

    def get_journals_for_week(self, week_start: str, week_end: str):
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM journals WHERE timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp ASC
        """, (week_start, week_end))
        return cursor.fetchall()

    # ── Council sessions ──────────────────────────────────────────────────────

    def save_council_session(self, user_input: str, intent_type: str, full_response: str):
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT INTO council_sessions (timestamp, user_input, intent_type, full_response)
                VALUES (?, ?, ?, ?)
            """, (self.current_utc_timestamp(), user_input, intent_type, full_response))
            self.conn.commit()
        except sqlite3.Error as e:
            self.conn.rollback()
            print(f"[DB ERROR] save_council_session: {e}")

    def get_recent_council_sessions(self, limit: int = 10):
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM council_sessions ORDER BY timestamp DESC LIMIT ?", (limit,)
        )
        return cursor.fetchall()

    def get_council_sessions_for_week(self, week_start: str, week_end: str):
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM council_sessions WHERE timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp ASC
        """, (week_start, week_end))
        return cursor.fetchall()

    # ── Goals ─────────────────────────────────────────────────────────────────

    def save_goal(self, title: str, description: str = "", target_date: str = ""):
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT INTO goals (title, description, target_date) VALUES (?, ?, ?)
            """, (title, description, target_date))
            self.conn.commit()
            return cursor.lastrowid
        except sqlite3.Error as e:
            self.conn.rollback()
            print(f"[DB ERROR] save_goal: {e}")
            return None

    def get_active_goals(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM goals WHERE status = 'active' ORDER BY created_at ASC")
        return cursor.fetchall()

    def get_all_goals(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM goals ORDER BY created_at DESC")
        return cursor.fetchall()

    def update_goal_progress(self, goal_id: int, progress_note: str):
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                UPDATE goals SET progress_note = ?, updated_at = ? WHERE id = ?
            """, (progress_note, self.current_utc_timestamp(), goal_id))
            self.conn.commit()
        except sqlite3.Error as e:
            self.conn.rollback()
            print(f"[DB ERROR] update_goal_progress: {e}")

    def update_goal_status(self, goal_id: int, status: str):
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                UPDATE goals SET status = ?, updated_at = ? WHERE id = ?
            """, (status, self.current_utc_timestamp(), goal_id))
            self.conn.commit()
        except sqlite3.Error as e:
            self.conn.rollback()
            print(f"[DB ERROR] update_goal_status: {e}")

    def get_goals_as_context(self) -> str:
        goals = self.get_active_goals()
        if not goals:
            return "No active goals set."
        lines = []
        for i, g in enumerate(goals, 1):
            line = f"Goal {i}: {g['title']}"
            if g['description']:
                line += f" - {g['description']}"
            if g['progress_note']:
                line += f" [Progress: {g['progress_note']}]"
            if g['target_date']:
                line += f" [Target: {g['target_date']}]"
            lines.append(line)
        return "\n".join(lines)

    # ── Weekly reports ────────────────────────────────────────────────────────

    def save_weekly_report(self, week_start: str, week_end: str, content: str):
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT INTO weekly_reports (week_start, week_end, report_content) VALUES (?, ?, ?)
            """, (week_start, week_end, content))
            self.conn.commit()
        except sqlite3.Error as e:
            self.conn.rollback()
            print(f"[DB ERROR] save_weekly_report: {e}")

    def get_latest_weekly_report(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM weekly_reports ORDER BY generated_at DESC LIMIT 1")
        return cursor.fetchone()

    def get_all_weekly_reports(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM weekly_reports ORDER BY generated_at DESC")
        return cursor.fetchall()

    # ── Auto reports (Tier 2) ─────────────────────────────────────────────────

    def save_auto_report(self, report_type: str, period_start: str, period_end: str, content: str):
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT INTO auto_reports (report_type, period_start, period_end, content)
                VALUES (?, ?, ?, ?)
            """, (report_type, period_start, period_end, content))
            self.conn.commit()
            return cursor.lastrowid
        except sqlite3.Error as e:
            self.conn.rollback()
            print(f"[DB ERROR] save_auto_report: {e}")
            return None

    def get_undelivered_auto_reports(self) -> list:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM auto_reports WHERE delivered = 0 ORDER BY generated_at ASC"
        )
        return cursor.fetchall()

    def mark_auto_report_delivered(self, report_id: int):
        try:
            self.conn.execute(
                "UPDATE auto_reports SET delivered = 1 WHERE id = ?", (report_id,)
            )
            self.conn.commit()
        except sqlite3.Error as e:
            self.conn.rollback()

    def get_latest_auto_report(self, report_type: str = "weekly"):
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM auto_reports WHERE report_type = ? ORDER BY generated_at DESC LIMIT 1",
            (report_type,)
        )
        return cursor.fetchone()

    def should_generate_auto_report(self, report_type: str = "weekly") -> bool:
        """Returns True if a new auto report should be generated (none in last 6 days)."""
        latest = self.get_latest_auto_report(report_type)
        if not latest:
            return True
        try:
            last_dt  = datetime.fromisoformat(latest["generated_at"])
            age_days = (datetime.now(timezone.utc) - last_dt.replace(tzinfo=timezone.utc)).days
            return age_days >= 6
        except Exception:
            return True

    # ── Conversation history ──────────────────────────────────────────────────

    def save_conversation_turn(self, session_id: str, role: str, content: str, intent_type: str = ""):
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT INTO conversation_history (session_id, timestamp, role, content, intent_type)
                VALUES (?, ?, ?, ?, ?)
            """, (session_id, self.current_utc_timestamp(), role, content, intent_type))
            self.conn.commit()
        except sqlite3.Error as e:
            self.conn.rollback()
            print(f"[DB ERROR] save_conversation_turn: {e}")

    def get_conversation_history(self, session_id: str, limit: int = 30):
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM conversation_history WHERE session_id = ?
            ORDER BY timestamp DESC LIMIT ?
        """, (session_id, limit))
        return list(reversed(cursor.fetchall()))

    def get_conversation_history_extended(self, session_id: str, limit: int = 100, since_days: int = None):
        cursor = self.conn.cursor()
        query  = "SELECT * FROM conversation_history WHERE session_id = ?"
        params = [session_id]
        if since_days is not None:
            since_date = datetime.now(timezone.utc) - timedelta(days=since_days)
            query     += " AND timestamp >= ?"
            params.append(since_date.isoformat())
        query  += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        cursor.execute(query, params)
        return list(reversed(cursor.fetchall()))

    def get_conversation_as_messages(self, session_id: str, limit: int = 30) -> list:
        rows = self.get_conversation_history(session_id, limit)
        return [{"role": row["role"], "content": row["content"]} for row in rows]

    def clear_conversation(self, session_id: str):
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "DELETE FROM conversation_history WHERE session_id = ?", (session_id,)
            )
            self.conn.commit()
        except sqlite3.Error as e:
            self.conn.rollback()
            print(f"[DB ERROR] clear_conversation: {e}")

    # ── Safe space + mood checkins ────────────────────────────────────────────

    def save_safe_space_session(self):
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT INTO safe_space_sessions (timestamp) VALUES (?)",
                (self.current_utc_timestamp(),)
            )
            self.conn.commit()
        except sqlite3.Error as e:
            self.conn.rollback()

    def get_safe_space_session_count(self) -> int:
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM safe_space_sessions")
            row = cursor.fetchone()
            return row[0] if row else 0
        except sqlite3.Error:
            return 0

    def save_mood_checkin(self, score: int, label: str):
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT INTO mood_checkins (timestamp, score, label) VALUES (?, ?, ?)",
                (self.current_utc_timestamp(), score, label)
            )
            self.conn.commit()
        except sqlite3.Error as e:
            self.conn.rollback()

    def get_mood_checkin_today(self):
        try:
            today  = datetime.now().strftime("%Y-%m-%d")
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT * FROM mood_checkins WHERE timestamp LIKE ?
                ORDER BY id DESC LIMIT 1
            """, (f"{today}%",))
            return cursor.fetchone()
        except sqlite3.Error:
            return None

    def get_mood_checkins(self, limit: int = 7):
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM mood_checkins ORDER BY timestamp DESC LIMIT ?", (limit,)
        )
        return cursor.fetchall()

    def get_mood_as_context(self) -> str:
        checkins = self.get_mood_checkins(limit=7)
        if not checkins:
            return "No mood check-ins logged."
        lines = []
        for c in reversed(checkins):
            lines.append(f"{c['timestamp'][:10]}: {c['score']}/10 — {c['label']}")
        return "\n".join(lines)


# ── PersonaChat ───────────────────────────────────────────────────────────────

class PersonaChat:
    def __init__(self, db: DatabaseManager, persona_name: str, persona_system_prompt: str):
        self.db            = db
        self.persona_name  = persona_name
        self.system_prompt = persona_system_prompt
        self.session_id    = str(uuid.uuid4())

    def _build_context(self) -> str:
        context_parts = []

        patterns = self.db.get_all_persona_patterns(self.persona_name, min_repeats=2)
        if patterns:
            pattern_lines = [f"  - [{p['count']}x] {p['summary']}" for p in patterns[:15]]
            context_parts.append(
                "LONG-TERM PATTERNS OBSERVED:\n" + "\n".join(pattern_lines)
            )

        memories, pattern_summary = self.db.get_persona_memory_extended(
            self.persona_name, limit=50, since_days=90
        )
        if pattern_summary:
            context_parts.append(f"RECENT RECURRING THEMES (90 days):\n{pattern_summary}")

        goals_context = self.db.get_goals_as_context()
        if goals_context and goals_context != "No active goals set.":
            context_parts.append(f"ACTIVE GOALS:\n{goals_context}")

        # Inject goal momentum into persona chat context
        try:
            from core.decision_tracker import GoalMomentumScorer
            scorer = GoalMomentumScorer(self.db)
            momentum_ctx = scorer.build_momentum_context()
            if momentum_ctx:
                context_parts.append(momentum_ctx)
        except Exception:
            pass

        return "\n\n".join(context_parts)

    def send_message(self, user_message: str) -> str:
        self.db.save_conversation_turn(self.session_id, role="user", content=user_message)
        response = self._generate_response(user_message)
        self.db.save_conversation_turn(self.session_id, role=self.persona_name, content=response)

        if len(user_message) > 30:
            self.db.save_persona_memory(
                persona_name=self.persona_name,
                summary=user_message[:120],
                risk_score=0,
                confidence_score=5,
                decision="Logged from chat session"
            )
        return response

    def _generate_response(self, user_message: str) -> str:
        context_block = self._build_context()
        history       = self.db.get_conversation_history_extended(self.session_id, limit=100)

        context_section = context_block if context_block else "No prior context available."

        system_content = f"""{self.system_prompt}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MEMORY & CONTEXT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{context_section}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You are in a one-on-one direct conversation.
Speak like the person you are. Do not behave like a menu or a report generator.
Ask follow-up questions. Respond to what was actually said. Be present.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

        messages = [{"role": "system", "content": system_content}]
        for turn in history:
            role = "user" if turn["role"] == "user" else "assistant"
            messages.append({"role": role, "content": turn["content"]})
        messages.append({"role": "user", "content": user_message})

        try:
            from core.groq_client import chat_completion, is_available
            if is_available():
                return chat_completion(messages, temperature=0.75, max_tokens=900)
        except Exception:
            pass

        try:
            import requests as _requests
            history_text = ""
            for turn in history:
                speaker = "Antonio" if turn["role"] == "user" else self.persona_name
                history_text += f"{speaker}: {turn['content']}\n"
            full_prompt = f"""{system_content}\n\n{history_text}Antonio: {user_message}\n{self.persona_name}:"""
            response = _requests.post(
                "http://localhost:11434/api/generate",
                json={"model": "llama3", "prompt": full_prompt, "stream": False},
                timeout=120
            )
            response.raise_for_status()
            return response.json().get("response", "").strip()
        except Exception as e:
            return random.choice(_OFFLINE_MESSAGES_CHAT.get(self.persona_name, ["Unavailable right now."]))

    def get_full_conversation(self) -> list:
        return self.db.get_conversation_as_messages(self.session_id)

    def clear_conversation(self):
        self.db.clear_conversation(self.session_id)
