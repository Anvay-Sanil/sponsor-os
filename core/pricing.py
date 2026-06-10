"""Price v1: read cached posteriors, Monte Carlo tier math, honest summaries.

The model (notebooks/fit_pricing_model.ipynb) caches ~2,000 posterior draws of
IMPRESSIONS PER ASSET UNIT into pricing_posteriors. This module only does
vector math on those samples — no MCMC, no network beyond one cached read —
so the Tier Simulator answers in well under a second.

Honesty rules implemented here (user-approved 2026-06-11):
* Intervals always; the 10th–90th percentile band is the headline number.
* Multi-unit bundles say "up to": linear bundling ignores audience overlap,
  so the top end is a ceiling, and our error must never lean seller-favorable.
* Components with no posterior are surfaced as "not yet modeled" — visibly
  excluded from the math, never silently dropped.
* The Instagram CPM band is CONTEXT (measured platform impressions are a
  different metric from modeled reach). The deck framing makes that explicit.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

META_KEY = "_meta"

# tiers.components_json keys -> posterior asset_type names
COMPONENT_TO_ASSET: dict[str, str] = {
    "stage_logo": "stage_logo",
    "reels": "reel",
    "booth": "booth",
    "banner": "banner",
    "shoutouts": "shoutout",
}

# Typical India Instagram-ads CPM band, ₹ per 1,000 MEASURED impressions.
# Source: public agency rate cards 2024–25 commonly quote ₹80–250. Shown as a
# band, labeled as context — never as an equivalence to modeled fest reach.
IG_CPM_RANGE: tuple[int, int] = (80, 250)


# ---------------------------------------------------------------------------
# Posterior cache access (the only I/O in this module)
# ---------------------------------------------------------------------------
def fetch_posteriors(client: Any) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Latest cached posterior samples per asset + the model-card meta dict.

    Returns ({} , {}) when the model has never been fitted — callers must
    degrade gracefully (the app worked before Phase 4 and must keep working).
    """
    try:
        rows = (
            client.table("pricing_posteriors").select("*")
            .order("fitted_at", desc=True).execute().data or []
        )
    except Exception as exc:  # noqa: BLE001 — pricing is enhancement, not lifeline
        logger.warning("pricing_posteriors fetch failed: %s", exc)
        return {}, {}
    samples: dict[str, np.ndarray] = {}
    meta: dict[str, Any] = {}
    for row in rows:  # newest first; keep first occurrence per asset_type
        asset = str(row.get("asset_type"))
        payload = row.get("posterior_samples")
        if asset == META_KEY:
            if not meta and isinstance(payload, dict):
                meta = payload
        elif asset not in samples and isinstance(payload, list) and payload:
            samples[asset] = np.asarray(payload, dtype=float)
    return samples, meta


# ---------------------------------------------------------------------------
# Pure tier math (covered by tests/test_pricing.py)
# ---------------------------------------------------------------------------
def split_components(components: dict[str, Any]) -> tuple[dict[str, int], list[str]]:
    """(modeled asset->count, unmodeled component keys). Loud, never silent."""
    modeled: dict[str, int] = {}
    unmodeled: list[str] = []
    for key, value in (components or {}).items():
        count = int(value) if not isinstance(value, bool) else int(bool(value))
        if count <= 0:
            continue
        asset = COMPONENT_TO_ASSET.get(key)
        if asset is None:
            unmodeled.append(key)
        else:
            modeled[asset] = modeled.get(asset, 0) + count
    return modeled, unmodeled


def tier_reach_samples(components: dict[str, Any],
                       posteriors: dict[str, np.ndarray],
                       ) -> tuple[np.ndarray | None, list[str]]:
    """Monte Carlo total-impressions samples for a bundle.

    Unmodeled = components with no posterior (unknown key OR model not fitted
    for that asset). They are excluded from the math and returned for display.
    """
    modeled, unmodeled = split_components(components)
    missing = [asset for asset in modeled if asset not in posteriors]
    unmodeled.extend(missing)
    usable = {asset: count for asset, count in modeled.items() if asset in posteriors}
    if not usable:
        return None, unmodeled
    length = min(len(posteriors[asset]) for asset in usable)
    total = np.zeros(length)
    for asset, count in usable.items():
        total += count * posteriors[asset][:length]
    return total, unmodeled


def reach_summary(samples: np.ndarray) -> dict[str, int]:
    """10/50/90th percentiles, rounded to presentation-friendly ints."""
    p10, p50, p90 = (float(np.percentile(samples, q)) for q in (10, 50, 90))
    return {"p10": int(round(p10, -2)), "p50": int(round(p50, -2)), "p90": int(round(p90, -2))}


def total_units(components: dict[str, Any]) -> int:
    modeled, _ = split_components(components)
    return sum(modeled.values())


def plain_english(summary: dict[str, int], components: dict[str, Any]) -> str:
    """The one-line junior-readable verdict. Multi-unit bundles say 'up to'."""
    low, high = f"{summary['p10']:,}", f"{summary['p90']:,}"
    if total_units(components) > 1:
        return (f"This package reaches up to {low}–{high} people — repeated placements "
                "overlap audiences, so treat the top end as a ceiling.")
    return f"This package likely reaches {low}–{high} people."


def cpm_range(price: float, summary: dict[str, int]) -> tuple[float, float] | None:
    """₹ per 1,000 estimated views, as a (best-case, worst-case) band."""
    if not price or summary["p90"] <= 0 or summary["p10"] <= 0:
        return None
    return (price / summary["p90"] * 1000, price / summary["p10"] * 1000)


def deck_roi_memo(tier_name: str, price: float, summary: dict[str, int]) -> str:
    """The ROI line printed on slide 5. Computed, never LLM-written.

    Deck framing rule: OUR number is the headline; the Instagram band is
    context about a different (measured) metric — never an equivalence claim.
    """
    band = cpm_range(price, summary)
    cpm_text = f" — about ₹{band[0]:,.0f}–{band[1]:,.0f} per 1,000 estimated views" if band else ""
    return (f"{tier_name} at ₹{price:,.0f}: estimated reach "
            f"{summary['p10']:,}–{summary['p90']:,} people{cpm_text}. "
            f"For reference, typical Instagram ads in India run ₹{IG_CPM_RANGE[0]}–"
            f"₹{IG_CPM_RANGE[1]} per 1,000 measured impressions — a different, "
            "platform-verified metric, shown here only as context.")


def reach_cell(components: dict[str, Any], posteriors: dict[str, np.ndarray]) -> str:
    """Compact per-tier reach text for tables ('12,000–28,000' or honest gaps)."""
    samples, unmodeled = tier_reach_samples(components, posteriors)
    if samples is None:
        return "model not fitted yet" if not posteriors else "—"
    summary = reach_summary(samples)
    text = f"{summary['p10']:,}–{summary['p90']:,}"
    if unmodeled:
        text += f" (+ not yet modeled: {', '.join(sorted(unmodeled))})"
    return text


def model_card(meta: dict[str, Any]) -> str:
    """Junior-readable provenance line for the UI."""
    if not meta:
        return "The pricing model has not been fitted yet."
    observations = int(meta.get("observations_n", 0))
    fitted = str(meta.get("fitted_at", ""))[:10]
    version = meta.get("model_version", "v1")
    if observations == 0:
        basis = ("educated starting assumptions only (no real fest data yet) — "
                 "that is why the ranges are wide, and that is honest")
    else:
        basis = f"{observations} real observation(s) plus starting assumptions"
    return f"Model {version}, fitted {fitted}, based on {basis}."
