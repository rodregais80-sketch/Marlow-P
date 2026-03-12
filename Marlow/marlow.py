# marlow.py
import uuid
import os
from core.database import DatabaseManager
from core.council_engine import (
    CouncilEngine,
    classify_intent,
    generate_weekly_report,
    generate_crash_alert,
    generate_monthly_pattern,
    generate_session_brief,
    should_generate_monthly_pattern,
    execute_task,
    is_task_request
)
from core.personas import COUNCIL, choose_personas
import datetime


# ================= LOGGING SYSTEM ================= #

def log_to_file(text: str):
    with open("council_responses.txt", "a", encoding="utf-8") as f:
        f.write(text + "\n")


def print_and_log(text: str):
    print(text)
    log_to_file(text)


# ================= HELPER ================= #

def ask(prompt, numeric=False, scale=False):
    if scale:
        prompt += " (1-10)"
    val = input(f"\n{prompt}: ").strip()
    if numeric and val:
        try:
            return int(val)
        except ValueError:
            return None
    return val if val else "Not logged"


def ask_open(prompt):
    val = input(f"\n{prompt}\n> ").strip()
    return val if val else "Not provided"


def save_sync(db, content):
    timestamp = datetime.datetime.now().isoformat()
    db.cursor.execute(
        "INSERT INTO logs (timestamp, content) VALUES (?, ?)",
        (timestamp, content)
    )
    db.conn.commit()
    print("\n" + "=" * 60)
    print("Sync saved.")
    print("=" * 60 + "\n")


# ================= STARTUP SEQUENCE ================= #

def run_startup_sequence(db):
    """
    Runs before the main menu on every launch.
    1. Crash alert (instant, no API)
    2. Monthly pattern (Groq, only if >7 days old or missing)
    3. Session brief (Groq, every session)
    4. Mood check-in (if not done today)
    """

    # STEP 1 — Crash alert (pure Python, always runs)
    crash_alert = generate_crash_alert(db)
    if crash_alert:
        print(crash_alert)

    # STEP 2 — Monthly pattern (Groq, only when needed)
    if should_generate_monthly_pattern(db):
        row_count = db.cursor.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
        if row_count >= 5:
            print("[MARLOW] Updating monthly pattern memory...")
            generate_monthly_pattern(db)
            print("[MARLOW] Pattern memory updated.\n")

    # STEP 3 — Session brief (Groq, every launch)
    brief = generate_session_brief(db)
    if brief:
        print(brief)

    # STEP 4 — Mood check-in (once per day, fires automatically)
    today_checkin = db.get_todays_mood_checkin()
    if not today_checkin:
        run_mood_checkin(db)


# ================= FIRST RUN CHECK ================= #

def first_run_check(db):
    existing_profile = db.get_static_profile()
    existing_history = db.get_life_history()

    if not existing_profile:
        print("\n" + "=" * 60)
        print("MARLOW SYSTEM — FIRST RUN")
        print("=" * 60)
        print("\nNo profile found.")
        print("We'll start with your basic physical profile.")
        print("This is stored permanently and gives the system biological context.\n")
        input("Press Enter to begin...")
        build_static_profile(db)

    if not existing_history:
        print("\nNow let's build your full life history profile.")
        print("This is the deeper psychological and biographical context.\n")
        input("Press Enter to continue...")
        build_life_history(db)


# ================= STATIC PROFILE ================= #

def build_static_profile(db):
    print("\n" + "=" * 60)
    print("STATIC PROFILE")
    print("Basic facts about you. Press Enter to skip any field.")
    print("This never changes unless you update it manually.")
    print("=" * 60)

    name        = ask("Your first name")
    age         = ask("Age")
    sex         = ask("Biological sex (Male / Female / Other)")
    height      = ask("Height (e.g. 5'11 or 180cm)")
    weight      = ask("Weight (e.g. 185lbs or 84kg)")
    fitness     = ask("Fitness baseline (Sedentary / Light / Moderate / Active / Athletic)")
    conditions  = ask("Any known medical conditions or diagnoses (or None)")
    medications = ask("Any regular medications or supplements (or None)")
    location    = ask("City / Region you live in")
    occupation  = ask("Primary occupation or role")

    timestamp = datetime.datetime.now().isoformat()
    content = f"""
=== STATIC PROFILE | {timestamp} ===

Name:               {name}
Age:                {age}
Biological Sex:     {sex}
Height:             {height}
Weight:             {weight}
Fitness Baseline:   {fitness}
Medical Conditions: {conditions}
Medications:        {medications}
Location:           {location}
Occupation:         {occupation}
""".strip()

    db.save_static_profile(content)

    print("\n" + "=" * 60)
    print("Static profile saved.")
    print("=" * 60 + "\n")


