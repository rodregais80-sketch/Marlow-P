# MARLOW PLATFORM — COMPLETE ARCHITECTURE BLUEPRINT
# Generated: 2026-03-11 (Session 3 update)
# Purpose: Session continuity. Paste this into any new Claude chat to resume with full context.

---

## HOW TO USE THIS FILE
If this chat has ended or tokens are maxed, open a new Claude session and paste:
1. This entire blueprint
2. The specific file(s) you need help with
3. Your question or the error you are seeing

Claude will have full context of the entire system immediately.

---

## OPERATOR
- Name: Antonio Gaspard
- Age: 28, Okanagan Valley BC
- Project root: C:\Marlow Int\Marlow_Platform\
- GitHub: https://github.com/rodregais80-sketch/Marlow
- Python: C:\Users\rodre\AppData\Local\Python\pythoncore-3.14-64\python.exe
- Groq model: llama-3.1-8b-instant
- DB: vault.db (SQLite)
- Groq key: stored in core/.env as GROQ_API_KEY

---

## WHAT THIS SYSTEM IS
Marlow is a personal strategic intelligence CLI. The operator runs it from the terminal.
It has 4 AI personas (ALDRIC, SEREN, MORRO, ORYN) powered by Groq API.
Each persona has a distinct voice and domain. They respond in parallel to user input.
MARLOW synthesizes their responses into one final statement.
The system tracks behavioral data (energy, mood, sleep, fog, impulse) over time,
builds pattern intelligence, predicts crashes, tracks substance-outcome correlation,
and logs decisions for 30-day retrospective rating.
Data is compressed into tiered long-term memory so the system retains full context
across months and years without exceeding token limits.

---

## FILE MAP — COMPLETE

### ROOT FILES
| File | Purpose | Status |
|------|---------|--------|
| marlow.py | Main CLI entry point. Menu system. All user interaction. | ACTIVE |
| personas.py | Defines PERSONAS list with system prompts for all 4 personas | ACTIVE |
| requirements.txt | Python dependencies | ACTIVE |
| vault.db | SQLite database. All data lives here. | ACTIVE |
| dump.py | Unknown — not part of core system | UNKNOWN |
| council_responses.txt | Log of past council responses | ACTIVE |

### CORE/ FILES
| File | Purpose | Key Exports | Status |
|------|---------|-------------|--------|
| __init__.py | Makes core/ a Python package | nothing | ACTIVE |
| .env | API keys | GROQ_API_KEY, GROQ_MODEL | ACTIVE |
| database.py | All SQLite operations. Schema. All data read/write. Also contains PersonaChat class. | DatabaseManager, PersonaChat | ACTIVE |
| council_engine.py | Main intelligence orchestration. Runs personas in parallel. Builds context. Injects tiered memory per persona. | classify_intent, run_council, build_synthesis, generate_weekly_report, generate_crash_alert, generate_predictive_crash_warning, maybe_generate_auto_weekly_report, should_generate_monthly_pattern, generate_monthly_pattern, generate_session_brief, is_task_request, execute_task | ACTIVE — UPDATED SESSION 3 |
| groq_client.py | Single entry point for ALL Groq API calls. Retry logic. Rate limit handling. | chat_completion, is_available, GROQ_MODEL | ACTIVE |
| context_builder.py | Builds shared context data structure once per session. Tiered delivery per persona domain. | build_shared_context, build_context_for_persona | ACTIVE |
| memory.py | Cross-persona memory retrieval and context block construction. Accepts persona_name param to inject tiered compressed history. | build_memory_block | ACTIVE — UPDATED SESSION 3 |
| memory_consolidator.py | Tiered memory compression engine. Weekly/monthly/annual compression. Pinned memory system. | maybe_consolidate_memory, get_tiered_context_for_persona, pin_memory, get_pinned_memories, run_pin_menu | ACTIVE — NEW SESSION 3 |
| pattern_engine.py | Deep behavioral pattern analysis. 929 lines. The brain of the system. | PatternEngine (class), get_or_refresh_patterns, build_pattern_context | ACTIVE |
| predictor.py | Predictive crash window. Decision quality. Autonomous intervention. | PredictiveEngine (class), predict_crash_window, predict_relapse_risk, assess_decision_quality_state, build_causal_model, build_prediction_context | ACTIVE |
| correlations.py | Substance-outcome correlation. Relapse risk signature. Cross-variable matrix. Lagged causal analysis. | CorrelationEngine (class), build_substance_context | ACTIVE |
| substance_tracker.py | Substance detection and next-day outcome tracking. | build_substance_context | ACTIVE |
| decision_tracker.py | Decision logging, 30-day retrospective rating, goal momentum scoring. | DecisionTracker (class), GoalMomentumScorer (class), build_decision_context | ACTIVE |
| persona_chat.py | One-on-one chat with a single persona. PLACEHOLDER VERSION — does not use Groq. Real PersonaChat is in database.py | PersonaChat (old placeholder) | NEEDS REWRITE |
| persona_menu.py | Old menu system for persona chat. Imports from persona_chat.py | persona_menu | LEGACY |

