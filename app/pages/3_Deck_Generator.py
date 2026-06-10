"""Deck Generator — bespoke PPTX + email per lead. Arrives in Phase 3."""
from __future__ import annotations

import streamlit as st

import _bootstrap  # noqa: F401
from core import auth

st.set_page_config(page_title="Deck Generator · Sponsor OS", page_icon="🎨", layout="wide")
auth.require_role("deck_generator")

st.title("🎨 Deck Generator")
st.caption(
    "Soon: pick a lead, watch the stages (Researching brand → Writing story → "
    "Building slides), then download the PPTX and copy the email."
)
st.info("🔧 Coming in **Phase 3**.")
st.warning(
    "House rule, forever: the email is always shown for you to **review and edit "
    "before sending. Never send unread AI output.** Sponsor OS never auto-sends "
    "anything — sending is a human act."
)