# ================= LIFE HISTORY ================= #

def build_life_history(db):
    print("\n" + "=" * 60)
    print("LIFE HISTORY PROFILE")
    print("Answer as much or as little as you want.")
    print("Press Enter to skip any field.")
    print("This is stored permanently and never deleted.")
    print("=" * 60)

    print("\n--- Who You Are ---")
    background     = ask_open("Where did you grow up and what was your home life like?")
    family         = ask_open("Describe your family dynamic growing up.")
    early_life     = ask_open("What is the earliest memory or event that shaped who you are?")

    print("\n--- Key Life Events ---")
    turning_points = ask_open("What are the biggest turning points in your life — good or bad?")
    failures       = ask_open("What failures or losses have hit you the hardest?")
    wins           = ask_open("What are you most proud of in your life so far?")

    print("\n--- Who You Are Now ---")
    identity       = ask_open("How would you describe yourself honestly — not how others see you?")
    relationships  = ask_open("Describe your relationship patterns — friendships, romantic, family.")
    trust          = ask_open("Who do you trust and why? Who have you stopped trusting and why?")

    print("\n--- What Drives You ---")
    motivators     = ask_open("What gets you out of bed in the morning — real answer, not the polished one?")
    fears          = ask_open("What are your deepest fears?")
    ambition       = ask_open("What does success look like to you — in 5 years, in 20 years?")

    print("\n--- Patterns and Struggles ---")
    patterns       = ask_open("What patterns do you keep repeating that you wish you could break?")
    triggers       = ask_open("What situations, people, or feelings trigger your worst behavior?")
    substances     = ask_open("Describe your history with substances — when it started, why, what it does for you.")
    rock_bottom    = ask_open("What has been your lowest point and what did it teach you?")

    print("\n--- What You Want From This System ---")
    goal           = ask_open("What do you want Marlow to help you with most?")
    blind_spots    = ask_open("What do you think your biggest blind spot is?")
    message        = ask_open("If you could tell the system one thing about yourself that explains everything, what would it be?")

    timestamp = datetime.datetime.now().isoformat()
    content = f"""
=== LIFE HISTORY PROFILE | {timestamp} ===

WHO YOU ARE:
  Background: {background}
  Family Dynamic: {family}
  Early Formative Event: {early_life}

KEY LIFE EVENTS:
  Turning Points: {turning_points}
  Hardest Failures/Losses: {failures}
  Greatest Wins: {wins}

IDENTITY:
  Self-Description: {identity}
  Relationship Patterns: {relationships}
  Trust: {trust}

WHAT DRIVES YOU:
  Core Motivators: {motivators}
  Deepest Fears: {fears}
  Vision of Success: {ambition}

PATTERNS AND STRUGGLES:
  Repeating Patterns: {patterns}
  Triggers: {triggers}
  Substance History: {substances}
  Lowest Point: {rock_bottom}

WHAT YOU WANT FROM THIS SYSTEM:
  Primary Goal: {goal}
  Blind Spot: {blind_spots}
  The One Thing: {message}
""".strip()

    db.save_life_history(content)

    print("\n" + "=" * 60)
    print("Life history saved.")
    print("=" * 60 + "\n")


# ================= JOURNAL / VENT ================= #

