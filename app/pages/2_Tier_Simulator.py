"""Tier Simulator — interactive pricing arrives in Phase 4; tiers list for now."""
from __future__ import annotations

import streamlit as st

import _bootstrap  # noqa: F401
from core import auth, db

st.set_page_config(page_title="Tier Simulator · Sponsor OS", page_icon="🎚️", layout="wide")
auth.require_role("tier_simulator")

st.title("🎚️ Tier Simulator")
st.caption(
    "Soon: move sliders to build a sponsorship package and instantly see how many "
    "people it likely reaches — always shown as a range, because honest numbers "
    "have uncertainty."
)

tiers = db.fetch_tiers()
if tiers:
    st.subheader("Current tiers")
    st.dataframe(
        [
            {
                "Tier": tier.get("name"),
                "Base price (₹)": tier.get("base_price"),
                "What's included": ", ".join(
                    f"{key} ×{value}" if not isinstance(value, bool) else key
                    for key, value in (tier.get("components_json") or {}).items()
                    if value
                ),
            }
            for tier in tiers
        ],
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("No tiers defined yet — the seed script creates four starter tiers.")

st.info("🔧 The interactive simulator with uncertainty bands lands in **Phase 4**.")
