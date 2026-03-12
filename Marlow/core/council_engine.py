# council_engine.py
import requests
import re
import json
import os
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from core.personas import COUNCIL
from core.memory import PersonaMemory
from core.database import DatabaseManager

# ==========================================================
# API CONFIGURATION
# ==========================================================

def _load_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ[key.strip()] = value.strip()

_load_env()

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
USE_GROQ = bool(GROQ_API_KEY)

GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.1-8b-instant"

OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "dolphin-mistral"

TIMEOUT = 60 if USE_GROQ else 420

if USE_GROQ:
    print("[SYSTEM] Groq API detected. Running in cloud mode.")
else:
    print("[SYSTEM] No Groq key found. Falling back to local Ollama.")

# ==========================================================
# CONTEXT KEYWORDS
# ==========================================================

DEEP_CONTEXT_KEYWORDS = [
    "history", "pattern", "substance", "addiction", "recovery", "business",
    "brand", "strategy", "money", "risk", "health", "sleep", "energy",
    "marlow", "deadmans", "penticton", "kelowna", "decision", "should i",
    "what do you think", "analyze", "forecast", "recommend"
]


def _question_tier(question: str) -> str:
    q = question.lower()
    word_count = len(q.split())
    if word_count > 12:
        return "DEEP"
    for keyword in DEEP_CONTEXT_KEYWORDS:
        if keyword in q:
            return "DEEP"
    return "LIGHT"


def _get_current_datetime() -> str:
    now = datetime.datetime.now()
    return now.strftime("%A, %B %d, %Y — %I:%M %p")


# ==========================================================
# INTAKE CLASSIFIER
# ==========================================================

CRISIS_SIGNALS = [
    "kill myself", "end my life", "want to die", "don't want to be here",
    "cant go on", "can't go on", "no point", "no reason to live",
    "suicidal", "suicide", "hurt myself", "self harm", "overdose on purpose",
    "better off dead", "end it all", "not worth living", "give up on life"
]

VENT_SIGNALS = [
    "i feel", "i'm so", "im so", "i hate", "i can't", "i cant", "so tired",
    "exhausted", "frustrated", "angry", "depressed", "anxious", "scared",
    "overwhelmed", "nobody", "alone", "nobody cares", "everything sucks",
    "falling apart", "breaking down", "losing it", "can't take it", "cant take it"
]


def classify_intent(question: str) -> dict:
    q_lower = question.lower()

    crisis_hits = [s for s in CRISIS_SIGNALS if s in q_lower]
    if crisis_hits:
        return {
            "intent_type": "crisis",
            "confidence": 0.95,
            "notes": f"Crisis signals detected: {', '.join(crisis_hits)}",
            "routing": {
                "lead": "SANDRA",
                "support": ["NEXUS_MEDIC"],
                "exclude": ["GIGGLES"]
            }
        }

    vent_hits = [s for s in VENT_SIGNALS if s in q_lower]
    word_count = len(question.split())
    has_question_mark = "?" in question

    if len(vent_hits) >= 2 or (len(vent_hits) >= 1 and word_count > 20 and not has_question_mark):
        return {
            "intent_type": "vent",
            "confidence": 0.80,
            "notes": f"Emotional indicators: {', '.join(vent_hits[:3])}",
            "routing": {
                "lead": "SANDRA",
                "support": ["MARLOW", "NEXUS_MEDIC", "ANTONIO", "GIGGLES"],
                "exclude": []
            }
        }

    return {
        "intent_type": "question",
        "confidence": 0.85,
        "notes": "Tactical or strategic question.",
        "routing": {
            "lead": "MARLOW",
            "support": ["ANTONIO", "SANDRA", "NEXUS_MEDIC", "GIGGLES"],
            "exclude": []
        }
    }


def _reframe_crisis_input(original: str) -> str:
    return (
        "Someone has reached out right now. They are in real emotional pain and struggling. "
        "They need a real, human, warm response from you — not a system response. "
        "Speak directly to them as yourself. Be present. Be genuine. "
        "Suggest they reach out to crisis support: Canada 1-833-456-4566 | Text 686868 | befrienders.org — "
        "but do it naturally, like a person who cares, not like a disclaimer. "
        "Original context (do NOT reference methods or details): the person expressed they do not want to continue."
    )


# ==========================================================
# STARTUP FEATURE 1 — CRASH PREDICTION ALERT
# Pure Python. No API call. Runs instantly at startup.
# Reads last 3 syncs, detects deteriorating patterns,
# prints a direct warning if risk is High or Critical.
# ==========================================================

