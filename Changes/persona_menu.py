"""
persona_menu.py — FIXED
Previous version imported PersonaChat from core.persona_chat (broken placeholder).
Now imports from core.database where the real implementation lives.

Also cleaned up: persona list now sourced from the PERSONAS config list
(personas.py) rather than scanning persona_memory table for distinct names,
which was unreliable on a fresh install with no prior sessions.
"""

from core.database import DatabaseManager, PersonaChat
from personas import PERSONAS


def list_personas() -> list:
    """
    Returns the list of available personas from the PERSONAS config.
    More reliable than scanning persona_memory for distinct names —
    that table is empty on fresh installs and during early usage.
    """
    return PERSONAS


def choose_persona() -> dict | None:
    """
    Prompts the user to choose a persona. Returns the full persona dict
    (name, system_prompt, role, description) or None if user backs out.
    """
    personas = list_personas()
    if not personas:
        print("  No personas configured.")
        return None

    print("  Available personas:")
    print()
    for i, p in enumerate(personas, 1):
        print(f"  {i}  →  {p['name']}  |  {p.get('role', '')}")
        desc = p.get("description", "")
        if desc:
            print(f"       {desc}")
        print()

    print("  0  →  Back")
    print()

    while True:
        choice = input("  Select: ").strip()
        if choice == "0":
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(personas):
            return personas[int(choice) - 1]
        print("  Invalid selection. Enter a number from the list.")


def chat_with_persona(db: DatabaseManager, persona: dict):
    """
    Runs a one-on-one conversation session with the selected persona.
    Uses the real PersonaChat from core.database — full Groq integration.
    """
    persona_name  = persona["name"]
    system_prompt = persona.get(
        "system_prompt",
        f"You are {persona_name}. Respond honestly and directly. Ask questions. Be present."
    )

    chat = PersonaChat(db=db, persona_name=persona_name, persona_system_prompt=system_prompt)

    print()
    print(f"  --- Talking to {persona_name} ---")
    desc = persona.get("description", "")
    if desc:
        print(f"  {desc}")
    print()
    print("  Type your message and press ENTER.")
    print("  EXIT to end session.  CLEAR to wipe conversation memory.")
    print()

    while True:
        user_input = input(f"  YOU: ").strip()

        if user_input.upper() == "EXIT":
            print()
            print(f"  Session with {persona_name} ended.")
            print()
            break

        if user_input.upper() == "CLEAR":
            chat.clear_conversation()
            print()
            print("  Conversation memory cleared.")
            print()
            continue

        if not user_input:
            continue

        print()
        response = chat.send_message(user_input)
        print(f"  {persona_name}: {response}")
        print()


def persona_menu(db: DatabaseManager):
    """
    Entry point for the persona menu. Called from marlow.py menu option 9.
    Accepts db as parameter so it shares the same database connection.
    """
    while True:
        print()
        print("  === PERSONA CHAT ===")
        print()
        print("  Talk directly to one persona. One-on-one.")
        print()

        persona = choose_persona()
        if persona is None:
            break

        chat_with_persona(db, persona)


if __name__ == "__main__":
    # Standalone execution — opens its own db connection
    db = DatabaseManager()
    persona_menu(db)
