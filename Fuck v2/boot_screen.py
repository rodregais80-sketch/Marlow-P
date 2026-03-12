import sys
import os
import time
import shutil
import random
from datetime import datetime

# ---------------------------------------------------------
# UTF-8 + ANSI ENABLEMENT (Windows-safe)
# ---------------------------------------------------------
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer,
        encoding="utf-8",
        errors="replace"
    )
    os.system("")

# ---------------------------------------------------------
# COLORS
# ---------------------------------------------------------
GREEN  = "\033[92m"
DIM    = "\033[2m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"

# ---------------------------------------------------------
# HYBRID GLYPHS
# ---------------------------------------------------------
GLYPHS = ["⟡", "✧", "✦", "✶", "✴", "ᚠ", "ᚦ", "ᚨ", "ᚱ", "ᛃ"]

# ---------------------------------------------------------
# CENTERING FUNCTIONS
# ---------------------------------------------------------
def terminal_width():
    return shutil.get_terminal_size((72, 45)).columns

def terminal_height():
    return shutil.get_terminal_size((72, 45)).lines

def center_line(text):
    return text.center(terminal_width())

def vertical_offset(lines=10):
    print("\n" * lines, end="")

def print_centered(text, color=""):
    print(color + text.center(terminal_width()) + RESET)

def print_logo_centered(logo_lines, vertical_pos=8):
    """Print logo block centered horizontally, pushed down by vertical_pos lines."""
    width = terminal_width()
    vertical_offset(vertical_pos)
    for line in logo_lines:
        padding = max(0, (width - len(line)) // 2)
        print(GREEN + " " * padding + line + RESET)

# ---------------------------------------------------------
# FIX 2: LOGO — correct MARLOW spelling
# ---------------------------------------------------------
LOGO_LINES = [
    "══════════════════════════════════════════════════════════════════════════════",
    "",
    "  ███╗   ███╗ █████╗ ██████╗ ██╗      ██████╗ ██╗    ██╗",
    "  ████╗ ████║██╔══██╗██╔══██╗██║     ██╔═══██╗██║    ██║",
    "  ██╔████╔██║███████║██████╔╝██║     ██║   ██║██║ █╗ ██║",
    "  ██║╚██╔╝██║██╔══██║██╔══██╗██║     ██║   ██║██║███╗██║",
    "  ██║ ╚═╝ ██║██║  ██║██║  ██║███████╗╚██████╔╝╚███╔███╔╝",
    "  ╚═╝     ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝ ╚═════╝  ╚══╝╚══╝ ",
    "",
    "══════════════════════════════════════════════════════════════════════════════",
    "   Strategic Continuity Intelligence  //  Personal Edition",
    "══════════════════════════════════════════════════════════════════════════════",
]

# ---------------------------------------------------------
# EFFECTS
# ---------------------------------------------------------
def whisper(text, delay=0.04):
    """Centered whisper."""
    width = terminal_width()
    padded = text.center(width)
    sys.stdout.write(DIM)
    for char in padded:
        sys.stdout.write(char)
        sys.stdout.flush()
        time.sleep(delay)
    sys.stdout.write(RESET + "\n")

def glyph_drift(duration=4):
    sys.stdout.write(HIDE_CURSOR)
    end = time.time() + duration
    i = 0
    msg = "…invoking the inner lattice…"
    width = terminal_width()
    while time.time() < end:
        g = GLYPHS[i % len(GLYPHS)]
        line = f"{g}  {msg}  {g}"
        padded = line.center(width)
        sys.stdout.write(f"\r{DIM}{padded}{RESET}")
        sys.stdout.flush()
        time.sleep(0.22)
        i += 1
    sys.stdout.write("\r" + " " * width + "\r")
    sys.stdout.write(SHOW_CURSOR)
    sys.stdout.flush()

# FIX 1: pulse_logo — glyph only, no logo printed here
def pulse_logo():
    width = terminal_width()
    pulse_symbol = "⟡"
    for _ in range(2):
        os.system("cls")
        print_centered(pulse_symbol, GREEN)
        time.sleep(0.35)
        os.system("cls")
        time.sleep(0.2)

# ---------------------------------------------------------
# CONTINUITY RITES
# ---------------------------------------------------------
RITES = [
    "Aligning continuity threads",
    "Summoning behavioral vault echoes",
    "Rebinding session memory strata",
    "Opening the Groq conduit",
    "Scanning for fractures in the weave",
    "Cleansing stale session sigils",
    "Harmonizing council nodes",
    "Sealing telemetry runes",
    "Calibrating predictive lattice",
    "Weaving redundancy wards",
    "Refreshing behavioral heuristics",
    "Anchoring continuity anchors",
    "Polling the oracle cache",
    "Verifying integrity of the vault",
    "PLACEHOLDER_16",
    "PLACEHOLDER_17",
    "Morning attunement of threads",
    "Sunlit cache refresh",
    "Afternoon resonance check",
    "Daylight predictive sync",
    "Dusk consolidation ritual",
    "Evening entropy sweep",
    "Sealing the ephemeral bindings"
]

def time_adjusted_rites():
    hour = datetime.now().hour
    if hour < 6:
        RITES[14] = "Whispering to the midnight lattice"
        RITES[15] = "Tending nocturnal memory embers"
    elif hour < 12:
        RITES[14] = "Morning attunement of threads"
        RITES[15] = "Sunlit cache refresh"
    elif hour < 18:
        RITES[14] = "Afternoon resonance check"
        RITES[15] = "Daylight predictive sync"
    else:
        RITES[14] = "Dusk consolidation ritual"
        RITES[15] = "Evening entropy sweep"

def perform_rites():
    time_adjusted_rites()
    width = terminal_width()
    selected = random.sample(RITES, 6)
    for i, rite in enumerate(selected):
        glyph = GLYPHS[i % len(GLYPHS)]
        line = f"[ {glyph} ]  {rite}…"
        centered = line.center(width)
        sys.stdout.write(GREEN + centered.rstrip() + RESET)
        sys.stdout.flush()
        time.sleep(0.7)
        sys.stdout.write(DIM + "  ✓" + RESET + "\n")
        sys.stdout.flush()
        time.sleep(0.3)

# ---------------------------------------------------------
# BOOT SEQUENCE
# ---------------------------------------------------------
def main():
    os.system("cls")

    # 1. Prelude glyph drift
    glyph_drift(4)

    # 2. Veil descent — centered whispers
    os.system("cls")
    vertical_offset(12)
    whisper("…the lattice stirs…")
    whisper("…continuity threads re-align…")
    whisper("…MARLOW descends through the veil…")
    time.sleep(1.2)

    # 3. Glyph pulse only — no logo inside
    pulse_logo()

    # 4. One clean logo print — upper-middle (Version B)
    os.system("cls")
    print_logo_centered(LOGO_LINES, vertical_pos=8)
    time.sleep(1.0)

    # 5. Continuity rites — centered
    print()
    perform_rites()

    # 6. Final centered whisper
    print()
    whisper("MARLOW is awake.")
    time.sleep(1.2)

    # 7. Launch marlow.py — blocking
    base = os.path.dirname(os.path.abspath(__file__))
    marlow_path = os.path.join(base, "marlow.py")
    import subprocess
    subprocess.call([sys.executable, marlow_path])

if __name__ == "__main__":
    main()