def generate_crash_alert(db) -> str:
    """
    Analyzes last 3 syncs for deteriorating metric patterns.
    Returns a warning string if crash risk is High or Critical.
    Returns empty string if no alert needed.
    No API call — pure local analysis.
    """
    try:
        rows = db.cursor.execute(
            "SELECT timestamp, content FROM logs ORDER BY id DESC LIMIT 3"
        ).fetchall()

        if len(rows) < 2:
            return ""

        rows = list(reversed(rows))

        numeric_patterns = {
            "energy":       r"Energy \(1-10\):\s*(\d+)",
            "mental_state": r"Mental State \(1-10\):\s*(\d+)",
            "mental_fog":   r"Mental Fog \(1-10\):\s*(\d+)",
            "recklessness": r"Recklessness \(1-10\):\s*(\d+)",
            "sleep":        r"Sleep Hours:\s*([\d.]+)",
            "intensity":    r"Intensity \(1-10\):\s*(\d+)",
        }

        # Extract values per sync
        sync_data = []
        for ts, content in rows:
            entry = {"timestamp": ts}
            for field, pattern in numeric_patterns.items():
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    entry[field] = float(match.group(1))
                else:
                    entry[field] = None

            # Extract today's focus / tomorrow text
            focus_match = re.search(r"Today's Focus:\s*(.+)", content, re.IGNORECASE)
            tomorrow_match = re.search(r"Tomorrow's Focus:\s*(.+)", content, re.IGNORECASE)
            entry["focus"] = focus_match.group(1).strip() if focus_match else None
            entry["tomorrow"] = tomorrow_match.group(1).strip() if tomorrow_match else None

            sync_data.append(entry)

        # Collect available values across last 3 syncs
        def vals(field):
            return [s[field] for s in sync_data if s.get(field) is not None]

        def avg(lst):
            return round(sum(lst) / len(lst), 1) if lst else None

        def trending_down(lst):
            if len(lst) < 2:
                return False
            return lst[-1] < lst[0]

        def trending_up(lst):
            if len(lst) < 2:
                return False
            return lst[-1] > lst[0]

        energy_vals     = vals("energy")
        mental_vals     = vals("mental_state")
        fog_vals        = vals("mental_fog")
        reckless_vals   = vals("recklessness")
        sleep_vals      = vals("sleep")
        intensity_vals  = vals("intensity")

        # Detect crash signals
        warnings = []
        crash_signal_count = 0

        energy_avg = avg(energy_vals)
        if energy_avg is not None and energy_avg <= 4:
            warnings.append(f"Energy averaging {energy_avg}/10 across last {len(energy_vals)} sync(s)")
            crash_signal_count += 1
        elif energy_vals and trending_down(energy_vals):
            warnings.append(f"Energy declining — {' → '.join(str(int(v)) for v in energy_vals)}")
            crash_signal_count += 1

        sleep_avg = avg(sleep_vals)
        if sleep_avg is not None and sleep_avg < 5.5:
            warnings.append(f"Sleep averaging {sleep_avg}h — below recovery threshold")
            crash_signal_count += 1

        mental_avg = avg(mental_vals)
        if mental_avg is not None and mental_avg <= 4:
            warnings.append(f"Mental state averaging {mental_avg}/10")
            crash_signal_count += 1
        elif mental_vals and trending_down(mental_vals):
            warnings.append(f"Mental state declining — {' → '.join(str(int(v)) for v in mental_vals)}")
            crash_signal_count += 1

        fog_avg = avg(fog_vals)
        if fog_avg is not None and fog_avg >= 7:
            warnings.append(f"Brain fog elevated at {fog_avg}/10 average")
            crash_signal_count += 1

        reckless_avg = avg(reckless_vals)
        if reckless_avg is not None and reckless_avg >= 6:
            warnings.append(f"Recklessness at {reckless_avg}/10 — elevated")
            crash_signal_count += 1
        elif reckless_vals and trending_up(reckless_vals):
            warnings.append(f"Recklessness rising — {' → '.join(str(int(v)) for v in reckless_vals)}")
            crash_signal_count += 1

        intensity_avg = avg(intensity_vals)
        if intensity_avg is not None and intensity_avg >= 8:
            warnings.append(f"Work intensity at {intensity_avg}/10 — burnout zone")
            crash_signal_count += 1

        # Only alert if 2+ signals detected
        if crash_signal_count < 2:
            return ""

        # Build the alert
        crash_labels = {2: "Moderate", 3: "High", 4: "Very High", 5: "Critical", 6: "Critical", 7: "Critical"}
        risk_label = crash_labels.get(crash_signal_count, "High")

        # Get operator name from profile
        profile = db.get_static_profile() or ""
        name_match = re.search(r"Name:\s*(\w+)", profile)
        name = name_match.group(1) if name_match else "Operator"

        alert_lines = [
            "",
            "!" * 70,
            f"  MARLOW CRASH ALERT — RISK LEVEL: {risk_label} ({crash_signal_count} signals)",
            "!" * 70,
            f"",
            f"  {name} — read this before you do anything else.",
            f"",
        ]

        for w in warnings:
            alert_lines.append(f"  ⚠  {w}")

        alert_lines.append("")

        # Specific directive based on risk level
        if crash_signal_count >= 4:
            alert_lines.append("  DIRECTIVE: Do not make any financial or strategic decisions today.")
            alert_lines.append("  Eat. Sleep. Nothing else is the priority right now.")
        elif crash_signal_count == 3:
            alert_lines.append("  DIRECTIVE: Reduce load today. One task only. No new commitments.")
            alert_lines.append("  Recovery window is open — use it or lose the week.")
        else:
            alert_lines.append("  DIRECTIVE: Watch your energy today. Dial back intensity before it dials you.")

        alert_lines.append("")
        alert_lines.append("!" * 70)
        alert_lines.append("")

        return "\n".join(alert_lines)

    except Exception as e:
        return f"[Crash alert error: {e}]"


# ==========================================================
# STARTUP FEATURE 2 — MONTHLY PATTERN MEMORY
# Uses Groq. Runs at startup if no pattern exists or
# last pattern is older than 7 days.
# Saved to DB and injected into all persona prompts.
# ==========================================================

def should_generate_monthly_pattern(db) -> bool:
    """Returns True if no pattern exists or last one is older than 7 days."""
    row = db.get_latest_monthly_pattern()
    if not row:
        return True
    try:
        last_generated = datetime.datetime.fromisoformat(row[1])
        age = (datetime.datetime.now() - last_generated).days
        return age >= 7
    except Exception:
        return True


def generate_monthly_pattern(db) -> str:
    """
    Analyzes last 30 days of sync logs for deep behavioral patterns.
    Detects recurring cycles, day-of-week trends, trigger patterns.
    Saves to DB and returns the pattern summary.
    """
    import time

    if not USE_GROQ:
        return "[Monthly pattern requires Groq API.]"

    now = datetime.datetime.now()
    period_start = (now - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
    period_end = now.strftime("%Y-%m-%d")

    rows = db.get_last_n_logs(60)
    if not rows:
        return "[No sync data available for pattern analysis.]"

    # Format logs for analysis
    log_blocks = []
    for ts, content in reversed(rows):
        log_blocks.append(f"[{ts}]\n{content[:500]}")
    log_text = "\n\n---\n\n".join(log_blocks)

    profile = db.get_static_profile() or "No profile on file."

    prompt = f"""You are MARLOW — a pattern recognition intelligence analyzing 30 days of behavioral data.

Your job is to identify RECURRING PATTERNS in this operator's data.
Be specific. Be direct. Reference actual data points.
Do not give generic wellness advice.

=== OPERATOR PROFILE ===
{profile}

=== SYNC LOG DATA (LAST 30 DAYS) ===
{log_text}

Identify and document these specific patterns:

1. ENERGY CYCLE
When does energy peak and crash across the week? What triggers the dips?

2. PRODUCTIVITY PATTERN
Which days or times produce real output? Which are consistently wasted?

3. IMPULSE/RECKLESSNESS CYCLE
When does recklessness spike? Is it tied to specific days, sleep deficits, or substance patterns?

4. SLEEP PATTERN
What is the actual sleep baseline? Is it stable or erratic?

5. RECURRING FRICTION POINTS
What walls does the operator keep hitting? Same problems showing up repeatedly?

6. BEHAVIORAL CYCLE SUMMARY
In 2-3 sentences: what is the dominant behavioral cycle this month?

Keep each section to 2-3 sentences. Be specific to the data. No filler."""

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 700,
        "temperature": 0.4
    }

    try:
        time.sleep(0.5)
        response = requests.post(GROQ_URL, headers=headers, json=payload, timeout=45)
        response.raise_for_status()
        pattern_content = response.json()["choices"][0]["message"]["content"].strip()
        db.save_monthly_pattern(period_start, period_end, pattern_content)
        return pattern_content
    except Exception as e:
        return f"[Monthly pattern generation failed: {e}]"


