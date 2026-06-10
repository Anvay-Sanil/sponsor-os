"""Tests for core.scoring — Evidence Score v1 rules and brand normalization."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.scoring import (
    DEFAULT_SOURCE_WEIGHT,
    compute_evidence_score,
    evidence_strength,
    normalize_brand_name,
    priority_from_score,
    strength_label,
)

NOW = datetime(2026, 6, 10, tzinfo=timezone.utc)


def _item(source: str = "rival_fest_site", confidence: float | None = 1.0,
          age_days: float = 0.0) -> dict:
    return {
        "source_type": source,
        "confidence": confidence,
        "detected_at": NOW - timedelta(days=age_days),
    }


# --- normalization (dedup key) ----------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("boAt Lifestyle Pvt Ltd", "boatlifestyle"),
        ("Red Bull India", "redbull"),
        ("RED BULL", "redbull"),
        ("Coding Ninjas", "codingninjas"),
        ("Swiggy!", "swiggy"),
        ("7UP", "7up"),
        ("Zebronics  Corp", "zebronics"),
        ("Pvt Ltd", ""),  # all tokens stripped -> caller rejects len < 2
    ],
)
def test_normalize_brand_name(raw: str, expected: str) -> None:
    assert normalize_brand_name(raw) == expected


def test_normalized_variants_collide() -> None:
    """The whole point: spelling variants of one brand share one dedup key."""
    assert normalize_brand_name("Red Bull") == normalize_brand_name("RedBull India Pvt Ltd")


# --- per-evidence strength ----------------------------------------------------
def test_fresh_perfect_rival_fest_evidence_is_full_strength() -> None:
    assert evidence_strength(_item(), NOW) == pytest.approx(1.0)


def test_recency_halves_at_half_life() -> None:
    assert evidence_strength(_item(age_days=180), NOW) == pytest.approx(0.5, abs=0.01)


def test_missing_confidence_defaults_to_half() -> None:
    assert evidence_strength(_item(confidence=None), NOW) == pytest.approx(0.5)


def test_unknown_source_gets_default_weight() -> None:
    assert evidence_strength(_item(source="carrier_pigeon"), NOW) == pytest.approx(DEFAULT_SOURCE_WEIGHT)


def test_iso_string_dates_accepted() -> None:
    item = {"source_type": "news", "confidence": 1.0,
            "detected_at": "2026-06-10T00:00:00Z"}
    assert evidence_strength(item, NOW) == pytest.approx(0.8)


# --- combined score -----------------------------------------------------------
def test_no_evidence_scores_zero() -> None:
    assert compute_evidence_score([], NOW) == 0.0


def test_single_perfect_evidence_scores_thirty_five() -> None:
    assert compute_evidence_score([_item()], NOW) == pytest.approx(35.0)


def test_two_proofs_saturate_not_add() -> None:
    score = compute_evidence_score([_item(), _item()], NOW)
    assert score == pytest.approx(57.75, abs=0.1)  # < 70: diminishing returns


def test_more_evidence_never_lowers_score() -> None:
    items: list[dict] = []
    previous = 0.0
    for _ in range(10):
        items.append(_item(confidence=0.8))
        score = compute_evidence_score(items, NOW)
        assert score >= previous
        previous = score


def test_score_never_reaches_one_hundred() -> None:
    assert compute_evidence_score([_item() for _ in range(50)], NOW) < 100.0


def test_stale_evidence_scores_below_fresh() -> None:
    fresh = compute_evidence_score([_item()], NOW)
    stale = compute_evidence_score([_item(age_days=720)], NOW)
    assert stale < fresh / 2


# --- labels & priority ----------------------------------------------------------
@pytest.mark.parametrize(("strength", "label"),
                         [(0.7, "strong signal"), (0.4, "medium signal"), (0.1, "weak signal")])
def test_strength_labels(strength: float, label: str) -> None:
    assert strength_label(strength) == label


@pytest.mark.parametrize(("score", "priority"),
                         [(92, "high"), (70, "high"), (69.9, "medium"), (45, "medium"), (44.9, "low"), (0, "low")])
def test_priority_from_score(score: float, priority: str) -> None:
    assert priority_from_score(score) == priority
