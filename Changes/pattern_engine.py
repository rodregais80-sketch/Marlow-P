"""
pattern_engine.py — SOVEREIGN PATTERN RECOGNITION ENGINE
The most sophisticated analytical layer in the system.

This engine does not just count what happened.
It finds the hidden structure underneath what happened.

Capabilities:
  - Emotional signature mapping (what makes this operator genuinely flourish)
  - Danger signature detection (the unique fingerprint before a crash)
  - Temporal pattern analysis (day-of-week, time-of-day, monthly cycles)
  - Linguistic pattern extraction (what words appear in good vs bad states)
  - Sequence detection (N good days → crash, productive morning → afternoon drop)
  - Multi-variable correlation matrix
  - Happiness/excitement condition mapping
  - Activity-to-outcome correlation
  - AI-synthesized insight generation from raw pattern data
"""

import re
import math
import json
from datetime import datetime, timedelta, timezone
from collections import defaultdict, Counter


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

MIN_ENTRIES_FOR_PATTERNS  = 10
MIN_ENTRIES_FOR_TEMPORAL  = 14
MIN_ENTRIES_FOR_SEQUENCE  = 20

EMOTION_LEXICON = {
    "joy":        ["happy", "joy", "joyful", "great", "amazing", "wonderful", "fantastic",
                   "thrilled", "delighted", "elated", "content", "love", "loving", "loved",
                   "good", "well", "positive", "bright", "light", "free", "alive"],
    "excitement": ["excited", "pumped", "fired up", "hyped", "energized", "electric",
                   "buzzing", "ready", "can't wait", "motivated", "driven", "on fire",
                   "unstoppable", "invincible", "momentum", "locked in", "dialed"],
    "pride":      ["proud", "accomplished", "achieved", "nailed", "crushed", "smashed",
                   "earned", "built", "created", "finished", "completed", "delivered",
                   "strong", "capable", "skilled", "good at", "better"],
    "peace":      ["calm", "peaceful", "centered", "grounded", "clear", "settled",
                   "balanced", "at ease", "relaxed", "still", "present", "focused",
                   "quiet", "serene", "stable"],
    "anxiety":    ["anxious", "worried", "nervous", "scared", "fear", "afraid", "panic",
                   "overwhelmed", "stress", "stressed", "tense", "tight", "racing",
                   "spinning", "can't stop", "too much", "falling apart"],
    "anger":      ["angry", "pissed", "furious", "frustrated", "annoyed", "irritated",
                   "rage", "mad", "livid", "fed up", "done", "over it", "sick of",
                   "hate", "resent"],
    "shame":      ["ashamed", "guilty", "embarrassed", "pathetic", "failure", "loser",
                   "worthless", "stupid", "idiot", "weak", "can't", "never", "always fail",
                   "disappointing", "let down", "waste", "useless"],
    "despair":    ["hopeless", "pointless", "give up", "giving up", "done", "finished",
                   "can't go on", "what's the point", "nothing matters", "empty",
                   "hollow", "numb", "dead inside", "don't care anymore", "alone"],
    "drive":      ["working", "building", "grinding", "hustling", "pushing", "moving",
                   "making progress", "getting it done", "executing", "output", "produced",
                   "accomplished", "active", "busy", "operational"],
    "connection": ["talked to", "called", "met with", "friends", "family", "connected",
                   "laughed", "people", "together", "community", "belonged", "seen",
                   "understood", "supported"],
    "trapped":    ["trapped", "stuck", "can't escape", "no way out", "ceiling", "wall",
                   "blocked", "going nowhere", "same place", "spinning wheels", "hamster",
                   "repeat", "cycle", "same shit"],
    "hunger":     ["want", "need", "craving", "want more", "not enough", "hungry",
                   "ambitious", "bigger", "more", "level up", "next level", "beyond this"]
}

POSITIVE_EMOTIONS = {"joy", "excitement", "pride", "peace", "drive", "connection", "hunger"}

_STOP_WORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of",
    "with", "is", "was", "are", "were", "be", "been", "have", "has", "had",
    "do", "does", "did", "will", "would", "could", "should", "i", "you", "he",
    "she", "it", "we", "they", "my", "your", "his", "her", "its", "our", "their",
    "this", "that", "these", "those", "from", "by", "up", "out", "not", "so",
    "if", "as", "into", "after", "before", "just", "then", "when", "where",
    "all", "any", "more", "also", "very", "still", "than", "too", "s", "t",
    "m", "re", "ve", "ll", "d", "no", "yes", "ok", "yeah", "got", "get",
    "went", "go", "going", "came", "come", "day", "today", "yesterday", "week",
    "time", "thing", "things", "something", "anything", "nothing", "everything",
    "some", "much", "many", "most", "really", "actually", "kind", "like", "feel",
    "felt", "feeling", "think", "thinking", "thought", "know", "knowing", "knew"
}


# ─────────────────────────────────────────────────────────────────────────────
# UTILITY FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def _safe_val(row, key, default=None):
    try:
        v = row[key]
        return v if v is not None else default
    except (KeyError, IndexError, TypeError):
        return default


def _avg(lst):
    return round(sum(lst) / len(lst), 2) if lst else None


def _std(lst):
    if len(lst) < 2:
        return 0.0
    mean = sum(lst) / len(lst)
    return round(math.sqrt(sum((x - mean) ** 2 for x in lst) / len(lst)), 2)