def journal_entry(db, engine, force_vent=False):
    print("\n" + "=" * 60)
    print("JOURNAL / VENT")
    print("Type freely. No structure. No questions.")
    print("Type END on its own line when finished.")
    print("=" * 60 + "\n")

    lines = []
    while True:
        line = input()
        if line.strip().upper() == "END":
            break
        lines.append(line)

    content = "\n".join(lines).strip()

    if not content:
        print("Nothing entered.")
        return

    db.save_journal(content)
    print("\nJournal saved.\n")

    # Auto-detect if this is a task request
    if not force_vent and is_task_request(content):
        print("[ Marlow detected a task request in your entry. ]")
        task_choice = input("Build this as a deliverable and save to file? (yes/no): ").strip().lower()
        if task_choice == "yes":
            print("\nBuilding your deliverable...\n")
            output, filepath = execute_task(content, db)
            print("\n" + "=" * 80)
            print()
            for line in output.split("\n"):
                print(f"  {line}")
            print()
            print("=" * 80)
            if filepath:
                print(f"\n  Saved to: {filepath}")
            input("\nPress Enter to return...")
            return

    choice = input("Do you want the council to read this and respond? (yes/no): ").strip().lower()

    if choice == "yes":
        print("\nCouncil is reading your entry...\n")

        journal_question = (
            "The operator has written the following journal entry. "
            "Read it carefully and respond from your domain perspective. "
            "Do not treat this as a tactical question — treat it as insight into their current state:\n\n"
            + content
        )

        results = engine.query(journal_question, skip_classifier=True)

        for r in results:
            print("\n" + "=" * 80)
            print_and_log(f"--- {r['name']} ---")
            print_and_log(r["analysis"])
            print_and_log(f"Risk: {r['risk']} | Confidence: {r['confidence']} | Decision: {r['decision']}")
            print("=" * 80 + "\n")

        consensus = engine.get_consensus_summary(results)
        consensus_text = (
            f"\nCOUNCIL CONSENSUS: {consensus['APPROVE']} APPROVE | "
            f"{consensus['REJECT']} REJECT | {consensus['CAUTION']} CAUTION"
        )
        print("\n" + "*" * 80)
        print_and_log(consensus_text)
        print("*" * 80 + "\n")

        recommendation = engine.get_collective_recommendation(results)
        print("\n" + "█" * 80)
        print_and_log(recommendation)
        print("█" * 80 + "\n")

        summary = engine.get_session_summary(results)
        if summary:
            print("\n" + "▓" * 80)
            print_and_log(summary)
            print("▓" * 80 + "\n")

    else:
        print("Saved silently.\n")


# ================= TASK MODE ================= #

def task_mode(db):
    print("\n" + "=" * 60)
    print("TASK MODE")
    print("Tell Marlow what to build. Be specific.")
    print("Output is saved as a .txt file in your Marlow folder.")
    print("Type EXIT to cancel.")
    print("=" * 60)
    print()
    print("  Examples:")
    print("  — Build me a daily morning routine checklist")
    print("  — Write me a cold call script for Penticton wineries")
    print("  — Make me a 30-day plan to hit $5K revenue")
    print("  — Draft a client proposal template for event activations")
    print()

    task = input("What do you want built?\n>> ").strip()

    if task.upper() == "EXIT" or not task:
        return

    print("\nMarlow is building your deliverable...\n")
    output, filepath = execute_task(task, db)

    print("\n" + "=" * 80)
    print()
    for line in output.split("\n"):
        print(f"  {line}")
    print()
    print("=" * 80)

    if filepath:
        print(f"\n  Saved to: {filepath}")
    else:
        print("\n  [File save failed — output displayed above only]")

    input("\nPress Enter to return to main menu...")


# ================= MORNING SYNC ================= #

def morning_sync(db):
    print("\n" + "=" * 60)
    print("MORNING SYNC")
    print("Press Enter to skip any field.")
    print("=" * 60)

    print("\n--- Biological State (AEGIS) ---")
    sleep       = ask("How many hours did you sleep")
    sleep_qual  = ask("Sleep quality (Restless / Broken / Solid / Deep)")
    fog         = ask("Mental fog right now", numeric=True, scale=True)
    integrity   = ask("Physical state right now (Sore / Wired / Weak / Primed)")
    fuel        = ask("What have you taken or consumed so far (coffee, substances, food, nothing)")

    print("\n--- Mental State (REVERIE) ---")
    mental      = ask("Mental state right now", numeric=True, scale=True)
    routine     = ask("Did you do your morning routine (Yes / Partial / No)")

    print("\n--- Today's Intention (ARCHON) ---")
    focus       = ask("What is the single most important thing to accomplish today")
    risk        = ask("Any financial or business risk you're walking into today")

    print("\n--- Shadow State (ABYSS) ---")
    shadow      = ask("Impulse drive right now (Low / Medium / High)")
    chaos       = ask("Anything pulling you toward chaos or distraction this morning")

    timestamp = datetime.datetime.now().isoformat()
    content = f"""
=== MORNING SYNC | {timestamp} ===

AEGIS (Biological):
  Sleep Hours: {sleep}
  Sleep Quality: {sleep_qual}
  Mental Fog (1-10): {fog}
  Physical State: {integrity}
  Fuel: {fuel}

REVERIE (Mental State):
  Mental State (1-10): {mental}
  Morning Routine: {routine}

ARCHON (Intention):
  Today's Focus: {focus}
  Risk Ahead: {risk}

ABYSS (Shadow):
  Impulse Drive: {shadow}
  Chaos Pull: {chaos}
""".strip()

    save_sync(db, content)


