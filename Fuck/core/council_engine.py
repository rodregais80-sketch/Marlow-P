"""
council_engine.py
Core intelligence layer.

New in this version:
- Tier 1: Pattern engine context injected into every council session
- Tier 1: Predictive crash window (not reactive) in startup + post-sync
- Tier 1: Substance-outcome correlation context injected per-session
- Tier 1: Decision quality context injected per-session
- Tier 1: Goal momentum scoring injected per-session
- Tier 2: Automatic weekly synthesis (generated without user action)
- Tier 2: Behavioral tagging from free-text in journals
- Tier 3: Causal chain model injected when sufficient data exists
- Tiered memory: Each persona gets compressed history from 30d → 90d → 1yr → annual arc
- Silent ORYN during vents: ORYN fires a real Groq call silently, biological read injected
  into SEREN's context before she responds. ORYN output NOT shown to operator.
- All prior gap fixes preserved in full
"""

import os
import re
import json
import time
import math
import random
import requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from core.database import DatabaseManager
from core.memory import build_memory_block
from core.context_builder import build_shared_context
from core.groq_client import chat_completion as groq_chat, is_available as groq_available, GROQ_MODEL
from core.pattern_engine import PatternEngine
from core.predictor import build_prediction_context
from core.substance_tracker import build_substance_context
from core.decision_tracker import  GoalMomentumScorer, build_decision_context

# Tiered memory consolidator — graceful fallback if not yet installed
try:
    from core.memory_consolidator import get_tiered_context_for_persona as _get_tiered_ctx
    _TIERED_MEMORY_AVAILABLE = True
except ImportError:
    _TIERED_MEMORY_AVAILABLE = False
    def _get_tiered_ctx(db, persona_name):
        return ""

# ── Debate engine integration ─────────────────────────────────────────────────
try:
    from core.debate_engine import (
        run_debate_round,
        store_council_reasoning,
        generate_session_id,
        get_persona_reasoning_patterns
    )
    _DEBATE_ENGINE_AVAILABLE = True
except ImportError:
    _DEBATE_ENGINE_AVAILABLE = False
    def run_debate_round(*a, **kw): return {}
    def store_council_reasoning(*a, **kw): pass
    def generate_session_id(): return "no_session"
    def get_persona_reasoning_patterns(*a, **kw): return ""
# ── Context relevance flags ───────────────────────────────────────────────────
try:
    from core.context_relevance import get_active_context_flags as _get_ctx_flags
    _CONTEXT_RELEVANCE_AVAILABLE = True
except ImportError:
    _CONTEXT_RELEVANCE_AVAILABLE = False
    def _get_ctx_flags(db): return {}

# ── Persona learning engine ───────────────────────────────────────────────────
try:
    from core.persona_learning_engine import get_persona_lessons_context as _get_lessons
    _PERSONA_LEARNING_AVAILABLE = True
except ImportError:
    _PERSONA_LEARNING_AVAILABLE = False
    def _get_lessons(db, persona_name, limit=5): return ""

# ── Background runner council flag ────────────────────────────────────────────
try:
    from core.background_runner import set_council_active as _set_council_active
    _BACKGROUND_RUNNER_AVAILABLE = True
except ImportError:
    _BACKGROUND_RUNNER_AVAILABLE = False
    def _set_council_active(active): pass




_OFFLINE_MESSAGES = {
    "ALDRIC": [
        "ALDRIC stepped away from the board. Whatever he was calculating, it wasn't going well for anyone.",
        "ALDRIC is currently unreachable. He saw a number he didn't like and has been staring at a wall for 20 minutes.",
        "ALDRIC went quiet. This either means he's thinking very hard or he's given up on all of us.",
        "ALDRIC is not available. He's redesigning your entire life strategy on a napkin and refuses to be interrupted."
    ],
    "SEREN": [
        "SEREN is somewhere quiet. She'll find you when you need her.",
        "SEREN stepped out. She left the light on for you.",
        "SEREN is unavailable right now. She heard something in the silence and went to investigate.",
        "SEREN is sitting with someone who needed her more urgently. She hasn't forgotten about you."
    ],
    "MORRO": [
        "MORRO went dark. Probably for the best.",
        "MORRO has nothing to say right now. That's either very good or very bad.",
        "MORRO is unavailable. He saw something coming and decided not to comment on it this time.",
        "MORRO stepped out. Whatever he was about to say, consider yourself lucky you didn't hear it."
    ],
    "ORYN": [
        "ORYN is elbow deep in a spreadsheet about your mitochondria. He says this is time sensitive.",
        "ORYN is unavailable. He's comparing your last three sleep scores to a case study from 1987 Finland. He's very excited about it.",
        "ORYN stepped away to review some data that he says is alarming but won't elaborate on.",
        "ORYN is currently arguing with a medical journal. The journal is losing but it's taking a while."
    ]
}

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"


def get_groq_key():
    from dotenv import load_dotenv
    from pathlib import Path
    env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(env_path, override=True)
    return os.getenv("GROQ_API_KEY", "")


def _field(obj, key, default=""):
    if obj is None:
        return default
    try:
        return obj[key] or default
    except (KeyError, IndexError, TypeError):
        return default


def _row_get(row, key, default=None):
    try:
        v = row[key]
        return v if v is not None else default
    except (KeyError, IndexError, TypeError):
        return default


# ── Monthly pattern infrastructure ───────────────────────────────────────────

def _ensure_monthly_pattern_table(db: DatabaseManager):
    db.conn.execute("""
        CREATE TABLE IF NOT EXISTS monthly_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generated_at TEXT NOT NULL,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            pattern_content TEXT NOT NULL
        )
    """)
    db.conn.commit()


def _save_monthly_pattern(db: DatabaseManager, period_start: str, period_end: str, content: str):
    _ensure_monthly_pattern_table(db)
    now = datetime.now().isoformat()
    db.conn.execute(
        "INSERT INTO monthly_patterns (generated_at, period_start, period_end, pattern_content) VALUES (?, ?, ?, ?)",
        (now, period_start, period_end, content)
    )
    db.conn.commit()


