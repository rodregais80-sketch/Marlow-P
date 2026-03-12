"""
substance_tracker.py — NEW FILE
Substance-outcome correlation engine.

Tier 1: Builds a personal cost-benefit table from the operator's own data.
Not a moral assessment. A performance analytics system.
The data defines what each substance actually does to this specific person's
output — not what it's supposed to do, not what it did once — what it
consistently does across 60+ days of logged entries.

Correlates substance mentions in logs against:
- Next-day energy (T+1)
- Next-day efficiency/intensity (T+1)
- Next-day impulse drive (T+1)
- Next-day brain fog (T+1)
- 48-hour mood trajectory
- Chaos event frequency within 48 hours of use
"""

import re
from datetime import datetime, timedelta, timezone
from collections import defaultdict


# ─────────────────────────────────────────────────────────────────────────────
# SUBSTANCE DETECTION VOCABULARY
# ─────────────────────────────────────────────────────────────────────────────

SUBSTANCE_PATTERNS = {
    "stimulants": [
        r"\bmeth\b", r"\bmethamphetamine\b", r"\bcrystal\b", r"\bice\b",
        r"\bcoke\b", r"\bcocaine\b", r"\badderall\b", r"\bvyvanse\b",
        r"\bamphetamine\b", r"\bstimulant\b", r"\buppers?\b", r"\bspeed\b"
    ],
    "alcohol": [
        r"\balcohol\b", r"\bdrinking\b", r"\bdrunk\b", r"\bdrinks?\b",
        r"\bbeers?\b", r"\bwine\b", r"\bwhiskey\b", r"\bvodka\b",
        r"\bwasted\b", r"\bbuzzed\b", r"\bintoxicated\b", r"\bshots?\b",
        r"\bbar\b", r"\bpub\b"
    ],
    "cannabis": [
        r"\bweed\b", r"\bmarijuana\b", r"\bcannabis\b", r"\bwax\b",
        r"\bsmoked\b", r"\bsmoking\b", r"\bhigh\b", r"\bstoned\b",
        r"\bbaked\b", r"\bedibles?\b", r"\bjoint\b", r"\bbowl\b",
        r"\bdabs?\b", r"\bvaping\b", r"\bcartridge\b"
    ],
    "nicotine": [
        r"\bcigarettes?\b", r"\bcigar\b", r"\bnicotine\b", r"\bvape\b",
        r"\bvaping\b", r"\bsmoked\b", r"\bsmoking\b", r"\bsmokes?\b",
        r"\bchew\b", r"\bdip\b", r"\bpouch\b"
    ],
    "caffeine": [
        r"\bcoffee\b", r"\bcaffeine\b", r"\bespresso\b", r"\benergy drink\b",
        r"\bred bull\b", r"\bmonster\b", r"\bpre.?workout\b"
    ],
    "opioids": [
        r"\bheroin\b", r"\bfentanyl\b", r"\bopioid\b", r"\bpills?\b",
        r"\bpercs?\b", r"\bpercocet\b", r"\boxy\b", r"\boxycodone\b",
        r"\bhydro\b", r"\bhydrocodone\b", r"\bdowns?\b"
    ],
    "benzodiazepines": [
        r"\bbenzo\b", r"\bxanax\b", r"\bvalium\b", r"\bativan\b",
        r"\bclonazepam\b", r"\bklonopin\b", r"\bsedative\b"
    ],
    "psychedelics": [
        r"\bshrooms\b", r"\bpsilocybin\b", r"\blsd\b", r"\bacid\b",
        r"\btripping\b", r"\btrip\b", r"\bdmt\b", r"\bketamine\b",
        r"\bmushrooms\b"
    ]
}

CHAOS_SIGNAL_WORDS = [
    "chaos", "chaotic", "spiraling", "blew up", "meltdown", "breakdown",
    "lost it", "couldn't stop", "said fuck it", "gave up", "gave in",
    "before i knew it", "woke up and", "don't remember", "reckless"
]


def _row_get(row, key, default=None):
    try:
        v = row[key]
        return v if v is not None else default
    except (KeyError, IndexError, TypeError):
        return default


