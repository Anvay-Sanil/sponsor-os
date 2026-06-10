"""Outcomes — one-tap logging of what happened after each pitch. Arrives in Phase 5."""
from __future__ import annotations

import streamlit as st

import _bootstrap  # noqa: F401
from core import auth

st.set_page_config(page_title="Outcomes · Sponsor OS", page_icon="📝", layout="wide")
auth.require_role("outcomes")

st.title("📝 Outcomes")
st.caption(
    "Soon: after you send a pitch, tap one button — Sent / Reply / Meeting / "
    "Signed / Ghosted. Every tap teaches Sponsor OS which brands and pitches work."
)
st.info("🔧 Coming in **Phase 5**.")