def _get_latest_monthly_pattern(db: DatabaseManager):
    _ensure_monthly_pattern_table(db)
    row = db.conn.execute(
        "SELECT generated_at, pattern_content FROM monthly_patterns ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row


def get_monthly_pattern_as_context(db: DatabaseManager) -> str:
    row = _get_latest_monthly_pattern(db)
    if not row:
        return "No monthly pattern data yet."
    return f"[Pattern analysis from {row[0][:10]}]\n{row[1]}"


# ── Intent classification ─────────────────────────────────────────────────────

def classify_intent(user_input: str) -> dict:
    classifier_prompt = f"""You are an intake routing system for a personal AI support platform.

Analyze this input and determine routing for 4 personas:
- ALDRIC: strategy, money, goals, decisions, plans, logic
- SEREN: emotions, sadness, relationships, mental health, feeling states
- MORRO: impulse, risk, shadow thoughts, things being avoided
- ORYN: biology, sleep, energy, substances, physical health, data/stats

User input:
\"\"\"{user_input}\"\"\"

Determine:
1. intent_type: "question" | "vent" | "crisis" | "mixed"
2. For each persona: "active" (responds visibly) | "silent" (feeds MARLOW only) | "off" (not needed)
3. lead: which persona leads

Rules:
- Pure emotion, no action request → SEREN active, ALDRIC silent, ORYN silent, MORRO off
- Emotion + wants solution → SEREN active, ALDRIC active, ORYN silent, MORRO off
- Strategy/business/money → ALDRIC active, MORRO active if risk present, SEREN silent, ORYN silent
- Health/stats/data/biology → ORYN active, ALDRIC active, SEREN silent, MORRO off
- Risky/impulsive → MORRO active, ALDRIC active, SEREN silent, ORYN silent
- Crisis → SEREN active, ORYN active, ALDRIC silent, MORRO off (HARDCODED — no exceptions)
- Vent (emotional release, no advice sought) → SEREN active, ALDRIC silent, ORYN silent, MORRO off
- Mixed/general → all active except MORRO unless impulse language detected
- MARLOW always synthesizes regardless

Respond ONLY in valid JSON. No preamble:
{{
  "intent_type": "question|vent|crisis|mixed",
  "confidence": 0.0,
  "notes": "one sentence reason",
  "lead": "PERSONA_NAME",
  "routing": {{
    "ALDRIC": "active|silent|off",
    "SEREN": "active|silent|off",
    "MORRO": "active|silent|off",
    "ORYN": "active|silent|off"
  }}
}}"""

    if not groq_available():
        return {
            "intent_type": "question",
            "confidence": 0.5,
            "notes": "No API key — defaulting to full council",
            "lead": "ALDRIC",
            "routing": {
                "ALDRIC": "active",
                "SEREN":  "active",
                "MORRO":  "active",
                "ORYN":   "active"
            }
        }
    try:
        raw = groq_chat(
            messages=[{"role": "user", "content": classifier_prompt}],
            temperature=0.1, max_tokens=300
        )
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        return {
            "intent_type": "question",
            "confidence": 0.5,
            "notes": f"Classifier failed: {e}",
            "lead": "ALDRIC",
            "routing": {
                "ALDRIC": "active",
                "SEREN":  "active",
                "MORRO":  "active",
                "ORYN":   "active"
            }
        }


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
    t = text.lower()
    return any(signal in t for signal in TASK_SIGNALS)


def execute_task(task_request: str, db: DatabaseManager, output_dir: str = ".") -> tuple:
    if not groq_available():
        return "[Task mode requires Groq API key.]", None

    profile = db.get_static_profile()
    goals   = db.get_goals_as_context()
    history = db.get_life_history()

    profile_block = "No profile on file."
    if profile:
        profile_block = (
            f"Name: {_field(profile, 'name', '')}\n"
            f"Location: {_field(profile, 'location', '')}\n"
            f"Occupation: {_field(profile, 'occupation', '')}\n"
            f"Primary Goal: {_field(profile, 'primary_goal', '')}\n"
            f"Biggest Challenge: {_field(profile, 'biggest_challenge', '')}"
        )

    history_block = "No history on file."
    if history:
        relevant = []
        for key_field in ["background", "current_struggles", "current_strengths", "goals_longterm", "additional_context"]:
            val = _field(history, key_field, "")
            if val:
                relevant.append(f"{key_field}: {val}")
        history_block = "\n".join(relevant[:800])

    # Include goal momentum in task context
    try:
        scorer       = GoalMomentumScorer(db)
        momentum_ctx = scorer.build_momentum_context()
    except Exception:
        momentum_ctx = ""

    prompt = f"""You are MARLOW — a sovereign strategic intelligence.

The operator has requested a specific deliverable. Produce it.

=== OPERATOR CONTEXT ===
{profile_block}

=== ACTIVE GOALS ===
{goals}

=== GOAL MOMENTUM ===
{momentum_ctx}

=== BACKGROUND ===
{history_block}

=== TASK REQUEST ===
{task_request}

RULES:
- Produce the deliverable directly. No opener.
- Format it clean. Use headers, numbered lists, or checkboxes as appropriate.
- Make it specific to this operator's actual life and context.
- If it is a routine or checklist, make it realistic and actionable.
- If it is a script, write in their voice — direct, confident, no corporate filler.
- If it is a plan, make it sequenced and time-aware.
- Length: as long as needed to be genuinely useful. No padding. No cutting short.
- End with one line labeled: MARLOW NOTE: [one honest sentence of strategic commentary]"""

    try:
        time.sleep(0.5)
        output = groq_chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6, max_tokens=1200, timeout=45
        )

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = re.sub(r'[^a-z0-9_]', '_', task_request.lower()[:40].strip())
        filename  = f"marlow_task_{timestamp}_{safe_name}.txt"
        filepath  = os.path.join(output_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"MARLOW TASK OUTPUT\n")
            f.write(f"Generated: {datetime.now().strftime('%A, %B %d %Y — %I:%M %p')}\n")
            f.write(f"Request: {task_request}\n")
            f.write("=" * 60 + "\n\n")
            f.write(output)
            f.write("\n")

        return output, filepath

    except Exception as e:
        return f"[Task execution failed: {e}]", None


# ── Behavioral trend analysis ─────────────────────────────────────────────────

def _std_dev(lst: list) -> float:
    if len(lst) < 2:
        return 0.0
    mean     = sum(lst) / len(lst)
    variance = sum((x - mean) ** 2 for x in lst) / len(lst)
    return round(math.sqrt(variance), 1)


def _volatility_label(std: float) -> str:
    if std >= 2.5:
        return "HIGH"
    elif std >= 1.5:
        return "MODERATE"
    else:
        return "LOW"


def build_trend_report(db: DatabaseManager) -> str:
    metrics = db.get_recent_metrics(limit=30)
    if not metrics:
        return "No metric history available yet."

    energy_vals  = [_row_get(m, "energy")        for m in metrics if _row_get(m, "energy")        is not None]
    mood_vals    = [_row_get(m, "mood")          for m in metrics if _row_get(m, "mood")          is not None]
    fog_vals     = [_row_get(m, "mental_fog")    for m in metrics if _row_get(m, "mental_fog")    is not None]
    impulse_vals = [_row_get(m, "impulse_drive") for m in metrics if _row_get(m, "impulse_drive") is not None]
    sleep_vals   = [_row_get(m, "sleep_hours")   for m in metrics if _row_get(m, "sleep_hours")   is not None]

    def avg(lst): return round(sum(lst)/len(lst), 1) if lst else "N/A"
    def trend(lst):
        if len(lst) < 4:
            return "insufficient data"
        recent = sum(lst[:3]) / 3
        older  = sum(lst[-3:]) / 3
        if recent > older + 0.5:   return "improving"
        elif recent < older - 0.5: return "declining"
        else:                      return "stable"

    crash_risk = 0
    if energy_vals:
        recent_energy_avg = avg(energy_vals[:3])
        if isinstance(recent_energy_avg, float):
            if recent_energy_avg >= 8: crash_risk += 3
            elif recent_energy_avg <= 4: crash_risk += 3
    if sleep_vals   and isinstance(avg(sleep_vals[:3]),float)   and avg(sleep_vals[:3]) < 5:    crash_risk += 3
    if fog_vals     and isinstance(avg(fog_vals[:3]),float)     and avg(fog_vals[:3]) >= 7:     crash_risk += 2
    if impulse_vals and isinstance(avg(impulse_vals[:3]),float) and avg(impulse_vals[:3]) >= 7: crash_risk += 2
    crash_risk = min(crash_risk, 10)

    warnings = []
    if sleep_vals   and isinstance(avg(sleep_vals[:3]),float)   and avg(sleep_vals[:3]) < 5:
        warnings.append("LOW SLEEP - cognitive performance degrading")
    if energy_vals  and isinstance(avg(energy_vals[:3]),float):
        if avg(energy_vals[:3]) >= 9:
            warnings.append("SUSTAINED HIGH ENERGY - crash pattern likely within 48 hours")
        elif avg(energy_vals[:3]) <= 3:
            warnings.append("CRITICAL LOW ENERGY - in or near crash. No major decisions.")
    if impulse_vals and isinstance(avg(impulse_vals[:3]),float) and avg(impulse_vals[:3]) >= 8:
        warnings.append("HIGH IMPULSE DRIVE - decision quality at risk")
    if fog_vals     and isinstance(avg(fog_vals[:3]),float)     and avg(fog_vals[:3]) >= 7:
        warnings.append("ELEVATED BRAIN FOG - execution speed reduced")

    e_std = _std_dev(energy_vals)
    m_std = _std_dev(mood_vals)
    i_std = _std_dev(impulse_vals)
    if _volatility_label(e_std) == "HIGH":
        warnings.append(f"HIGH ENERGY VOLATILITY (±{e_std}) - unpredictable output quality")
    if _volatility_label(i_std) == "HIGH":
        warnings.append(f"HIGH IMPULSE VOLATILITY (±{i_std}) - decision pattern unstable")

    warnings_str  = "\n".join(f"  ⚠ {w}" for w in warnings) if warnings else "  None detected"
    monthly       = get_monthly_pattern_as_context(db)
    monthly_block = f"\n--- MONTHLY BEHAVIORAL PATTERNS ---\n{monthly}\n" if monthly != "No monthly pattern data yet." else ""

    return f"""--- BEHAVIORAL TREND REPORT (last 30 entries) ---
Average Energy:      {avg(energy_vals)}/10  [{trend(energy_vals)}]  Volatility: {_volatility_label(e_std)} (σ={e_std})
Average Mood:        {avg(mood_vals)}/10   [{trend(mood_vals)}]  Volatility: {_volatility_label(m_std)} (σ={m_std})
Average Brain Fog:   {avg(fog_vals)}/10   [{trend(fog_vals)}]
Average Impulse:     {avg(impulse_vals)}/10 [{trend(impulse_vals)}]  Volatility: {_volatility_label(i_std)} (σ={i_std})
Average Sleep:       {avg(sleep_vals)} hrs [{trend(sleep_vals)}]
Composite Crash Risk: {crash_risk}/10

Active Warnings:
{warnings_str}{monthly_block}"""


