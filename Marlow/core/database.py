# database.py
import sqlite3
import datetime


class DatabaseManager:
    def __init__(self, db_path="vault.db"):
        self.conn = sqlite3.connect(db_path)
        self.cursor = self.conn.cursor()
        self._setup()

    def _setup(self):
        self.cursor.executescript("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                content TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                sleep_hours REAL,
                stimulant_use INTEGER,
                mood_score INTEGER,
                focus_score INTEGER,
                notes TEXT
            );

            CREATE TABLE IF NOT EXISTS persona_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                persona_name TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                summary TEXT NOT NULL,
                risk_score INTEGER,
                confidence_score INTEGER,
                decision TEXT
            );

            CREATE TABLE IF NOT EXISTS life_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                content TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS journals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                content TEXT NOT NULL,
                council_responded INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS static_profile (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                content TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                status TEXT DEFAULT 'active',
                progress_note TEXT,
                target_date TEXT
            );

            CREATE TABLE IF NOT EXISTS weekly_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                generated_at TEXT NOT NULL,
                week_start TEXT,
                week_end TEXT,
                report_content TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS conversation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS crisis_flags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                content TEXT NOT NULL,
                confidence REAL,
                notes TEXT,
                reviewed INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS monthly_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                generated_at TEXT NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                pattern_content TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mood_checkins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                date TEXT NOT NULL,
                score INTEGER NOT NULL,
                word TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS safe_space_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                session_occurred INTEGER DEFAULT 1
            );
        """)
        self.conn.commit()

    # ─── CORE ──────────────────────────────────────────────────

    def get_life_history(self):
        row = self.cursor.execute(
            "SELECT content FROM life_history ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None

    def save_life_history(self, content):
        timestamp = datetime.datetime.now().isoformat()
        self.cursor.execute(
            "INSERT INTO life_history (timestamp, content) VALUES (?, ?)",
            (timestamp, content)
        )
        self.conn.commit()

    def get_static_profile(self):
        row = self.cursor.execute(
            "SELECT content FROM static_profile ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None

    def save_static_profile(self, content):
        timestamp = datetime.datetime.now().isoformat()
        self.cursor.execute(
            "INSERT INTO static_profile (timestamp, content) VALUES (?, ?)",
            (timestamp, content)
        )
        self.conn.commit()

    def save_journal(self, content, council_responded=0):
        timestamp = datetime.datetime.now().isoformat()
        self.cursor.execute(
            "INSERT INTO journals (timestamp, content, council_responded) VALUES (?, ?, ?)",
            (timestamp, content, council_responded)
        )
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ─── GOALS ─────────────────────────────────────────────────

    def save_goal(self, title, description="", target_date=""):
        now = datetime.datetime.now().isoformat()
        self.cursor.execute(
            "INSERT INTO goals (created_at, updated_at, title, description, target_date) VALUES (?, ?, ?, ?, ?)",
            (now, now, title, description, target_date)
        )
        self.conn.commit()
        return self.cursor.lastrowid

    def get_active_goals(self):
        return self.cursor.execute(
            "SELECT * FROM goals WHERE status = 'active' ORDER BY created_at ASC"
        ).fetchall()

    def get_all_goals(self):
        return self.cursor.execute(
            "SELECT * FROM goals ORDER BY created_at DESC"
        ).fetchall()

    def update_goal_progress(self, goal_id, progress_note):
        now = datetime.datetime.now().isoformat()
        self.cursor.execute(
            "UPDATE goals SET progress_note = ?, updated_at = ? WHERE id = ?",
            (progress_note, now, goal_id)
        )
        self.conn.commit()

    def update_goal_status(self, goal_id, status):
        now = datetime.datetime.now().isoformat()
        self.cursor.execute(
            "UPDATE goals SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, goal_id)
        )
        self.conn.commit()

    def get_goals_as_context(self):
        goals = self.get_active_goals()
        if not goals:
            return "No active goals set."
        lines = []
        for i, g in enumerate(goals, 1):
            line = f"Goal {i}: {g[3]}"
            if g[4]:  line += f" — {g[4]}"
            if g[6]:  line += f" [Progress: {g[6]}]"
            if g[7]:  line += f" [Target: {g[7]}]"
            lines.append(line)
        return "\n".join(lines)

    # ─── WEEKLY REPORTS ────────────────────────────────────────

    def save_weekly_report(self, week_start, week_end, content):
        now = datetime.datetime.now().isoformat()
        self.cursor.execute(
            "INSERT INTO weekly_reports (generated_at, week_start, week_end, report_content) VALUES (?, ?, ?, ?)",
            (now, week_start, week_end, content)
        )
        self.conn.commit()

    def get_latest_weekly_report(self):
        return self.cursor.execute(
            "SELECT * FROM weekly_reports ORDER BY generated_at DESC LIMIT 1"
        ).fetchone()

    def get_logs_for_week(self, week_start, week_end):
        return self.cursor.execute(
            "SELECT timestamp, content FROM logs WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC",
            (week_start, week_end)
        ).fetchall()

    # ─── CONVERSATION MEMORY ───────────────────────────────────

    def save_conversation_turn(self, session_id, role, content):
        now = datetime.datetime.now().isoformat()
        self.cursor.execute(
            "INSERT INTO conversation_history (session_id, timestamp, role, content) VALUES (?, ?, ?, ?)",
            (session_id, now, role, content)
        )
        self.conn.commit()

    def get_conversation_history(self, session_id, limit=8):
        rows = self.cursor.execute(
            "SELECT role, content FROM conversation_history WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?",
            (session_id, limit)
        ).fetchall()
        return list(reversed(rows))

    def clear_conversation(self, session_id):
        self.cursor.execute(
            "DELETE FROM conversation_history WHERE session_id = ?",
            (session_id,)
        )
        self.conn.commit()

    # ─── CRISIS FLAGS ──────────────────────────────────────────

    def save_crisis_flag(self, content, confidence, notes):
        now = datetime.datetime.now().isoformat()
        self.cursor.execute(
            "INSERT INTO crisis_flags (timestamp, content, confidence, notes) VALUES (?, ?, ?, ?)",
            (now, content, confidence, notes)
        )
        self.conn.commit()

    # ─── MONTHLY PATTERNS ──────────────────────────────────────

    def save_monthly_pattern(self, period_start, period_end, content):
        now = datetime.datetime.now().isoformat()
        self.cursor.execute(
            "INSERT INTO monthly_patterns (generated_at, period_start, period_end, pattern_content) VALUES (?, ?, ?, ?)",
            (now, period_start, period_end, content)
        )
        self.conn.commit()

    def get_latest_monthly_pattern(self):
        return self.cursor.execute(
            "SELECT * FROM monthly_patterns ORDER BY generated_at DESC LIMIT 1"
        ).fetchone()

    def get_monthly_pattern_as_context(self):
        row = self.get_latest_monthly_pattern()
        if not row:
            return "No monthly pattern data yet."
        return f"[Pattern report from {row[1][:10]}]\n{row[4]}"

    def get_last_n_logs(self, n=30):
        return self.cursor.execute(
            "SELECT timestamp, content FROM logs ORDER BY id DESC LIMIT ?",
            (n,)
        ).fetchall()

    def get_last_sync(self):
        return self.cursor.execute(
            "SELECT timestamp, content FROM logs ORDER BY id DESC LIMIT 1"
        ).fetchone()

    # ─── MOOD CHECK-IN ─────────────────────────────────────────

    def save_mood_checkin(self, score: int, word: str):
        now = datetime.datetime.now()
        self.cursor.execute(
            "INSERT INTO mood_checkins (timestamp, date, score, word) VALUES (?, ?, ?, ?)",
            (now.isoformat(), now.strftime("%Y-%m-%d"), score, word)
        )
        self.conn.commit()

    def get_todays_mood_checkin(self):
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        return self.cursor.execute(
            "SELECT score, word, timestamp FROM mood_checkins WHERE date = ? ORDER BY id DESC LIMIT 1",
            (today,)
        ).fetchone()

    def get_mood_checkins(self, limit=30):
        return self.cursor.execute(
            "SELECT timestamp, date, score, word FROM mood_checkins ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()

    def get_mood_as_context(self):
        today = self.get_todays_mood_checkin()
        checkins = self.get_mood_checkins(limit=7)
        if not checkins and not today:
            return "No mood check-in data yet."
        lines = []
        if today:
            lines.append(f"Today's mood: {today[0]}/10 — {today[1]}")
        if checkins:
            lines.append("Recent 7-day check-ins:")
            for ts, date, score, word in checkins:
                lines.append(f"  {date}: {score}/10 — {word}")
        return "\n".join(lines)

    def get_mood_trend(self, days=7):
        rows = self.cursor.execute(
            "SELECT score FROM mood_checkins ORDER BY id DESC LIMIT ?",
            (days,)
        ).fetchall()
        if not rows:
            return None, "No data"
        scores = [r[0] for r in rows]
        avg = round(sum(scores) / len(scores), 1)
        if len(scores) >= 4:
            recent = sum(scores[:2]) / 2
            older  = sum(scores[-2:]) / 2
            if recent > older + 0.5:
                trend = "Improving"
            elif recent < older - 0.5:
                trend = "Declining"
            else:
                trend = "Stable"
        else:
            trend = "Insufficient data"
        return avg, trend

    # ─── SAFE SPACE SESSIONS ───────────────────────────────────

    def log_safe_space_session(self):
        """
        Records ONLY that a session occurred.
        No content is ever written to this table.
        Counselors can see engagement count — never what was said.
        """
        now = datetime.datetime.now().isoformat()
        self.cursor.execute(
            "INSERT INTO safe_space_sessions (timestamp) VALUES (?)",
            (now,)
        )
        self.conn.commit()

    def get_safe_space_session_count(self):
        row = self.cursor.execute(
            "SELECT COUNT(*) FROM safe_space_sessions"
        ).fetchone()
        return row[0] if row else 0