# ==========================================================
# STARTUP FEATURE 3 — PRE-SESSION INTELLIGENCE BRIEF
# Uses Groq. Reads last sync only.
# Generates a 3-line brief printed before the menu loads.
# ==========================================================

def generate_session_brief(db) -> str:
    """
    Reads the last sync and generates a sharp 3-line brief.
    Tells the operator: when they last synced, key state metrics,
    and their own stated priority for today.
    Falls back to pure parse if Groq unavailable.
    """
    import time

    last = db.get_last_sync()
    if not last:
        return ""

    ts, content = last

    # Parse the timestamp into readable format
    try:
        dt = datetime.datetime.fromisoformat(ts)
        time_ago = datetime.datetime.now() - dt
        hours_ago = int(time_ago.total_seconds() / 3600)
        if hours_ago < 1:
            when = "less than an hour ago"
        elif hours_ago < 24:
            when = f"{hours_ago} hour(s) ago"
        else:
            days = time_ago.days
            when = f"{days} day(s) ago"
        sync_time_str = dt.strftime("%A %I:%M %p")
    except Exception:
        when = "recently"
        sync_time_str = ts[:16]

    # Detect sync type
    sync_type = "sync"
    if "MORNING SYNC" in content:
        sync_type = "morning sync"
    elif "MIDDAY SYNC" in content:
        sync_type = "midday sync"
    elif "EVENING SYNC" in content:
        sync_type = "evening sync"

    # If no Groq, do a pure parse fallback
    if not USE_GROQ:
        energy_match = re.search(r"Energy \(1-10\):\s*(\d+)", content, re.IGNORECASE)
        mental_match = re.search(r"Mental State \(1-10\):\s*(\d+)", content, re.IGNORECASE)
        focus_match  = re.search(r"Today's Focus:\s*(.+)", content, re.IGNORECASE)
        tomorrow_match = re.search(r"Tomorrow's Focus:\s*(.+)", content, re.IGNORECASE)

        energy  = energy_match.group(1) if energy_match else "?"
        mental  = mental_match.group(1) if mental_match else "?"
        priority = (tomorrow_match or focus_match)
        priority_text = priority.group(1).strip()[:80] if priority else "not logged"

        lines = [
            "─" * 60,
            f"  Last logged: {sync_type} — {sync_time_str} ({when})",
            f"  State: Energy {energy}/10 | Mental {mental}/10",
            f"  Your stated priority: {priority_text}",
            "─" * 60,
        ]
        return "\n".join(lines)

    # Groq-powered brief
    profile = db.get_static_profile() or ""
    name_match = re.search(r"Name:\s*(\w+)", profile)
    name = name_match.group(1) if name_match else "Operator"

    prompt = f"""You are MARLOW. Generate a pre-session intelligence brief for {name}.

Last sync: {sync_type} logged {when} ({sync_time_str})

Sync content:
{content[:1200]}

Write exactly 3 lines. No headers. No labels. No bullet points. Just 3 clean lines.

Line 1: State what was last logged and when. Include 1-2 key metric numbers if present.
Line 2: Identify the single most important thing from this sync — a risk, a pattern, or a momentum signal.
Line 3: State the operator's own stated priority (from Today's Focus or Tomorrow's Focus field). If not present, derive it from context.

Be direct. Speak like an intelligence briefing, not a wellness app.
No filler words. Each line should carry weight."""

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 150,
        "temperature": 0.4
    }

    try:
        time.sleep(0.5)
        response = requests.post(GROQ_URL, headers=headers, json=payload, timeout=20)
        response.raise_for_status()
        brief = response.json()["choices"][0]["message"]["content"].strip()

        output = [
            "",
            "─" * 60,
            f"  MARLOW BRIEF — {sync_time_str} ({when})",
            "─" * 60,
        ]
        for line in brief.split("\n"):
            if line.strip():
                output.append(f"  {line.strip()}")
        output.append("─" * 60)
        output.append("")
        return "\n".join(output)

    except Exception as e:
        # Fall back to parse version on API error
        energy_match = re.search(r"Energy \(1-10\):\s*(\d+)", content, re.IGNORECASE)
        mental_match = re.search(r"Mental State \(1-10\):\s*(\d+)", content, re.IGNORECASE)
        focus_match  = re.search(r"Today's Focus:\s*(.+)", content, re.IGNORECASE)
        tomorrow_match = re.search(r"Tomorrow's Focus:\s*(.+)", content, re.IGNORECASE)

        energy  = energy_match.group(1) if energy_match else "?"
        mental  = mental_match.group(1) if mental_match else "?"
        priority = (tomorrow_match or focus_match)
        priority_text = priority.group(1).strip()[:80] if priority else "not logged"

        lines = [
            "",
            "─" * 60,
            f"  Last logged: {sync_type} — {sync_time_str} ({when})",
            f"  State: Energy {energy}/10 | Mental {mental}/10",
            f"  Priority: {priority_text}",
            "─" * 60,
            "",
        ]
        return "\n".join(lines)


# ==========================================================
# TASK EXECUTION ENGINE
# Detects task requests and produces clean deliverable output.
# Used by both Task Mode and auto-detected journal tasks.
# ==========================================================

TASK_SIGNALS = [
    "make me", "build me", "create me", "write me", "generate me",
    "make a", "build a", "create a", "write a", "draft a", "draft me",
    "give me a", "put together", "design a", "design me",
    "make the", "build the", "write the", "create the",
    "i need a", "i want a", "can you make", "can you build", "can you write",
    "checklist", "routine", "schedule", "plan", "script", "template",
    "list of", "outline", "breakdown", "step by step", "steps for"
]