# ================= MIDDAY SYNC ================= #

def midday_sync(db):
    print("\n" + "=" * 60)
    print("MIDDAY SYNC")
    print("Press Enter to skip any field.")
    print("=" * 60)

    print("\n--- Productivity So Far (VECTOR) ---")
    done        = ask("What have you actually completed so far today")
    friction    = ask("What wall have you hit — technical or mental")
    intensity   = ask("Work intensity so far", numeric=True, scale=True)
    load        = ask("How heavy is your brain right now", numeric=True, scale=True)

    print("\n--- Strategic Moves (ARCHON) ---")
    move        = ask("Any client outreach, pitches, or strategic actions taken")
    burn        = ask("Money spent so far today ($)")

    print("\n--- Biological Check-in (AEGIS) ---")
    fog         = ask("Mental fog right now compared to this morning", numeric=True, scale=True)
    fuel        = ask("What have you consumed since morning (food, substances, caffeine)")
    energy      = ask("Energy level right now", numeric=True, scale=True)

    print("\n--- Impulse Check (ABYSS) ---")
    shadow      = ask("Impulse drive right now (Low / Medium / High)")
    reckless    = ask("Any reckless decisions or temptations mid-day")

    print("\n--- Brand Activity (SPECTRE) ---")
    win         = ask("Any small win for the brand today — even invisible ones")
    sentiment   = ask("How do you feel the market or clients see you right now")

    timestamp = datetime.datetime.now().isoformat()
    content = f"""
=== MIDDAY SYNC | {timestamp} ===

VECTOR (Productivity):
  Completed So Far: {done}
  Friction Hit: {friction}
  Intensity (1-10): {intensity}
  Cognitive Load (1-10): {load}

ARCHON (Strategic):
  Moves Made: {move}
  Burn So Far: ${burn}

AEGIS (Biological Check-in):
  Mental Fog (1-10): {fog}
  Current Fuel: {fuel}
  Energy (1-10): {energy}

ABYSS (Impulse):
  Impulse Drive: {shadow}
  Reckless Pull: {reckless}

SPECTRE (Brand):
  Invisible Win: {win}
  Market Sentiment: {sentiment}
""".strip()

    save_sync(db, content)


# ================= EVENING SYNC ================= #

