# core/persona_menu.py

from core.database import DatabaseManager
from core.persona_chat import PersonaChat

def list_personas(db: DatabaseManager):
    """List all personas based on persona_memory entries."""
    cursor = db.conn.cursor()
    cursor.execute("SELECT DISTINCT persona_name FROM persona_memory")
    rows = cursor.fetchall()
    return [row["persona_name"] for row in rows]

def choose_persona(db: DatabaseManager):
    """Prompt the user to choose a persona to talk to."""
    personas = list_personas(db)
    if not personas:
        print("No personas found. Please create persona memory entries first.")
        return None

    print("Available personas:")
    for i, p in enumerate(personas, 1):
        print(f"{i}. {p}")

    while True:
        choice = input("Choose a persona by number: ")
        if choice.isdigit() and 1 <= int(choice) <= len(personas):
            return personas[int(choice) - 1]
        else:
            print("Invalid choice. Try again.")

def chat_with_persona(db: DatabaseManager, persona_name: str):
    """Start a one-on-one conversation session with a persona."""
    chat = PersonaChat(db, persona_name)
    print(f"\n--- Starting chat with {persona_name} ---")
    print("Type 'exit' to end the conversation.\n")

    while True:
        user_input = input("You: ")
        if user_input.lower() in ["exit", "quit"]:
            print(f"Ending chat with {persona_name}.\n")
            break

        response = chat.send_message(user_input)
        print(f"{persona_name}: {response}\n")

def persona_menu():
    db = DatabaseManager()
    while True:
        print("=== Persona Menu ===")
        print("1. List Personas")
        print("2. Talk to a Persona")
        print("3. Exit")

        choice = input("Enter your choice: ")
        if choice == "1":
            personas = list_personas(db)
            if personas:
                print("Existing personas:")
                for p in personas:
                    print(f"- {p}")
            else:
                print("No personas found.")
            print()

        elif choice == "2":
            persona_name = choose_persona(db)
            if persona_name:
                chat_with_persona(db, persona_name)

        elif choice == "3":
            print("Exiting Persona Menu.")
            break
        else:
            print("Invalid choice. Try again.\n")

if __name__ == "__main__":
    persona_menu()