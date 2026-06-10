"""Tier Simulator — sliders → live reach estimate with an honest uncertainty band.

Reads cached posterior samples only (no model fitting here); all math is
vectorized numpy on ~2,000 draws, so the page answers instantly.
"""
from __future__ import annotations

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

import _bootstrap  # noqa: F401
from core import auth, db, pricing

st.set_page_config(page_title="Tier Simulator · Sponsor OS", page_icon="🎚️", layout="wide")
auth.require_role("tier_simulator")

st.title("🎚️ Tier Simulator")
st.caption("Build a package and instantly see how many people it likely reaches — "
           "always as a range, because honest numbers have uncertainty.")


@st.cache_data(ttl=600, show_spinner=False)
def _cached_posteriors() -> tuple[dict[str, list[float]], dict]:
    samples, meta = pricing.fetch_posteriors(db.get_client())
    return {asset: list(values) for asset, values in samples.items()}, meta


raw_samples, meta = _cached_posteriors()
posteriors = {asset: np.asarray(values) for asset, values in raw_samples.items()}

if not posteriors:
    st.info("The pricing model hasn't been fitted yet — an admin runs "
            "`notebooks/fit_pricing_model.ipynb` once to switch this page on. "
            "Until then, here are the current tiers:")
    tiers = db.fetch_tiers()
    if tiers:
        st.dataframe([{"Tier": tier["name"], "Base price (₹)": tier["base_price"]}
                      for tier in tiers], hide_index=True)
    st.stop()

# --- Build-a-package controls -------------------------------------------------
control_cols = st.columns([1, 1, 1, 1, 1])
components = {
    "stage_logo": control_cols[0].toggle(
        "Stage logo", value=True, help="Your logo on the main stage backdrop, both days."),
    "booth": control_cols[1].toggle(
        "Booth", value=True, help="A physical stall in the fest area."),
    "reels": control_cols[2].slider(
        "Reels", 0, 6, 2, help="Instagram reels featuring your brand, posted by us."),
    "banner": control_cols[3].slider(
        "Banners", 0, 8, 2, help="Printed banners around the venue."),
    "shoutouts": control_cols[4].slider(
        "Shoutouts", 0, 12, 4, help="Stage + story mentions during the fest."),
}
price = st.number_input("Package price (₹)", min_value=0, value=50_000, step=5_000,
                        help="The price you'd quote — we'll show the cost per 1,000 estimated views.")

samples, unmodeled = pricing.tier_reach_samples(components, posteriors)
if unmodeled:
    st.warning(f"⚠️ Not yet modeled (excluded from the numbers below): {', '.join(unmodeled)}")

if samples is None:
    st.info("Add at least one component to see the reach estimate.")
    st.stop()

summary = pricing.reach_summary(samples)
st.subheader(pricing.plain_english(summary, components))

metric_cols = st.columns(3)
metric_cols[0].metric("Low estimate", f"{summary['p10']:,}",
                      help="9 times out of 10, the reach should beat this number.")
metric_cols[1].metric("Middle estimate", f"{summary['p50']:,}",
                      help="The single most representative number — but always quote the range.")
band = pricing.cpm_range(price, summary)
metric_cols[2].metric(
    "₹ per 1,000 est. views",
    f"₹{band[0]:,.0f}–{band[1]:,.0f}" if band else "—",
    help="Package price divided by estimated reach. Lower is better value.",
)
st.caption(f"Context: typical Instagram ads in India run ₹{pricing.IG_CPM_RANGE[0]}–"
           f"₹{pricing.IG_CPM_RANGE[1]} per 1,000 **measured** impressions — a different, "
           "platform-verified metric; ours is a modeled estimate.")

# --- Distribution chart with shaded 10–90% band -----------------------------------
counts, edges = np.histogram(samples, bins=40)
frame = pd.DataFrame({
    "reach": (edges[:-1] + edges[1:]) / 2,
    "draws": counts,
})
frame["zone"] = np.where(
    (frame["reach"] >= summary["p10"]) & (frame["reach"] <= summary["p90"]),
    "likely range (10–90%)", "less likely",
)
chart = (
    alt.Chart(frame)
    .mark_area(interpolate="step", opacity=0.85)
    .encode(
        x=alt.X("reach:Q", title="People reached"),
        y=alt.Y("draws:Q", title="How often the model lands here"),
        color=alt.Color("zone:N", title="",
                        scale=alt.Scale(domain=["likely range (10–90%)", "less likely"],
                                        range=["#6C5CE7", "#D6D3F0"])),
    )
)
median_rule = (
    alt.Chart(pd.DataFrame({"reach": [summary["p50"]]}))
    .mark_rule(strokeDash=[6, 3], color="#1A1A2E").encode(x="reach:Q")
)
st.altair_chart(chart + median_rule, use_container_width=True)

# --- Current tiers with reach + Catch-2 visibility ---------------------------------
st.subheader("Our current tiers")
tiers = db.fetch_tiers()
if tiers:
    st.dataframe(
        [{"Tier": tier["name"],
          "Base price (₹)": tier["base_price"],
          "Estimated reach": pricing.reach_cell(tier.get("components_json") or {}, posteriors)}
         for tier in tiers],
        use_container_width=True, hide_index=True,
    )

with st.expander("❓ Where do these numbers come from?"):
    st.markdown(
        f"**{pricing.model_card(meta)}**\n\n"
        "We simulate thousands of plausible fests from past data and starting "
        "assumptions, then read off the range. The shaded band covers the middle "
        "80% of simulations. Multi-item packages use *up to* wording because the "
        "same people see repeated placements — audiences overlap. Every refit of "
        "the model updates this page automatically."
    )