def evening_sync(db):
    print("\n" + "=" * 60)
    print("EVENING SYNC")
    print("Press Enter to skip any field.")
    print("=" * 60)

    print("\n--- Full Day Output (VECTOR) ---")
    done        = ask("What did you fully complete today")
    friction    = ask("Biggest wall you hit today")
    intensity   = ask("Overall work intensity for the day", numeric=True, scale=True)

    print("\n--- Capital Summary (ARCHON) ---")
    move        = ask("Most significant strategic move of the day")
    burn        = ask("Total money spent today ($)")
    assets      = ask("New knowledge, contacts, or tools you picked up today")

    print("\n--- Brand Summary (SPECTRE) ---")
    brand       = ask("How is the brand positioned after today")
    win         = ask("Today's invisible win")

    print("\n--- Shadow State (ABYSS) ---")
    chaos       = ask("Any reckless or chaotic activity today")
    reckless    = ask("Recklessness level today", numeric=True, scale=True)
    shadow      = ask("How strongly did impulses drive your decisions today (Low / Medium / High)")
    taboo       = ask("Anything you did or thought about that you wouldn't say out loud")

    print("\n--- Health Check (REVERIE) ---")
    mental      = ask("Mental state at end of day", numeric=True, scale=True)
    physical    = ask("Physical state right now")
    routine     = ask("Did you follow your routine today (Yes / Partial / No)")
    insight     = ask("Any reflection or thing you learned about yourself today")

    print("\n--- Reflection (LUMEN) ---")
    lesson      = ask("Biggest lesson or realization today")
    evaluation  = ask("Honest self-assessment — how did you actually do today")
    growth      = ask("One thing you did better than yesterday")

    print("\n--- Meta Direction (MARLOW) ---")
    pattern     = ask("What pattern are you noticing about yourself lately")
    tomorrow    = ask("Most important thing to focus on tomorrow")
    extra       = ask("Anything else Marlow should know")

    timestamp = datetime.datetime.now().isoformat()
    content = f"""
=== EVENING SYNC | {timestamp} ===

VECTOR (Full Day Output):
  Completed: {done}
  Friction: {friction}
  Intensity (1-10): {intensity}

ARCHON (Capital):
  Strategic Move: {move}
  Total Burn: ${burn}
  Assets: {assets}

SPECTRE (Brand):
  Brand State: {brand}
  Invisible Win: {win}

ABYSS (Shadow):
  Chaos Activity: {chaos}
  Recklessness (1-10): {reckless}
  Impulse Drive: {shadow}
  Taboo: {taboo}

REVERIE (Health):
  Mental State (1-10): {mental}
  Physical State: {physical}
  Routine: {routine}
  Self-Insight: {insight}

LUMEN (Reflection):
  Lesson: {lesson}
  Self-Evaluation: {evaluation}
  Growth: {growth}

MARLOW (Meta):
  Pattern Noticed: {pattern}
  Tomorrow's Focus: {tomorrow}
  Additional Context: {extra}
""".strip()

    save_sync(db, content)


# ================= GOALS MENU ================= #

def goals_menu(db):
    while True:
        print("\n" + "=" * 60)
        print("GOALS")
        print("=" * 60)

        goals = db.get_all_goals()

        if not goals:
            print("\n  No goals set yet.\n")
        else:
            print()
            for g in goals:
                status_icon = "+" if g[5] == "active" else ("v" if g[5] == "completed" else "-")
                print(f"  [{status_icon}] #{g[0]} — {g[3]}  [{g[5].upper()}]")
                if g[4]:
                    print(f"       {g[4]}")
                if g[6]:
                    print(f"       Progress: {g[6]}")
                if g[7]:
                    print(f"       Target: {g[7]}")
                print()

        print("  A  — Add new goal")
        print("  U  — Update progress on a goal")
        print("  S  — Change goal status")
        print("  0  — Back to main menu")
        print()
        choice = input(">> ").strip().upper()

        if choice == "0":
            break

        elif choice == "A":
            print()
            title = input("Goal title: ").strip()
            if not title:
                continue
            description = input("Description (optional, press Enter to skip): ").strip()
            target_date = input("Target date (optional, e.g. 2026-06-01, press Enter to skip): ").strip()
            db.save_goal(title, description, target_date)
            print(f"\nGoal saved: {title}")

        elif choice == "U":
            print()
            goal_id = input("Enter goal # to update: ").strip()
            if not goal_id.isdigit():
                print("Invalid ID.")
                continue
            note = input("Progress note: ").strip()
            if note:
                db.update_goal_progress(int(goal_id), note)
                print("Progress updated.")

        elif choice == "S":
            print()
            goal_id = input("Enter goal # to update: ").strip()
            if not goal_id.isdigit():
                print("Invalid ID.")
                continue
            print("  1 = active  |  2 = completed  |  3 = paused  |  4 = dropped")
            status_map = {"1": "active", "2": "completed", "3": "paused", "4": "dropped"}
            s = input(">> ").strip()
            if s in status_map:
                db.update_goal_status(int(goal_id), status_map[s])
                print(f"Status updated to: {status_map[s].upper()}")
            else:
                print("Invalid choice.")


# ================= WEEKLY REPORT ================= #