---

## THE 4 PERSONAS

### ALDRIC
- Domain: Strategy, economics, systems thinking, long-term consequences
- Voice: Cold, dry, calculated. Finds self-deception quietly amusing.
- Routing: Leads on question/strategy inputs
- Context injected: Decision quality data, goal momentum, financial context
- Tiered memory: Compresses decisions, goal trajectories, strategic outcomes, momentum patterns

### SEREN
- Domain: Emotional intelligence, psychological depth, crisis support
- Voice: Warm but not soft. Reads what wasn't said.
- Routing: Leads on vent and crisis inputs
- Crisis resources she provides: Canada 1-833-456-4566 | Text 686868 | befrienders.org
- Note: ORYN runs silently during vents — his biological data feeds her context
- Tiered memory: Compresses emotional crisis points, mental health patterns, relationship events, what helped vs what didn't

### MORRO
- Domain: Shadow voice. Says what others won't. No moral framework.
- Voice: Short, blunt, irreverent
- Routing: Only runs when impulse_drive >= 6 in last sync. Excluded from crisis routing.
- Context: Minimal — intentional. Receives tiered history but not clinical intelligence blocks.
- Tiered memory: Compresses impulse events, near-misses, reckless decisions, avoidance patterns

### ORYN
- Domain: Clinical/biological. Reads physiology. Neurobiology specialist.
- Voice: Clinical, precise, slightly detached
- Routing: Excluded from vent routing (by design). Runs silently during vents to feed SEREN.
- Context injected: Substance-outcome correlation data
- Tiered memory: Compresses substance patterns, sleep baselines, biological turning points, crash histories

---

## MARLOW
- Not a 5th persona
- Sits above the 4 personas as synthesis layer
- Reads all 4 responses and delivers the single most important takeaway
- Speaks as sovereign intelligence — direct, no filler

---

## MENU STRUCTURE (marlow.py)

1. Morning Sync — logs energy, mood, sleep, fog, impulse at day start
2. Midday Sync — same fields, midday state
3. Evening Sync — same fields, evening state
4. Ask Council — fires all relevant personas in parallel. Main Q&A mode.
5. Journal — free text entry, processed through behavioral tagging
6. View Goals — shows active goals with live momentum scores
7. Weekly Report — generates or displays weekly intelligence report
8. Update Profile — update static profile or life history
9. Persona Chat — one-on-one with single persona
10. Decision Log — log decisions, rate outcomes 30 days later
11. Pinned Memory — pin permanent memories to specific personas (NEW SESSION 3)

---

## STARTUP SEQUENCE (fires every time marlow.py runs)
1. maybe_consolidate_memory(db) — silent compression pass if any tier is due. Prints summary only if something ran.
2. generate_crash_alert() — 0 Groq calls — reactive signal check from recent metrics
3. generate_predictive_crash_warning() — 0 Groq calls — trajectory analysis
4. maybe_generate_auto_weekly_report() — 1 Groq call IF 6+ days since last report
5. Pending decision reviews badge — checks decision_log for unrated decisions
6. Monthly pattern update — fires if 7+ days since last pattern, requires 5+ log entries
7. generate_session_brief() — 1 Groq call — brief on last sync

---

## TIERED MEMORY ARCHITECTURE (NEW SESSION 3)

### The Problem It Solves
Raw log history grows unbounded. Injecting a year of daily syncs into every persona call
burns tokens, hits rate limits, and buries signal in noise. But deleting old data means
losing the longitudinal view that makes MARLOW genuinely intelligent over time.

### The Solution: Compression Without Deletion
Raw data is never deleted from vault.db. Instead, old data is compressed into
progressively smaller representations that still preserve signal. Each persona
gets its own compressed history filtered to its domain only.

### Memory Tiers
- Tier 1 — Raw (0-30 days): Full sync logs. No compression. Existing behavior unchanged.
- Tier 2 — Weekly (30-90 days): Algorithmic compression. No Groq cost. One block per week per persona.
- Tier 3 — Monthly (90 days - 1 year): Groq-generated narrative per persona. 4 calls, fires once per month.
- Tier 4 — Annual (1 year+): Groq-generated arc summary per persona. 4 calls, fires once per year.