def _pearson(x: list, y: list) -> float:
    """Pearson correlation coefficient between two numeric lists."""
    n = min(len(x), len(y))
    if n < 3:
        return 0.0
    x, y = x[:n], y[:n]
    mx, my = sum(x) / n, sum(y) / n
    num   = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    denom = math.sqrt(
        sum((x[i] - mx) ** 2 for i in range(n)) *
        sum((y[i] - my) ** 2 for i in range(n))
    )
    return round(num / denom, 3) if denom else 0.0


def _extract_text_from_log(content: str) -> str:
    """Strips key: value structure from sync logs to get narrative text."""
    lines = []
    for line in content.split("\n"):
        if ":" in line:
            parts = line.split(":", 1)
            if len(parts) > 1:
                lines.append(parts[1].strip())
        else:
            lines.append(line.strip())
    return " ".join(lines)


def _keywords(text: str, min_length: int = 4) -> list:
    """Extracts meaningful keywords from text."""
    words = re.findall(r"[a-z']+", text.lower())
    return [w for w in words if len(w) >= min_length and w not in _STOP_WORDS]


def _score_emotions(text: str) -> dict:
    """
    Scores text against each emotional dimension in the EMOTION_LEXICON.
    Returns {emotion: score} where score is 0.0-1.0.
    Normalized by text length so longer texts don't inflate scores.
    """
    text_lower = text.lower()
    word_count = max(len(text_lower.split()), 1)
    scores = {}

    for emotion, keywords in EMOTION_LEXICON.items():
        hits = sum(
            1 for kw in keywords
            if re.search(r'\b' + re.escape(kw) + r'\b', text_lower)
        )
        scores[emotion] = round(min(hits / max(word_count * 0.05, 1), 1.0), 3)

    return scores


# ─────────────────────────────────────────────────────────────────────────────
# CORE PATTERN ENGINE CLASS
# ─────────────────────────────────────────────────────────────────────────────

