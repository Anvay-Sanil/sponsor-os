"""Supabase client management and query helpers for the Streamlit app.

The app uses ONLY the anon key — Row Level Security in supabase/schema.sql is
the real permission system. The service-role key is used exclusively by
scripts in jobs/ and is never imported here.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import streamlit as st
from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()

logger = logging.getLogger(__name__)


def get_secret(key: str) -> str | None:
    """Read a secret from st.secrets, falling back to environment variables."""
    try:
        if key in st.secrets:
            return str(st.secrets[key])
    except Exception:  # noqa: BLE001 — no secrets.toml at all is fine locally
        pass
    return os.environ.get(key)


def get_client() -> Client:
    """Return this browser session's Supabase client (anon key).

    One client per Streamlit session, stored in st.session_state: the client
    carries the logged-in user's tokens, so it must never be shared across
    sessions (st.cache_resource would leak one user's auth to another).
    """
    if "sb_client" not in st.session_state:
        url = get_secret("SUPABASE_URL")
        key = get_secret("SUPABASE_KEY")
        if not url or not key:
            st.error(
                "Sponsor OS is not connected to its database yet. An admin needs "
                "to set SUPABASE_URL and SUPABASE_KEY in Streamlit secrets — "
                "see the README setup guide."
            )
            st.stop()
        st.session_state.sb_client = create_client(url, key)
    return st.session_state.sb_client


def _safe_rows(query: Any, what: str) -> list[dict[str, Any]]:
    """Execute a PostgREST query, returning [] with a friendly warning on failure."""
    try:
        response = query.execute()
        return response.data or []
    except Exception as exc:  # noqa: BLE001 — any DB error must not crash a page
        logger.error("Query for %s failed: %s", what, exc)
        st.warning(f"Couldn't load {what} right now. Check your connection and refresh.")
        return []


def fetch_leads() -> list[dict[str, Any]]:
    """All leads with their brand and owner names, highest Evidence Score first."""
    client = get_client()
    query = (
        client.table("leads")
        .select("*, brands(name, industry, website, is_demo), profiles(name)")
        .order("evidence_score", desc=True)
    )
    return _safe_rows(query, "leads")


def fetch_evidence(brand_id: int) -> list[dict[str, Any]]:
    """Evidence rows for one brand, newest first."""
    client = get_client()
    query = (
        client.table("evidence")
        .select("*")
        .eq("brand_id", brand_id)
        .order("detected_at", desc=True)
    )
    return _safe_rows(query, "evidence")


def fetch_tiers() -> list[dict[str, Any]]:
    """All sponsorship tiers."""
    client = get_client()
    return _safe_rows(client.table("tiers").select("*").order("base_price", desc=True), "tiers")


def fetch_profiles() -> list[dict[str, Any]]:
    """All member profiles (admin user management + owner lookups)."""
    client = get_client()
    return _safe_rows(client.table("profiles").select("*").order("created_at"), "members")


def fetch_invite_codes() -> list[dict[str, Any]]:
    """All invite codes — RLS restricts this to admins."""
    client = get_client()
    query = client.table("invite_codes").select("*").order("created_at", desc=True)
    return _safe_rows(query, "invite codes")


def lead_status_counts() -> dict[str, int]:
    """Count of leads per status for the Home pipeline summary."""
    counts: dict[str, int] = {}
    for lead in fetch_leads():
        status = str(lead.get("status", "new"))
        counts[status] = counts.get(status, 0) + 1
    return counts
