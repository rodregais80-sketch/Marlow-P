"""
debate_engine.py
Multi-round persona debate system for Marlow Platform 3.

Upgrade: Personas see each other's Round 1 reasoning before finalizing positions.
This produces genuine analytical conflict surfacing instead of parallel monologues.

Architecture:
  Round 1: Independent persona analysis (existing run_council behavior)
  Round 2 (Debate): Each active persona reads all other Round 1 outputs,
                    critiques weak reasoning, identifies blind spots,
                    then states their FINAL position.

The debate round output replaces Round 1 output in the synthesis call.
All rounds are stored in council_reasoning_log for longitudinal tracking.

Groq call cost per debate:
  - Round 1: N persona calls (existing)
  - Round 2: N persona calls (new)
  - Synthesis: 1 call (existing)
  Total additional cost: N calls (one per active persona)

Design decisions:
  - MORRO excluded from debate round (same routing rules as council)
  - Debate prompts are shorter than full persona prompts — no full context reinject
  - 0.3s stagger between debate calls (tighter than 0.4s council stagger)
  - Graceful fallback: if debate round fails, Round 1 output is used unchanged
  - Council reasoning log stores both rounds per session
"""

import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime


# ── Context builder ───────────────────────────────────────────────────────────

def build_debate_context(persona_outputs: dict, exclude_name: str = "") -> str:
    """
    Formats all Round 1 persona outputs into a readable debate context block.
    The calling persona is excluded so they read others' positions, not their own.

    Args:
        persona_outputs: dict of {persona_name: round1_response}
        exclude_name: persona name to exclude (the one doing the critiquing)

    Returns:
        Formatted string of all other personas' Round 1 positions.
    """
    lines = ["=== COUNCIL ROUND 1 POSITIONS ===\n"]
    for name, response in persona_outputs.items():
        if name == exclude_name:
            continue
        # Truncate very long responses to keep debate prompt manageable
        truncated = response[:600] + "..." if len(response) > 600 else response
        lines.append(f"--- {name} ---\n{truncated}\n")
    return "\n".join(lines)


def build_debate_prompt(
    persona_name: str,
    persona_system_prompt: str,
    user_input: str,
    round1_own_output: str,
    other_positions: str,
    intent_type: str
) -> list:
    """
    Builds the message list for a single persona's debate round call.

    The persona receives:
    - Their own Round 1 position (to revise or defend)
    - All other personas' Round 1 positions (to critique)
    - A structured debate task

    Returns list of message dicts for groq_chat().
    """
    debate_system = f"""{persona_system_prompt}

--- DEBATE ROUND ---
You are in Round 2 of a council debate. You have already given your initial analysis.
Now you have seen what the other council members said. Your job is to think harder.
"""

    debate_user = f"""The original question or input:
\"\"\"{user_input}\"\"\"

Your Round 1 position:
{round1_own_output[:400]}

{other_positions}

Your task for Round 2:

1. CRITIQUE — Identify one specific flaw, blind spot, or missing risk in the other positions. Be specific. Name the persona and the reasoning gap.

2. DEFEND OR REVISE — Does seeing their positions change yours? If yes, revise. If no, sharpen your argument with what they missed.

3. FINAL POSITION — State your definitive conclusion. This replaces your Round 1 output.

Format:
CRITIQUE: [one sharp sentence naming the flaw you found in another position]
REVISION: [did your view change? yes/no and why in one sentence]
FINAL: [your definitive 2-4 sentence position after debate]

Stay in your persona voice. Be specific. No filler. No repetition of what others already covered well."""

    return [
        {"role": "system", "content": debate_system},
        {"role": "user",   "content": debate_user}
    ]


# ── Debate round executor ─────────────────────────────────────────────────────

def run_debate_round(
    user_input: str,
    round1_outputs: dict,
    personas: list,
    active_names: list,
    intent_type: str,
    groq_chat_fn,
    session_id: str = None
) -> dict:
    """
    Executes the debate round for all active personas.
    Each persona reads other personas' Round 1 outputs and produces a final position.

    Args:
        user_input: original user input string
        round1_outputs: dict of {persona_name: round1_response_text}
        personas: full personas list (for system prompts)
        active_names: list of persona names that are active this session
        intent_type: from intent classification
        groq_chat_fn: the groq_chat callable from groq_client
        session_id: optional session identifier for logging

    Returns:
        dict of {persona_name: debate_round_response}
        Falls back to round1_outputs for any persona whose debate call fails.
    """
    if not round1_outputs:
        return {}

    # MORRO does not participate in debate round — same rule as synthesis exclusion
    debate_participants = [n for n in active_names if n != "MORRO"]

    if not debate_participants:
        return round1_outputs.copy()

    # Build persona system prompt lookup
    persona_prompts = {p["name"]: p.get("system_prompt", "") for p in personas}

    tasks = []
    for i, name in enumerate(debate_participants):
        if name not in round1_outputs:
            continue

        own_output       = round1_outputs[name]
        other_positions  = build_debate_context(round1_outputs, exclude_name=name)
        messages         = build_debate_prompt(
            persona_name=name,
            persona_system_prompt=persona_prompts.get(name, ""),
            user_input=user_input,
            round1_own_output=own_output,
            other_positions=other_positions,
            intent_type=intent_type
        )
        tasks.append((name, messages, i * 0.3))

    debate_results = {}

    with ThreadPoolExecutor(max_workers=max(1, len(tasks))) as executor:
        future_map = {}
        for name, messages, stagger in tasks:
            future = executor.submit(
                _call_debate_persona,
                name, messages, stagger, groq_chat_fn
            )
            future_map[future] = name

        for future in as_completed(future_map):
            name   = future_map[future]
            result = future.result()
            if result["success"]:
                debate_results[name] = result["response"]
            else:
                # Fallback: use Round 1 output if debate call fails
                debate_results[name] = round1_outputs.get(name, "")

    # Preserve any personas not in debate (e.g. MORRO) from Round 1
    final_outputs = round1_outputs.copy()
    for name, response in debate_results.items():
        final_outputs[name] = response

    return final_outputs