# ── Crash alert ────────────────────────────────────────────────────────────────

def generate_crash_alert(db: DatabaseManager) -> str:
    try:
        metrics = db.get_recent_metrics(limit=3)
        if not metrics or len(metrics) < 2:
            return ""
        metrics = list(reversed(metrics))

        def vals(field):
            result = []
            for m in metrics:
                v = _row_get(m, field)
                if v is not None: result.append(v)
            return result

        def avg(lst): return round(sum(lst)/len(lst), 1) if lst else None
        def trending_down(lst): return len(lst) >= 2 and lst[-1] < lst[0]
        def trending_up(lst):   return len(lst) >= 2 and lst[-1] > lst[0]

        energy_vals   = vals("energy")
        mood_vals     = vals("mood")
        fog_vals      = vals("mental_fog")
        impulse_vals  = vals("impulse_drive")
        sleep_vals    = vals("sleep_hours")

        warnings           = []
        crash_signal_count = 0

        energy_avg = avg(energy_vals)
        if energy_avg is not None:
            if energy_avg >= 8.5 and trending_down(energy_vals):
                warnings.append(f"Energy peak {energy_avg}/10 now declining — crash window active")
                crash_signal_count += 1
            elif energy_avg <= 3:
                warnings.append(f"Energy critical ({energy_avg}/10) — in crash territory")
                crash_signal_count += 2

        sleep_avg = avg(sleep_vals)
        if sleep_avg is not None and sleep_avg < 5:
            warnings.append(f"Sleep deficit ({sleep_avg} hrs avg) — cognitive baseline compromised")
            crash_signal_count += 1

        fog_avg = avg(fog_vals)
        if fog_avg is not None and fog_avg >= 7:
            warnings.append(f"Brain fog elevated ({fog_avg}/10) — execution at reduced capacity")
            crash_signal_count += 1

        impulse_avg = avg(impulse_vals)
        if impulse_avg is not None and impulse_avg >= 8:
            warnings.append(f"Impulse drive spiked ({impulse_avg}/10) — decision quality at risk")
            crash_signal_count += 1

        mood_avg = avg(mood_vals)
        if mood_avg is not None and mood_avg <= 3:
            warnings.append(f"Mood floor ({mood_avg}/10) — watch for behavioral shutdown")
            crash_signal_count += 1

        if not warnings or crash_signal_count < 2:
            return ""

        severity = "CRITICAL" if crash_signal_count >= 3 else "WARNING"
        lines = [
            "",
            "!" * 60,
            f"  MARLOW ALERT — {severity} — {crash_signal_count} CRASH SIGNALS DETECTED",
            "!" * 60,
            "",
        ]
        for w in warnings:
            lines.append(f"  ◉ {w}")
        lines.append("")
        lines.append("  Directive: No major decisions or high-stakes conversations until metrics stabilize.")
        lines += ["", "!" * 60, ""]
        return "\n".join(lines)

    except Exception:
        return ""


def generate_predictive_crash_warning(db: DatabaseManager) -> str:
    """
    Tier 1: Calls predict_crash_window() and formats for display.
    Runs at startup AND after every sync. Distinct from generate_crash_alert()
    which is reactive. This one fires before the crash materializes.
    """
    from core.predictor import predict_crash_window
    result = predict_crash_window(db)

    if not result.get("predicted"):
        return ""

    conf    = result.get("confidence", 0)
    hours   = result.get("hours_out", 36)
    signals = result.get("signals", [])
    directive = result.get("directive", "")

    lines = [
        "",
        "~" * 70,
        f"  MARLOW PREDICTIVE WARNING — {conf:.0%} confidence — ~{hours}h window",
        "~" * 70,
        "",
        "  Trajectory analysis suggests a crash window is building.",
        "",
    ]
    for s in signals:
        lines.append(f"  ◉ {s}")
    lines.append("")
    lines.append(f"  {directive}")
    lines += ["", "~" * 70, ""]
    return "\n".join(lines)


def should_generate_monthly_pattern(db: DatabaseManager) -> bool:
    row = _get_latest_monthly_pattern(db)
    if not row:
        return True
    try:
        last = datetime.fromisoformat(row[0])
        return (datetime.now() - last).days >= 7
    except Exception:
        return True


def generate_monthly_pattern(db: DatabaseManager) -> str:
    if not groq_available():
        return "[Monthly pattern requires Groq API key.]"

    now          = datetime.now()
    period_start = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    period_end   = now.strftime("%Y-%m-%d")

    try:
        rows = db.conn.execute(
            "SELECT timestamp, sync_type, content FROM logs ORDER BY id DESC LIMIT 60"
        ).fetchall()
    except Exception:
        try:
            rows = db.conn.execute(
                "SELECT timestamp, content FROM logs ORDER BY id DESC LIMIT 60"
            ).fetchall()
            rows = [(r[0], "sync", r[1]) for r in rows]
        except Exception:
            return "[Could not retrieve log data for pattern analysis.]"

    if not rows:
        return "[No sync data available for pattern analysis.]"

    # Include deep pattern analysis (Tier 1)
    try:
        engine        = PatternEngine(db)
        deep_patterns = engine.synthesize_master_insights()
        pattern_ctx   = engine.format_insights_for_context(deep_patterns)[:800]
    except Exception:
        pattern_ctx = ""

    log_blocks = []
    for row in reversed(rows):
        ts      = row[0]
        stype   = row[1] if len(row) > 2 else "sync"
        content = row[2] if len(row) > 2 else row[1]
        log_blocks.append(f"[{ts} — {stype.upper()}]\n{content[:400]}")

    log_text     = "\n\n---\n\n".join(log_blocks)
    profile      = db.get_static_profile()
    profile_block= ""
    if profile:
        profile_block = (
            f"Name: {_field(profile,'name','')}, "
            f"Occupation: {_field(profile,'occupation','')}, "
            f"Primary Goal: {_field(profile,'primary_goal','')}"
        )

    prompt = f"""You are MARLOW — a pattern recognition intelligence analyzing 30 days of behavioral data.

=== OPERATOR ===
{profile_block}

=== DEEP PATTERN ANALYSIS (pre-computed) ===
{pattern_ctx}

=== SYNC DATA (LAST 30 DAYS) ===
{log_text}

Document these patterns — be specific to the actual data:

1. ENERGY CYCLE — When does energy peak and crash? What triggers dips?
2. PRODUCTIVITY PATTERN — Which times produce real output? What gets wasted?
3. IMPULSE/RECKLESSNESS CYCLE — When does impulse spike? What precedes it?
4. SLEEP PATTERN — Actual baseline. Stable or erratic?
5. WHAT GENERATES HAPPINESS & EXCITEMENT — Specific activities, interactions, and conditions.
6. WHAT GENERATES FLATNESS & DREAD — Specific triggers.
7. RECURRING FRICTION POINTS — Walls that keep appearing.
8. BEHAVIORAL CYCLE SUMMARY — In 2-3 sentences, the dominant cycle this month.

Keep each section to 2-3 sentences. Specific to the data. No filler."""

    try:
        time.sleep(0.5)
        content = groq_chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4, max_tokens=800, timeout=45
        )
        _save_monthly_pattern(db, period_start, period_end, content)
        return content
    except Exception as e:
        return f"[Monthly pattern generation failed: {e}]"