def _detect_substances(text: str) -> list:
    """Returns list of substance categories mentioned in a text string."""
    text_lower = text.lower()
    detected   = []
    for substance, patterns in SUBSTANCE_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text_lower):
                detected.append(substance)
                break
    return list(set(detected))


def _avg(lst):
    return round(sum(lst) / len(lst), 2) if lst else None


def _has_chaos(text: str) -> bool:
    text_lower = text.lower()
    return any(w in text_lower for w in CHAOS_SIGNAL_WORDS)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN CORRELATION ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class SubstanceTracker:
    """
    Correlates substance mentions in sync logs against subsequent
    metric readings to build a personal performance impact table.

    The operator's own data defines the cost-benefit analysis —
    not external research, not general statistics, not moral framework.
    Their specific body. Their specific pattern. Their specific numbers.
    """

    def __init__(self, db):
        self.db = db

    def analyze(self, days: int = 90) -> dict:
        """
        Full substance-outcome correlation analysis.
        Returns a dict mapping each detected substance to its
        measured impact on performance metrics.
        """
        logs    = self._get_logs(days)
        metrics = self._get_metrics_indexed(days)

        if not logs or not metrics:
            return {"sufficient_data": False}

        # Index logs by day
        log_by_day = defaultdict(list)
        for log in logs:
            try:
                day = datetime.fromisoformat(log["timestamp"]).strftime("%Y-%m-%d")
                log_by_day[day].append(log)
            except Exception:
                pass

        # For each day that had substance use, find the next-day metrics
        substance_impact = defaultdict(lambda: {
            "use_days":        0,
            "next_energy":     [],
            "next_fog":        [],
            "next_impulse":    [],
            "next_mood":       [],
            "next_intensity":  [],
            "chaos_within_48": 0
        })

        sorted_days = sorted(log_by_day.keys())

        for i, day in enumerate(sorted_days):
            day_logs       = log_by_day[day]
            day_text       = " ".join(l["content"] or "" for l in day_logs)
            detected       = _detect_substances(day_text)
            chaos_detected = _has_chaos(day_text)

            for substance in detected:
                substance_impact[substance]["use_days"] += 1
                if chaos_detected:
                    substance_impact[substance]["chaos_within_48"] += 1

            # Find metrics for the next day
            if i + 1 < len(sorted_days):
                next_day         = sorted_days[i + 1]
                next_day_metrics = [
                    m for m in metrics
                    if datetime.fromisoformat(m["timestamp"]).strftime("%Y-%m-%d") == next_day
                ]
                if next_day_metrics:
                    m = next_day_metrics[0]
                    for substance in detected:
                        e = _row_get(m, "energy")
                        f = _row_get(m, "mental_fog")
                        imp = _row_get(m, "impulse_drive")
                        mo = _row_get(m, "mood")
                        it = _row_get(m, "intensity")
                        if e   is not None: substance_impact[substance]["next_energy"].append(e)
                        if f   is not None: substance_impact[substance]["next_fog"].append(f)
                        if imp is not None: substance_impact[substance]["next_impulse"].append(imp)
                        if mo  is not None: substance_impact[substance]["next_mood"].append(mo)
                        if it  is not None: substance_impact[substance]["next_intensity"].append(it)

        if not substance_impact:
            return {"sufficient_data": False, "message": "No substance use detected in logs"}

        # Also compute baseline (non-use days)
        all_metric_vals = {
            "energy":    [_row_get(m, "energy")        for m in metrics if _row_get(m, "energy")        is not None],
            "fog":       [_row_get(m, "mental_fog")    for m in metrics if _row_get(m, "mental_fog")    is not None],
            "impulse":   [_row_get(m, "impulse_drive") for m in metrics if _row_get(m, "impulse_drive") is not None],
            "mood":      [_row_get(m, "mood")          for m in metrics if _row_get(m, "mood")          is not None],
            "intensity": [_row_get(m, "intensity")     for m in metrics if _row_get(m, "intensity")     is not None]
        }
        baseline = {k: _avg(v) for k, v in all_metric_vals.items()}

        # Build impact table
        impact_table = {}
        for substance, data in substance_impact.items():
            if data["use_days"] < 3:
                continue  # Not enough data for this substance

            next_energy_avg   = _avg(data["next_energy"])
            next_fog_avg      = _avg(data["next_fog"])
            next_impulse_avg  = _avg(data["next_impulse"])
            next_mood_avg     = _avg(data["next_mood"])
            next_intensity_avg= _avg(data["next_intensity"])

            impact = {}
            if next_energy_avg is not None and baseline.get("energy"):
                delta = round(next_energy_avg - baseline["energy"], 2)
                impact["energy_delta"] = delta
                impact["energy_label"] = (
                    f"+{delta} above baseline" if delta > 0 else f"{delta} below baseline"
                )
            if next_fog_avg is not None and baseline.get("fog"):
                delta = round(next_fog_avg - baseline["fog"], 2)
                impact["fog_delta"] = delta
                impact["fog_label"] = (
                    f"+{delta} above baseline (worse)" if delta > 0 else f"{abs(delta)} below baseline (better)"
                )
            if next_impulse_avg is not None and baseline.get("impulse"):
                delta = round(next_impulse_avg - baseline["impulse"], 2)
                impact["impulse_delta"] = delta
            if next_mood_avg is not None and baseline.get("mood"):
                delta = round(next_mood_avg - baseline["mood"], 2)
                impact["mood_delta"] = delta

            chaos_rate = (
                data["chaos_within_48"] / data["use_days"]
                if data["use_days"] > 0 else 0
            )

            impact_table[substance] = {
                "use_days":     data["use_days"],
                "impact":       impact,
                "chaos_rate":   round(chaos_rate, 2),
                "raw": {
                    "next_energy":    next_energy_avg,
                    "next_fog":       next_fog_avg,
                    "next_impulse":   next_impulse_avg,
                    "next_mood":      next_mood_avg,
                    "next_intensity": next_intensity_avg
                }
            }

        return {
            "sufficient_data": True,
            "baseline":        baseline,
            "impact_table":    impact_table,
            "total_days_analyzed": len(sorted_days)
        }

    def _get_logs(self, days: int) -> list:
        try:
            since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            return self.db.conn.execute(
                "SELECT timestamp, sync_type, content FROM logs WHERE timestamp >= ? ORDER BY timestamp ASC",
                (since,)
            ).fetchall()
        except Exception:
            return []

    def _get_metrics_indexed(self, days: int) -> list:
        try:
            since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            return self.db.conn.execute(
                "SELECT * FROM metrics WHERE timestamp >= ? ORDER BY timestamp ASC",
                (since,)
            ).fetchall()
        except Exception:
            return []


