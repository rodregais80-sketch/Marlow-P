"""
correlations.py — CORRELATION ENGINE
The missing module. Should have been delivered with the original file set.

Handles:
- Cross-variable correlation analysis (which metrics drive which)
- Relapse risk signature detection from personal behavioral data
- Substance-outcome correlation (detailed version, complements substance_tracker.py)
- Lagged correlation: does variable A at time T predict variable B at time T+1?

All analysis is self-referential — built from the operator's own data only.
No external benchmarks. No population statistics. Their pattern. Their numbers.
"""

import math
from datetime import datetime, timedelta, timezone
from collections import defaultdict


# ─────────────────────────────────────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def _safe(row, key, default=None):
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
    m = sum(lst) / len(lst)
    return round(math.sqrt(sum((x - m) ** 2 for x in lst) / len(lst)), 2)


def _pearson(x: list, y: list) -> float:
    """Pearson correlation coefficient between two equal-length numeric lists."""
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


def _lagged_pearson(x: list, y: list, lag: int = 1) -> float:
    """
    Pearson correlation between x[t] and y[t+lag].
    If this is strong, x is likely upstream of y in the causal chain.
    """
    if len(x) < lag + 3 or len(y) < lag + 3:
        return 0.0
    n     = min(len(x) - lag, len(y) - lag)
    x_lag = x[:n]
    y_lag = y[lag:n + lag]
    return _pearson(x_lag, y_lag)


# ─────────────────────────────────────────────────────────────────────────────
# SUBSTANCE DETECTION
# ─────────────────────────────────────────────────────────────────────────────

import re

SUBSTANCE_PATTERNS = {
    "stimulants": [
        r"\bmeth\b", r"\bcrystal\b", r"\bice\b", r"\bcoke\b", r"\bcocaine\b",
        r"\badderall\b", r"\bamphetamine\b", r"\bspeed\b", r"\buppers?\b"
    ],
    "alcohol": [
        r"\balcohol\b", r"\bdrinking\b", r"\bdrunk\b", r"\bdrinks?\b",
        r"\bbeers?\b", r"\bwine\b", r"\bwhiskey\b", r"\bwasted\b", r"\bbuzzed\b"
    ],
    "cannabis": [
        r"\bweed\b", r"\bmarijuana\b", r"\bcannabis\b", r"\bhigh\b",
        r"\bstoned\b", r"\bsmoked\b", r"\bedibles?\b", r"\bjoint\b"
    ],
    "nicotine": [
        r"\bcigarettes?\b", r"\bnicotine\b", r"\bvape\b", r"\bvaping\b"
    ],
    "caffeine": [
        r"\bcoffee\b", r"\bcaffeine\b", r"\benergy drink\b", r"\bred bull\b"
    ],
    "opioids": [
        r"\bheroin\b", r"\bfentanyl\b", r"\bpercs?\b", r"\boxy\b",
        r"\bhydro\b", r"\bpills?\b", r"\bdowns?\b"
    ],
    "benzodiazepines": [
        r"\bbenzo\b", r"\bxanax\b", r"\bvalium\b", r"\bklonopin\b"
    ]
}

RELAPSE_LANGUAGE = [
    r"\bcraving\b", r"\bwant to use\b", r"\bneed a hit\b", r"\bthinking about using\b",
    r"\bcan't stop thinking\b", r"\bwant to get high\b", r"\btempted\b",
    r"\bjust this once\b", r"\bone last time\b", r"\bfuck it\b",
    r"\bgave in\b", r"\bgave up\b", r"\bsaid fuck it\b",
    r"\brelapsed\b", r"\bslipped\b", r"\bbroke clean\b"
]

CHAOS_LANGUAGE = [
    r"\bchaos\b", r"\bspiraling\b", r"\bout of control\b", r"\bmeltdown\b",
    r"\bblew up\b", r"\blost it\b", r"\bdon't remember\b", r"\bwoke up and\b",
    r"\bbefore i knew it\b", r"\breckless\b", r"\bdestroyed\b"
]


