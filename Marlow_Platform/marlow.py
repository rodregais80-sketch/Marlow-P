"""
marlow.py — Main entry point / CLI

Fixes applied in this version:
- SWAP 8:  Vent routing — intent forced to "vent", classifier routing preserved if SEREN active
- SWAP 9:  Decision log prompt gated on decision-relevant language (prevents fatigue)
- SWAP 10: Pending review count — direct DB query, no DecisionTracker instantiation per loop
- SWAP 11: Predictive crash warning only fires if reactive alert did NOT already fire
- SWAP 6:  build_synthesis() now passes PERSONAS so excluded_from_synthesis field is respected
- Morning sync: intensity field added (was always NULL before)
- All v3 features preserved in full

Session 4 additions:
- --mode flag: loads persona set and config from config/personas_<mode>.py
  Defaults to personal. Options: personal | school | rehab
- Strategy planner wired into Goals menu (option P per goal)
- Structured logging: marlow_logger imported, error badge shown at startup
- Meta-reasoning synthesis: council_engine.build_synthesis enriched with
  behavioral feedback, plan status, and prior session outcome
"""

import os
import sys
import uuid
import argparse
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from core.database import DatabaseManager, PersonaChat
from core.council_engine import (
    classify_intent,
    run_council,
    build_synthesis,
    generate_weekly_report,
    generate_crash_alert,
    generate_predictive_crash_warning,
    maybe_generate_auto_weekly_report,
    should_generate_monthly_pattern,
    generate_monthly_pattern,
    generate_session_brief,
    is_task_request,
    execute_task
)
from core.decision_tracker import DecisionTracker, GoalMomentumScorer
from core.marlow_logger import log_info, log_warning, get_recent_error_count, get_log_path

# ── Mode / branch loader ──────────────────────────────────────────────────────
# --mode personal | school | rehab
# Defaults to personal if not specified.
# Each config file exports PERSONAS list and MODE_CONFIG dict.