### Compression Schedule
- Weekly pass: Runs every 7 days at startup. Compresses all complete weeks older than 30 days.
- Monthly pass: Runs every 30 days at startup. Compresses all complete months older than 90 days.
- Annual pass: Runs every 365 days at startup. Compresses complete years older than 365 days.
All passes are silent unless something actually ran.

### Per-Persona Domain Filters
Each persona compresses only what is relevant to its domain:
- ALDRIC: decisions, goals, strategic outcomes, momentum patterns, financial moves
- SEREN: emotional events, mental health patterns, relationship changes, crisis points
- ORYN: substance patterns, sleep data, biological turning points, crash histories
- MORRO: impulse events, reckless decisions, near-misses, self-sabotage patterns

### Pinned Memory System
Any memory can be pinned to a specific persona via menu option 11.
Pinned memories always appear in that persona's context regardless of age.
The operator can manually pin anything. The system can also auto-pin during compression.
Managed via run_pin_menu(db) in memory_consolidator.py.

### New DB Tables Added
- compressed_memories: stores per-persona per-tier compression blocks
- pinned_memories: stores permanent pinned memories per persona
- consolidation_log: records when each tier last ran

### Design Philosophy (from operator)
The operator wants to look back a year from now and see the difference between then and now.
Old daily drug logs from 11 months ago are noise — but the month they quit, the week they
had a breakdown, the quarter the business took off — that is signal.
Compression preserves signal, discards noise.
Each persona remembers differently because each cares about different things.

---

## SMART ROUTING + SILENT PERSONAS (built session 2, preserved)

### classify_intent() routing rules
- Pure emotion, no action request → SEREN active, others silent
- Emotion + wants solution → SEREN + ALDRIC active, ORYN silent, MORRO off unless impulse detected
- Strategy/business/money → ALDRIC active, MORRO active if risk present, SEREN silent, ORYN silent
- Health/stats/data/biology → ORYN active, ALDRIC active, SEREN silent
- Risky/impulsive → MORRO active, ALDRIC active, SEREN silent, ORYN silent
- Crisis → SEREN active, ORYN active, ALDRIC silent, MORRO off
- Mixed/general → all active
- MARLOW always synthesizes regardless

### Silent persona behavior
Personas marked "silent" do not make API calls and do not display to the operator.
Their domain perspective is summarized by _build_silent_persona_summaries() and
injected into MARLOW's synthesis prompt so MARLOW always has the full picture.

### Return dict from run_council()
{ intent_type, lead, responses, raw, silent_names, silent_context }

---

## GROQ API CALL COUNT PER MODE
- Question: 5-6 calls (1 classify + 3-4 personas + 1 synthesis)
- Vent: 5 calls (1 classify + 3 personas + 1 synthesis)
- Task: 1 call
- Journal: 5-6 calls
- Persona Chat: 1 call per message
- Startup: 1 call (brief) + 1 if weekly report due
- Memory consolidation weekly pass: 0 Groq calls (algorithmic)
- Memory consolidation monthly pass: 4 Groq calls (once per month, fires rarely)
- Memory consolidation annual pass: 4 Groq calls (once per year, fires rarely)

Rate limit: Free tier hits token-per-minute ceiling when all persona prompts are large.
Fix: Upgrade Groq to paid tier (Developer plan) at console.groq.com

---

## KNOWN ISSUES

### ISSUE 1 — RESOLVED: council_engine.py import syntax error
Fixed in the session 3 council_engine.py. No action needed if using that file.

### ISSUE 2 — RESOLVED: Dim imports in council_engine.py
Fixed in the session 3 council_engine.py. No action needed if using that file.

### ISSUE 3 — COSMETIC: os.system line-through in marlow.py
The line: os.system("cls" if os.name == "nt" else "clear")
VS Code flags os.system() with a strikethrough as a security warning.
This is cosmetic only. It works fine. Ignore it.

### ISSUE 4 — PENDING: persona_chat.py rewrite
The persona_chat.py in core/ is an old non-functional placeholder.
It does not use Groq. It returns fake hardcoded responses.
The REAL PersonaChat class that uses Groq lives in database.py.
marlow.py correctly imports PersonaChat from core.database.
persona_menu.py incorrectly imports from core.persona_chat.
ACTION: persona_chat.py needs to be rewritten with real Groq integration.
Menu option 9 still works because marlow.py uses database.py's version.