def generate_session_brief(db: DatabaseManager) -> str:
    try:
        row = db.conn.execute(
            "SELECT timestamp, sync_type, content FROM logs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return ""
        ts, sync_type, content = row[0], row[1], row[2]
    except Exception:
        try:
            row = db.conn.execute(
                "SELECT timestamp, content FROM logs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if not row:
                return ""
            ts, sync_type, content = row[0], "sync", row[1]
        except Exception:
            return ""

    try:
        dt        = datetime.fromisoformat(ts)
        time_ago  = datetime.now() - dt
        hours_ago = int(time_ago.total_seconds() / 3600)
        when      = "less than an hour ago" if hours_ago < 1 else f"{hours_ago} hour(s) ago" if hours_ago < 24 else f"{time_ago.days} day(s) ago"
        sync_time_str = dt.strftime("%A %I:%M %p")
    except Exception:
        when = "recently"; sync_time_str = ts[:16]

    profile = db.get_static_profile()
    name    = _field(profile, 'name', 'Operator') if profile else "Operator"

    if not groq_available():
        energy_match = re.search(r"energy[:\s]+(\d+)", content, re.IGNORECASE)
        mood_match   = re.search(r"mood[:\s]+(\d+)", content, re.IGNORECASE)
        focus_match  = re.search(r"(todays_focus|tomorrows_focus|tomorrow)[:\s]+(.+)", content, re.IGNORECASE)
        energy   = energy_match.group(1) if energy_match else "?"
        mood     = mood_match.group(1) if mood_match else "?"
        priority = focus_match.group(2).strip()[:80] if focus_match else "not logged"
        return "\n".join(["", "─"*60, f"  Last logged: {sync_type} — {sync_time_str} ({when})",
                          f"  State: Energy {energy}/10 | Mood {mood}/10",
                          f"  Priority: {priority}", "─"*60])

    # Include goal momentum in session brief
    try:
        scorer       = GoalMomentumScorer(db)
        momentum_ctx = scorer.build_momentum_context()
    except Exception:
        momentum_ctx = ""

    # Forward-looking context: streak and contradiction signals
    forward_ctx = ""
    try:
        from core.streak_tracker import build_streak_context
        forward_ctx += build_streak_context(db, max_chars=150)
    except Exception:
        pass
    try:
        from core.contradiction_engine import build_contradiction_context
        forward_ctx += "\n" + build_contradiction_context(db, max_chars=200)
    except Exception:
        pass

    prompt = f"""You are MARLOW. Generate a pre-session intelligence brief for {name}.

Last sync: {sync_type} logged {when} ({sync_time_str})

Sync content:
{content[:1000]}

Goal momentum:
{momentum_ctx[:300]}

{forward_ctx[:300]}

Write exactly 5 lines. No headers. No bullet points.

Line 1: State what was last logged and when. Include 1-2 key metric numbers.
Line 2: Identify the single most important signal from this sync.
Line 3: Note goal momentum — which goal is moving, which is stalling.
Line 4: State the operator's own stated priority. If not logged, derive from context.
Line 5: Forward projection — based on trajectory and patterns, what is this session most likely to surface? One sentence. Probabilistic, not motivational.

Direct. Intelligence briefing tone. Every line carries weight."""

    try:
        time.sleep(0.5)
        brief = groq_chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4, max_tokens=220, timeout=20
        )
        output = ["", "─"*60, f"  MARLOW BRIEF — {sync_time_str} ({when})", "─"*60]
        for line in brief.split("\n"):
            if line.strip():
                output.append(f"  {line.strip()}")
        output += ["─"*60, ""]
        return "\n".join(output)
    except Exception:
        return ""


# ── Automatic weekly synthesis (Tier 2) ──────────────────────────────────────

def maybe_generate_auto_weekly_report(db: DatabaseManager) -> str:
    """
    Tier 2: Generates weekly report automatically when 6+ days have elapsed
    since the last one. Fires at startup without user action.
    Returns the report content if generated, empty string if not needed.
    """
    if not db.should_generate_auto_report("weekly"):
        return ""

    try:
        row_count = db.conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
        if row_count < 7:
            return ""
    except Exception:
        return ""

    report = generate_weekly_report(db)
    if report and not report.startswith("["):
        now          = datetime.now()
        period_start = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        period_end   = now.strftime("%Y-%m-%d")
        db.save_auto_report("weekly", period_start, period_end, report)

    return report


# ── Behavioral tagging from free text (Tier 2) ────────────────────────────────

def extract_behavioral_tags(text: str) -> list:
    """
    Tier 2: Extracts behavioral signal tags from free-text journal/sync entries.
    Uses the new pattern_engine EMOTION_LEXICON via _score_emotions.
    """
    try:
        from core.pattern_engine import _score_emotions
        scores = _score_emotions(text)
        tags   = []
        for emotion, score in scores.items():
            if score >= 0.05:
                tags.append(emotion)
        return tags
    except Exception:
        return []

# ── Persona prompt builder ────────────────────────────────────────────────────

def build_persona_prompt(
    persona: dict,
    user_input: str,
    intent_classification: dict,
    is_lead: bool,
    shared_context: dict,
    conversation_history: list = None,
    pattern_context: str = "",
    prediction_context: str = "",
    substance_context: str = "",
    decision_context: str = "",
    tiered_history: str = "",
    oryn_bio_context: str = ""
) -> list:
    """
    Builds the message list for a single persona API call.
    Includes pattern, prediction, substance, decision contexts.
    Also injects tiered compressed historical memory when available.

    tiered_history: compressed memory from 30d → 90d → 1yr → annual arc.
    Each persona only receives history relevant to their own domain.

    oryn_bio_context: SEREN-only. When ORYN ran silently during a vent,
    his biological read is injected here so SEREN knows what the body is
    doing before she responds emotionally. Never shown to operator.
    """
    intent_type  = intent_classification.get("intent_type", "question")
    prompt_input = user_input
    if intent_type == "crisis":
        prompt_input = f"""[CRISIS MODE]\n\nOriginal message: {user_input}"""

    now          = shared_context.get("now", datetime.now().strftime("%A, %B %d, %Y - %I:%M %p"))
    intent_notes = intent_classification.get("notes", "")
    persona_name = persona["name"]

    from core.context_builder import build_context_for_persona
    context_block = build_context_for_persona(persona_name, shared_context)

    lead_instruction = (
        "\nYou are the LEAD RESPONDER for this input. Your response should be the most thorough.\n"
        if is_lead else
        "\nYou are a SUPPORTING RESPONDER. Be focused and complementary — don't repeat the lead.\n"
    )

    intent_instruction = ""
    if intent_type == "vent":
        intent_instruction = (
            "VENT MODE: The person primarily needs to be heard. "
            + ("You are lead — hold space before solving." if is_lead
               else "You are support — warm and grounded only.")
        )
    elif intent_type == "crisis":
        intent_instruction = """CRISIS MODE:
Respond as a real person who genuinely cares. Stay in your persona voice.
Acknowledge what they said. Suggest grounding: music, sitting down, breathing, water.
Mention crisis support naturally: Canada 1-833-456-4566 | Text 686868 | befrienders.org
Never describe any method of harm. Never be clinical. Never refuse. Be present."""
    elif intent_type == "question":
        intent_instruction = "QUESTION MODE: The person wants clarity, direction, or a plan. Be specific and actionable."

    # Compose additional intelligence blocks for this persona
    # MORRO gets minimal extra context; others get the full stack
    intelligence_block = ""
    if persona_name != "MORRO":
        parts = []
        if pattern_context:
            parts.append(pattern_context)
        if prediction_context:
            parts.append(prediction_context)
        if substance_context and persona_name == "ORYN":
            parts.append(substance_context)
        if decision_context and persona_name == "ALDRIC":
            parts.append(decision_context)
        if tiered_history:
            parts.append(tiered_history)
        # ── Silent ORYN biological context injection (SEREN only) ────────
        # When ORYN ran silently during a vent, his biological read is
        # injected into SEREN's system prompt here.
        # SEREN sees the body's state. The operator never sees this block.
        if persona_name == "SEREN" and oryn_bio_context:
            parts.append(
                "--- ORYN BIOLOGICAL READ (silent — operator does not see this) ---\n"
                + oryn_bio_context +
                "\n--- Use this to inform your emotional response, not to cite it. ---"
            )
        if parts:
            intelligence_block = "\n\n" + "\n\n".join(parts)
    else:
        # MORRO gets tiered history but nothing else — he needs historical
        # shadow patterns but not clinical intelligence blocks
        if tiered_history:
            intelligence_block = "\n\n" + tiered_history

    system_content = f"""{persona['system_prompt']}

--- SYSTEM CONTEXT ---
Current Date/Time: {now}
Intent Classification: {intent_type.upper()} (confidence: {intent_classification.get('confidence', 0):.0%})
Classifier Notes: {intent_notes}
{intent_instruction}
{lead_instruction}

{context_block}{intelligence_block}
--- END CONTEXT ---"""

    messages = [{"role": "system", "content": system_content}]

    if conversation_history:
        for turn in conversation_history:
            messages.append({"role": turn["role"], "content": turn["content"]})

    messages.append({"role": "user", "content": prompt_input})
    return messages


# ── Persona memory extraction ─────────────────────────────────────────────────

_RISK_WORDS    = {"critical","danger","dangerous","risk","warning","severe",
                   "crash","crisis","emergency","urgent","harmful","unsafe",
                   "escalating","destructive","spiral","collapse"}
_APPROVE_WORDS = {"proceed","approve","go","solid","good","strong","healthy",
                   "stable","aligned","momentum","positive","forward"}
_REJECT_WORDS  = {"stop","reject","avoid","don't","do not","dangerous","abort",
                   "pause","hold","wait","reconsider","destructive","sabotage"}
_CAUTION_WORDS = {"caution","careful","watch","monitor","flag","consider",
                   "uncertain","mixed","concern","check","review","unclear"}


def _extract_persona_memory(persona_name: str, response_text: str) -> tuple:
    clean   = re.sub(r"[*#_\-=]{2,}", "", response_text).strip()
    summary = " ".join(clean.split())[:200]

    words_lower  = set(response_text.lower().split())
    risk_hits    = len(words_lower & _RISK_WORDS)
    risk_score   = min(10, max(1, risk_hits * 2 + 3)) if risk_hits else 4

    approve_hits = len(words_lower & _APPROVE_WORDS)
    reject_hits  = len(words_lower & _REJECT_WORDS)
    caution_hits = len(words_lower & _CAUTION_WORDS)

    if reject_hits > approve_hits and reject_hits > caution_hits:
        decision = "REJECT"
    elif caution_hits > approve_hits:
        decision = "CAUTION"
    else:
        decision = "APPROVE"

    return summary, risk_score, decision


def _write_persona_memories(db: DatabaseManager, council_responses: list):
    for name, response in council_responses:
        if not response or (response.startswith("[") and "OFFLINE" in response):
            continue
        try:
            summary, risk_score, decision = _extract_persona_memory(name, response)
            db.save_persona_memory(
                persona_name=name, summary=summary,
                risk_score=risk_score, confidence_score=7, decision=decision
            )
        except Exception:
            pass


# ── Silent persona summaries for MARLOW ───────────────────────────────────────

def _build_silent_persona_summaries(
    silent_names: list,
    personas: list,
    user_input: str,
    shared_ctx: dict,
    oryn_silent_output: str = ""
) -> str:
    """
    For personas that are silent (not displaying to operator), generates a brief
    domain perspective to feed into MARLOW's synthesis.
    MARLOW always knows what every persona would have said.

    oryn_silent_output: when ORYN ran a real silent call during a vent,
    his actual biological read is passed here and included in MARLOW's
    synthesis context. More useful than a domain label alone.
    """
    if not silent_names and not oryn_silent_output:
        return ""

    persona_domains = {
        "ALDRIC": "strategic/economic lens — systems, consequences, leverage, long-term positioning",
        "SEREN":  "emotional/psychological lens — what is felt but unsaid, relational impact, human cost",
        "MORRO":  "shadow lens — what is being avoided, the uncomfortable truth, impulse risk",
        "ORYN":   "biological lens — physical state, neurochemistry, what the body is doing right now"
    }

    lines = ["--- SILENT PERSONA PERSPECTIVES (for MARLOW synthesis only) ---"]

    for name in silent_names:
        # If ORYN was silent and we have his actual output, use it
        if name == "ORYN" and oryn_silent_output:
            lines.append(f"ORYN (biological lens — ran silently, output below):")
            lines.append(oryn_silent_output.strip()[:400])
        else:
            domain = persona_domains.get(name, "")
            lines.append(f"{name} ({domain}): not displayed to operator but considered in synthesis")

    # Edge case: ORYN ran silently but is not in silent_names
    # (e.g. was marked "off" by classifier but we overrode for bio read)
    if oryn_silent_output and "ORYN" not in silent_names:
        lines.append(f"ORYN (biological lens — ran silently, output below):")
        lines.append(oryn_silent_output.strip()[:400])

    return "\n".join(lines)


# ── Silent ORYN biological read ───────────────────────────────────────────────

def _build_oryn_silent_prompt(
    user_input: str,
    shared_ctx: dict,
    substance_ctx: str,
    prediction_ctx: str,
    oryn_persona: dict
) -> list:
    """
    Builds a stripped-down prompt for ORYN's silent biological analysis.
    Fires synchronously before the main parallel task pool during vents/crisis.
    Output feeds SEREN's context and MARLOW's synthesis. Never shown to operator.

    Intentionally short and focused — ORYN only needs to read the body, not advise.
    """
    from core.context_builder import build_context_for_persona
    context_block = build_context_for_persona("ORYN", shared_ctx)

    bio_blocks = []
    if context_block:
        bio_blocks.append(context_block)
    if substance_ctx:
        bio_blocks.append(substance_ctx)
    if prediction_ctx:
        bio_blocks.append(prediction_ctx[:500])

    bio_context = "\n\n".join(bio_blocks)

    system_content = f"""{oryn_persona.get('system_prompt', 'You are ORYN — clinical biological analyst.')}

--- SILENT MODE ---
You are running silently. The operator is venting. Your output will NOT be shown to them.
Your ONLY job: give SEREN the biological context she needs to respond accurately.

{bio_context}
--- END CONTEXT ---

Biological read protocol:
- What is the current physiological state based on recent sync data?
- What substances are present or recently cleared? What is the neurochemical aftermath?
- What does the physical baseline (sleep, energy, fog) tell you about what is driving this emotional state?
- What should SEREN know before she responds?

3-4 sentences. Clinical. Specific. No moral judgment. No advice to the operator.
SEREN reads this. The operator does not."""

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": f"Operator input (vent — do not respond to this directly): {user_input[:500]}"}
    ]


