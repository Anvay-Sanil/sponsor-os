"""Tests for core.pricing — tier math, honest language, loud unmodeled handling."""
from __future__ import annotations

import numpy as np
import pytest

from core.pricing import (
    cpm_range,
    deck_roi_memo,
    model_card,
    plain_english,
    reach_cell,
    reach_summary,
    split_components,
    tier_reach_samples,
    total_units,
)

POSTERIORS = {
    "stage_logo": np.full(2000, 4000.0),
    "reel": np.full(2000, 6000.0),
    "booth": np.full(2000, 900.0),
}
GOLD = {"stage_logo": True, "reels": 2, "booth": True}


# --- Catch 2: unmodeled components fail loudly ---------------------------------
def test_unknown_component_key_is_surfaced_not_dropped() -> None:
    _, unmodeled = split_components({"reels": 2, "led_wall": 1})
    assert unmodeled == ["led_wall"]


def test_component_without_posterior_is_surfaced() -> None:
    samples, unmodeled = tier_reach_samples({"reels": 1, "shoutouts": 4}, POSTERIORS)
    assert samples is not None
    assert unmodeled == ["shoutout"]  # known key, but model never fitted it


def test_reach_cell_makes_gaps_visible() -> None:
    cell = reach_cell({"reels": 1, "led_wall": 2}, POSTERIORS)
    assert "not yet modeled: led_wall" in cell
    assert "6,000" in cell.replace("–", "-") or "6,000" in cell


def test_no_posteriors_at_all_says_so() -> None:
    assert reach_cell(GOLD, {}) == "model not fitted yet"


# --- bundle math -----------------------------------------------------------------
def test_bundle_is_linear_sum_of_units() -> None:
    samples, unmodeled = tier_reach_samples(GOLD, POSTERIORS)
    assert unmodeled == []
    assert samples is not None
    assert float(samples[0]) == pytest.approx(4000 + 2 * 6000 + 900)


def test_boolean_components_count_as_one_and_false_as_zero() -> None:
    modeled, _ = split_components({"stage_logo": True, "booth": False, "reels": 0})
    assert modeled == {"stage_logo": 1}


def test_reach_summary_percentiles_ordered() -> None:
    rng = np.random.default_rng(42)
    summary = reach_summary(rng.lognormal(9, 0.5, 4000))
    assert summary["p10"] < summary["p50"] < summary["p90"]


# --- "up to" language on multi-unit bundles ----------------------------------------
def test_single_unit_says_likely() -> None:
    text = plain_english({"p10": 3000, "p50": 4000, "p90": 6000}, {"stage_logo": True})
    assert text.startswith("This package likely reaches")


def test_multi_unit_says_up_to_with_ceiling_warning() -> None:
    text = plain_english({"p10": 8000, "p50": 11000, "p90": 14000}, GOLD)
    assert "up to" in text
    assert "ceiling" in text
    assert total_units(GOLD) > 1


# --- CPM + Catch 1 deck framing -----------------------------------------------------
def test_cpm_band_orientation() -> None:
    band = cpm_range(50000, {"p10": 25000, "p50": 40000, "p90": 50000})
    assert band is not None
    best, worst = band
    assert best == pytest.approx(1000.0)   # price / p90 reach
    assert worst == pytest.approx(2000.0)  # price / p10 reach
    assert best < worst


def test_cpm_handles_zero_price_and_zero_reach() -> None:
    assert cpm_range(0, {"p10": 1, "p50": 2, "p90": 3}) is None
    assert cpm_range(100, {"p10": 0, "p50": 0, "p90": 0}) is None


def test_deck_memo_headline_is_ours_and_ig_is_context_only() -> None:
    memo = deck_roi_memo("Gold", 50000, {"p10": 25000, "p50": 40000, "p90": 50000})
    assert memo.startswith("Gold at ₹50,000: estimated reach 25,000–50,000")
    assert "For reference" in memo
    assert "different" in memo and "context" in memo  # never an equivalence claim


# --- model card ------------------------------------------------------------------------
def test_model_card_unfitted_and_priors_only_modes() -> None:
    assert "not been fitted" in model_card({})
    card = model_card({"model_version": "v1-20260611", "fitted_at": "2026-06-11T10:00:00",
                       "observations_n": 0})
    assert "no real fest data yet" in card


def test_model_card_with_observations() -> None:
    card = model_card({"model_version": "v1", "fitted_at": "2026-06-11", "observations_n": 5})
    assert "5 real observation(s)" in card


# --- speed budget: simulator math must be instant ------------------------------------
def test_tier_math_is_fast_enough_for_live_sliders() -> None:
    import time

    big = {asset: np.random.default_rng(1).lognormal(8, 0.6, 2000)
           for asset in ("stage_logo", "reel", "booth", "banner", "shoutout")}
    bundle = {"stage_logo": True, "reels": 4, "booth": True, "banner": 6, "shoutouts": 10}
    start = time.perf_counter()
    for _ in range(50):
        samples, _ = tier_reach_samples(bundle, big)
        reach_summary(samples)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0  # 50 full recomputes well under the page's 1s budget
