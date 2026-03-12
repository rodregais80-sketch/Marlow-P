import datetime


class PersonaMemory:

    def __init__(self, db):
        self.db = db

    def fetch_recent(self, persona_name, limit=5):
        rows = self.db.cursor.execute("""
            SELECT timestamp, summary
            FROM persona_memory
            WHERE persona_name = ?
            ORDER BY id DESC
            LIMIT ?
        """, (persona_name, limit)).fetchall()

        if not rows:
            return "No prior memory."

        memory_block = ""
        for ts, summary in rows:
            memory_block += f"[{ts}] {summary}\n"

        return memory_block

    def store(self, persona_name, summary, risk, confidence, decision):
        timestamp = datetime.datetime.now().isoformat()

        self.db.cursor.execute("""
            INSERT INTO persona_memory
            (persona_name, timestamp, summary, risk_score, confidence_score, decision)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (persona_name, timestamp, summary, risk, confidence, decision))

        self.db.conn.commit()