def _run_oryn_silent(
    user_input: str,
    shared_ctx: dict,
    substance_ctx: str,
    prediction_ctx: str,
    oryn_persona: dict
) -> str:
    """
    Fires ORYN's silent biological call synchronously.
    Returns his biological read as a string, or empty string on failure.
    Graceful fallback — system continues normally if this fails.
    """
    if not groq_available():
        return ""
    try:
        messages = _build_oryn_silent_prompt(
            user_input, shared_ctx, substance_ctx, prediction_ctx, oryn_persona
        )
        response = groq_chat(
            messages=messages,
            temperature=0.3,
            max_tokens=250,
            stagger_delay=0.0
        )
        return response.strip() if response else ""
    except Exception:
        return ""


# ── Council runner ────────────────────────────────────────────────────────────

def run_council(
    user_input: str,
    db: DatabaseManager,
    personas: list,
    intent_classification: dict,
    conversation_history: list = None,
    enable_debate: bool = True
) -> dict:
    """
    Main council orchestration. Upgraded with:

    1. CONTEXT CACHING — Pattern, prediction, substance, decision contexts are
       cached in the DB for 30 minutes. Heavy recomputation only runs once per
       session window, not once per persona call or once per council run.

    2. DEBATE ROUND — After Round 1 (existing), each active persona sees all
       other personas' Round 1 outputs and produces a final debated position.
       Personas critique each other's reasoning before MARLOW synthesizes.
       MORRO excluded from debate (same rule as synthesis exclusion).

    3. COUNCIL REASONING LOG — Round 1 and Round 2 outputs stored per session
       for longitudinal pattern tracking.

    4. BEHAVIORAL TAG INTEGRATION — Extracted tags saved to MARLOW persona
       memory so PatternEngine can read them on future sessions.

    All existing behavior preserved: silent ORYN, tiered history, routing
    rules, hardcoded MORRO/crisis exclusions.
    """
    lead_name   = intent_classification.get("lead", "ALDRIC")
    intent_type = intent_classification.get("intent_type", "question")
    routing     = intent_classification.get("routing", {
        "ALDRIC": "active", "SEREN": "active",
        "MORRO":  "active", "ORYN":  "active"
    })

    active_names = [k for k, v in routing.items() if v == "active"]
    silent_names = [k for k, v in routing.items() if v == "silent"]

    # Hardcoded safety overrides — classifier cannot override these
    if intent_type in ("vent", "crisis"):
        routing["MORRO"] = "off"
        if "MORRO" in active_names:
            active_names.remove("MORRO")
        if "MORRO" in silent_names:
            silent_names.remove("MORRO")

    if intent_type == "crisis":
        db.save_crisis_flag(
            content=user_input,
            confidence=intent_classification.get("confidence", 0.0),
            notes=intent_classification.get("notes", "")
        )

    # Generate session ID for council_reasoning_log grouping
    session_id = generate_session_id() if _DEBATE_ENGINE_AVAILABLE else "no_session"

    # Expire stale cache entries from previous sessions
    db.clear_context_cache()

    # Build all shared context once
    trend_report = build_trend_report(db)
    memory_block = build_memory_block(db, personas)
    shared_ctx   = build_shared_context(db, trend_report, memory_block)

    # ── Context relevance flags — conditional loading ────────────────────────
    # Checks what's actually relevant this session before building any context.
    # Each flag is a fast SQLite read. No computation. Under 5ms total.
    ctx_flags = {}
    if _CONTEXT_RELEVANCE_AVAILABLE:
        try:
            ctx_flags = _get_ctx_flags(db)
        except Exception:
            ctx_flags = {}

    # Notify background runner that council is active
    _set_council_active(True)

    # ── Context caching: build heavy blocks once, cache for session window ────
    # Each block checks cache first. If warm, uses cached value.
    # Conditional blocks check relevance flags before even attempting computation.
    # TTL: 30 minutes — long enough to cover a full interactive session.
    context_health = {}

    # Pattern context — always relevant, always compute. Cache aggressively.
    # Background runner pre-warms this every 2h so it's usually already cached.
    pattern_ctx = db.get_context_cache("pattern_ctx")
    if not pattern_ctx and ctx_flags.get("pattern_sufficient_data", True):
        try:
            engine       = PatternEngine(db)
            pattern_data = engine.synthesize_master_insights()
            pattern_ctx  = engine.format_insights_for_context(pattern_data)[:1200]
            db.set_context_cache("pattern_ctx", pattern_ctx, ttl_minutes=30)
            context_health["Pattern Intelligence"] = "OK"
        except Exception as e:
            pattern_ctx = ""
            context_health["Pattern Intelligence"] = f"DEGRADED — {str(e)[:60]}"
    elif pattern_ctx:
        context_health["Pattern Intelligence"] = "OK (cached)"
    else:
        pattern_ctx = ""
        context_health["Pattern Intelligence"] = "SKIPPED — insufficient data"

    # Prediction context — always relevant. Background runner pre-warms.
    prediction_ctx = db.get_context_cache("prediction_ctx")
    if not prediction_ctx:
        try:
            prediction_ctx = build_prediction_context(db, max_chars=1000)
            db.set_context_cache("prediction_ctx", prediction_ctx, ttl_minutes=30)
            context_health["Predictor"] = "OK"
        except Exception as e:
            prediction_ctx = ""
            context_health["Predictor"] = f"DEGRADED — {str(e)[:60]}"
    else:
        context_health["Predictor"] = "OK (cached)"

    # Substance context — CONDITIONAL on recent activity
    # active: full engine | recent: use cache only | inactive: silent
    substance_ctx = ""
    if not ctx_flags.get("substance_skip", False):
        substance_ctx = db.get_context_cache("substance_ctx")
        if not substance_ctx and ctx_flags.get("substance_active", True):
            try:
                substance_ctx = build_substance_context(db, max_chars=800)
                if substance_ctx:
                    db.set_context_cache("substance_ctx", substance_ctx, ttl_minutes=120)
                context_health["Substance Tracker"] = "OK"
            except Exception as e:
                substance_ctx = ""
                context_health["Substance Tracker"] = f"DEGRADED — {str(e)[:60]}"
        elif substance_ctx:
            context_health["Substance Tracker"] = "OK (cached)"
        else:
            context_health["Substance Tracker"] = "SKIPPED — no recent activity"
    else:
        context_health["Substance Tracker"] = "SKIPPED — inactive (30+ days)"

    # Decision context — CONDITIONAL on active goals or pending decisions
    decision_ctx = ""
    if ctx_flags.get("decisions_pending", True) or ctx_flags.get("streak_active", True):
        decision_ctx = db.get_context_cache("decision_ctx")
        if not decision_ctx:
            try:
                decision_ctx = build_decision_context(db, max_chars=500)
                context_health["Decision Tracker"] = "OK"
            except Exception as e:
                decision_ctx = ""
                context_health["Decision Tracker"] = f"DEGRADED — {str(e)[:60]}"
        else:
            context_health["Decision Tracker"] = "OK (cached)"
    else:
        context_health["Decision Tracker"] = "SKIPPED — no active goals or pending decisions"

    # Strategy plan context — appended to ALDRIC's decision_ctx
    try:
        from core.strategy_planner import build_plan_context
        plan_ctx = build_plan_context(db, max_chars=400)
        if plan_ctx:
            decision_ctx = (decision_ctx + "\n\n" + plan_ctx).strip()
    except Exception:
        pass

    # Contradiction context — CONDITIONAL on pending intentions
    if ctx_flags.get("contradictions_pending", True):
        try:
            from core.contradiction_engine import build_contradiction_context
            contradiction_ctx = build_contradiction_context(db, max_chars=400)
            if contradiction_ctx:
                decision_ctx = (decision_ctx + "\n\n" + contradiction_ctx).strip()
        except Exception:
            pass

    # Streak context — CONDITIONAL on active goals
    if ctx_flags.get("streak_active", True):
        try:
            from core.streak_tracker import build_streak_context
            streak_ctx = build_streak_context(db, max_chars=250)
            if streak_ctx:
                decision_ctx = (decision_ctx + "\n\n" + streak_ctx).strip()
        except Exception:
            pass

    # Cache the assembled decision_ctx for this session
    if decision_ctx:
        db.set_context_cache("decision_ctx", decision_ctx, ttl_minutes=30)

    degraded = {k: v for k, v in context_health.items() if v not in ("OK", "OK (cached)")}
    if degraded:
        try:
            from core.marlow_logger import log_context_health
            log_context_health(context_health)
        except Exception:
            pass
        print("\n  \u250c\u2500 INTELLIGENCE LAYER WARNING \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510")
        for layer, status in degraded.items():
            print(f"  \u2502  \u26a0  {layer}: {status}")
        print("  \u2502  Council will respond with reduced context.")
        print("  \u2514\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2518\n")

    # ── Silent ORYN: fire biological read before main pool ────────────────────
    oryn_silent_output = ""
    oryn_ran_silently  = False

    if intent_type in ("vent", "crisis") and "ORYN" not in active_names:
        oryn_persona = next((p for p in personas if p["name"] == "ORYN"), None)
        if oryn_persona:
            oryn_silent_output = _run_oryn_silent(
                user_input, shared_ctx, substance_ctx, prediction_ctx, oryn_persona
            )
            oryn_ran_silently = bool(oryn_silent_output)

    # ── Tiered history: fetch per active persona ──────────────────────────────
    tiered_histories = {}
    if _TIERED_MEMORY_AVAILABLE:
        for name in active_names:
            try:
                tiered_histories[name] = _get_tiered_ctx(db, name)
            except Exception:
                tiered_histories[name] = ""

    # ── Persona learning injection ───────────────────────────────────────────
    # Fetch learned patterns from rated decisions per persona.
    # Injected into each persona's prompt alongside decision_ctx.
    # Empty string for MORRO (no learning engine for shadow voice).
    persona_lessons = {}
    if _PERSONA_LEARNING_AVAILABLE:
        for pname in ["ALDRIC", "SEREN", "ORYN"]:
            try:
                lessons = _get_lessons(db, pname, limit=5)
                if lessons:
                    persona_lessons[pname] = lessons
            except Exception:
                pass

    # ── Round 1: independent persona analysis (existing behavior) ─────────────
    tasks = []
    for i, persona in enumerate(personas):
        name = persona["name"]
        if name not in active_names:
            continue
        if name == "MORRO" and intent_type == "crisis":
            continue
        is_lead  = (name == lead_name)
        # Append persona learning block to decision_ctx for relevant personas
        persona_decision_ctx = decision_ctx
        if name in persona_lessons and persona_lessons[name]:
            persona_decision_ctx = (
                (persona_decision_ctx + "\n\n" if persona_decision_ctx else "") +
                persona_lessons[name]
            ).strip()
        messages = build_persona_prompt(
            persona, user_input, intent_classification, is_lead,
            shared_ctx, conversation_history,
            pattern_context=pattern_ctx,
            prediction_context=prediction_ctx,
            substance_context=substance_ctx,
            decision_context=persona_decision_ctx,
            tiered_history=tiered_histories.get(name, ""),
            oryn_bio_context=oryn_silent_output if name == "SEREN" else ""
        )
        tasks.append((persona, messages, is_lead, i * 0.4))

    round1_results = {}
    with ThreadPoolExecutor(max_workers=max(1, len(tasks))) as executor:
        future_map = {
            executor.submit(_call_persona, task[0], task[1], task[3]): task[0]["name"]
            for task in tasks
        }
        for future in as_completed(future_map):
            name   = future_map[future]
            result = future.result()
            round1_results[name] = result["response"]

    # Store Round 1 reasoning
    if _DEBATE_ENGINE_AVAILABLE:
        store_council_reasoning(db, session_id, 1, round1_results)

    # ── Round 2: debate (personas critique each other before finalizing) ───────
    # Fires if: debate engine available, enabled, 2+ active personas, not crisis.
    # Crisis routing always uses Round 1 output only — no delay for debate in crisis.
    final_results = round1_results.copy()

    debate_ran = False
    if (
        _DEBATE_ENGINE_AVAILABLE
        and enable_debate
        and len(active_names) >= 2
        and intent_type != "crisis"
        and groq_available()
    ):
        try:
            debate_outputs = run_debate_round(
                user_input=user_input,
                round1_outputs=round1_results,
                personas=personas,
                active_names=active_names,
                intent_type=intent_type,
                groq_chat_fn=groq_chat,
                session_id=session_id
            )
            if debate_outputs:
                final_results = debate_outputs
                debate_ran    = True
                # Store Round 2 reasoning
                store_council_reasoning(db, session_id, 2, debate_outputs)
        except Exception:
            # Debate failure is non-fatal — fall back to Round 1 output
            final_results = round1_results.copy()

    # ── Assemble ordered output ───────────────────────────────────────────────
    ordered_output = []
    if lead_name in final_results:
        ordered_output.append((lead_name, final_results[lead_name]))
    for name in active_names:
        if name in final_results and name != lead_name:
            ordered_output.append((name, final_results[name]))

    _write_persona_memories(db, ordered_output)

    # Behavioral tag integration — tags saved to MARLOW memory so PatternEngine
    # reads them on future sessions. Previously extracted and discarded.
    try:
        tags = extract_behavioral_tags(user_input)
        if tags:
            tag_summary = "Emotional tags: " + ", ".join(tags[:8])
            db.save_persona_memory(
                persona_name="MARLOW",
                summary=tag_summary,
                risk_score=3,
                confidence_score=6,
                decision="Auto-tagged"
            )
    except Exception:
        pass

    # Build silent persona summaries for MARLOW synthesis
    silent_context = _build_silent_persona_summaries(
        silent_names, personas, user_input, shared_ctx,
        oryn_silent_output=oryn_silent_output if oryn_ran_silently else ""
    )

    # Release council active flag — background runner can resume
    _set_council_active(False)

    return {
        "intent_type":       intent_type,
        "lead":              lead_name,
        "responses":         ordered_output,
        "raw":               final_results,
        "round1_raw":        round1_results,
        "silent_names":      silent_names,
        "silent_context":    silent_context,
        "oryn_ran_silently": oryn_ran_silently,
        "debate_ran":        debate_ran,
        "session_id":        session_id
    }