def _load_mode(mode_arg: str) -> tuple:
    """
    Load PERSONAS and MODE_CONFIG from config/personas_<mode>.py.
    Falls back to root personas.py if config file not found.
    Returns (PERSONAS, MODE_CONFIG).
    """
    mode = (mode_arg or "personal").lower().strip()
    config_path = os.path.join(os.path.dirname(__file__), "config", f"personas_{mode}.py")

    if os.path.exists(config_path):
        import importlib.util
        spec   = importlib.util.spec_from_file_location(f"personas_{mode}", config_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        personas    = getattr(module, "PERSONAS", [])
        mode_config = getattr(module, "MODE_CONFIG", {"mode": mode})
        log_info("ModeLoader", f"Loaded mode '{mode}' from config/personas_{mode}.py — {len(personas)} personas")
        return personas, mode_config
    else:
        # Fall back to root personas.py
        log_warning("ModeLoader", f"config/personas_{mode}.py not found — falling back to root personas.py")
        from personas import PERSONAS as _P
        return _P, {"mode": "personal", "display_name": "Personal Intelligence",
                    "morro_enabled": True, "substance_tracking": True,
                    "crisis_hardened": False, "db_path": "vault.db"}

# Parse --mode before anything else
_parser = argparse.ArgumentParser(add_help=False)
_parser.add_argument("--mode", default="personal", help="Run mode: personal | school | rehab")
_args, _ = _parser.parse_known_args()

PERSONAS, MODE_CONFIG = _load_mode(_args.mode)
_ACTIVE_MODE = MODE_CONFIG.get("mode", "personal")
_MODE_DISPLAY = MODE_CONFIG.get("display_name", "Personal Intelligence")

# Tiered memory consolidator — graceful fallback if not yet installed
try:
    from core.memory_consolidator import maybe_consolidate_memory, run_pin_menu
    _CONSOLIDATOR_AVAILABLE = True
except ImportError:
    _CONSOLIDATOR_AVAILABLE = False
    def maybe_consolidate_memory(db): return []
    def run_pin_menu(db): print("  Memory consolidator not installed.")

# Use mode-specific DB if configured (school and rehab get separate vaults)
_db_path = MODE_CONFIG.get("db_path", "vault.db")
db = DatabaseManager(_db_path) if _db_path != "vault.db" else DatabaseManager()

# Strategy planner — graceful fallback if not yet installed
try:
    from core.strategy_planner import (
        generate_plan_for_goal,
        get_active_plan,
        mark_step_complete,
        mark_step_skipped,
        build_plan_context,
        get_stalled_steps,
        _ensure_strategy_tables
    )
    _STRATEGY_PLANNER_AVAILABLE = True
    _ensure_strategy_tables(db)
except ImportError:
    _STRATEGY_PLANNER_AVAILABLE = False
    def generate_plan_for_goal(*a, **k): return {"error": "Strategy planner not installed"}
    def get_active_plan(*a, **k): return None
    def mark_step_complete(*a, **k): return False
    def mark_step_skipped(*a, **k): return False
    def build_plan_context(*a, **k): return ""
    def get_stalled_steps(*a, **k): return []


def clear():
    os.system("cls" if os.name == "nt" else "clear")


def divider(char="─", length=60):
    print(char * length)


def header(title: str):
    divider("═")
    print(f"  {title}")
    divider("═")


def section(title: str):
    print()
    divider()
    print(f"  {title}")
    divider()


def print_council_response(council_output: dict, synthesis: str):
    intent = council_output["intent_type"].upper()
    lead   = council_output["lead"]
    print()
    print(f"  [ INTENT: {intent} ]  [ LEAD: {lead} ]")
    divider()
    for name, response in council_output["responses"]:
        print()
        print(f"  ◆ {name}")
        divider("·")
        print()
        for line in response.split("\n"):
            print(f"  {line}")
        print()
    if synthesis:
        divider("═")
        print()
        print("  ◆ MARLOW — SYNTHESIS")
        divider("·")
        print()
        for line in synthesis.split("\n"):
            print(f"  {line}")
        print()
        divider("═")


def _safe_int(value: str, default=None):
    if not value:
        return default
    clean = value.strip().split("/")[0].strip()
    clean = clean.replace("~", "").replace(">", "").replace("<", "").strip()
    try:
        return int(float(clean))
    except (ValueError, TypeError):
        return default


def _safe_float(value: str, default=None):
    if not value:
        return default
    clean = value.strip().split("/")[0].strip()
    clean = clean.replace("~", "").replace(">", "").replace("<", "").strip()
    try:
        return float(clean)
    except (ValueError, TypeError):
        return default


def _run_post_sync_crash_check():
    # SWAP 11 logic applied here too: reactive first, predictive only if reactive silent
    alert = generate_crash_alert(db)
    if alert:
        print()
        print(alert)
    if not alert:
        predictive = generate_predictive_crash_warning(db)
        if predictive:
            print()
            print(predictive)


def run_startup_sequence():
    # ── Tiered memory consolidation ───────────────────────────────────────
    if _CONSOLIDATOR_AVAILABLE:
        try:
            consolidation_actions = maybe_consolidate_memory(db)
            if consolidation_actions:
                print()
                divider("·")
                print("  [ MARLOW ] Memory consolidation ran:")
                for action in consolidation_actions:
                    print(f"  → {action}")
                divider("·")
                print()
        except Exception:
            pass

    # ── Contradiction engine: assess pending intentions (0 Groq calls) ───
    try:
        from core.contradiction_engine import assess_pending_intentions
        assess_pending_intentions(db)
    except Exception:
        pass

    # ── Streak tracker: update today's execution status (0 Groq calls) ───
    try:
        from core.streak_tracker import assess_today
        assess_today(db)
    except Exception:
        pass

    # SWAP 11: Reactive crash alert fires first.
    alert = generate_crash_alert(db)
    if alert:
        print(alert)

    if not alert:
        predictive = generate_predictive_crash_warning(db)
        if predictive:
            print(predictive)

    # Tier 2: Auto weekly report
    try:
        auto_report = maybe_generate_auto_weekly_report(db)
        if auto_report and not auto_report.startswith("["):
            print()
            divider("═")
            print()
            print("  ◆ MARLOW AUTO-GENERATED WEEKLY REPORT")
            divider("·")
            print()
            for line in auto_report.split("\n"):
                print(f"  {line}")
            print()
            divider("═")
            print()
    except Exception:
        pass

    # ── Weekly pre-mortem (1 Groq call, fires Monday or if 10+ days) ────
    try:
        from core.weekly_premortem import should_run_premortem, generate_weekly_premortem
        if should_run_premortem(db):
            premortem = generate_weekly_premortem(db)
            if premortem and not premortem.startswith("["):
                print()
                divider("·")
                print("  ◆ ALDRIC — WEEKLY PRE-MORTEM")
                divider("·")
                print()
                for line in premortem.split("\n"):
                    if line.strip():
                        print(f"  {line.strip()}")
                print()
                divider("·")
                print()
    except Exception:
        pass

    # Tier 1: Pending decision reviews
    try:
        tracker = DecisionTracker(db)
        pending = tracker.get_pending_reviews()
        if pending:
            print()
            divider("·")
            print(f"  ◉ {len(pending)} decision(s) are due for retrospective rating.")
            print("    Select option 10 from the main menu to rate them.")
            divider("·")
            print()
    except Exception:
        pass

    # Monthly pattern update
    if should_generate_monthly_pattern(db):
        try:
            row_count = db.conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
            if row_count >= 5:
                print("  [ MARLOW ] Updating pattern memory...")
                generate_monthly_pattern(db)
                print("  [ MARLOW ] Pattern memory updated.\n")
        except Exception:
            pass

    # Session brief
    brief = generate_session_brief(db)
    if brief:
        print(brief)


def run_first_time_intake():
    clear()
    header("MARLOW PLATFORM — FIRST TIME SETUP")
    print()
    print("  Before the council can work, we need to know who you are.")
    print("  This takes 5-10 minutes. It only happens once.")
    print("  Everything stays local on your device.")
    print()
    input("  Press ENTER to begin...")

    section("PART 1 — BASIC PROFILE")
    print()
    profile = {}
    profile["name"]               = input("  Your name (or what you want to be called): ").strip()
    profile["age"]                = input("  Your age: ").strip()
    profile["location"]           = input("  Your city / region: ").strip()
    profile["occupation"]         = input("  What do you do for work (or what are you working toward): ").strip()
    profile["primary_goal"]       = input("  What is your #1 goal right now: ").strip()
    profile["biggest_challenge"]  = input("  What is the biggest thing in your way right now: ").strip()
    print()
    print("  How do you prefer to be supported when you're struggling?")
    print("  1. Just listen — I need to vent, don't fix it")
    print("  2. Be direct — tell me what to do")
    print("  3. Both — read the room")
    choice = input("  Choose (1/2/3): ").strip()
    style_map = {"1": "listener", "2": "directive", "3": "adaptive"}
    profile["support_style"]      = style_map.get(choice, "adaptive")
    profile["additional_context"] = input("  Anything else the council should know about you upfront: ").strip()
    db.save_static_profile(profile)

    section("PART 2 — YOUR STORY")
    print()
    print("  The more honest you are here, the smarter the system gets.")
    print("  Nothing leaves this device.")
    print()
    history = {}
    history["background"]            = input("  Describe your background in a few sentences: ").strip()
    history["significant_events"]    = input("  What significant events have shaped who you are: ").strip()
    history["current_struggles"]     = input("  What are you struggling with right now (be specific): ").strip()
    history["current_strengths"]     = input("  What are your actual strengths: ").strip()
    history["relationship_status"]   = input("  Relationship status / key relationships: ").strip()
    history["support_network"]       = input("  Who do you have in your corner right now: ").strip()
    history["mental_health_history"] = input("  Any mental health history worth knowing: ").strip()
    history["substance_history"]     = input("  Any substance use history (optional): ").strip()
    history["goals_longterm"]        = input("  Where do you want to be in 3 years: ").strip()
    history["additional_context"]    = input("  Anything else important about your life right now: ").strip()
    db.save_life_history(history)

    print()
    divider("═")
    print()
    print(f"  Profile saved. The council is ready, {profile['name']}.")
    print()
    divider("═")
    input("  Press ENTER to continue to the main menu...")


# ── Sync modes ────────────────────────────────────────────────────────────────

def run_morning_sync():
    section("MORNING SYNC")
    print()
    data = {}
    data["sleep_hours"]     = input("  Hours of sleep: ").strip()
    data["sleep_quality"]   = input("  Sleep quality (1-10): ").strip()
    data["mental_fog"]      = input("  Brain fog level right now (1-10): ").strip()
    data["physical_state"]  = input("  Physical state (how does your body feel): ").strip()
    data["fuel"]            = input("  What have you had to eat/drink so far: ").strip()
    data["mental_state"]    = input("  Mental/emotional state right now: ").strip()
    data["morning_routine"] = input("  Did you complete a morning routine (yes/no/partial): ").strip()
    data["intensity"]       = input("  Work intensity so far (1-10, or skip): ").strip()  # FIX: was missing
    data["todays_focus"]    = input("  What is your #1 focus for today: ").strip()
    data["risk_ahead"]      = input("  Any risks or threats to today you can see already: ").strip()
    data["impulse_drive"]   = input("  Impulse drive — urge to chase something new (1-10): ").strip()
    data["chaos_pull"]      = input("  Chaos pull — urge to blow something up (1-10): ").strip()
    data["additional"]      = input("  Anything else worth logging: ").strip()

    log_content = "\n".join([f"{k}: {v}" for k, v in data.items() if v])
    db.save_log("morning", log_content)
    metrics = {
        "sleep_hours":   _safe_float(data["sleep_hours"]),
        "mental_fog":    _safe_int(data["mental_fog"]),
        "impulse_drive": _safe_int(data["impulse_drive"]),
        "intensity":     _safe_int(data["intensity"]),  # FIX: now saved
    }
    db.save_metrics("morning", metrics)
    print()
    print("  Morning sync logged.")
    _run_post_sync_crash_check()


def run_midday_sync():
    section("MIDDAY SYNC")
    print()
    data = {}
    data["completed_work"]   = input("  What have you completed so far today: ").strip()
    data["friction_hit"]     = input("  Any friction or blockers hit: ").strip()
    data["intensity"]        = input("  Work intensity so far (1-10): ").strip()
    data["cognitive_load"]   = input("  Cognitive load — how full is your head (1-10): ").strip()
    data["moves_made"]       = input("  Any strategic moves made (calls, emails, decisions): ").strip()
    data["mental_fog"]       = input("  Brain fog level right now (1-10): ").strip()
    data["current_fuel"]     = input("  What have you eaten/had to drink: ").strip()
    data["energy"]           = input("  Energy level right now (1-10): ").strip()
    data["mood"]             = input("  Mood right now (1-10): ").strip()
    data["impulse_drive"]    = input("  Impulse drive (1-10): ").strip()
    data["reckless_pull"]    = input("  Urge to do something reckless (1-10): ").strip()
    data["invisible_win"]    = input("  Any invisible win — something positive that happened: ").strip()
    data["market_sentiment"] = input("  How does the world feel today — opportunity or threat: ").strip()
    data["additional"]       = input("  Anything else worth logging: ").strip()

    log_content = "\n".join([f"{k}: {v}" for k, v in data.items() if v])
    db.save_log("midday", log_content)
    metrics = {
        "energy":        _safe_int(data["energy"]),
        "mood":          _safe_int(data["mood"]),
        "mental_fog":    _safe_int(data["mental_fog"]),
        "impulse_drive": _safe_int(data["impulse_drive"]),
        "intensity":     _safe_int(data["intensity"]),
    }
    db.save_metrics("midday", metrics)
    print()
    print("  Midday sync logged.")
    _run_post_sync_crash_check()


def run_evening_sync():
    section("EVENING SYNC")
    print()
    data = {}
    data["completed_work"]  = input("  What did you accomplish today: ").strip()
    data["friction"]        = input("  What slowed you down today: ").strip()
    data["intensity"]       = input("  Overall intensity of today (1-10): ").strip()
    data["strategic_move"]  = input("  Best strategic move you made today: ").strip()
    data["assets_gained"]   = input("  Any assets, skills, or relationships gained today: ").strip()
    data["brand_state"]     = input("  How did you represent yourself today: ").strip()
    data["invisible_win"]   = input("  Invisible win — something positive others didn't see: ").strip()
    data["chaos_activity"]  = input("  Did chaos win at any point today (yes/no/partial): ").strip()
    data["impulse_drive"]   = input("  Impulse drive right now (1-10): ").strip()
    data["mood"]            = input("  Mood right now (1-10): ").strip()
    data["energy"]          = input("  Energy level (1-10): ").strip()
    data["physical_state"]  = input("  Physical state — how does your body feel: ").strip()
    data["self_insight"]    = input("  One honest insight about yourself from today: ").strip()
    data["lesson"]          = input("  What did today teach you: ").strip()
    data["self_evaluation"] = input("  Rate your own performance today (1-10) and why: ").strip()
    data["pattern_noticed"] = input("  Did you notice any pattern in yourself today: ").strip()
    data["tomorrows_focus"] = input("  What is your #1 focus for tomorrow: ").strip()
    data["additional"]      = input("  Anything else worth logging: ").strip()

    log_content = "\n".join([f"{k}: {v}" for k, v in data.items() if v])
    db.save_log("evening", log_content)
    metrics = {
        "energy":        _safe_int(data["energy"]),
        "mood":          _safe_int(data["mood"]),
        "impulse_drive": _safe_int(data["impulse_drive"]),
        "intensity":     _safe_int(data["intensity"]),
    }
    db.save_metrics("evening", metrics)
    print()
    print("  Evening sync logged.")
    _run_post_sync_crash_check()


def run_question_mode():
    section("ASK A QUESTION")
    print()
    print("  Strategic, tactical, or analytical questions.")
    print("  The council deliberates. MARLOW synthesizes.")
    print("  Type EXIT to return. Type CLEAR to reset conversation memory.")
    print()

    session_id = str(uuid.uuid4())

    while True:
        print()
        user_input = input("  YOU: ").strip()

        if user_input.upper() == "EXIT":
            break
        if user_input.upper() == "CLEAR":
            db.clear_conversation(session_id)
            session_id = str(uuid.uuid4())
            print("  Conversation memory cleared.")
            continue
        if not user_input:
            continue

        if is_task_request(user_input):
            print()
            print("  [ This looks like a task request. ]")
            redirect = input("  Switch to Task Mode? (Y/N): ").strip().upper()
            if redirect == "Y":
                run_task_mode(prefill=user_input)
                continue

        print()
        print("  Classifying intent...")
        classification = classify_intent(user_input)
        intent_type    = classification.get("intent_type", "question")
        print(f"  Intent: {intent_type.upper()}")
        print()
        print("  Council is responding...")
        print()

        conversation_history = db.get_conversation_as_messages(session_id, limit=10)
        council_output       = run_council(user_input, db, PERSONAS, classification, conversation_history)

        # SWAP 6: pass PERSONAS so excluded_from_synthesis field is read properly
        synthesis = build_synthesis(
            council_output, user_input, db,
            silent_context=council_output.get("silent_context", ""),
            personas=PERSONAS
        )

        full_response_text = ""
        for name, response in council_output["responses"]:
            full_response_text += f"\n{name}:\n{response}\n"
        if synthesis:
            full_response_text += f"\nMARLOW SYNTHESIS:\n{synthesis}"

        db.save_conversation_turn(session_id, "user", user_input, "question")
        assistant_turn = synthesis if synthesis else full_response_text[:500]
        db.save_conversation_turn(session_id, "assistant", assistant_turn, "question")
        db.save_council_session(user_input, intent_type, full_response_text)

        print_council_response(council_output, synthesis)

        # SWAP 9: Decision log prompt only fires when decision-relevant language is detected.
        # Prevents prompt fatigue from firing after every casual exchange.
        _decision_signals = [
            "should i", "decide", "decision", "choose", "going with", "going to",
            "plan to", "commit", "invest", "quit", "leave", "start", "sign", "buy",
            "sell", "hire", "fire", "move", "drop", "take the", "accept", "refuse"
        ]
        _input_lower  = user_input.lower()
        _is_decision  = any(sig in _input_lower for sig in _decision_signals)

        if _is_decision:
            print()
            print("  [ Decision language detected — log for retrospective rating? ]")
            log_decision = input("  Log this decision? (Y/N): ").strip().upper()
        else:
            log_decision = "N"

        if log_decision == "Y":
            decision_text = input("  Describe the decision in one sentence: ").strip()
            if decision_text:
                try:
                    latest_metrics = db.get_recent_metrics(limit=1)
                    state = {}
                    if latest_metrics:
                        m = latest_metrics[0]
                        state = {
                            "energy":  m["energy"],
                            "mood":    m["mood"],
                            "fog":     m["mental_fog"],
                            "impulse": m["impulse_drive"],
                            "sleep":   m["sleep_hours"]
                        }
                    tracker     = DecisionTracker(db)
                    decision_id = tracker.log_decision(decision_text, state)
                    print(f"  Decision logged (ID: {decision_id}). "
                          f"You'll be reminded to rate the outcome in 30 days.")
                except Exception as e:
                    print(f"  [Could not log decision: {e}]")


def run_vent_mode():
    section("VENT / PROCESS")
    print()
    print("  No structure needed. Say what's on your mind.")
    print("  SEREN leads. The council holds space.")
    print("  Type DONE on a new line when finished.")
    print()

    lines = []
    while True:
        line = input()
        if line.strip().upper() == "DONE":
            break
        lines.append(line)

    user_input = "\n".join(lines).strip()
    if not user_input:
        print("  Nothing entered. Returning to menu.")
        return

    journal_id = db.save_journal(user_input, "vent")

    # Extract stated intentions for contradiction engine (0 Groq calls)
    try:
        from core.contradiction_engine import save_intentions_from_entry
        save_intentions_from_entry(db, user_input, source_type="vent")
    except Exception:
        pass
    print()
    print("  Classifying intent...")
    classification = classify_intent(user_input)

    # SWAP 8: Force intent_type to "vent" regardless of classifier output.
    # Classifier may read factual language and return "question" — irrelevant in vent mode.
    # If classifier routed SEREN correctly, keep that routing. Otherwise fall back to vent defaults.
    classification["intent_type"] = "vent"
    if classification.get("routing", {}).get("SEREN") not in ("active", "silent"):
        classification["routing"] = {
            "SEREN": "active", "ALDRIC": "silent",
            "ORYN":  "silent", "MORRO":  "off"
        }
        classification["lead"] = "SEREN"
    intent_type = "vent"

    print(f"  Intent: VENT")
    print()
    print("  Council is reading...")
    print()

    council_output = run_council(user_input, db, PERSONAS, classification)

    # SWAP 6: pass PERSONAS so excluded_from_synthesis field is read
    synthesis = build_synthesis(
        council_output, user_input, db,
        silent_context=council_output.get("silent_context", ""),
        personas=PERSONAS
    )

    full_response_text = ""
    for name, response in council_output["responses"]:
        full_response_text += f"\n{name}:\n{response}\n"
    if synthesis:
        full_response_text += f"\nMARLOW SYNTHESIS:\n{synthesis}"

    db.update_journal_response(journal_id, full_response_text)
    db.save_council_session(user_input, intent_type, full_response_text)
    print_council_response(council_output, synthesis)
    input("  Press ENTER to return to menu...")


def run_journal_mode():
    section("JOURNAL / FREE WRITE")
    print()
    print("  Write whatever you need to. MARLOW classifies your intent and routes.")
    print("  Type DONE on a new line when finished.")
    print()

    lines = []
    while True:
        line = input()
        if line.strip().upper() == "DONE":
            break
        lines.append(line)

    user_input = "\n".join(lines).strip()
    if not user_input:
        print("  Nothing entered. Returning to menu.")
        return

    if is_task_request(user_input):
        print()
        print("  [ MARLOW detected a task request. ]")
        task_choice = input("  Build this as a deliverable? (Y/N): ").strip().upper()
        if task_choice == "Y":
            run_task_mode(prefill=user_input)
            return

    print()
    print("  Classifying intent...")
    classification = classify_intent(user_input)
    intent_type    = classification.get("intent_type", "question")
    print(f"  Intent: {intent_type.upper()} — {classification.get('notes','')}")
    print()

    journal_id     = db.save_journal(user_input, intent_type)

    # Extract stated intentions for contradiction engine (0 Groq calls)
    try:
        from core.contradiction_engine import save_intentions_from_entry
        save_intentions_from_entry(db, user_input, source_type="journal")
    except Exception:
        pass

    print("  Council is reading...")
    print()

    council_output = run_council(user_input, db, PERSONAS, classification)

    # SWAP 6: pass PERSONAS so excluded_from_synthesis field is read
    synthesis = build_synthesis(
        council_output, user_input, db,
        silent_context=council_output.get("silent_context", ""),
        personas=PERSONAS
    )

    full_response_text = ""
    for name, response in council_output["responses"]:
        full_response_text += f"\n{name}:\n{response}\n"
    if synthesis:
        full_response_text += f"\nMARLOW SYNTHESIS:\n{synthesis}"

    db.update_journal_response(journal_id, full_response_text)
    db.save_council_session(user_input, intent_type, full_response_text)
    print_council_response(council_output, synthesis)
    input("  Press ENTER to return to menu...")


def run_task_mode(prefill: str = None):
    section("TASK MODE")
    print()
    print("  Tell MARLOW what to build. Output saved as a .txt file.")
    print()

    if prefill:
        print(f"  Task: {prefill}")
        task = prefill
    else:
        task = input("  What do you want built?\n  >> ").strip()

    if not task or task.upper() == "EXIT":
        return

    print()
    print("  MARLOW is building your deliverable...")
    print()

    output, filepath = execute_task(task, db)

    print()
    divider("═")
    print()
    for line in output.split("\n"):
        print(f"  {line}")
    print()
    divider("═")

    if filepath:
        print(f"\n  Saved to: {filepath}")
    else:
        print("\n  [ File save failed — output displayed above only ]")

    print()
    input("  Press ENTER to return to menu...")


def _display_goal_plan(goal_id: int) -> None:
    """Show the active step plan for a goal, with step completion controls."""
    plan = get_active_plan(db, goal_id)
    if not plan:
        print("\n  No execution plan exists for this goal.")
        print("  Select P from the goal menu to generate one.\n")
        input("  Press ENTER to return...")
        return

    print()
    divider("─")
    print(f"  EXECUTION PLAN  (generated {plan['created_at'][:10]})")
    divider("─")
    print()

    status_symbols = {"pending": "○", "complete": "✓", "skipped": "–"}
    for step in plan["steps"]:
        sym   = status_symbols.get(step["status"], "○")
        stall = "  ⚠ STALLED" if step["status"] == "pending" else ""
        print(f"  [{sym}] Step {step['step_number']}: {step['title']}{stall}")
        if step["description"]:
            # Wrap description at 70 chars
            desc = step["description"]
            while desc:
                print(f"       {desc[:70]}")
                desc = desc[70:]
        if step["notes"]:
            print(f"       Note: {step['notes']}")
        print()

    divider("─")
    print()
    print("  C <step#>  →  Mark step complete")
    print("  K <step#>  →  Mark step skipped")
    print("  0          →  Back")
    print()

    while True:
        cmd = input("  > ").strip().upper()
        if cmd == "0":
            break
        parts = cmd.split()
        if len(parts) == 2 and parts[0] in ("C", "K") and parts[1].isdigit():
            target_num = int(parts[1])
            matched    = [s for s in plan["steps"] if s["step_number"] == target_num]
            if not matched:
                print(f"  Step {target_num} not found.")
                continue
            step_id = matched[0]["id"]
            notes   = input("  Notes (optional): ").strip()
            if parts[0] == "C":
                if mark_step_complete(db, step_id, notes):
                    print(f"  Step {target_num} marked complete.")
            else:
                if mark_step_skipped(db, step_id, notes):
                    print(f"  Step {target_num} marked skipped.")
            # Reload and redisplay
            print()
            _display_goal_plan(goal_id)
            return
        else:
            print("  Unrecognized command. Use: C 2  or  K 3  or  0")


def run_goals_mode():
    while True:
        section("GOALS")

        # Streak display
        try:
            from core.streak_tracker import format_streak_display
            streak_display = format_streak_display(db)
            if streak_display:
                print(streak_display)
                print()
        except Exception:
            pass

        # Tier 1: Show goal momentum scores
        try:
            scorer    = GoalMomentumScorer(db)
            scored    = scorer.score_all_goals(days=14)
            score_map = {g["goal_id"]: g for g in scored}
        except Exception:
            score_map = {}

        goals = db.get_all_goals()

        if not goals:
            print("  No goals set yet.")
        else:
            for g in goals:
                status_icon = "+" if g["status"] == "active" else "x" if g["status"] == "dropped" else "o"
                momentum    = score_map.get(g["id"], {})
                label       = momentum.get("label", "")
                score       = momentum.get("momentum_score", "")
                score_str   = f"  [MOMENTUM: {score}/100 — {label}]" if score != "" else ""
                stall       = "  ⚠ STALLING" if momentum.get("stall_flag") else ""
                print(f"  [{status_icon}] #{g['id']} — {g['title']}  [{g['status'].upper()}]{stall}")
                if score_str:
                    print(f"       {score_str}")
                if g.get("description"):
                    print(f"       {g['description']}")
                if g.get("progress_note"):
                    print(f"       Progress: {g['progress_note']}")
                if g.get("target_date"):
                    print(f"       Target: {g['target_date']}")

                # Show plan progress inline if a plan exists
                if _STRATEGY_PLANNER_AVAILABLE:
                    plan = get_active_plan(db, g["id"])
                    if plan:
                        total    = len(plan["steps"])
                        complete = sum(1 for s in plan["steps"] if s["status"] == "complete")
                        pct      = round((complete / total) * 100) if total else 0
                        next_s   = next((s for s in plan["steps"] if s["status"] == "pending"), None)
                        print(f"       Plan: {complete}/{total} steps ({pct}%)", end="")
                        if next_s:
                            print(f"  →  Next: {next_s['title'][:50]}", end="")
                        print()
                print()

        print()
        print("  A        →  Add new goal")
        print("  U        →  Update goal progress")
        print("  S        →  Change goal status")
        if _STRATEGY_PLANNER_AVAILABLE:
            print("  P <id>   →  Generate ALDRIC execution plan for a goal")
            print("  V <id>   →  View / update execution plan steps")
        print("  0        →  Back")
        print()
        choice = input("  Select: ").strip().upper()

        if choice == "0":
            break
        elif choice == "A":
            print()
            title       = input("  Goal title: ").strip()
            if not title:
                continue
            description = input("  Description (optional): ").strip()
            target_date = input("  Target date (optional, e.g. 2026-06-01): ").strip()
            db.save_goal(title, description, target_date)
            print(f"\n  Goal saved: {title}")
            input("  Press ENTER to continue...")
        elif choice == "U":
            print()
            goal_id = input("  Enter goal # to update: ").strip()
            if not goal_id.isdigit():
                continue
            note = input("  Progress note: ").strip()
            db.update_goal_progress(int(goal_id), note)
            print("  Progress updated.")
            input("  Press ENTER to continue...")
        elif choice == "S":
            print()
            goal_id = input("  Enter goal # to update: ").strip()
            if not goal_id.isdigit():
                continue
            print("  1=active  2=completed  3=paused  4=dropped")
            status_map = {"1": "active", "2": "completed", "3": "paused", "4": "dropped"}
            s = input("  Select: ").strip()
            if s in status_map:
                db.update_goal_status(int(goal_id), status_map[s])
                print(f"  Status updated to: {status_map[s]}")
            input("  Press ENTER to continue...")
        elif _STRATEGY_PLANNER_AVAILABLE and choice.startswith("P"):
            # P <id> — generate plan
            parts = choice.split()
            goal_id_str = parts[1] if len(parts) > 1 else input("  Goal # to plan: ").strip()
            if not goal_id_str.isdigit():
                continue
            goal_id = int(goal_id_str)
            goal_row = next((g for g in goals if g["id"] == goal_id), None)
            if not goal_row:
                print(f"  Goal #{goal_id} not found.")
                input("  Press ENTER to continue...")
                continue
            print(f"\n  Asking ALDRIC to build a plan for: {goal_row['title']}")
            print("  Generating...")
            result = generate_plan_for_goal(goal_row, db)
            if result.get("error"):
                print(f"\n  Plan generation failed: {result['error']}")
            else:
                print(f"\n  Plan generated — {len(result['steps'])} steps")
                if result.get("summary"):
                    print(f"  ALDRIC: {result['summary']}")
                print()
                _display_goal_plan(goal_id)
        elif _STRATEGY_PLANNER_AVAILABLE and choice.startswith("V"):
            # V <id> — view/update steps
            parts = choice.split()
            goal_id_str = parts[1] if len(parts) > 1 else input("  Goal # to view: ").strip()
            if not goal_id_str.isdigit():
                continue
            _display_goal_plan(int(goal_id_str))


def run_weekly_report():
    section("WEEKLY REPORT")
    print()

    # Show latest pre-mortem if available
    try:
        from core.weekly_premortem import get_latest_premortem
        pm = get_latest_premortem(db)
        if pm:
            print(f"  Last pre-mortem: week of {pm['period_start'][:10]}")
    except Exception:
        pm = None

    latest = db.get_latest_weekly_report()
    if latest:
        print(f"  Last report generated: {latest['generated_at']}")
        print()
        print("  G  →  Generate new report for this week")
        print("  V  →  View last report")
        if pm:
            print("  P  →  View latest pre-mortem (ALDRIC week forecast)")
        print("  0  →  Back")
        print()
        choice = input("  Select: ").strip().upper()
        if choice == "0":
            return
        elif choice == "V":
            print()
            divider("═")
            print()
            for line in latest["report_content"].split("\n"):
                print(f"  {line}")
            print()
            divider("═")
            input("  Press ENTER to return...")
            return
        elif choice == "P" and pm:
            print()
            divider("·")
            print(f"  ALDRIC — WEEK OF {pm['period_start'][:10]}")
            divider("·")
            print()
            for line in pm["content"].split("\n"):
                if line.strip():
                    print(f"  {line.strip()}")
            print()
            divider("·")
            input("  Press ENTER to return...")
            return
        elif choice != "G":
            return
    else:
        print("  No report generated yet.")
        print()
        choice = input("  Generate weekly report now? (Y/N): ").strip().upper()
        if choice != "Y":
            return

    print()
    print("  Generating weekly report...")
    print()
    report = generate_weekly_report(db)
    print()
    divider("═")
    print()
    for line in report.split("\n"):
        print(f"  {line}")
    print()
    divider("═")
    input("  Press ENTER to return to menu...")


def run_decision_log():
    """
    Tier 1: Decision logging and retrospective rating.
    Operators log decisions with current state context.
    After 30 days, they rate the outcome.
    The system builds a personal decision quality fingerprint.
    """
    tracker = DecisionTracker(db)

    while True:
        section("DECISION LOG")
        print()
        print("  Log decisions now. Rate their outcomes in 30 days.")
        print("  The system builds your personal decision quality map.")
        print()

        # Show pending reviews first
        pending = tracker.get_pending_reviews()
        if pending:
            divider("·")
            print(f"  {len(pending)} DECISION(S) DUE FOR RATING:")
            for p in pending:
                print(f"    #{p['id']} — {p['decision_text'][:60]}")
                print(f"           Logged: {p['timestamp'][:10]}")
            divider("·")
            print()

        # Show decision quality map if enough data
        dmap = tracker.build_decision_quality_map()
        if dmap.get("sufficient_data"):
            print("  YOUR DECISION QUALITY MAP:")
            for insight in dmap.get("insights", []):
                print(f"    ◉ {insight}")
            print()

        print("  L  →  Log a new decision")
        print("  R  →  Rate a past decision outcome")
        print("  V  →  View all logged decisions")
        print("  0  →  Back to menu")
        print()
        choice = input("  Select: ").strip().upper()

        if choice == "0":
            break

        elif choice == "L":
            print()
            print("  Describe the decision you are making or just made.")
            print("  Be specific. This is what you will rate in 30 days.")
            print()
            decision_text = input("  Decision: ").strip()
            if not decision_text:
                continue

            # Capture current state
            latest = db.get_recent_metrics(limit=1)
            state  = {}
            if latest:
                m = latest[0]
                state = {
                    "energy":  m["energy"], "mood": m["mood"],
                    "fog":     m["mental_fog"], "impulse": m["impulse_drive"],
                    "sleep":   m["sleep_hours"]
                }
            else:
                print("  No recent sync on file. Log your state manually:")
                state["energy"]  = _safe_int(input("  Energy right now (1-10): ").strip())
                state["mood"]    = _safe_int(input("  Mood right now (1-10): ").strip())
                state["impulse"] = _safe_int(input("  Impulse drive (1-10): ").strip())

            decision_id = tracker.log_decision(decision_text, state)
            if decision_id:
                print(f"\n  Decision #{decision_id} logged.")
                print("  You will be reminded to rate the outcome in 30 days.")
            else:
                print("  [Could not save decision]")
            input("  Press ENTER to continue...")

        elif choice == "R":
            print()
            if pending:
                print("  Decisions due for rating:")
                for p in pending:
                    print(f"    #{p['id']} — {p['decision_text'][:70]}")
                print()
                decision_id = input("  Enter decision # to rate: ").strip()
                if not decision_id.isdigit():
                    continue
                decision_id = int(decision_id)
                print()
                print("  Rate the outcome: 1 (disaster) to 10 (perfect)")
                score_str = input("  Outcome score: ").strip()
                score     = _safe_int(score_str)
                if score is None or score < 1 or score > 10:
                    print("  Invalid score. Enter a number 1-10.")
                    input("  Press ENTER to continue...")
                    continue
                notes = input("  Notes on what happened (optional): ").strip()
                tracker.rate_decision(decision_id, score, notes)
                print(f"\n  Outcome logged. The system will learn from this.")
                input("  Press ENTER to continue...")
            else:
                print("  No decisions currently due for rating.")
                input("  Press ENTER to continue...")

        elif choice == "V":
            all_decisions = tracker.get_all_decisions(limit=20)
            if not all_decisions:
                print("  No decisions logged yet.")
            else:
                print()
                for d in all_decisions:
                    rated     = f"Outcome: {d['outcome_score']}/10" if d["outcome_score"] else "Pending rating"
                    due       = f" | Due: {d['review_due_at'][:10]}" if not d["outcome_score"] else ""
                    print(f"  #{d['id']} [{d['timestamp'][:10]}] — {d['decision_text'][:50]}")
                    print(f"       {rated}{due}")
                    if d["outcome_score"]:
                        state_str = f"State at decision: E={d['state_energy']} M={d['state_mood']} Fog={d['state_fog']} Imp={d['state_impulse']}"
                        print(f"       {state_str}")
                    print()
            input("  Press ENTER to continue...")


def run_update_profile():
    section("UPDATE PROFILE")
    print()
    print("  1  →  Update Static Profile")
    print("  2  →  Update Life History")
    print("  0  →  Back")
    print()
    choice = input("  Select: ").strip()

    if choice == "1":
        confirm = input("  Continue? (Y/N): ").strip().upper()
        if confirm == "Y":
            _run_static_profile_update()
    elif choice == "2":
        confirm = input("  Continue? (Y/N): ").strip().upper()
        if confirm == "Y":
            _run_life_history_update()


def _run_static_profile_update():
    section("UPDATE STATIC PROFILE")
    print()
    existing = db.get_static_profile() or {}
    profile  = {}
    profile["name"]               = input(f"  Name [{existing.get('name','')}]: ").strip() or existing.get("name","")
    profile["age"]                = input(f"  Age [{existing.get('age','')}]: ").strip() or existing.get("age","")
    profile["location"]           = input(f"  Location [{existing.get('location','')}]: ").strip() or existing.get("location","")
    profile["occupation"]         = input(f"  Occupation [{existing.get('occupation','')}]: ").strip() or existing.get("occupation","")
    profile["primary_goal"]       = input(f"  Primary Goal [{existing.get('primary_goal','')}]: ").strip() or existing.get("primary_goal","")
    profile["biggest_challenge"]  = input(f"  Biggest Challenge [{existing.get('biggest_challenge','')}]: ").strip() or existing.get("biggest_challenge","")
    print("  Support style: 1=listener  2=directive  3=adaptive")
    style_choice = input(f"  Select [{existing.get('support_style','adaptive')}]: ").strip()
    style_map = {"1": "listener", "2": "directive", "3": "adaptive"}
    profile["support_style"]      = style_map.get(style_choice, existing.get("support_style","adaptive"))
    profile["additional_context"] = input(f"  Additional context [{existing.get('additional_context','')}]: ").strip() or existing.get("additional_context","")
    db.save_static_profile(profile)
    print()
    print("  Static profile updated.")
    input("  Press ENTER to continue...")


def _run_life_history_update():
    section("UPDATE LIFE HISTORY")
    print()
    existing = db.get_life_history() or {}
    history  = {}
    history["background"]            = input("  Background: ").strip() or existing.get("background","")
    history["significant_events"]    = input("  Significant events: ").strip() or existing.get("significant_events","")
    history["current_struggles"]     = input("  Current struggles: ").strip() or existing.get("current_struggles","")
    history["current_strengths"]     = input("  Current strengths: ").strip() or existing.get("current_strengths","")
    history["relationship_status"]   = input("  Relationship status: ").strip() or existing.get("relationship_status","")
    history["support_network"]       = input("  Support network: ").strip() or existing.get("support_network","")
    history["mental_health_history"] = input("  Mental health history: ").strip() or existing.get("mental_health_history","")
    history["substance_history"]     = input("  Substance history: ").strip() or existing.get("substance_history","")
    history["goals_longterm"]        = input("  Long-term goals: ").strip() or existing.get("goals_longterm","")
    history["additional_context"]    = input("  Additional context: ").strip() or existing.get("additional_context","")
    db.save_life_history(history)
    print()
    print("  Life history updated.")
    input("  Press ENTER to continue...")


def run_persona_chat():
    section("PERSONA CHAT")
    print()
    print("  Talk directly to one persona. One-on-one.")
    print("  They remember your prior sessions and ask questions.")
    print()

    if not PERSONAS:
        print("  No personas found.")
        input("  Press ENTER to return...")
        return

    print("  Available personas:")
    print()
    for i, p in enumerate(PERSONAS, 1):
        print(f"  {i}  →  {p['name']}  |  {p.get('role', '')}")
        print(f"       {p.get('description', '')}")
        print()

    print("  0  →  Back")
    print()
    choice = input("  Select persona: ").strip()

    if choice == "0":
        return
    if not choice.isdigit() or int(choice) < 1 or int(choice) > len(PERSONAS):
        print("  Invalid selection.")
        input("  Press ENTER to return...")
        return

    selected_persona = PERSONAS[int(choice) - 1]
    selected_key     = selected_persona["name"]
    system_prompt    = selected_persona.get(
        "system_prompt",
        f"You are {selected_key}. Respond honestly and directly. Ask questions. Talk like a person."
    )

    chat = PersonaChat(db=db, persona_name=selected_key, persona_system_prompt=system_prompt)

    clear()
    section(f"TALKING TO: {selected_key}")
    print()
    print(f"  {selected_persona.get('description', '')}")
    print()
    print("  Type your message and press ENTER.")
    print("  EXIT to end.  CLEAR to wipe session memory.")
    print()
    divider()
    print()

    while True:
        user_input = input("  YOU: ").strip()

        if not user_input:
            continue
        if user_input.upper() == "EXIT":
            print()
            print(f"  Conversation with {selected_key} ended.")
            print()
            input("  Press ENTER to return to menu...")
            break
        if user_input.upper() == "CLEAR":
            chat.clear_conversation()
            print()
            print("  Session memory cleared.")
            print()
            continue

        print()
        print(f"  {selected_key} is thinking...")
        print()
        response = chat.send_message(user_input)

        print(f"  {selected_key}:")
        divider("·")
        print()
        for line in response.split("\n"):
            print(f"  {line}")
        print()
        divider()
        print()


def run_pinned_memory():
    """
    Menu option 11 — Pinned Memory Manager.
    Allows the operator to pin permanent memories to specific personas.
    Pinned memories always surface in that persona's context regardless of age.
    """
    if not _CONSOLIDATOR_AVAILABLE:
        section("PINNED MEMORY")
        print()
        print("  Memory consolidator not installed.")
        print("  Copy memory_consolidator.py to core/ to enable this feature.")
        print()
        input("  Press ENTER to return...")
        return

    run_pin_menu(db)
    input("  Press ENTER to return to menu...")


def main_menu():
    profile      = db.get_static_profile()
    name         = profile["name"] if profile else "User"
    active_goals = db.get_active_goals()
    goal_count   = len(active_goals)
    now          = datetime.now().strftime("%A, %B %d — %I:%M %p")

    # SWAP 10: Direct DB query — no DecisionTracker instantiation per menu loop.
    # DecisionTracker instantiation every loop was a wasted object creation.
    try:
        pending_reviews = len(db.conn.execute(
            "SELECT id FROM decision_log WHERE outcome_score IS NULL "
            "AND review_due_at <= ? LIMIT 20",
            (datetime.now().isoformat(),)
        ).fetchall())
    except Exception:
        pending_reviews = 0

    # Error badge — show if subsystem failures logged in last 24h
    try:
        err_count = get_recent_error_count(hours=24)
    except Exception:
        err_count = 0

    print()
    divider("═")
    mode_tag = f"  [{_MODE_DISPLAY.upper()}]" if _ACTIVE_MODE != "personal" else ""
    print(f"  MARLOW PLATFORM  |  {name}  |  {now}{mode_tag}")
    if err_count:
        print(f"  ⚠  {err_count} subsystem error(s) in last 24h  —  see {get_log_path()}")
    divider("═")
    print()
    print("  1  Daily Sync")
    print("       Log your morning, midday, or evening state.")
    print()
    print("  2  Ask a Question")
    print("       Strategic or tactical. ALDRIC leads. MARLOW synthesizes.")
    print()
    print("  3  Vent / Process")
    print("       Say what's on your mind. SEREN leads.")
    print()
    print("  4  Task Mode")
    print("       Tell MARLOW to build something. Saves to file.")
    print()
    print("  5  Journal / Free Write")
    print("       Unstructured. MARLOW routes automatically.")
    print()
    if goal_count > 0:
        print(f"  6  Goals  [{goal_count} active]")
    else:
        print("  6  Goals")
    print("       Set and track goals with live momentum scoring.")
    print()
    print("  7  Weekly Report")
    print("       7-day intelligence summary.")
    print()
    print("  8  Update Profile")
    print()
    print("  9  Persona Chat")
    print("       One-on-one with a single persona.")
    print()
    if pending_reviews > 0:
        print(f"  10 Decision Log  [{pending_reviews} REVIEW(S) DUE]")
    else:
        print("  10 Decision Log")
    print("       Log and retrospectively rate decisions.")
    print("       Builds your personal decision quality fingerprint.")
    print()
    print("  11 Pinned Memory")
    print("       Pin permanent memories to specific personas.")
    print("       These always surface in context regardless of how old they are.")
    print()
    print("  0  Exit")
    print()
    divider()

    return input("  Select: ").strip()


def main():
    profile = db.get_static_profile()
    if not profile:
        run_first_time_intake()

    run_startup_sequence()

    while True:
        choice = main_menu()

        if choice == "1":
            print()
            print("  Which sync?  1=Morning  2=Midday  3=Evening")
            print()
            sync_choice = input("  Select: ").strip()
            if sync_choice == "1":
                run_morning_sync()
                input("  Press ENTER to return to menu...")
            elif sync_choice == "2":
                run_midday_sync()
                input("  Press ENTER to return to menu...")
            elif sync_choice == "3":
                run_evening_sync()
                input("  Press ENTER to return to menu...")
            else:
                print("  Enter 1, 2, or 3.")

        elif choice == "2":
            run_question_mode()
        elif choice == "3":
            run_vent_mode()
        elif choice == "4":
            run_task_mode()
        elif choice == "5":
            run_journal_mode()
        elif choice == "6":
            run_goals_mode()
        elif choice == "7":
            run_weekly_report()
        elif choice == "8":
            run_update_profile()
        elif choice == "9":
            run_persona_chat()
        elif choice == "10":
            run_decision_log()
        elif choice == "11":
            run_pinned_memory()
        elif choice == "0":
            print()
            print("  Session closed.")
            print()
            sys.exit(0)
        else:
            print("  Invalid selection.")


if __name__ == "__main__":
    main()