### ISSUE 5 — PENDING: Silent ORYN during vents
Currently ORYN is excluded from vents entirely.
Planned: ORYN runs silently during vents, his biological analysis feeds SEREN's context.
council_engine.py run_council() needs modification to support this.
NOT YET BUILT.

---

## WHAT WAS BUILT SESSION 3 (files to copy)

### Files delivered:
1. memory_consolidator.py → core/memory_consolidator.py (NEW — 878 lines)
2. memory.py → core/memory.py (REPLACEMENT — 191 lines)
3. council_engine.py → core/council_engine.py (REPLACEMENT — 1303 lines)
4. marlow.py → marlow.py (REPLACEMENT — 1138 lines)

### What changed in each file:

**memory_consolidator.py (NEW)**
- Full tiered memory compression system
- _compress_week_algorithmic(): weekly pass, no Groq, filters by persona domain keywords
- _compress_month_groq(): monthly pass, 4 Groq calls, narrative synthesis per persona
- _compress_year_groq(): annual pass, 4 Groq calls, arc summary per persona
- maybe_consolidate_memory(): startup entry point, checks all three tiers, returns action log
- get_tiered_context_for_persona(): returns full compressed history for a single persona
- pin_memory() / unpin_memory() / get_pinned_memories(): pinned memory CRUD
- run_pin_menu(): interactive CLI for managing pinned memories (called from menu option 11)
- New DB tables: compressed_memories, pinned_memories, consolidation_log
- PERSONA_DOMAINS dict: domain focus, metric fields, and keywords per persona

**memory.py (UPDATED)**
- build_memory_block() now accepts optional persona_name parameter
- When persona_name provided, calls get_tiered_context_for_persona() and appends
  compressed historical context block after the raw logs block
- Graceful fallback: if consolidator not installed or no compressed history yet, unchanged

**council_engine.py (UPDATED)**
- Graceful import of memory_consolidator at top (_TIERED_MEMORY_AVAILABLE flag)
- build_persona_prompt() now accepts tiered_history parameter
- tiered_history injected into intelligence_block for all personas
  (MORRO receives tiered history but not clinical blocks — intentional)
- run_council() fetches per-persona tiered history before building task list
  tiered_histories dict built once per session, passed into each build_persona_prompt() call
- All prior changes preserved: smart routing, silent personas, offline messages,
  pattern/prediction/substance/decision context injection

**marlow.py (UPDATED)**
- Imports maybe_consolidate_memory and run_pin_menu from core.memory_consolidator
  with graceful fallback (system works even if consolidator not yet copied to core/)
- run_startup_sequence() now calls maybe_consolidate_memory(db) as first step
  Prints consolidation summary only if something actually ran, silent otherwise
- Added run_pinned_memory() function routing to run_pin_menu(db)
- Menu option 11 added: Pinned Memory
- main() handles choice "11"

---

## ARCHITECTURE NOTES FOR CLAUDE
- ThreadPoolExecutor with 0.4s stagger between persona calls
- WAL mode on SQLite
- MORRO excluded from crisis routing (hardcoded)
- ORYN excluded from vent routing (intentional by design)
- Ollama fallback exists in PersonaChat only
- Context char limits: pattern 1200, prediction 1000, substance 800, decision 500
- pattern_engine.py uses PatternEngine.format_insights_for_context() not build_pattern_context()
- correlations.py also has build_substance_context() — substance_tracker.py has its own version too
- The compatibility wrapper in pattern_engine.py bridges old build_pattern_context() calls
- memory_consolidator.py is a graceful-fallback import everywhere — system works without it
- compressed_memories table is additive — raw data in vault.db is never touched or deleted
- Compression only affects what gets injected into context, not what is stored
- PERSONA_DOMAINS in memory_consolidator.py drives all domain filtering logic
- _TIERED_MEMORY_AVAILABLE flag in council_engine.py and marlow.py guards all consolidator calls

---

## NEXT THINGS TO BUILD (in priority order)
1. Silent ORYN during vents — ORYN runs silently, feeds SEREN's biological context (Issue 5)
2. Rewrite persona_chat.py with real Groq integration (Issue 4)
3. Verify memory_consolidator weekly pass runs correctly after 30+ days of data accumulate
4. Consider auto-pin logic inside _compress_month_groq() for flagged high-signal events

---

## HOW TO START A NEW SESSION
Paste this entire file at the start of a new Claude chat, then say:
"I am Antonio. This is my Marlow system blueprint. [describe what you need]"

Claude will have full context immediately.