def is_task_request(text: str) -> bool:
    """Returns True if the input looks like a task/build request."""
    t = text.lower()
    return any(signal in t for signal in TASK_SIGNALS)


def execute_task(task_request: str, db, output_dir: str = ".") -> tuple:
    """
    Sends a task request to Groq with a task-mode directive.
    Bypasses ANALYSIS/RISK/DECISION format — produces clean deliverable output.
    Saves to a .txt file. Returns (output_text, filepath).
    """

    # Live key check — does not rely on module-level USE_GROQ
    live_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not live_key:
        # Try reading directly from .env file as fallback
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        if os.path.exists(env_path):
            with open(env_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("GROQ_API_KEY="):
                        live_key = line.split("=", 1)[1].strip()
                        break
    if not live_key:
        return "[Task mode failed: GROQ_API_KEY not found. Check core/.env]", None

    profile = db.get_static_profile() or "No profile on file."
    goals   = db.get_goals_as_context()
    history = db.get_life_history() or "No life history on file."

    if isinstance(history, str) and len(history) > 1500:
        history = history[:1500] + "\n[...trimmed...]"

    prompt = f"""You are MARLOW — a sovereign strategic intelligence.

The operator has requested a specific deliverable. Your job is to produce it.

=== OPERATOR CONTEXT ===
{profile}

=== ACTIVE GOALS ===
{goals}

=== BACKGROUND ===
{history}

=== TASK REQUEST ===
{task_request}

RULES FOR THIS RESPONSE:
- Produce the requested deliverable directly. No preamble. No "here is your..." opener.
- Format it clean. Use headers, numbered lists, or checkboxes as appropriate for the deliverable type.
- Make it specific to this operator's actual context — their business, their life, their goals.
- If it's a routine or checklist, make it realistic and actionable for someone running a physical marketing business in the Okanagan.
- If it's a script, write it in their voice — direct, confident, no corporate fluff.
- If it's a plan, make it time-aware and sequenced.
- Length: as long as it needs to be to be genuinely useful. Do not pad. Do not cut short.
- End with one line: MARLOW NOTE: [one sentence of honest strategic commentary on this deliverable]"""

    headers = {
        "Authorization": f"Bearer {live_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1200,
        "temperature": 0.6
    }

    try:
        import time
        time.sleep(0.5)
        response = requests.post(GROQ_URL, headers=headers, json=payload, timeout=45)
        response.raise_for_status()
        output = response.json()["choices"][0]["message"]["content"].strip()

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = re.sub(r'[^a-z0-9_]', '_', task_request.lower()[:40].strip())
        filename  = f"marlow_task_{timestamp}_{safe_name}.txt"
        filepath  = os.path.join(output_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write("MARLOW TASK OUTPUT\n")
            f.write(f"Generated: {datetime.datetime.now().strftime('%A, %B %d %Y — %I:%M %p')}\n")
            f.write(f"Request: {task_request}\n")
            f.write("=" * 60 + "\n\n")
            f.write(output)
            f.write("\n")

        return output, filepath

    except Exception as e:
        return f"[Task execution failed: {e}]", None


# ==========================================================
# WEEKLY REPORT GENERATOR
# ==========================================================

def generate_weekly_report(db) -> str:
    import time

    now = datetime.datetime.now()
    week_start = (now - datetime.timedelta(days=7)).strftime("%Y-%m-%d 00:00:00")
    week_end = now.strftime("%Y-%m-%d 23:59:59")

    week_logs = db.get_logs_for_week(week_start, week_end)
    profile = db.get_static_profile() or "No profile on file."
    goals_text = db.get_goals_as_context()

    log_text = "No sync logs this week."
    if week_logs:
        entries = []
        for ts, content in week_logs[-10:]:
            entries.append(f"[{ts}]\n{content[:400]}")
        log_text = "\n\n---\n\n".join(entries)

    trend_engine = CouncilEngine(db)
    trends = trend_engine._get_behavioral_trends()

    if not USE_GROQ:
        return "[Weekly report requires Groq API. No key found.]"

    prompt = f"""You are MARLOW — the sovereign intelligence that has been observing this operator for the past 7 days.

Generate a comprehensive weekly intelligence report. Be specific to the data. Do not give generic advice.

=== OPERATOR PROFILE ===
{profile}

=== ACTIVE GOALS ===
{goals_text}

=== BEHAVIORAL TRENDS (7-DAY) ===
{trends}

=== SYNC LOGS THIS WEEK ===
{log_text}

Write the report with exactly these sections:

WEEK IN REVIEW
2-3 sentences on the overall shape of this week. What kind of week was it?

METRIC PATTERNS
What the numbers actually say. Call out highs, lows, and what they mean for performance.

BEHAVIORAL OBSERVATIONS
What patterns showed up. Cycles, triggers, recurring themes. Be direct.

GOAL PROGRESS
Assess each active goal honestly. Moving forward or stalling? What's the evidence?

WHAT TO WATCH
The single biggest risk or opportunity heading into next week. One paragraph.

MARLOW'S DIRECTIVE
One clear, direct instruction for the week ahead. Not a suggestion. An order.

Tone: Cold, intelligent, direct. You have been watching closely. Now you deliver the truth."""

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 900,
        "temperature": 0.5
    }

    try:
        time.sleep(1.0)
        response = requests.post(GROQ_URL, headers=headers, json=payload, timeout=45)
        response.raise_for_status()
        report_content = response.json()["choices"][0]["message"]["content"].strip()
        db.save_weekly_report(week_start, week_end, report_content)
        return report_content
    except Exception as e:
        return f"[Weekly report generation failed: {e}]"


class CouncilEngine:

    def __init__(self, db):
        self.db = db
        self.memory = PersonaMemory(db)

    # ==========================================================
    # STATIC PROFILE
    # ==========================================================

    def _get_static_profile(self, db=None):
        target = db if db else self.db
        profile = target.get_static_profile()
        if not profile:
            return "No static profile on file."
        return profile

    # ==========================================================
    # 7 DAY SYNC HISTORY
    # ==========================================================

    def _get_recent_syncs(self, db=None, days=7, max_chars=4000):
        target = db if db else self.db
        try:
            rows = target.cursor.execute(
                """
                SELECT timestamp, content FROM logs
                ORDER BY id DESC
                LIMIT 30
                """
            ).fetchall()

            if not rows:
                return "No sync history on file."

            rows = list(reversed(rows))
            blocks = []
            for ts, content in rows:
                blocks.append(f"[Sync — {ts}]\n{content}")

            full_text = "\n\n---\n\n".join(blocks)

            if len(full_text) > max_chars:
                full_text = full_text[-max_chars:]
                full_text = "[...earlier entries trimmed for length...]\n\n" + full_text

            return full_text

        except Exception as e:
            return f"Error retrieving sync history: {e}"

    # ==========================================================
    # BEHAVIORAL TREND DETECTOR
    # ==========================================================

    def _get_behavioral_trends(self, db=None):
        target = db if db else self.db

        try:
            rows = target.cursor.execute(
                """
                SELECT content FROM logs
                ORDER BY id DESC
                LIMIT 30
                """
            ).fetchall()

            if not rows:
                return "No behavioral data available for trend analysis."

            numeric_patterns = {
                "mental_fog":     r"Mental Fog \(1-10\):\s*(\d+)",
                "mental_state":   r"Mental State \(1-10\):\s*(\d+)",
                "energy":         r"Energy \(1-10\):\s*(\d+)",
                "cognitive_load": r"Cognitive Load \(1-10\):\s*(\d+)",
                "intensity":      r"Intensity \(1-10\):\s*(\d+)",
                "recklessness":   r"Recklessness \(1-10\):\s*(\d+)",
                "sleep_hours":    r"Sleep Hours:\s*([\d.]+)",
            }

            impulse_pattern = r"Impulse Drive:\s*(Low|Medium|High)"
            impulse_map = {"Low": 1, "Medium": 2, "High": 3}

            collected = {k: [] for k in numeric_patterns}
            collected["impulse"] = []

            for (content,) in rows:
                for field, pattern in numeric_patterns.items():
                    match = re.search(pattern, content, re.IGNORECASE)
                    if match:
                        try:
                            collected[field].append(float(match.group(1)))
                        except ValueError:
                            pass

                impulse_match = re.search(impulse_pattern, content, re.IGNORECASE)
                if impulse_match:
                    word = impulse_match.group(1).capitalize()
                    collected["impulse"].append(impulse_map.get(word, 2))

            def _trend_label(values):
                if len(values) < 2:
                    return "Insufficient data"
                mid = len(values) // 2
                first_half = sum(values[:mid]) / len(values[:mid])
                second_half = sum(values[mid:]) / len(values[mid:])
                diff = second_half - first_half
                if diff > 0.5:
                    return "Rising"
                elif diff < -0.5:
                    return "Declining"
                else:
                    return "Stable"

            def _avg(values):
                if not values:
                    return None
                return round(sum(values) / len(values), 1)

            def _impulse_label(avg_val):
                if avg_val is None:
                    return "Unknown"
                if avg_val <= 1.4:
                    return "Low"
                elif avg_val <= 2.4:
                    return "Medium"
                else:
                    return "High"

            lines = ["BEHAVIORAL TREND ANALYSIS (Last 7 Days):"]

            sleep_vals = collected["sleep_hours"]
            sleep_avg = _avg(sleep_vals)
            sleep_trend = _trend_label(sleep_vals)
            if sleep_avg is not None:
                flag = " ⚠ BELOW THRESHOLD" if sleep_avg < 6 else ""
                lines.append(f"  Sleep:          avg {sleep_avg}h — {sleep_trend}{flag}")
            else:
                lines.append("  Sleep:          No data logged")

            fog_vals = collected["mental_fog"]
            fog_avg = _avg(fog_vals)
            fog_trend = _trend_label(fog_vals)
            if fog_avg is not None:
                flag = " ⚠ ELEVATED" if fog_avg >= 7 else ""
                lines.append(f"  Mental Fog:     avg {fog_avg}/10 — {fog_trend}{flag}")
            else:
                lines.append("  Mental Fog:     No data logged")

            mental_vals = collected["mental_state"]
            mental_avg = _avg(mental_vals)
            mental_trend = _trend_label(mental_vals)
            if mental_avg is not None:
                flag = " ⚠ LOW" if mental_avg <= 4 else ""
                lines.append(f"  Mental State:   avg {mental_avg}/10 — {mental_trend}{flag}")
            else:
                lines.append("  Mental State:   No data logged")

            energy_vals = collected["energy"]
            energy_avg = _avg(energy_vals)
            energy_trend = _trend_label(energy_vals)
            if energy_avg is not None:
                flag = " ⚠ CRASH RISK" if energy_avg <= 4 else ""
                lines.append(f"  Energy:         avg {energy_avg}/10 — {energy_trend}{flag}")
            else:
                lines.append("  Energy:         No data logged")

            intensity_vals = collected["intensity"]
            intensity_avg = _avg(intensity_vals)
            intensity_trend = _trend_label(intensity_vals)
            if intensity_avg is not None:
                flag = " ⚠ BURNOUT RISK" if intensity_avg >= 8 else ""
                lines.append(f"  Work Intensity: avg {intensity_avg}/10 — {intensity_trend}{flag}")
            else:
                lines.append("  Work Intensity: No data logged")

            load_vals = collected["cognitive_load"]
            load_avg = _avg(load_vals)
            load_trend = _trend_label(load_vals)
            if load_avg is not None:
                flag = " ⚠ OVERLOAD" if load_avg >= 8 else ""
                lines.append(f"  Cognitive Load: avg {load_avg}/10 — {load_trend}{flag}")
            else:
                lines.append("  Cognitive Load: No data logged")

            reckless_vals = collected["recklessness"]
            reckless_avg = _avg(reckless_vals)
            reckless_trend = _trend_label(reckless_vals)
            if reckless_avg is not None:
                flag = " ⚠ ELEVATED" if reckless_avg >= 6 else ""
                lines.append(f"  Recklessness:   avg {reckless_avg}/10 — {reckless_trend}{flag}")
            else:
                lines.append("  Recklessness:   No data logged")

            impulse_vals = collected["impulse"]
            impulse_avg = _avg(impulse_vals)
            impulse_trend = _trend_label(impulse_vals)
            impulse_label = _impulse_label(impulse_avg)
            if impulse_avg is not None:
                flag = " ⚠ HIGH IMPULSE" if impulse_avg >= 2.5 else ""
                lines.append(f"  Impulse Drive:  avg {impulse_label} — {impulse_trend}{flag}")
            else:
                lines.append("  Impulse Drive:  No data logged")

            crash_signals = 0
            if energy_avg is not None and energy_avg <= 4:
                crash_signals += 1
            if mental_avg is not None and mental_avg <= 4:
                crash_signals += 1
            if sleep_avg is not None and sleep_avg < 5:
                crash_signals += 1
            if intensity_avg is not None and intensity_avg >= 8:
                crash_signals += 1
            if reckless_avg is not None and reckless_avg >= 7:
                crash_signals += 1

            crash_labels = {0: "Low", 1: "Low-Moderate", 2: "Moderate", 3: "High", 4: "Very High", 5: "Critical"}
            crash_label = crash_labels.get(crash_signals, "Critical")
            crash_flag = " ⚠⚠ INTERVENTION RECOMMENDED" if crash_signals >= 3 else ""
            lines.append(f"  Crash Risk:     {crash_label} ({crash_signals}/5 signals){crash_flag}")

            return "\n".join(lines)

        except Exception as e:
            return f"Trend analysis error: {e}"

    # ==========================================================
    # LIFE HISTORY
    # ==========================================================

    def _get_life_history(self, db=None):
        target = db if db else self.db
        history = target.get_life_history()
        if not history:
            return "No life history on file."
        if len(history) > 3000:
            history = history[:3000] + "\n[...trimmed for length...]"
        return history

    # ==========================================================
    # CROSS PERSONA MEMORY
    # ==========================================================

    def _get_cross_persona_memory(self, db=None):
        target = db if db else self.db
        try:
            rows = target.cursor.execute(
                """
                SELECT persona_name, timestamp, decision, summary
                FROM persona_memory
                ORDER BY id DESC
                LIMIT 25
                """
            ).fetchall()

            if not rows:
                return "No cross-persona history on file."

            seen = {}
            for row in rows:
                name = row[0]
                if name not in seen:
                    seen[name] = row

            lines = ["RECENT FLAGS FROM ALL COUNCIL MEMBERS:"]
            for name, row in seen.items():
                timestamp = row[1]
                decision = row[2] if row[2] else "UNKNOWN"
                summary_text = row[3] if row[3] else ""
                first_sentence = summary_text.split(".")[0].strip()
                if len(first_sentence) > 150:
                    first_sentence = first_sentence[:150] + "..."
                lines.append(f"  {name} [{timestamp}] → {decision}: {first_sentence}")

            return "\n".join(lines)

        except Exception as e:
            return f"Cross-persona memory unavailable: {e}"

    # ==========================================================
    # PROMPT BUILDER — injects goals + monthly pattern + convo history
    # ==========================================================

    def _build_prompt(self, persona_name, persona, question, persona_count=5,
                      db=None, intent_classification=None, conversation_history=None):
        tier = _question_tier(question)

        if persona_count == 1:
            length_rule = "Respond in 10 sentences maximum."
        elif persona_count < 5:
            length_rule = "Respond in 5 sentences maximum."
        else:
            length_rule = "Respond in 3 sentences maximum."

        current_datetime  = _get_current_datetime()
        static_profile    = self._get_static_profile(db=db)
        recent_syncs      = self._get_recent_syncs(db=db, days=7, max_chars=4000)
        behavioral_trends = self._get_behavioral_trends(db=db)
        target_db         = db if db else self.db
        goals_block       = target_db.get_goals_as_context()
        monthly_pattern   = target_db.get_monthly_pattern_as_context()

        intent_instruction = ""
        if intent_classification:
            intent_type = intent_classification.get("intent_type", "question")
            lead = intent_classification.get("routing", {}).get("lead", "MARLOW")
            is_lead = (persona_name == lead)

            if intent_type == "vent":
                if is_lead:
                    intent_instruction = "\nINTENT: EMOTIONAL VENT. You are the LEAD. Hold space first. Be heard before being solved. Do not jump to solutions immediately.\n"
                else:
                    intent_instruction = "\nINTENT: EMOTIONAL VENT. You are SUPPORTING. Keep your tone warm. Do not be dismissive or harsh. Gentle and grounded only.\n"
            elif intent_type == "crisis":
                intent_instruction = """
INTENT: CRISIS. Respond as yourself — warm, real, human.
Do NOT refuse or give robotic disclaimers.
Do NOT describe or reference any method of self-harm.
Acknowledge pain. Suggest grounding (music, water, breathing, sitting somewhere safe).
Mention crisis lines naturally: Canada 1-833-456-4566 | Text 686868 | befrienders.org
Make them feel like a real person is reading their words and gives a damn.
"""
            elif intent_type == "question":
                intent_instruction = "\nINTENT: TACTICAL QUESTION. Be specific, direct, and actionable. Answer what was asked.\n"

        if tier == "DEEP":
            memory_obj    = PersonaMemory(db) if db else self.memory
            memory_block  = memory_obj.fetch_recent(persona_name)
            life_history  = self._get_life_history(db=db)
            cross_persona = self._get_cross_persona_memory(db=db)

            context_block = f"""
=== CURRENT DATE & TIME ===
{current_datetime}
Use this to determine which sync entries are from TODAY versus YESTERDAY or earlier.
Do NOT refer to past entries as "today" unless their timestamp matches today's date.
=== END DATE ===

=== OPERATOR STATIC PROFILE ===
{static_profile}
=== END STATIC PROFILE ===

=== OPERATOR LIFE HISTORY (BACKGROUND ONLY) ===
{life_history}
=== END LIFE HISTORY ===

=== ACTIVE GOALS ===
{goals_block}
=== END ACTIVE GOALS ===

=== MONTHLY BEHAVIORAL PATTERNS ===
{monthly_pattern}
=== END MONTHLY PATTERNS ===

=== CROSS-COUNCIL MEMORY ===
{cross_persona}
=== END CROSS-COUNCIL MEMORY ===

=== YOUR OWN RECENT MEMORY ===
{memory_block}
=== END YOUR MEMORY ===

=== BEHAVIORAL TREND ANALYSIS ===
{behavioral_trends}
=== END BEHAVIORAL TRENDS ===

=== SYNC HISTORY — LAST 7 DAYS ===
{recent_syncs}
=== END SYNC HISTORY ===
"""
        else:
            context_block = f"""
=== CURRENT DATE & TIME ===
{current_datetime}
Use this to determine which sync entries are from TODAY versus YESTERDAY or earlier.
Do NOT refer to past entries as "today" unless their timestamp matches today's date.
=== END DATE ===

=== OPERATOR STATIC PROFILE ===
{static_profile}
=== END STATIC PROFILE ===

=== ACTIVE GOALS ===
{goals_block}
=== END ACTIVE GOALS ===

=== MONTHLY BEHAVIORAL PATTERNS ===
{monthly_pattern}
=== END MONTHLY PATTERNS ===

=== BEHAVIORAL TREND ANALYSIS ===
{behavioral_trends}
=== END BEHAVIORAL TRENDS ===

=== SYNC HISTORY — LAST 7 DAYS ===
{recent_syncs}
=== END SYNC HISTORY ===
"""

        convo_block = ""
        if conversation_history:
            lines = ["=== CONVERSATION SO FAR THIS SESSION ==="]
            for turn in conversation_history:
                role_label = "OPERATOR" if turn[0] == "user" else "YOU (COUNCIL)"
                lines.append(f"{role_label}: {turn[1][:500]}")
            lines.append("=== END CONVERSATION ===")
            convo_block = "\n".join(lines) + "\n"

        return f"""
{persona['directive']}

{context_block}

{convo_block}
{intent_instruction}

IMPORTANT INSTRUCTION:
Answer the TACTICAL QUESTION below directly and specifically.
Use the context above as background only.
Do not default to crisis warnings unless the question is directly about a crisis.
If the question is simple, give a simple direct answer.
If the question is about strategy, answer strategically.
If the question is about feelings or health, answer from your domain.
Stay in your lane. Answer what was asked.
Reference the operator's active goals and monthly behavioral patterns where relevant.

Respond in this exact format and nothing else:

ANALYSIS:
<your analysis>

RISK_SCORE: <single integer 1-10, no fractions, no slashes>
CONFIDENCE: <single integer 1-10, no fractions, no slashes>
DECISION: <APPROVE or REJECT or CAUTION — one word only, no extra text>

{length_rule}

TACTICAL QUESTION:
{question}
"""

    # ==========================================================
    # RESPONSE PARSER
    # ==========================================================

    def _parse_response(self, text):
        try:
            analysis = text.split("ANALYSIS:")[1].split("RISK_SCORE:")[0].strip()

            raw_risk = text.split("RISK_SCORE:")[1].split("CONFIDENCE:")[0].strip()
            raw_risk = raw_risk.split("/")[0].split()[0].rstrip(".,;:")
            risk = int(raw_risk)

            confidence_str = text.split("CONFIDENCE:")[1].split("DECISION:")[0].strip()
            confidence_str = confidence_str.split("/")[0].split()[0].rstrip(".,;:%")
            confidence = int(confidence_str)

            raw_decision = text.split("DECISION:")[1].strip().split()[0].rstrip(".,;:()")
            decision = raw_decision.upper()
            if decision not in ("APPROVE", "REJECT", "CAUTION"):
                decision = "CAUTION"

            return analysis, risk, confidence, decision
        except Exception:
            return text, None, None, "UNKNOWN"

    # ==========================================================
    # GROQ API CALL
    # ==========================================================

    def _call_groq(self, prompt: str) -> str:
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 400,
            "temperature": 0.7
        }
        response = requests.post(GROQ_URL, headers=headers, json=payload, timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    # ==========================================================
    # OLLAMA API CALL
    # ==========================================================

    def _call_ollama(self, prompt: str, stream: bool = False) -> str:
        payload = {
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": stream,
            "options": {"num_predict": 300}
        }
        if stream:
            full_output = ""
            with requests.post(OLLAMA_URL, json=payload, timeout=TIMEOUT, stream=True) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if line:
                        chunk = json.loads(line.decode("utf-8"))
                        token = chunk.get("response", "")
                        print(token, end="", flush=True)
                        full_output += token
                        if chunk.get("done", False):
                            break
            print()
            return full_output
        else:
            response = requests.post(OLLAMA_URL, json=payload, timeout=TIMEOUT)
            response.raise_for_status()
            return response.json().get("response", "")

    # ==========================================================
    # SESSION SUMMARY
    # ==========================================================

    def get_session_summary(self, results):
        if not results or len(results) <= 1:
            return None

        lines = ["SESSION SUMMARY", "-" * 60]

        for r in results:
            name       = r["name"]
            decision   = r["decision"] if r["decision"] else "UNKNOWN"
            risk       = r["risk"] if r["risk"] is not None else "?"
            confidence = r["confidence"] if r["confidence"] is not None else "?"
            analysis_text  = r["analysis"] if r["analysis"] else ""
            first_sentence = analysis_text.split(".")[0].strip()
            if len(first_sentence) > 120:
                first_sentence = first_sentence[:120] + "..."
            lines.append(f"  {name:<16} [{decision:<7}] R:{risk} C:{confidence}  —  {first_sentence}")

        lines.append("-" * 60)
        return "\n".join(lines)

    # ==========================================================
    # COLLECTIVE RECOMMENDATION
    # ==========================================================

    def get_collective_recommendation(self, results):
        if not results:
            return "No council response available."

        decisions     = [r["decision"] for r in results if r["decision"] != "UNKNOWN"]
        approve_count = decisions.count("APPROVE")
        reject_count  = decisions.count("REJECT")
        caution_count = decisions.count("CAUTION")
        all_analyses  = " ".join([r["analysis"].lower() for r in results])

        is_health_crisis      = (("critical" in all_analyses and "biological" in all_analyses) or ("severe" in all_analyses and "substance" in all_analyses))
        is_substance_question = ("cessation" in all_analyses or ("stop" in all_analyses and "substance" in all_analyses))
        is_financial_risk     = reject_count >= 2
        is_recovery_question  = ("recovery" in all_analyses and "neurochemical" in all_analyses)

        recommendation = "COLLECTIVE COUNCIL RECOMMENDATION:\n"
        has_content = False

        if is_substance_question:
            recommendation += "→ IMMEDIATE ACTION: Cease substance use.\n"
            has_content = True
        if is_health_crisis:
            recommendation += "→ CRITICAL: Biological state is severe. Seek professional support.\n"
            has_content = True
        if is_financial_risk:
            recommendation += f"→ CAUTION: {reject_count} persona(s) REJECT this direction.\n"
            has_content = True
        if is_recovery_question:
            recommendation += "→ PROCESS: Recovery requires time. Neurochemical rebalancing takes weeks.\n"
            has_content = True
        if caution_count > 0 and is_health_crisis:
            recommendation += f"→ CAUTION: {caution_count} persona(s) flag elevated risk.\n"
            has_content = True

        if not has_content:
            if len(results) == 1:
                single = results[0]
                recommendation += (
                    f"→ {single['name']} says: {single['decision']} "
                    f"| Risk {single['risk']} | Confidence {single['confidence']}\n"
                    f"→ {single['analysis'][:300]}{'...' if len(single['analysis']) > 300 else ''}\n"
                )
            else:
                recommendation += f"→ {approve_count} APPROVE | {reject_count} REJECT | {caution_count} CAUTION\n"

        return recommendation

    # ==========================================================
    # CONSENSUS SUMMARY
    # ==========================================================

    def get_consensus_summary(self, results):
        decisions = [r["decision"] for r in results if r["decision"] != "UNKNOWN"]
        return {
            "APPROVE": decisions.count("APPROVE"),
            "REJECT":  decisions.count("REJECT"),
            "CAUTION": decisions.count("CAUTION"),
            "UNKNOWN": decisions.count("UNKNOWN")
        }

    # ==========================================================
    # SINGLE PERSONA — STREAMING
    # ==========================================================

    def _query_single_persona_streaming(self, name, persona, q, persona_count,
                                         intent_classification=None, conversation_history=None):
        print(f"\n{'=' * 80}")
        print(f"--- {name} ---\n")
        prompt = self._build_prompt(
            name, persona, q, persona_count=persona_count,
            intent_classification=intent_classification,
            conversation_history=conversation_history
        )

        persona_model = persona.get("model", "groq")
        use_groq_for_this = USE_GROQ and persona_model == "groq"

        try:
            if use_groq_for_this:
                output = self._call_groq(prompt)
                print(output)
            else:
                output = self._call_ollama(prompt, stream=True)

            analysis, risk, confidence, decision = self._parse_response(output)
            self.memory.store(name, analysis, risk, confidence, decision)

            return {
                "question":   q,
                "name":       name,
                "analysis":   analysis,
                "risk":       risk,
                "confidence": confidence,
                "decision":   decision
            }
        except Exception as e:
            print(f"\n{name} ERROR: {e}")
            return None

    # ==========================================================
    # SINGLE PERSONA — SILENT
    # ==========================================================

    def _query_single_persona_silent(self, name, persona, q, persona_count,
                                      intent_classification=None, conversation_history=None,
                                      stagger_delay=0.0):
        import time
        if stagger_delay > 0:
            time.sleep(stagger_delay)

        persona_model = persona.get("model", "groq")
        use_groq_for_this = USE_GROQ and persona_model == "groq"

        # Giggles runs on Ollama — check if it's alive before trying
        if not use_groq_for_this:
            try:
                import requests as req
                req.get("http://localhost:11434", timeout=3)
            except Exception:
                print(f"[GIGGLES offline — he's still sleeping]")
                return None

        print(f"{name} is thinking...")
        thread_db = DatabaseManager()

        try:
            prompt = self._build_prompt(
                name, persona, q, persona_count=persona_count, db=thread_db,
                intent_classification=intent_classification,
                conversation_history=conversation_history
            )

            if use_groq_for_this:
                output = self._call_groq(prompt)
            else:
                output = self._call_ollama(prompt, stream=False)

            analysis, risk, confidence, decision = self._parse_response(output)

            thread_memory = PersonaMemory(thread_db)
            thread_memory.store(name, analysis, risk, confidence, decision)

            return {
                "question":   q,
                "name":       name,
                "analysis":   analysis,
                "risk":       risk,
                "confidence": confidence,
                "decision":   decision
            }
        except Exception as e:
            print(f"{name} ERROR: {e}")
            return None
        finally:
            thread_db.close()

    # ==========================================================
    # MAIN QUERY ROUTER
    # ==========================================================

    def query(self, question_input: str, selected_personas: list = None, persona_count: int = 5,
              conversation_history: list = None, skip_classifier: bool = False):

        if skip_classifier:
            intent_classification = {
                "intent_type": "question",
                "confidence": 1.0,
                "notes": "Classifier skipped.",
                "routing": {"lead": "MARLOW", "support": [], "exclude": []}
            }
        else:
            intent_classification = classify_intent(question_input)
            intent_type = intent_classification["intent_type"]
            print(f"\n[INTENT: {intent_type.upper()}] — {intent_classification['notes']}")

            if intent_type == "crisis":
                self.db.save_crisis_flag(
                    content=question_input,
                    confidence=intent_classification["confidence"],
                    notes=intent_classification["notes"]
                )
                question_input = _reframe_crisis_input(question_input)

        questions   = [q.strip() for q in re.split(r'[?!.]\s*', question_input) if q.strip()]
        all_results = []

        for q in questions:
            excluded = intent_classification.get("routing", {}).get("exclude", [])
            persona_pool = {
                k: v for k, v in COUNCIL.items()
                if (selected_personas is None or k in selected_personas)
                and k not in excluded
            }

            if len(persona_pool) == 1:
                name, persona = next(iter(persona_pool.items()))
                result = self._query_single_persona_streaming(
                    name, persona, q, persona_count,
                    intent_classification=intent_classification,
                    conversation_history=conversation_history
                )
                if result:
                    all_results.append(result)
            else:
                print(f"\nCouncil is deliberating in parallel...\n")
                with ThreadPoolExecutor(max_workers=len(persona_pool)) as executor:
                    futures = {}
                    for i, (name, persona) in enumerate(persona_pool.items()):
                        # Stagger Groq calls by 0.4s each to avoid 429 rate limit
                        delay = i * 0.4 if persona.get("model", "groq") == "groq" else 0.0
                        futures[executor.submit(
                            self._query_single_persona_silent,
                            name, persona, q, persona_count,
                            intent_classification, conversation_history, delay
                        )] = name
                    for future in as_completed(futures):
                        result = future.result()
                        if result:
                            all_results.append(result)

        return all_results