class PatternEngine:
    """
    The sovereign pattern recognition system.

    All analysis is performed against the operator's actual logged data.
    No external benchmarks. No population averages. Pure self-referential intelligence.
    """

    def __init__(self, db):
        self.db = db
        self._cache = {}

    def _load_metrics(self, limit: int = 90) -> list:
        """Load metrics in chronological order (oldest first for sequence analysis)."""
        rows = self.db.get_recent_metrics(limit=limit)
        return list(reversed(rows))

    def _load_logs(self, limit: int = 90) -> list:
        rows = self.db.get_recent_logs(limit=limit)
        return list(reversed(rows))

    def _load_journals(self, limit: int = 60) -> list:
        try:
            cursor = self.db.conn.cursor()
            cursor.execute(
                "SELECT * FROM journals ORDER BY timestamp DESC LIMIT ?", (limit,)
            )
            rows = cursor.fetchall()
            return list(reversed(rows))
        except Exception:
            return []

    # ── 1. EMOTIONAL SIGNATURE MAPPING ───────────────────────────────────────

    def analyze_emotional_signatures(self) -> dict:
        """
        Builds the operator's emotional profile from all logged text.
        Identifies dominant emotional states, emotional range, and
        which emotions appear most frequently across all conditions.
        """
        logs     = self._load_logs(limit=90)
        journals = self._load_journals(limit=60)

        all_texts           = []
        timestamped_emotions = []

        for log in logs:
            content   = _safe_val(log, "content", "") or ""
            timestamp = _safe_val(log, "timestamp", "") or ""
            text      = _extract_text_from_log(content)
            if text.strip():
                all_texts.append(text)
                scores = _score_emotions(text)
                timestamped_emotions.append((timestamp, scores))

        for j in journals:
            content   = _safe_val(j, "content", "") or ""
            timestamp = _safe_val(j, "timestamp", "") or ""
            if content.strip():
                all_texts.append(content)
                scores = _score_emotions(content)
                timestamped_emotions.append((timestamp, scores))

        if not timestamped_emotions:
            return {"status": "insufficient_data", "entries_analyzed": 0}

        emotion_totals = defaultdict(float)
        emotion_counts = defaultdict(int)

        for _, scores in timestamped_emotions:
            for emotion, score in scores.items():
                if score > 0:
                    emotion_totals[emotion] += score
                    emotion_counts[emotion] += 1

        baseline = {}
        for emotion in EMOTION_LEXICON:
            if emotion_counts[emotion] > 0:
                baseline[emotion] = round(emotion_totals[emotion] / len(timestamped_emotions), 4)
            else:
                baseline[emotion] = 0.0

        sorted_emotions   = sorted(baseline.items(), key=lambda x: -x[1])
        dominant_emotions = [e for e, v in sorted_emotions[:3] if v > 0]
        positive_total    = sum(baseline.get(e, 0) for e in POSITIVE_EMOTIONS)
        negative_total    = sum(baseline.get(e, 0) for e in {"anxiety", "shame", "despair", "anger", "trapped"})
        emotional_valence = round(positive_total / max(positive_total + negative_total, 0.001), 2)

        return {
            "status":            "complete",
            "entries_analyzed":  len(timestamped_emotions),
            "baseline":          baseline,
            "dominant_emotions": dominant_emotions,
            "positive_baseline": round(positive_total, 3),
            "negative_baseline": round(negative_total, 3),
            "emotional_valence": emotional_valence,
            "emotional_range":   sorted_emotions,
        }

    # ── 2. HAPPINESS / FLOURISHING SIGNATURE ─────────────────────────────────

    def map_flourishing_conditions(self) -> dict:
        """
        Identifies the specific combination of conditions that correlate
        with the operator's highest emotional and performance states.
        """
        metrics = self._load_metrics(limit=90)
        logs    = self._load_logs(limit=90)

        if len(metrics) < MIN_ENTRIES_FOR_PATTERNS:
            return {"status": "insufficient_data", "minimum_required": MIN_ENTRIES_FOR_PATTERNS}

        high_state_entries = []
        low_state_entries  = []

        for m in metrics:
            energy  = _safe_val(m, "energy", 0) or 0
            mood    = _safe_val(m, "mood", 0) or 0
            sleep   = _safe_val(m, "sleep_hours", 0) or 0
            fog     = _safe_val(m, "mental_fog", 0) or 0
            impulse = _safe_val(m, "impulse_drive", 0) or 0
            ts      = _safe_val(m, "timestamp", "") or ""
            stype   = _safe_val(m, "sync_type", "") or ""

            entry = {"timestamp": ts, "energy": energy, "mood": mood,
                     "sleep": sleep, "fog": fog, "impulse": impulse, "sync_type": stype}

            if energy >= 7 and mood >= 7:
                high_state_entries.append(entry)
            elif energy <= 4 or mood <= 4:
                low_state_entries.append(entry)

        if not high_state_entries:
            return {"status": "no_high_state_data", "note": "No entries with energy AND mood >= 7"}

        high_sleep_avg   = _avg([e["sleep"]   for e in high_state_entries if e["sleep"]])
        high_fog_avg     = _avg([e["fog"]     for e in high_state_entries if e["fog"]])
        high_impulse_avg = _avg([e["impulse"] for e in high_state_entries if e["impulse"]])
        low_sleep_avg    = _avg([e["sleep"]   for e in low_state_entries  if e["sleep"]]) if low_state_entries else None
        low_fog_avg      = _avg([e["fog"]     for e in low_state_entries  if e["fog"]])   if low_state_entries else None

        day_counts      = Counter()
        hour_counts     = Counter()
        sync_type_counts = Counter()

        for e in high_state_entries:
            ts = e.get("timestamp", "")
            if ts:
                try:
                    dt = datetime.fromisoformat(ts)
                    day_counts[dt.strftime("%A")] += 1
                    hour_counts[dt.hour] += 1
                except Exception:
                    pass
            stype = e.get("sync_type", "")
            if stype:
                sync_type_counts[stype] += 1

        best_days = [d for d, c in day_counts.most_common(3)]
        best_sync = [s for s, c in sync_type_counts.most_common(2)]

        high_ts_set = set(e["timestamp"][:10] for e in high_state_entries)
        high_words  = []
        for log in logs:
            ts = (_safe_val(log, "timestamp", "") or "")[:10]
            if ts in high_ts_set:
                text = _extract_text_from_log(_safe_val(log, "content", "") or "")
                high_words.extend(_keywords(text))

        low_ts_set = set(e["timestamp"][:10] for e in low_state_entries)
        low_words  = []
        for log in logs:
            ts = (_safe_val(log, "timestamp", "") or "")[:10]
            if ts in low_ts_set:
                text = _extract_text_from_log(_safe_val(log, "content", "") or "")
                low_words.extend(_keywords(text))

        high_word_freq = Counter(high_words).most_common(20)
        low_word_set   = {w for w, _ in Counter(low_words).most_common(50)}
        flourish_words = [(w, c) for w, c in high_word_freq if w not in low_word_set][:10]

        return {
            "status":               "complete",
            "high_state_count":     len(high_state_entries),
            "low_state_count":      len(low_state_entries),
            "flourishing_conditions": {
                "sleep_hours":   high_sleep_avg,
                "brain_fog":     high_fog_avg,
                "impulse_drive": high_impulse_avg,
            },
            "struggle_conditions": {
                "sleep_hours": low_sleep_avg,
                "brain_fog":   low_fog_avg,
            },
            "best_days_of_week":  best_days,
            "best_sync_types":    best_sync,
            "flourishing_language": flourish_words,
            "sleep_delta": round((high_sleep_avg or 0) - (low_sleep_avg or 0), 1),
        }

    # ── 3. DANGER SIGNATURE — CRASH FINGERPRINT ──────────────────────────────

    def map_danger_signature(self) -> dict:
        """
        Identifies the unique fingerprint that appears in the operator's data
        BEFORE a crash event, not after.

        A crash is defined as: energy drops 3+ points over 3 consecutive entries.
        The signature is the 3-5 entries that preceded the crash.
        """
        metrics = self._load_metrics(limit=120)

        if len(metrics) < MIN_ENTRIES_FOR_SEQUENCE:
            return {"status": "insufficient_data", "minimum_required": MIN_ENTRIES_FOR_SEQUENCE}

        energy_series = [(
            _safe_val(m, "timestamp", ""),
            _safe_val(m, "energy", 5) or 5,
            _safe_val(m, "mood", 5) or 5,
            _safe_val(m, "sleep_hours", 6) or 6,
            _safe_val(m, "mental_fog", 3) or 3,
            _safe_val(m, "impulse_drive", 3) or 3,
        ) for m in metrics]

        crash_events      = []
        pre_crash_windows = []

        for i in range(2, len(energy_series)):
            e0 = energy_series[i-2][1]
            ts2, e2 = energy_series[i][0], energy_series[i][1]
            ts0     = energy_series[i-2][0]
            if e0 - e2 >= 3:
                crash_events.append({
                    "peak_ts":     ts0,
                    "crash_ts":    ts2,
                    "peak_energy": e0,
                    "crash_energy": e2,
                    "drop":        e0 - e2
                })
                pre_start = max(0, i - 7)
                pre_crash_windows.append(energy_series[pre_start:i-2])

        if not crash_events:
            return {"status": "no_crash_events_detected",
                    "note": "No energy drops of 3+ points found in data yet."}

        pre_energy, pre_sleep, pre_fog, pre_impulse = [], [], [], []
        for window in pre_crash_windows:
            for entry in window:
                pre_energy.append(entry[1])
                pre_sleep.append(entry[3])
                pre_fog.append(entry[4])
                pre_impulse.append(entry[5])

        avg_peak_drop = _avg([e["drop"] for e in crash_events])

        recent = metrics[-3:] if len(metrics) >= 3 else metrics
        current_energy_trend = [_safe_val(m, "energy", 5) or 5 for m in recent]
        current_impulse_avg  = _avg([_safe_val(m, "impulse_drive", 0) or 0 for m in recent])
        current_sleep_avg    = _avg([_safe_val(m, "sleep_hours", 0) or 0 for m in recent])

        match_signals = 0
        if current_energy_trend and _avg(current_energy_trend) >= (_avg(pre_energy) or 5) - 1:
            match_signals += 1
        if current_impulse_avg and current_impulse_avg >= (_avg(pre_impulse) or 5) - 1:
            match_signals += 1
        if current_sleep_avg and current_sleep_avg <= (_avg(pre_sleep) or 6) + 0.5:
            match_signals += 1

        match_pct = round(match_signals / 3 * 100)

        return {
            "status":              "complete",
            "crash_events_found":  len(crash_events),
            "avg_peak_drop":       avg_peak_drop,
            "pre_crash_signature": {
                "energy_avg":  _avg(pre_energy),
                "sleep_avg":   _avg(pre_sleep),
                "fog_avg":     _avg(pre_fog),
                "impulse_avg": _avg(pre_impulse),
                "note":        "Average conditions in the 5 entries before a crash."
            },
            "crash_events":            crash_events[-5:],
            "current_signature_match": match_pct,
            "intervention_urgency":    "HIGH" if match_pct >= 67 else "MODERATE" if match_pct >= 33 else "LOW"
        }

    # ── 4. TEMPORAL PATTERN ANALYSIS ─────────────────────────────────────────

    def analyze_temporal_patterns(self) -> dict:
        """
        Finds patterns by time — day of week, time of day, month.
        Answers: when is this operator at their best? When are they most at risk?
        """
        metrics = self._load_metrics(limit=120)

        if len(metrics) < MIN_ENTRIES_FOR_TEMPORAL:
            return {"status": "insufficient_data"}

        dow_energy  = defaultdict(list)
        dow_mood    = defaultdict(list)
        dow_impulse = defaultdict(list)
        sync_energy = defaultdict(list)
        sync_fog    = defaultdict(list)
        hour_energy = defaultdict(list)

        for m in metrics:
            ts      = _safe_val(m, "timestamp", "") or ""
            energy  = _safe_val(m, "energy")
            mood    = _safe_val(m, "mood")
            impulse = _safe_val(m, "impulse_drive")
            fog     = _safe_val(m, "mental_fog")
            stype   = _safe_val(m, "sync_type", "unknown") or "unknown"

            if ts:
                try:
                    dt  = datetime.fromisoformat(ts)
                    dow = dt.strftime("%A")
                    hr  = dt.hour
                    if energy  is not None: dow_energy[dow].append(energy)
                    if mood    is not None: dow_mood[dow].append(mood)
                    if impulse is not None: dow_impulse[dow].append(impulse)
                    if energy  is not None: hour_energy[hr].append(energy)
                except Exception:
                    pass

            if energy is not None: sync_energy[stype].append(energy)
            if fog    is not None: sync_fog[stype].append(fog)

        day_order  = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        dow_report = {}
        for day in day_order:
            if dow_energy.get(day):
                dow_report[day] = {
                    "energy":  _avg(dow_energy[day]),
                    "mood":    _avg(dow_mood.get(day, [])),
                    "impulse": _avg(dow_impulse.get(day, [])),
                    "n":       len(dow_energy[day])
                }

        best_day  = max(dow_report, key=lambda d: dow_report[d]["energy"], default=None)
        worst_day = min(dow_report, key=lambda d: dow_report[d]["energy"], default=None)

        sync_report = {}
        for stype in ["morning", "midday", "evening"]:
            if sync_energy.get(stype):
                sync_report[stype] = {
                    "avg_energy": _avg(sync_energy[stype]),
                    "avg_fog":    _avg(sync_fog.get(stype, [])),
                    "n":          len(sync_energy[stype])
                }

        hour_avgs   = {h: _avg(vals) for h, vals in hour_energy.items() if len(vals) >= 2}
        peak_hour   = max(hour_avgs, key=lambda h: hour_avgs[h], default=None)
        lowest_hour = min(hour_avgs, key=lambda h: hour_avgs[h], default=None)

        return {
            "status":       "complete",
            "day_of_week":  dow_report,
            "best_day":     best_day,
            "worst_day":    worst_day,
            "sync_time":    sync_report,
            "peak_hour":    peak_hour,
            "lowest_hour":  lowest_hour,
            "note":         "All times approximate based on sync timestamps."
        }

    # ── 5. LINGUISTIC PATTERN ANALYSIS ───────────────────────────────────────

    def analyze_linguistic_patterns(self) -> dict:
        """
        Mines the language the operator actually uses across all entries.
        Finds words unique to high states vs low states, and recent emotional tone.
        """
        metrics  = self._load_metrics(limit=90)
        logs     = self._load_logs(limit=90)
        journals = self._load_journals(limit=60)

        metric_by_date = {}
        for m in metrics:
            ts     = (_safe_val(m, "timestamp", "") or "")[:10]
            energy = _safe_val(m, "energy") or 0
            mood   = _safe_val(m, "mood") or 0
            if ts:
                metric_by_date[ts] = {"energy": energy, "mood": mood}

        high_words      = []
        low_words       = []
        all_words       = []
        emotion_by_date = {}

        for log in logs:
            ts      = (_safe_val(log, "timestamp", "") or "")[:10]
            content = _safe_val(log, "content", "") or ""
            text    = _extract_text_from_log(content)
            words   = _keywords(text)
            all_words.extend(words)

            m = metric_by_date.get(ts, {})
            e, mo = m.get("energy", 0), m.get("mood", 0)
            if e >= 7 and mo >= 7:
                high_words.extend(words)
            elif e <= 4 or mo <= 4:
                low_words.extend(words)

            emo = _score_emotions(text)
            emotion_by_date[ts] = emo

        for j in journals:
            ts      = (_safe_val(j, "timestamp", "") or "")[:10]
            content = _safe_val(j, "content", "") or ""
            if content.strip():
                words = _keywords(content)
                all_words.extend(words)
                emo = _score_emotions(content)
                if ts in emotion_by_date:
                    for k in emo:
                        emotion_by_date[ts][k] = max(emotion_by_date[ts].get(k, 0), emo[k])
                else:
                    emotion_by_date[ts] = emo

        all_freq  = Counter(all_words)
        high_freq = Counter(high_words)
        low_freq  = Counter(low_words)

        low_set    = {w for w, c in low_freq.most_common(100)}
        high_set   = {w for w, c in high_freq.most_common(100)}
        unique_high = [(w, c) for w, c in high_freq.most_common(30) if w not in low_set][:15]
        unique_low  = [(w, c) for w, c in low_freq.most_common(30)  if w not in high_set][:15]

        emo_timeline      = sorted(emotion_by_date.items(), key=lambda x: x[0])
        recent_emotion_avg = defaultdict(list)
        for ts, emo in emo_timeline[-14:]:
            for k, v in emo.items():
                recent_emotion_avg[k].append(v)
        recent_emotion_summary = {k: _avg(v) for k, v in recent_emotion_avg.items() if v}

        return {
            "status":                 "complete",
            "total_words_analyzed":   len(all_words),
            "most_common_words":      all_freq.most_common(25),
            "flourishing_language":   unique_high,
            "distress_markers":       unique_low,
            "recent_emotion_profile": recent_emotion_summary,
            "dominant_emotion_recent": max(
                recent_emotion_summary, key=lambda k: recent_emotion_summary[k], default="unknown"
            )
        }

    # ── 6. SEQUENCE PATTERN DETECTION ────────────────────────────────────────

    def detect_sequences(self) -> dict:
        """
        Detects behavioral sequences — repeating patterns of N events
        that reliably precede specific outcomes.
        """
        metrics = self._load_metrics(limit=120)

        if len(metrics) < MIN_ENTRIES_FOR_SEQUENCE:
            return {"status": "insufficient_data", "minimum_required": MIN_ENTRIES_FOR_SEQUENCE}

        energy_vals  = [_safe_val(m, "energy", 5) or 5 for m in metrics]
        sleep_vals   = [_safe_val(m, "sleep_hours", 6) or 6 for m in metrics]
        fog_vals     = [_safe_val(m, "mental_fog", 3) or 3 for m in metrics]
        impulse_vals = [_safe_val(m, "impulse_drive", 3) or 3 for m in metrics]

        sequences = []
        n = len(energy_vals)

        # Pattern 1: N consecutive high energy → crash
        consecutive_high  = 0
        high_to_crash_count = 0
        high_sequences    = 0
        for i in range(n):
            if energy_vals[i] >= 7:
                consecutive_high += 1
            else:
                if consecutive_high >= 3:
                    high_sequences += 1
                    if i < n - 1 and energy_vals[i] <= 4:
                        high_to_crash_count += 1
                consecutive_high = 0

        if high_sequences > 0:
            crash_after_high_pct = round(high_to_crash_count / high_sequences * 100)
            sequences.append({
                "pattern":      "3+ consecutive high-energy entries",
                "outcome":      "energy crash (drop to <=4)",
                "occurrences":  high_sequences,
                "outcome_rate": f"{crash_after_high_pct}%",
                "predictive":   crash_after_high_pct >= 50
            })

        # Pattern 2: 2 consecutive low sleep → fog spike
        low_sleep_fog_pairs = 0
        low_sleep_runs      = 0
        for i in range(1, n):
            if sleep_vals[i-1] < 5.5 and sleep_vals[i] < 5.5:
                low_sleep_runs += 1
                if i + 1 < n and fog_vals[i+1] >= 7:
                    low_sleep_fog_pairs += 1

        if low_sleep_runs > 0:
            fog_after_sleep_pct = round(low_sleep_fog_pairs / low_sleep_runs * 100)
            sequences.append({
                "pattern":      "2 consecutive low-sleep nights (<5.5h)",
                "outcome":      "elevated brain fog (>=7)",
                "occurrences":  low_sleep_runs,
                "outcome_rate": f"{fog_after_sleep_pct}%",
                "predictive":   fog_after_sleep_pct >= 50
            })

        # Pattern 3: Rising impulse trend → impulse peak
        impulse_escalation    = 0
        peak_after_escalation = 0
        for i in range(2, n):
            if impulse_vals[i-2] < impulse_vals[i-1] < impulse_vals[i]:
                impulse_escalation += 1
                if impulse_vals[i] >= 8:
                    peak_after_escalation += 1

        if impulse_escalation > 0:
            sequences.append({
                "pattern":      "3-step rising impulse trend",
                "outcome":      "peak impulse event (>=8)",
                "occurrences":  impulse_escalation,
                "outcome_rate": f"{round(peak_after_escalation / impulse_escalation * 100)}%",
                "predictive":   (peak_after_escalation / impulse_escalation) >= 0.4
            })

        # Pattern 4: Post-high-energy fog lag
        energy_fog_lag = 0
        energy_spikes  = 0
        for i in range(n - 2):
            if energy_vals[i] >= 8:
                energy_spikes += 1
                if fog_vals[i+2] >= (fog_vals[i] + 2):
                    energy_fog_lag += 1

        if energy_spikes > 0:
            sequences.append({
                "pattern":      "energy spike (>=8) at entry N",
                "outcome":      "fog increase (+2 points) at entry N+2",
                "occurrences":  energy_spikes,
                "outcome_rate": f"{round(energy_fog_lag / energy_spikes * 100)}%",
                "predictive":   (energy_fog_lag / energy_spikes) >= 0.4
            })

        predictive_seqs = [s for s in sequences if s.get("predictive")]
        top_sequence    = max(predictive_seqs, key=lambda s: s["occurrences"], default=None)

        return {
            "status":      "complete",
            "sequences":   sequences,
            "top_pattern": top_sequence,
            "total_found": len(sequences)
        }

    # ── 7. CORRELATION MATRIX ────────────────────────────────────────────────

    def compute_correlation_matrix(self) -> dict:
        """
        Pearson correlation between every pair of tracked metrics.
        Reveals the hidden structure of how this operator's variables relate.
        """
        metrics = self._load_metrics(limit=90)

        if len(metrics) < MIN_ENTRIES_FOR_PATTERNS:
            return {"status": "insufficient_data"}

        fields = ["energy", "mood", "mental_fog", "impulse_drive", "sleep_hours"]
        series = {}
        for f in fields:
            series[f] = [_safe_val(m, f) for m in metrics]

        for f in fields:
            vals = [v for v in series[f] if v is not None]
            mean = _avg(vals) or 5
            series[f] = [v if v is not None else mean for v in series[f]]

        matrix = {}
        for f1 in fields:
            matrix[f1] = {}
            for f2 in fields:
                if f1 == f2:
                    matrix[f1][f2] = 1.0
                else:
                    matrix[f1][f2] = _pearson(series[f1], series[f2])

        notable = []
        seen    = set()
        for f1 in fields:
            for f2 in fields:
                if f1 == f2:
                    continue
                key = tuple(sorted([f1, f2]))
                if key in seen:
                    continue
                seen.add(key)
                r = matrix[f1][f2]
                if abs(r) >= 0.4:
                    direction = "positive" if r > 0 else "negative"
                    strength  = "strong" if abs(r) >= 0.7 else "moderate"
                    notable.append({
                        "var1": f1, "var2": f2,
                        "r": r,
                        "direction": direction,
                        "strength":  strength,
                        "plain": f"{f1} and {f2} have a {strength} {direction} correlation ({r:+.2f})"
                    })

        notable.sort(key=lambda x: -abs(x["r"]))

        return {
            "status":   "complete",
            "matrix":   matrix,
            "notable":  notable,
            "strongest": notable[0] if notable else None
        }

    # ── 8. DECISION QUALITY TRACKING ─────────────────────────────────────────

    def analyze_decision_quality(self) -> dict:
        """
        Examines logged decisions and their retrospective quality ratings.
        Finds what conditions correlate with best vs worst decisions.
        """
        try:
            cursor = self.db.conn.cursor()
            cursor.execute("""
                SELECT * FROM decision_log
                WHERE quality_rating IS NOT NULL OR outcome_score IS NOT NULL
                ORDER BY logged_at ASC
            """)
            decisions = cursor.fetchall()
        except Exception:
            return {"status": "table_not_found"}

        if len(decisions) < 5:
            return {"status": "insufficient_data", "count": len(decisions), "minimum_required": 5}

        good_conditions = []
        bad_conditions  = []

        for d in decisions:
            # Accept either quality_rating or outcome_score
            rating = _safe_val(d, "quality_rating") or _safe_val(d, "outcome_score") or 5
            conds  = {
                "energy":  _safe_val(d, "energy_at_decision"),
                "sleep":   _safe_val(d, "sleep_at_decision"),
                "impulse": _safe_val(d, "impulse_at_decision"),
                "fog":     _safe_val(d, "fog_at_decision"),
            }
            conds = {k: v for k, v in conds.items() if v is not None}
            if rating >= 7:
                good_conditions.append(conds)
            elif rating <= 4:
                bad_conditions.append(conds)

        def avg_field(lst, field):
            vals = [d[field] for d in lst if field in d]
            return _avg(vals)

        fields = ["energy", "sleep", "impulse", "fog"]
        comparison = {
            f: {
                "good_decisions": avg_field(good_conditions, f),
                "bad_decisions":  avg_field(bad_conditions, f)
            }
            for f in fields
        }

        return {
            "status":              "complete",
            "total_rated":         len(decisions),
            "good_decision_count": len(good_conditions),
            "bad_decision_count":  len(bad_conditions),
            "condition_comparison": comparison,
            "insight": (
                f"Good decisions at avg energy {comparison.get('energy',{}).get('good_decisions','?')}, "
                f"bad decisions at avg energy {comparison.get('energy',{}).get('bad_decisions','?')}."
            )
        }

    # ── 9. GOAL MOMENTUM SCORING ─────────────────────────────────────────────

    def score_goal_momentum(self) -> dict:
        """
        Automatically scores momentum for each active goal by watching
        for mentions of goal-related language in recent syncs and journals.
        """
        goals = self.db.get_active_goals()
        if not goals:
            return {"status": "no_active_goals"}

        logs     = self._load_logs(limit=30)
        journals = self._load_journals(limit=20)

        all_entries = []
        for log in logs:
            ts   = (_safe_val(log, "timestamp", "") or "")[:10]
            text = _extract_text_from_log(_safe_val(log, "content", "") or "")
            all_entries.append((ts, text))
        for j in journals:
            ts   = (_safe_val(j, "timestamp", "") or "")[:10]
            text = _safe_val(j, "content", "") or ""
            all_entries.append((ts, text))

        now = datetime.now().date()
        momentum_scores = []

        for goal in goals:
            title       = (_safe_val(goal, "title", "") or "").lower()
            description = (_safe_val(goal, "description", "") or "").lower()
            keywords    = _keywords(title + " " + description, min_length=3)

            if not keywords:
                continue

            last_7_count  = 0
            last_14_count = 0

            for ts, text in all_entries:
                text_lower = text.lower()
                try:
                    entry_date = datetime.strptime(ts, "%Y-%m-%d").date()
                except Exception:
                    continue
                days_ago = (now - entry_date).days
                mentions = sum(1 for kw in keywords if kw in text_lower)
                if days_ago <= 7:
                    last_7_count  += mentions
                elif days_ago <= 14:
                    last_14_count += mentions

            if last_7_count == 0 and last_14_count == 0:
                momentum_label = "STALLED"
                momentum_score = 0
            elif last_7_count >= last_14_count:
                momentum_score = min(last_7_count * 10, 100)
                momentum_label = "ACCELERATING" if last_7_count > last_14_count else "HOLDING"
            else:
                momentum_score = max(last_7_count * 10 - 20, 10)
                momentum_label = "DECELERATING"

            days_since_mention = None
            for ts, text in sorted(all_entries, key=lambda x: x[0], reverse=True):
                text_lower = text.lower()
                if any(kw in text_lower for kw in keywords):
                    try:
                        days_since_mention = (now - datetime.strptime(ts, "%Y-%m-%d").date()).days
                    except Exception:
                        pass
                    break

            momentum_scores.append({
                "goal":               _safe_val(goal, "title", ""),
                "goal_id":            _safe_val(goal, "id"),
                "mentions_7d":        last_7_count,
                "mentions_prev_7d":   last_14_count,
                "momentum_score":     momentum_score,
                "momentum_label":     momentum_label,
                "days_since_mention": days_since_mention
            })

        momentum_scores.sort(key=lambda x: -x["momentum_score"])
        stalled = [g for g in momentum_scores if g["momentum_label"] == "STALLED"]
        active  = [g for g in momentum_scores if g["momentum_label"] != "STALLED"]

        return {
            "status":         "complete",
            "goals_analyzed": len(momentum_scores),
            "goals":          momentum_scores,
            "stalled_goals":  stalled,
            "active_goals":   active,
        }

    # ── 10. MASTER INSIGHT SYNTHESIS ─────────────────────────────────────────

    def synthesize_master_insights(self) -> dict:
        """
        Runs ALL analysis methods and compiles a unified intelligence brief.
        Expensive call — results are cached via save_pattern_cache().
        """
        results = {}
        methods = [
            ("emotional_signatures",   self.analyze_emotional_signatures),
            ("flourishing_conditions", self.map_flourishing_conditions),
            ("danger_signature",       self.map_danger_signature),
            ("temporal",               self.analyze_temporal_patterns),
            ("linguistic",             self.analyze_linguistic_patterns),
            ("sequences",              self.detect_sequences),
            ("correlations",           self.compute_correlation_matrix),
            ("decision_quality",       self.analyze_decision_quality),
            ("goal_momentum",          self.score_goal_momentum),
        ]
        for key, method in methods:
            try:
                results[key] = method()
            except Exception as e:
                results[key] = {"status": "error", "msg": str(e)}

        return results

    def format_insights_for_context(self, insights: dict = None) -> str:
        """
        Formats synthesized insights into a compact string for persona prompt injection.
        Called by context_builder.py / council_engine.py.
        """
        if insights is None:
            insights = self.synthesize_master_insights()

        lines = ["--- PATTERN INTELLIGENCE REPORT ---"]

        emo = insights.get("emotional_signatures", {})
        if emo.get("status") == "complete":
            dominant      = ", ".join(emo.get("dominant_emotions", []))
            valence       = emo.get("emotional_valence", 0)
            valence_label = "positive-dominant" if valence >= 0.6 else "negative-dominant" if valence <= 0.4 else "mixed"
            lines.append(f"Emotional profile: {dominant or 'unclassified'} dominant | Valence: {valence_label} ({valence:.0%})")

        fc = insights.get("flourishing_conditions", {})
        if fc.get("status") == "complete":
            conds    = fc.get("flourishing_conditions", {})
            fl_words = [w for w, _ in (fc.get("flourishing_language") or [])[:5]]
            best_days = ", ".join(fc.get("best_days_of_week", [])[:2])
            lines.append(
                f"Flourishing signature: sleep {conds.get('sleep_hours', '?')}h | "
                f"fog {conds.get('brain_fog', '?')}/10 | "
                f"best days: {best_days or 'unknown'} | "
                f"language: {', '.join(fl_words) if fl_words else 'none identified'}"
            )

        ds = insights.get("danger_signature", {})
        if ds.get("status") == "complete":
            match   = ds.get("current_signature_match", 0)
            urgency = ds.get("intervention_urgency", "LOW")
            sig     = ds.get("pre_crash_signature", {})
            lines.append(
                f"Crash signature match: {match}% ({urgency} urgency) | "
                f"Pre-crash avg: energy {sig.get('energy_avg','?')} | "
                f"impulse {sig.get('impulse_avg','?')} | "
                f"sleep {sig.get('sleep_avg','?')}h"
            )

        tp = insights.get("temporal", {})
        if tp.get("status") == "complete":
            best_day  = tp.get("best_day", "unknown")
            worst_day = tp.get("worst_day", "unknown")
            peak_hr   = tp.get("peak_hour")
            lines.append(
                f"Temporal: best day {best_day} | worst day {worst_day} | "
                f"peak hour {'~' + str(peak_hr) + ':00' if peak_hr else 'unknown'}"
            )

        seq = insights.get("sequences", {})
        if seq.get("status") == "complete":
            top = seq.get("top_pattern")
            if top:
                lines.append(f"Top behavioral sequence: [{top['pattern']}] -> [{top['outcome']}] ({top['outcome_rate']} rate)")

        gm = insights.get("goal_momentum", {})
        if gm.get("status") == "complete":
            stalled = gm.get("stalled_goals", [])
            if stalled:
                stalled_names = ", ".join(g["goal"] for g in stalled[:3])
                lines.append(f"Stalled goals: {stalled_names}")
            active = gm.get("active_goals", [])
            if active:
                lines.append(f"Highest momentum goal: {active[0]['goal']}")

        corr = insights.get("correlations", {})
        if corr.get("status") == "complete":
            for n in (corr.get("notable") or [])[:2]:
                lines.append(f"Correlation: {n['plain']}")

        ling = insights.get("linguistic", {})
        if ling.get("status") == "complete":
            dominant_emo = ling.get("dominant_emotion_recent", "")
            distress     = [w for w, _ in (ling.get("distress_markers") or [])[:3]]
            flourish     = [w for w, _ in (ling.get("flourishing_language") or [])[:3]]
            if dominant_emo:
                lines.append(f"Recent linguistic tone: {dominant_emo} dominant")
            if distress:
                lines.append(f"Distress markers in recent entries: {', '.join(distress)}")
            if flourish:
                lines.append(f"Flourishing language present: {', '.join(flourish)}")

        if len(lines) == 1:
            return "Pattern Intelligence: Insufficient data. Continue logging to activate."

        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# STANDALONE CACHE + UTILITY FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def save_pattern_cache(db, insights: dict):
    """Saves full pattern analysis JSON to DB for reuse within 24h."""
    try:
        content = json.dumps(insights, default=str)
        db.conn.execute("""
            CREATE TABLE IF NOT EXISTS pattern_cache (
                id           INTEGER PRIMARY KEY,
                generated_at TEXT NOT NULL,
                content      TEXT NOT NULL
            )
        """)
        db.conn.execute("DELETE FROM pattern_cache")
        db.conn.execute(
            "INSERT INTO pattern_cache (generated_at, content) VALUES (?, ?)",
            (datetime.now(timezone.utc).isoformat(), content)
        )
        db.conn.commit()
    except Exception:
        pass


