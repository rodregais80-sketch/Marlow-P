# check_models.py
import os
import json

PERSONAS_FILE = r"C:\Marlow\personas.py"

if not os.path.exists(PERSONAS_FILE):
    print(f"Personas file not found at {PERSONAS_FILE}")
    exit()

with open(PERSONAS_FILE, "r", encoding="utf-8") as f:
    content = f.read()

# This is a basic way to see which model each persona is set to
import re
matches = re.findall(r'"model":\s*"(.*?)"', content)
print("Models configured for personas:")
for i, model in enumerate(matches, 1):
    print(f"Persona {i}: {model}")