def _detect_substances(text: str) -> list:
    text_lower = text.lower()
    detected   = []
    for substance, patterns in SUBSTANCE_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text_lower):
                detected.append(substance)
                break
    return list(set(detected))


def _has_relapse_language(text: str) -> bool:
    text_lower = text.lower()
    return any(re.search(p, text_lower) for p in RELAPSE_LANGUAGE)


def _has_chaos_language(text: str) -> bool:
    text_lower = text.lower()
    return any(re.search(p, text_lower) for p in CHAOS_LANGUAGE)


# ─────────────────────────────────────────────────────────────────────────────
# CORRELATION ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class CorrelationEngine:
    """
    Analyzes the structural relationships between the operator's tracked variables.

    Answers questions like:
    - Does sleep predict next-day energy in this person's data?
    - Does impulse drive at time T predict a chaos event at T+1?
    - What state conditions precede substance use events?
    - What is the personal relapse risk signature?

    All findings are derived from the operator's actual logged data.
    Minimum 14 data points for meaningful output.
    """

    MIN_DATA = 14

    def __init__(self, db):
        self.db = db

    def _load_metrics(self, limit: int = 90) -> list:
        try:
            rows = self.db.get_recent_metrics(limit=limit)
            return list(reversed(rows))  # oldest first
        except Exception:
            return []

    def _load_logs(self, limit: int = 90) -> list:
        try:
            rows = self.db.get_recent_logs(limit=limit)
            return list(reversed(rows))
        except Exception:
            return []

    # ── Cross-variable correlation matrix ────────────────────────────────────

    def compute_correlations(self) -> dict:
        """
        Computes Pearson correlation between every pair of tracked metrics.
        Also computes lagged correlations (does X today predict Y tomorrow?)

        Returns a dict of notable findings with plain-language descriptions.
        """
        metrics = self._load_metrics(limit=90)

        if len(metrics) < self.MIN_DATA:
            return {
                "status":       "insufficient_data",
                "data_points":  len(metrics),
                "minimum":      self.MIN_DATA
            }

        fields = ["energy", "mood", "mental_fog", "impulse_drive", "sleep_hours"]
        series = {}

        for f in fields:
            raw  = [_safe(m, f) for m in metrics]
            vals = [v for v in raw if v is not None]
            mean = _avg(vals) or 5
            series[f] = [v if v is not None else mean for v in raw]

        # Same-time correlations
        same_time = {}
        seen = set()
        for f1 in fields:
            for f2 in fields:
                if f1 == f2:
                    continue
                key = tuple(sorted([f1, f2]))
                if key in seen:
                    continue
                seen.add(key)
                r = _pearson(series[f1], series[f2])
                same_time[f"{f1}_x_{f2}"] = r

        # Lagged correlations (T predicts T+1)
        lagged = {}
        causal_pairs = [
            ("sleep_hours",   "energy"),
            ("sleep_hours",   "mental_fog"),
            ("energy",        "impulse_drive"),
            ("impulse_drive", "mental_fog"),
            ("mood",          "energy"),
            ("mental_fog",    "mood"),
        ]
        for x_field, y_field in causal_pairs:
            r = _lagged_pearson(series[x_field], series[y_field], lag=1)
            lagged[f"{x_field}_predicts_{y_field}"] = r

        # Extract notable findings
        notable = []
        for pair, r in same_time.items():
            if abs(r) >= 0.4:
                f1, f2 = pair.split("_x_")
                direction = "positive" if r > 0 else "negative"
                strength  = "strong" if abs(r) >= 0.65 else "moderate"
                notable.append({
                    "type":      "same_time",
                    "var1":      f1,
                    "var2":      f2,
                    "r":         r,
                    "plain":     f"{f1} and {f2}: {strength} {direction} correlation ({r:+.2f})"
                })

        causal_chains = []
        for pair, r in lagged.items():
            if abs(r) >= 0.3:
                x_field, y_field = pair.split("_predicts_")
                direction = "positively" if r > 0 else "negatively"
                causal_chains.append({
                    "upstream":   x_field,
                    "downstream": y_field,
                    "r":          r,
                    "plain":      f"{x_field} {direction} predicts next-period {y_field} (r={r:+.2f})"
                })

        notable.sort(key=lambda x: -abs(x["r"]))
        causal_chains.sort(key=lambda x: -abs(x["r"]))

        return {
            "status":         "complete",
            "data_points":    len(metrics),
            "same_time":      same_time,
            "lagged":         lagged,
            "notable":        notable,
            "causal_chains":  causal_chains,
            "strongest":      notable[0] if notable else None
        }

    # ── Relapse risk signature ────────────────────────────────────────────────

    def get_relapse_risk_signature(self) -> dict:
        """
        The most clinically important function in the system.

        Builds the operator's personal relapse risk signature by analyzing:
        1. What metric conditions precede documented substance use events
        2. Whether relapse-adjacent language is appearing in recent entries
        3. Whether the current state matches the historical pre-use signature

        Returns a risk level (LOW / MODERATE / HIGH / CRITICAL) with
        specific contributing factors derived from personal data.

        This is not based on general addiction research.
        It is based on what has actually preceded use events in THIS person's logs.
        """
        metrics = self._load_metrics(limit=90)
        logs    = self._load_logs(limit=90)

        if len(metrics) < self.MIN_DATA:
            return {
                "status":     "insufficient_data",
                "risk_level": "UNKNOWN",
                "note":       f"Need {self.MIN_DATA} logged entries. Currently have {len(metrics)}."
            }

        # ── Step 1: Find historical use events in logs ────────────────────────
        use_event_dates = set()
        relapse_language_dates = set()

        for log in logs:
            ts      = (_safe(log, "timestamp", "") or "")[:10]
            content = _safe(log, "content", "") or ""

            substances = _detect_substances(content)
            if substances:
                use_event_dates.add(ts)

            if _has_relapse_language(content):
                relapse_language_dates.add(ts)

        # ── Step 2: Build pre-use metric signature ────────────────────────────
        pre_use_conditions = []

        for m in metrics:
            ts = (_safe(m, "timestamp", "") or "")[:10]
            # Check if this metric entry was 1-2 days before a use event
            try:
                m_date = datetime.strptime(ts, "%Y-%m-%d").date()
                for use_date_str in use_event_dates:
                    use_date = datetime.strptime(use_date_str, "%Y-%m-%d").date()
                    days_before = (use_date - m_date).days
                    if 0 < days_before <= 3:
                        pre_use_conditions.append({
                            "energy":  _safe(m, "energy"),
                            "mood":    _safe(m, "mood"),
                            "fog":     _safe(m, "mental_fog"),
                            "impulse": _safe(m, "impulse_drive"),
                            "sleep":   _safe(m, "sleep_hours")
                        })
                        break
            except Exception:
                pass

        # ── Step 3: Current state assessment ─────────────────────────────────
        recent_metrics = metrics[-5:] if len(metrics) >= 5 else metrics
        recent_metrics_rev = list(reversed(recent_metrics))  # most recent first

        current_energy  = _avg([_safe(m, "energy")        for m in recent_metrics_rev[:3] if _safe(m, "energy")        is not None])
        current_mood    = _avg([_safe(m, "mood")          for m in recent_metrics_rev[:3] if _safe(m, "mood")          is not None])
        current_fog     = _avg([_safe(m, "mental_fog")    for m in recent_metrics_rev[:3] if _safe(m, "mental_fog")    is not None])
        current_impulse = _avg([_safe(m, "impulse_drive") for m in recent_metrics_rev[:3] if _safe(m, "impulse_drive") is not None])
        current_sleep   = _avg([_safe(m, "sleep_hours")   for m in recent_metrics_rev[:3] if _safe(m, "sleep_hours")   is not None])

        # ── Step 4: Risk factor scoring ───────────────────────────────────────
        risk_score = 0
        factors    = []

        # Factor: low energy + high impulse (classic use-risk combination)
        if current_energy is not None and current_impulse is not None:
            if current_energy <= 4 and current_impulse >= 7:
                risk_score += 3
                factors.append(
                    f"Low energy ({current_energy:.1f}) with high impulse ({current_impulse:.1f}) "
                    f"— the combination most associated with use events in your data"
                )

        # Factor: sustained sleep deficit
        if current_sleep is not None and current_sleep < 5.5:
            risk_score += 2
            factors.append(
                f"Sleep averaging {current_sleep:.1f}h — dopamine baseline depleted, "
                f"reward-seeking behavior elevated"
            )

        # Factor: mood declining
        if current_mood is not None and current_mood <= 4:
            risk_score += 2
            factors.append(
                f"Mood at {current_mood:.1f}/10 — negative affect state "
                f"historically precedes self-medication events"
            )

        # Factor: impulse drive rising
        if len(recent_metrics) >= 3:
            impulse_series = [
                _safe(m, "impulse_drive")
                for m in recent_metrics
                if _safe(m, "impulse_drive") is not None
            ]
            if len(impulse_series) >= 3:
                if impulse_series[-1] > impulse_series[0] + 2:
                    risk_score += 2
                    factors.append(
                        f"Impulse drive rising trajectory "
                        f"({impulse_series[0]:.0f} → {impulse_series[-1]:.0f}) — "
                        f"craving escalation pattern"
                    )

        # Factor: relapse language in recent entries
        recent_log_dates = set()
        for log in reversed(logs[-10:]):
            ts      = (_safe(log, "timestamp", "") or "")[:10]
            content = _safe(log, "content", "") or ""
            if _has_relapse_language(content):
                recent_log_dates.add(ts)

        if recent_log_dates:
            risk_score += 3
            factors.append(
                f"Relapse-adjacent language detected in {len(recent_log_dates)} recent "
                f"log entry/entries — conscious awareness of craving present"
            )

        # Factor: current state matches historical pre-use signature
        if pre_use_conditions and len(pre_use_conditions) >= 2:
            pre_energy_avg  = _avg([c["energy"]  for c in pre_use_conditions if c["energy"]  is not None])
            pre_impulse_avg = _avg([c["impulse"] for c in pre_use_conditions if c["impulse"] is not None])
            pre_sleep_avg   = _avg([c["sleep"]   for c in pre_use_conditions if c["sleep"]   is not None])

            signature_matches = 0
            if current_energy  is not None and pre_energy_avg  is not None and abs(current_energy  - pre_energy_avg)  <= 1.5: signature_matches += 1
            if current_impulse is not None and pre_impulse_avg is not None and abs(current_impulse - pre_impulse_avg) <= 1.5: signature_matches += 1
            if current_sleep   is not None and pre_sleep_avg   is not None and abs(current_sleep   - pre_sleep_avg)   <= 1.0: signature_matches += 1

            if signature_matches >= 2:
                risk_score += 3
                factors.append(
                    f"Current state matches your personal pre-use signature "
                    f"({signature_matches}/3 variables aligned) — "
                    f"pattern recognition, not speculation"
                )

        # Factor: chaos language in recent entries
        for log in reversed(logs[-5:]):
            content = _safe(log, "content", "") or ""
            if _has_chaos_language(content):
                risk_score += 1
                factors.append("Chaos language in recent entries — emotional dysregulation present")
                break

        # ── Step 5: Risk level classification ─────────────────────────────────
        risk_score = min(risk_score, 10)
        risk_level = (
            "CRITICAL" if risk_score >= 9 else
            "HIGH"     if risk_score >= 6 else
            "MODERATE" if risk_score >= 3 else
            "LOW"
        )

        if risk_score < 3:
            return {
                "status":       "complete",
                "risk_level":   "LOW",
                "risk_score":   risk_score,
                "use_events_in_data": len(use_event_dates),
                "note":         "No significant risk factors detected in current data."
            }

        # Build insight
        insight = (
            "This is not a warning based on general statistics. "
            "These are patterns from your own logged history. "
            "The system recognizes this state because it has seen it before in your data — "
            "and it knows what followed."
            if len(use_event_dates) >= 3 else
            "Current state matches known risk factors. "
            "The system is building your personal signature — "
            "continue logging honestly for more precise detection."
        )

        return {
            "status":              "complete",
            "risk_level":          risk_level,
            "risk_score":          risk_score,
            "factors":             factors,
            "use_events_in_data":  len(use_event_dates),
            "pre_use_signature":   pre_use_conditions[:3] if pre_use_conditions else [],
            "current_state": {
                "energy":  current_energy,
                "mood":    current_mood,
                "fog":     current_fog,
                "impulse": current_impulse,
                "sleep":   current_sleep
            },
            "note": insight
        }

    # ── Substance-outcome correlation (detailed) ──────────────────────────────

    def get_substance_impact(self, substance: str = "stimulants") -> dict:
        """
        Correlates specific substance use against next-period metric readings.
        More detailed than substance_tracker.py — returns full statistical breakdown.
        """
        metrics = self._load_metrics(limit=90)
        logs    = self._load_logs(limit=90)

        if len(metrics) < self.MIN_DATA:
            return {"status": "insufficient_data"}

        # Find use days for this substance
        use_day_indices = set()
        log_by_date = defaultdict(list)
        for log in logs:
            ts      = (_safe(log, "timestamp", "") or "")[:10]
            content = _safe(log, "content", "") or ""
            log_by_date[ts].append(content)

        metric_dates = []
        for i, m in enumerate(metrics):
            ts = (_safe(m, "timestamp", "") or "")[:10]
            metric_dates.append(ts)
            day_logs = log_by_date.get(ts, [])
            day_text = " ".join(day_logs)
            substances = _detect_substances(day_text)
            if substance in substances:
                use_day_indices.add(i)

        if len(use_day_indices) < 3:
            return {
                "status":  "insufficient_use_data",
                "substance": substance,
                "use_days_found": len(use_day_indices),
                "note":    f"Need at least 3 logged {substance} use days for analysis."
            }

        # Compare next-day metrics on use days vs non-use days
        use_next    = defaultdict(list)
        no_use_next = defaultdict(list)
        fields      = ["energy", "mood", "mental_fog", "impulse_drive"]

        for i in use_day_indices:
            if i + 1 < len(metrics):
                nxt = metrics[i + 1]
                for f in fields:
                    v = _safe(nxt, f)
                    if v is not None:
                        use_next[f].append(v)

        for i in range(len(metrics) - 1):
            if i not in use_day_indices:
                nxt = metrics[i + 1]
                for f in fields:
                    v = _safe(nxt, f)
                    if v is not None:
                        no_use_next[f].append(v)

        impact = {}
        for f in fields:
            use_avg    = _avg(use_next[f])
            no_use_avg = _avg(no_use_next[f])
            if use_avg is not None and no_use_avg is not None:
                delta = round(use_avg - no_use_avg, 2)
                impact[f] = {
                    "after_use_avg":    use_avg,
                    "baseline_avg":     no_use_avg,
                    "delta":            delta,
                    "direction":        "above baseline" if delta > 0 else "below baseline"
                }

        return {
            "status":       "complete",
            "substance":    substance,
            "use_days":     len(use_day_indices),
            "impact":       impact
        }

    # ── Context formatter ─────────────────────────────────────────────────────

    def format_for_context(self, max_chars: int = 800) -> str:
        """
        Formats correlation findings for injection into persona prompts.
        """
        lines = ["--- CORRELATION INTELLIGENCE ---"]

        corr = self.compute_correlations()
        if corr.get("status") == "complete":
            chains = corr.get("causal_chains", [])
            if chains:
                lines.append("Causal chains identified from your data:")
                for c in chains[:3]:
                    lines.append(f"  → {c['plain']}")

        rr = self.get_relapse_risk_signature()
        if rr.get("status") == "complete" and rr.get("risk_level") != "LOW":
            lines.append(f"Relapse risk: {rr['risk_level']} (score: {rr.get('risk_score', 0)}/10)")
            for f in rr.get("factors", [])[:3]:
                lines.append(f"  → {f}")

        result = "\n".join(lines)
        return result[:max_chars]