def _call_debate_persona(
    persona_name: str,
    messages: list,
    stagger_delay: float,
    groq_chat_fn
) -> dict:
    """
    Single debate call for one persona. Graceful fallback on failure.
    """
    try:
        if stagger_delay > 0:
            time.sleep(stagger_delay)
        response = groq_chat_fn(
            messages=messages,
            temperature=0.65,
            max_tokens=400,
            stagger_delay=0.0
        )
        return {"name": persona_name, "response": response, "success": True}
    except Exception as e:
        return {
            "name":     persona_name,
            "response": "",
            "success":  False,
            "error":    str(e)
        }


# ── Council reasoning log ─────────────────────────────────────────────────────

def store_council_reasoning(db, session_id: str, round_number: int, persona_outputs: dict):
    """
    Persists all persona outputs for a given round to council_reasoning_log.
    Called after Round 1 and after Round 2 (debate).

    Args:
        db: DatabaseManager instance
        session_id: unique session identifier
        round_number: 1 for initial analysis, 2 for debate final positions
        persona_outputs: dict of {persona_name: response_text}
    """
    try:
        for persona, argument in persona_outputs.items():
            if not argument:
                continue
            # Truncate stored argument — full text is in council_sessions
            # We store the signal, not the full transcript
            stored_arg = argument[:1000] if len(argument) > 1000 else argument
            db.conn.execute(
                """
                INSERT INTO council_reasoning_log
                    (session_id, round_number, persona, argument, timestamp)
                VALUES (?, ?, ?, ?, datetime('now'))
                """,
                (session_id, round_number, persona, stored_arg)
            )
        db.conn.commit()
    except Exception:
        # Non-fatal — reasoning log is enhancement, not core system
        pass


def get_reasoning_history(db, persona_name: str = None, limit: int = 10) -> list:
    """
    Retrieves recent council reasoning entries.
    Used for longitudinal pattern analysis — what did ALDRIC argue last month?

    Args:
        db: DatabaseManager instance
        persona_name: filter by persona, or None for all personas
        limit: max rows to return

    Returns:
        List of dicts with keys: session_id, round_number, persona, argument, timestamp
    """
    try:
        if persona_name:
            rows = db.conn.execute(
                """
                SELECT session_id, round_number, persona, argument, timestamp
                FROM council_reasoning_log
                WHERE persona = ?
                ORDER BY id DESC LIMIT ?
                """,
                (persona_name, limit)
            ).fetchall()
        else:
            rows = db.conn.execute(
                """
                SELECT session_id, round_number, persona, argument, timestamp
                FROM council_reasoning_log
                ORDER BY id DESC LIMIT ?
                """,
                (limit,)
            ).fetchall()

        return [
            {
                "session_id":   row[0],
                "round_number": row[1],
                "persona":      row[2],
                "argument":     row[3],
                "timestamp":    row[4]
            }
            for row in rows
        ]
    except Exception:
        return []


def get_persona_reasoning_patterns(db, persona_name: str, lookback_days: int = 90) -> str:
    """
    Builds a compact string summarizing what a persona has argued over the past N days.
    Used for future meta-reasoning: "ALDRIC has flagged burnout risk 4 times this month."

    Args:
        db: DatabaseManager instance
        persona_name: which persona to summarize
        lookback_days: how far back to look

    Returns:
        Formatted string of argument snippets, or empty string if no data.
    """
    try:
        rows = db.conn.execute(
            """
            SELECT argument, timestamp
            FROM council_reasoning_log
            WHERE persona = ?
              AND datetime(timestamp) >= datetime('now', ? || ' days')
            ORDER BY id DESC LIMIT 20
            """,
            (persona_name, f"-{lookback_days}")
        ).fetchall()

        if not rows:
            return ""

        lines = [f"Recent {persona_name} reasoning ({len(rows)} sessions, last {lookback_days}d):"]
        for row in rows[:8]:
            snippet   = row[0][:120].replace("\n", " ").strip()
            timestamp = row[1][:10] if row[1] else "unknown"
            lines.append(f"  [{timestamp}] {snippet}...")

        return "\n".join(lines)
    except Exception:
        return ""


# ── Session ID generator ──────────────────────────────────────────────────────

def generate_session_id() -> str:
    """
    Generates a unique session ID for council_reasoning_log grouping.
    Format: timestamp + short uuid fragment for readability.
    """
    ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
    uid   = str(uuid.uuid4())[:8]
    return f"{ts}_{uid}"