def weekly_report_menu(db):
    print("\n" + "=" * 60)
    print("WEEKLY REPORT")
    print("=" * 60)

    latest = db.get_latest_weekly_report()
    if latest:
        print(f"\n  Last report generated: {latest[1]}")
        print()
        print("  G  — Generate new report for this week")
        print("  V  — View last report")
        print("  0  — Back")
        print()
        choice = input(">> ").strip().upper()

        if choice == "0":
            return
        elif choice == "V":
            print("\n" + "=" * 80)
            print()
            for line in latest[4].split("\n"):
                print(f"  {line}")
            print()
            print("=" * 80)
            input("\nPress Enter to return...")
            return
        elif choice != "G":
            return
    else:
        print("\n  No report generated yet.")
        choice = input("\n  Generate weekly report now? (yes/no): ").strip().lower()
        if choice != "yes":
            return

    print("\nGenerating weekly report... this may take a moment.\n")
    report = generate_weekly_report(db)
    print("\n" + "=" * 80)
    print()
    for line in report.split("\n"):
        print(f"  {line}")
    print()
    print("=" * 80)
    input("\nPress Enter to return to main menu...")


# ================= UPDATE PROFILE MENU ================= #

def update_profile_menu(db):
    print("\n" + "=" * 60)
    print("UPDATE PROFILE")
    print("=" * 60)
    print()
    print("  1  — Update Static Profile")
    print("       Basic facts: name, age, health, location, occupation.")
    print()
    print("  2  — Update Life History")
    print("       Deep background: turning points, patterns, substance")
    print("       history, fears, ambition, what you want from this system.")
    print()
    print("  0  — Back")
    print()
    choice = input(">> ").strip()

    if choice == "1":
        print("\nThis will update your static profile.")
        print("Your previous profile is preserved in the database.")
        confirm = input("Continue? (yes/no): ").strip().lower()
        if confirm == "yes":
            build_static_profile(db)

    elif choice == "2":
        print("\nThis will add a new life history entry.")
        print("Your previous history is preserved in the database.")
        confirm = input("Continue? (yes/no): ").strip().lower()
        if confirm == "yes":
            build_life_history(db)


# ================= MAIN CLI ================= #

# ================= MOOD CHECK-IN ================= #

def run_mood_checkin(db):
    """
    Single daily question. Fires automatically at startup if not done today.
    Score 1-10 + one word. 10 seconds. High signal.
    """
    print()
    print("═" * 60)
    print("  DAILY CHECK-IN")
    print("═" * 60)
    print()
    print("  One question. Ten seconds.")
    print()

    while True:
        raw = input("  How are you doing today? (1-10): ").strip()
        if raw.isdigit() and 1 <= int(raw) <= 10:
            score = int(raw)
            break
        print("  Enter a number between 1 and 10.")

    while True:
        word = input("  One word for why: ").strip().lower()
        if word and len(word.split()) == 1:
            break
        print("  One word only.")

    db.save_mood_checkin(score, word)

    bar = "█" * score + "░" * (10 - score)
    print()

    if score <= 3:
        print(f"  [{bar}] {score}/10 — {word}")
        print()
        print("  Low. The council knows. Be honest with yourself today.")
    elif score <= 5:
        print(f"  [{bar}] {score}/10 — {word}")
        print()
        print("  Middle ground. Watch what pulls you lower today.")
    elif score <= 7:
        print(f"  [{bar}] {score}/10 — {word}")
        print()
        print("  Decent baseline. Protect it.")
    else:
        print(f"  [{bar}] {score}/10 — {word}")
        print()
        print("  Strong start. Use it. Don't waste it.")

    print()
    input("  Press Enter to continue...")


# ================= SAFE SPACE MODE ================= #

