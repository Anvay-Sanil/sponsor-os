"""Evidence Score v1 — transparent rules, deliberately not ML.

The UI labels this "Evidence Score" (never "AI prediction") until ≥50 real
outcomes exist to train a ranker.

Formula (also explained in the Lead Board help expander):

    strength_i = source_weight(source_type) * recency * confidence
    recency    = 0.5 ** (age_days / 180)          # halves every ~6 months
    score      = 100 * (1 - PRODUCT(1 - 0.35 * strength_i))

Properties: one fresh, perfect rival-fest proof scores 35; additional
independent proofs push the score up with diminishing returns toward (but
never reaching) 100 — more evidence means warmer, but never fake certainty.

This module is pure (no I/O, no Streamlit) and fully covered by
tests/test_scoring.py. Both the Scout job and the Lead Board import it, so
the score shown is always the score computed.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

SOURCE_WEIGHTS: dict[str, float] = {
    "rival_fest_site": 1.0,  # the brand demonstrably sponsors fests like ours
    "news": 0.8,             # third-party coverage of their sponsorships
    "poster_logo": 0.6,      # logo detected on a poster (CV, Phase 2.5+)
    "instagram": 0.5,        # public social post (optional enrichment)
}
DEFAULT_SOURCE_WEIGHT = 0.4   # unknown source types count, but weakly
HALF_LIFE_DAYS = 180.0
SINGLE_EVIDENCE_CAP = 0.35    # one perfect proof alone scores 35/100

# Trailing tokens stripped during normalization. Conservative list: only legal
# suffixes and the country marker, so "Red Bull India" == "Red Bull" but
# brand-meaningful words are never removed.
_STRIP_SUFFIXES = frozenset(
    {"pvt", "ltd", "limited", "private", "inc", "incorporated",
     "llp", "llc", "corp", "corporation", "india"}
)


def normalize_brand_name(name: str) -> str:
    """Dedup key for brands: lowercase, alphanumeric, legal suffixes stripped.

    "boAt Lifestyle Pvt Ltd" -> "boatlifestyle"; "Red Bull India" -> "redbull".
    """
    tokens = re.findall(r"[a-z0-9]+", name.lower())
    while tokens and tokens[-1] in _STRIP_SUFFIXES:
        tokens.pop()
    return "".join(tokens)


def _as_datetime(value: Any) -> datetime:
    """Accept datetime or ISO string (Supabase returns strings)."""
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def evidence_strength(item: dict[str, Any], now: datetime | None = None) -> float:
    """Strength of one evidence row in [0, 1]: source weight x recency x confidence."""
    now = now or datetime.now(timezone.utc)
    weight = SOURCE_WEIGHTS.get(str(item.get("source_type")), DEFAULT_SOURCE_WEIGHT)
    confidence = item.get("confidence")
    confidence = 0.5 if confidence is None else max(0.0, min(1.0, float(confidence)))
    detected = item.get("detected_at")
    age_days = 0.0 if detected is None else max(0.0, (now - _as_datetime(detected)).total_seconds() / 86400)
    recency = 0.5 ** (age_days / HALF_LIFE_DAYS)
    return weight * recency * confidence


def strength_label(strength: float) -> str:
    """Junior-readable label for one evidence item's strength."""
    if strength >= 0.6:
        return "strong signal"
    if strength >= 0.3:
        return "medium signal"
    return "weak signal"


def compute_evidence_score(evidence: list[dict[str, Any]], now: datetime | None = None) -> float:
    """Combine all evidence for a brand into a 0-100 Evidence Score.

    Saturating combination: independent proofs multiply the remaining
    "doubt" down, so the score grows with evidence count but never hits 100.
    """
    now = now or datetime.now(timezone.utc)
    doubt = 1.0
    for item in evidence:
        doubt *= 1.0 - SINGLE_EVIDENCE_CAP * evidence_strength(item, now)
    # Hard ceiling at 99.9: rounding must never display 100 — no amount of
    # evidence makes a sponsorship certain (honest-AI rule).
    return min(99.9, round(100.0 * (1.0 - doubt), 1))


def priority_from_score(score: float) -> str:
    """Default priority for newly created leads (existing leads are never touched)."""
    if score >= 70:
        return "high"
    if score >= 45:
        return "medium"
    return "low"