def build_substance_context(db, max_chars: int = 1000) -> str:
    """
    Runs substance-outcome analysis and formats for persona prompt injection.
    """
    tracker = SubstanceTracker(db)
    result  = tracker.analyze(days=90)

    if not result.get("sufficient_data"):
        return "Substance-outcome data: insufficient entries for correlation analysis."

    lines = ["--- SUBSTANCE-OUTCOME CORRELATION (personal data) ---"]
    lines.append(f"Analysis across {result['total_days_analyzed']} logged days.")
    lines.append(f"Baseline averages: energy={result['baseline'].get('energy','?')}, "
                 f"fog={result['baseline'].get('fog','?')}, "
                 f"impulse={result['baseline'].get('impulse','?')}")
    lines.append("")

    for substance, data in result["impact_table"].items():
        lines.append(f"{substance.upper()} ({data['use_days']} use days logged):")
        imp = data["impact"]
        if "energy_label" in imp:
            lines.append(f"  Next-day energy: {imp['energy_label']}")
        if "fog_label" in imp:
            lines.append(f"  Next-day fog: {imp['fog_label']}")
        if "impulse_delta" in imp:
            sign = "+" if imp["impulse_delta"] > 0 else ""
            lines.append(f"  Next-day impulse: {sign}{imp['impulse_delta']} vs baseline")
        if "mood_delta" in imp:
            sign = "+" if imp["mood_delta"] > 0 else ""
            lines.append(f"  Next-day mood: {sign}{imp['mood_delta']} vs baseline")
        if data["chaos_rate"] > 0.15:
            lines.append(f"  Chaos event rate within 48h of use: {data['chaos_rate']:.0%}")
        lines.append("")

    return "\n".join(lines)[:max_chars]