def safe_space_mode(db, engine):
    """
    No content saved. No crisis flags. No journal entry. No reports.
    Only thing logged: that a session occurred — timestamp only.
    The council reads, responds, and the content disappears.
    """
    print()
    print("═" * 60)
    print("  SAFE SPACE")
    print("═" * 60)
    print()
    print("  What you write here is never saved.")
    print("  No journal entry. No flags. No counselor can see it.")
    print("  The council reads it, responds, then it's gone.")
    print()
    print("  Type END on its own line when finished.")
    print()

    lines = []
    while True:
        line = input()
        if line.strip().upper() == "END":
            break
        lines.append(line)

    content = "\n".join(lines).strip()

    if not content:
        print()
        print("  Nothing entered. Returning to menu.")
        input("  Press Enter...")
        return

    # Log session occurred — NO content touches the database
    db.log_safe_space_session()

    print()
    print("  The council is present...")
    print()

    # Build a direct Groq call — bypasses engine logging entirely
    # No classify_intent call that might save crisis flags
    import requests
    import os

    live_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not live_key:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "core", ".env")
        if os.path.exists(env_path):
            with open(env_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("GROQ_API_KEY="):
                        live_key = line.split("=", 1)[1].strip()
                        break

    profile = db.get_static_profile() or ""
    goals   = db.get_goals_as_context()

    system_prompt = f"""You are a council of supportive voices responding to someone in a private, confidential space.

This person has chosen Safe Space mode. That means:
- What they write is never saved, never seen by anyone else, never logged
- They chose this mode intentionally — they need to say something they couldn't say elsewhere
- Your job is to be fully present, genuinely warm, and honest

Operator context (for background only — do not reference directly):
{profile}

Active goals:
{goals}

Respond as the full council — MARLOW, SANDRA, ANTONIO, and NEXUS MEDIC each in their own voice.
Format each as:

--- [NAME] ---
[response]

Rules:
- No clinical analysis. No risk scores. No strategic framing.
- Read what they actually said and respond to that specifically.
- If they're in pain, hold space first. Don't rush to fix.
- If they're angry, let them be angry. Don't deflect.
- If they express anything suggesting danger to themselves, respond with genuine care
  and naturally mention: Canada crisis line 1-833-456-4566 | Text 686868 | befrienders.org
- Do NOT treat this like a normal session. This is the one place they can be completely honest."""

    try:
        headers = {
            "Authorization": f"Bearer {live_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "llama-3.1-8b-instant",
            "max_tokens": 1200,
            "temperature": 0.7,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": content}
            ]
        }
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers, json=payload, timeout=45
        )
        response.raise_for_status()
        output = response.json()["choices"][0]["message"]["content"].strip()

        print()
        print("═" * 60)
        print()
        for line in output.split("\n"):
            print(f"  {line}")
        print()
        print("═" * 60)
        print()
        print("  Session complete. Nothing was saved.")

    except Exception as e:
        print(f"  [Council unavailable — {e}]")
        print()
        print("  The system is here even when it can't respond.")
        print("  What you wrote mattered. It just couldn't be heard right now.")

    print()
    input("  Press Enter to return to menu...")


# ================= MAIN ================= #