def build_synthesis(
    council_output: dict,
    user_input: str,
    db: DatabaseManager,
    silent_context: str = "",
    personas: list = None
) -> str:
    """
    Upgraded synthesis with:
    1. Structured sections: core problem → behavioral signals → council conflicts →
       strategic recommendation → risk warnings
    2. Contradiction signals explicitly wired in
    3. Prior reasoning history from council_reasoning_log as meta-context
    4. Debate status awareness — MARLOW knows if debate ran
    5. Persona weighting by intent type
    """
    if personas:
        excluded = {p["name"] for p in personas if p.get("excluded_from_synthesis", False)}
    else:
        excluded = {"MORRO"}

    synthesis_responses = [
        (name, response)
        for name, response in council_output["responses"]
        if name not in excluded
    ]

    persona_responses = "\n\n".join([
        f"{name}:\n{response}" for name, response in synthesis_responses
    ])

    if not groq_available():
        return ""

    intent_type = council_output.get("intent_type", "question")
    debate_ran  = council_output.get("debate_ran", False)

    # ── Persona weighting context ─────────────────────────────────────────────
    # Tells MARLOW which perspective to weight more heavily in synthesis.
    # Does not override — it informs. MARLOW still forms its own position.
    weight_note = ""
    if intent_type == "vent":
        weight_note = "Weight note: SEREN's read leads. ALDRIC's is supporting context."
    elif intent_type == "strategy":
        weight_note = "Weight note: ALDRIC's strategic analysis leads. SEREN's emotional read is supporting context."
    elif intent_type == "crisis":
        weight_note = "Weight note: SEREN leads. ORYN's biological read is essential grounding."
    elif intent_type == "question":
        # Route weight by input content — detect biological vs strategic lean
        lower = user_input.lower()
        if any(w in lower for w in ["sleep", "energy", "tired", "foggy", "substance", "body", "health"]):
            weight_note = "Weight note: ORYN's biological read leads on this input type."
        elif any(w in lower for w in ["money", "business", "plan", "strategy", "goal", "decision"]):
            weight_note = "Weight note: ALDRIC's strategic analysis leads on this input type."

    # ── Meta-reasoning context block ──────────────────────────────────────────
    # Sources: pattern signal, stalled plans, last synthesis, prior reasoning patterns.
    meta_parts = []

    # 1. Cached pattern signal — avoids rerunning PatternEngine
    try:
        cached_pattern = db.get_context_cache("pattern_ctx")
        if cached_pattern:
            # Extract just first 200 chars as a signal hint — not the full block
            signal_snippet = cached_pattern[:200].replace("\n", " ").strip()
            meta_parts.append(f"Current behavioral signal: {signal_snippet}...")
        else:
            engine     = PatternEngine(db)
            insights   = engine.synthesize_master_insights()
            top_signal = insights.get("top_signal", "") or insights.get("primary_pattern", "")
            if top_signal:
                meta_parts.append(f"Current behavioral signal: {str(top_signal)[:200]}")
    except Exception:
        pass

    # 2. Stalled execution steps
    try:
        from core.strategy_planner import get_stalled_steps
        stalled = get_stalled_steps(db, stall_days=7)
        if stalled:
            stall_names = list({s["goal_title"] for s in stalled})[:3]
            meta_parts.append(f"Stalled execution: {', '.join(stall_names)}")
    except Exception:
        pass

    # 3. Last synthesis outcome — what MARLOW said last session
    try:
        last_synth = db.conn.execute(
            "SELECT content FROM logs WHERE sync_type = 'synthesis' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if last_synth:
            snippet = str(last_synth[0])[:150].replace("\n", " ")
            meta_parts.append(f"Last synthesis: {snippet}...")
    except Exception:
        pass

    # 4. Prior council reasoning patterns — what has the council argued recently?
    # Only surfaces if debate ran, to avoid adding noise on lightweight sessions.
    if debate_ran and _DEBATE_ENGINE_AVAILABLE:
        try:
            for pname in ["ALDRIC", "SEREN", "ORYN"]:
                pattern = get_persona_reasoning_patterns(db, pname, lookback_days=30)
                if pattern:
                    meta_parts.append(pattern[:200])
                    break  # One persona summary is enough context
        except Exception:
            pass

    # 5. Contradiction signals — behavioral gap between stated intentions and actions
    try:
        from core.contradiction_engine import build_contradiction_context
        contra_ctx = build_contradiction_context(db, max_chars=300)
        if contra_ctx:
            meta_parts.append(f"Contradiction signal: {contra_ctx[:200]}")
    except Exception:
        pass

    meta_block = ""
    if meta_parts:
        meta_block = "\nMeta-context:\n" + "\n".join(f"- {p}" for p in meta_parts)

    debate_note = ""
    if debate_ran:
        debate_note = "\nNote: The council completed a debate round. These are final positions after critique and revision."

    synthesis_prompt = f"""You are MARLOW — the sovereign intelligence that oversees the council.

The original input:
\"{user_input}\"

Intent type: {intent_type.upper()}
{weight_note}
{debate_note}

Council final positions:
{persona_responses}

{silent_context}
{meta_block}

Produce a structured synthesis using these sections:

CORE PROBLEM: One sentence identifying what is actually at stake — not what was asked, but what is really happening.

BEHAVIORAL SIGNALS: One sentence on what the operator's behavioral data (patterns, trends, contradictions) reveals about this situation.

COUNCIL CONFLICTS: If personas disagreed, name the conflict in one sentence. If they agreed, note what the consensus missed.

STRATEGIC RECOMMENDATION: One to two sentences. The specific move. Not a suggestion — a recommendation.

RISK WARNING: One sentence on the most likely way this goes wrong.

Rules:
- Each section is one sentence only unless specified
- Do not repeat what personas already said — add the layer they couldn't see
- Speak as sovereign intelligence — direct, no filler, no opener
- If intent was vent, BEHAVIORAL SIGNALS and RISK WARNING carry the weight
- If intent was crisis, skip STRATEGIC RECOMMENDATION — replace with IMMEDIATE GROUNDING
- No section headers in output — write as flowing intelligence, not a form"""

    try:
        time.sleep(1.5)
        result = groq_chat(
            messages=[{"role": "user", "content": synthesis_prompt}],
            temperature=0.6, max_tokens=350, timeout=25
        )
        # Save synthesis to log for next session meta-reasoning
        try:
            db.conn.execute(
                "INSERT INTO logs (sync_type, content) VALUES ('synthesis', ?)",
                (result,)
            )
            db.conn.commit()
        except Exception:
            pass
        return result
    except Exception as e:
        return f"[Synthesis unavailable - {e}]"


def generate_weekly_report(db: DatabaseManager) -> str:
    now        = datetime.now()
    week_start = (now - timedelta(days=7)).strftime("%Y-%m-%d 00:00:00")
    week_end   = now.strftime("%Y-%m-%d 23:59:59")

    week_logs     = db.get_logs_for_week(week_start, week_end)
    week_metrics  = db.get_metrics_for_week(week_start, week_end)
    week_journals = db.get_journals_for_week(week_start, week_end)
    active_goals  = db.get_active_goals()
    profile       = db.get_static_profile()
    user_name     = _field(profile, 'name', 'User')

    def avg(lst): return round(sum(lst)/len(lst),1) if lst else None

    energy_vals  = [_row_get(m,"energy")        for m in week_metrics if _row_get(m,"energy")        is not None]
    mood_vals    = [_row_get(m,"mood")          for m in week_metrics if _row_get(m,"mood")          is not None]
    fog_vals     = [_row_get(m,"mental_fog")    for m in week_metrics if _row_get(m,"mental_fog")    is not None]
    impulse_vals = [_row_get(m,"impulse_drive") for m in week_metrics if _row_get(m,"impulse_drive") is not None]
    sleep_vals   = [_row_get(m,"sleep_hours")   for m in week_metrics if _row_get(m,"sleep_hours")   is not None]

    e_std = _std_dev(energy_vals)
    i_std = _std_dev(impulse_vals)

    metric_summary = f"""Energy avg: {avg(energy_vals) or 'no data'}/10  (volatility σ={e_std})
Mood avg: {avg(mood_vals) or 'no data'}/10
Brain fog avg: {avg(fog_vals) or 'no data'}/10
Impulse drive avg: {avg(impulse_vals) or 'no data'}/10  (volatility σ={i_std})
Sleep avg: {avg(sleep_vals) or 'no data'} hrs
Total syncs: {len(week_metrics)} | Journal entries: {len(week_journals)}"""

    goals_text = "No active goals."
    if active_goals:
        lines = []
        for g in active_goals:
            line = f"- {_field(g,'title','Untitled')}"
            if _row_get(g,"progress_note"):
                line += f" [Progress: {_row_get(g,'progress_note')}]"
            lines.append(line)
        goals_text = "\n".join(lines)

    # Include goal momentum
    try:
        scorer       = GoalMomentumScorer(db)
        momentum_ctx = scorer.build_momentum_context()
    except Exception:
        momentum_ctx = ""

    # Include deep pattern analysis
    try:
        engine       = PatternEngine(db)
        pattern_data = engine.synthesize_master_insights()
        pattern_ctx  = engine.format_insights_for_context(pattern_data)[:600]
    except Exception:
        pattern_ctx = ""

    # Include substance correlation summary
    try:
        substance_ctx = build_substance_context(db, max_chars=400)
    except Exception:
        substance_ctx = ""

    journal_text = "No journal entries this week."
    if week_journals:
        entries = [
            f"Entry {i+1}: {_field(j,'content','')[:200]}..."
            for i, j in enumerate(week_journals[:5])
        ]
        journal_text = "\n\n".join(entries)

    log_text = "No sync logs this week."
    if week_logs:
        log_text = "\n\n".join([
            f"[{_field(l,'timestamp','')} - {_row_get(l,'sync_type','sync').upper()}]\n"
            f"{_field(l,'content','')[:1000]}"
            for l in week_logs[-10:]
        ])

    monthly = get_monthly_pattern_as_context(db)

    if not groq_available():
        return "[Weekly report unavailable - no API key]"

    report_prompt = f"""You are MARLOW — the sovereign intelligence.

Generate a comprehensive weekly intelligence report for {user_name}.

--- METRIC DATA ---
{metric_summary}

--- GOAL MOMENTUM ---
{momentum_ctx}

--- DEEP PATTERN ANALYSIS (this week) ---
{pattern_ctx}

--- SUBSTANCE-OUTCOME DATA ---
{substance_ctx}

--- MONTHLY BEHAVIORAL PATTERNS ---
{monthly}

--- ACTIVE GOALS ---
{goals_text}

--- JOURNAL ENTRIES ---
{journal_text}

--- SYNC LOG SUMMARY ---
{log_text}

Write the report with these sections:
1. WEEK IN REVIEW — 2-3 sentences on the overall shape of the week
2. METRIC PATTERNS — What the numbers reveal. Be specific.
3. VOLATILITY ANALYSIS — Was this week stable or chaotic? What drove the variance?
4. BEHAVIORAL OBSERVATIONS — Patterns, cycles, emotional drivers, what generated happiness vs dread.
5. GOAL MOMENTUM — Which goals are alive in your behavior vs which are only in your head.
6. WHAT TO WATCH — Biggest risk or opportunity heading into next week.
7. MARLOW'S DIRECTIVE — One clear, direct instruction for the week ahead.

Tone: Intelligent, direct, honest. Not a cheerleader. Not a therapist. A strategic intelligence delivering the truth."""

    try:
        time.sleep(1.5)
        report_content = groq_chat(
            messages=[{"role": "user", "content": report_prompt}],
            temperature=0.5, max_tokens=1000, timeout=30
        )
        db.save_weekly_report(week_start, week_end, report_content)
        return report_content
    except Exception as e:
        return f"[Weekly report generation failed - {e}]"