def load_pattern_cache(db) -> dict:
    """Loads cached pattern analysis if generated within last 24 hours."""
    try:
        db.conn.execute("""
            CREATE TABLE IF NOT EXISTS pattern_cache (
                id           INTEGER PRIMARY KEY,
                generated_at TEXT NOT NULL,
                content      TEXT NOT NULL
            )
        """)
        row = db.conn.execute(
            "SELECT generated_at, content FROM pattern_cache ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {}
        gen_at = datetime.fromisoformat(row[0])
        if gen_at.tzinfo is None:
            gen_at = gen_at.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - gen_at).total_seconds() > 86400:
            return {}
        return json.loads(row[1])
    except Exception:
        return {}


def get_or_refresh_patterns(db) -> tuple:
    """
    Returns (insights_dict, from_cache: bool).
    Uses cache if fresh (<24h old). Regenerates otherwise.
    """
    cached = load_pattern_cache(db)
    if cached:
        return cached, True

    engine   = PatternEngine(db)
    insights = engine.synthesize_master_insights()
    save_pattern_cache(db, insights)
    return insights, False


def build_pattern_context(pattern_data: dict, max_chars: int = 2000) -> str:
    """
    Compatibility wrapper for council_engine.py import.
    Accepts the insights dict and formats it for persona prompt injection.
    """
    if not pattern_data:
        return "Pattern Intelligence: Insufficient data. Continue logging to activate."
    try:
        engine = PatternEngine(None)
        result = engine.format_insights_for_context(pattern_data)
        return result[:max_chars]
    except Exception:
        return "Pattern Intelligence: Insufficient data. Continue logging to activate."