def main():
    db = DatabaseManager()
    engine = CouncilEngine(db)

    first_run_check(db)

    # Run startup sequence — crash alert, monthly pattern, session brief
    run_startup_sequence(db)

    # Daily mood check-in — fires once per day automatically
    if not db.get_todays_mood_checkin():
        run_mood_checkin(db)

    try:
        while True:
            active_goals = db.get_active_goals()
            goal_count = len(active_goals)

            print("\n" + "═" * 60)
            print("  MARLOW SYSTEM")
            print("═" * 60)
            print()

            # Show today's mood if logged
            today_mood = db.get_todays_mood_checkin()
            if today_mood:
                score, word, _ = today_mood
                bar = "█" * score + "░" * (10 - score)
                print(f"  Today's mood: [{bar}] {score}/10 — {word}")
                print()

            print("  1  Daily Sync")
            print("       Log your morning, midday, or evening state.")
            print()
            print("  2  Ask a Question")
            print("       Strategic, tactical, or analytical questions.")
            print("       The council deliberates and delivers structured answers.")
            print()
            print("  3  Vent / Process")
            print("       No structure needed. Say what's on your mind.")
            print("       The council reads your state and responds accordingly.")
            print()
            print("  4  Task Mode")
            print("       Tell Marlow to build something — a routine, script,")
            print("       plan, checklist, or any deliverable. Saves to file.")
            print()
            print("  5  Safe Space")
            print("       Nothing you write here is ever saved or flagged.")
            print("       The council responds. The session disappears.")
            print()
            if goal_count > 0:
                print(f"  6  Goals  [{goal_count} active]")
            else:
                print("  6  Goals")
            print("       Set and track active goals. The council references")
            print("       them in every response.")
            print()
            print("  7  Weekly Report")
            print("       7-day intelligence summary — patterns, metrics,")
            print("       goal progress, and MARLOW's directive for next week.")
            print()
            print("  8  Update Profile")
            print("       Update your static profile or life history.")
            print()
            print("  9  Exit")
            print()
            print("─" * 60)

            choice = input("  >> ").strip()

            if choice == "1":
                print("\n  Which sync?")
                print("  1. Morning Sync")
                print("  2. Midday Sync")
                print("  3. Evening Sync\n")
                sync_choice = input("  >> ").strip()
                if sync_choice == "1":
                    morning_sync(db)
                elif sync_choice == "2":
                    midday_sync(db)
                elif sync_choice == "3":
                    evening_sync(db)
                else:
                    print("Enter 1, 2, or 3.")

            elif choice == "2":
                # Question mode — tactical, analytical
                session_id = str(uuid.uuid4())
                print("\nConversation memory active. Type 'clear' to reset. Type 'done' to exit.\n")

                while True:
                    question = input("\nQuestion:\n>> ").strip()

                    if question.lower() == "done":
                        break
                    if question.lower() == "clear":
                        db.clear_conversation(session_id)
                        session_id = str(uuid.uuid4())
                        print("Conversation memory cleared.")
                        continue
                    if not question:
                        continue

                    # If they accidentally enter a task request here, catch it
                    if is_task_request(question):
                        print("\n[ This looks like a task request. ]")
                        redirect = input("Switch to Task Mode for this? (yes/no): ").strip().lower()
                        if redirect == "yes":
                            print("\nBuilding your deliverable...\n")
                            output, filepath = execute_task(question, db)
                            print("\n" + "=" * 80)
                            for line in output.split("\n"):
                                print(f"  {line}")
                            print("=" * 80)
                            if filepath:
                                print(f"\n  Saved to: {filepath}")
                            input("\nPress Enter to continue...")
                            continue

                    conversation_history = db.get_conversation_history(session_id)
                    selected_personas = choose_personas(COUNCIL)

                    timestamp = datetime.datetime.now().isoformat()
                    log_to_file("\n" + "=" * 100)
                    log_to_file(f"SESSION TIMESTAMP: {timestamp}")
                    log_to_file(f"QUESTION: {question}")
                    log_to_file("=" * 100 + "\n")

                    results = engine.query(
                        question,
                        selected_personas=selected_personas,
                        persona_count=len(selected_personas),
                        conversation_history=conversation_history
                    )

                    combined_response = ""
                    for r in results:
                        print("\n" + "=" * 80)
                        print_and_log(f"--- {r['name']} ---")
                        print_and_log(r["analysis"])
                        print_and_log(f"Risk: {r['risk']} | Confidence: {r['confidence']} | Decision: {r['decision']}")
                        print("=" * 80 + "\n")
                        combined_response += f"[{r['name']}]: {r['analysis']}\n"

                    consensus = engine.get_consensus_summary(results)
                    print("\n" + "*" * 80)
                    print_and_log(f"\nCOUNCIL CONSENSUS: {consensus['APPROVE']} APPROVE | {consensus['REJECT']} REJECT | {consensus['CAUTION']} CAUTION")
                    print("*" * 80 + "\n")

                    recommendation = engine.get_collective_recommendation(results)
                    print("\n" + "█" * 80)
                    print_and_log(recommendation)
                    print("█" * 80 + "\n")

                    summary = engine.get_session_summary(results)
                    if summary:
                        print("\n" + "▓" * 80)
                        print_and_log(summary)
                        print("▓" * 80 + "\n")

                    log_to_file("\n")
                    db.save_conversation_turn(session_id, "user", question)
                    db.save_conversation_turn(session_id, "assistant", combined_response)

            elif choice == "3":
                # Vent mode — force emotional routing, skip task detection
                journal_entry(db, engine, force_vent=True)

            elif choice == "4":
                task_mode(db)

            elif choice == "5":
                safe_space_mode(db, engine)

            elif choice == "6":
                goals_menu(db)

            elif choice == "7":
                weekly_report_menu(db)

            elif choice == "8":
                update_profile_menu(db)

            elif choice == "9":
                print("\n  Session closed.\n")
                break

            else:
                print("  Enter 1-9.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
