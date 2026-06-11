"""Admin — invite codes, member roles, Scout seeds and run status."""
from __future__ import annotations

import logging
import secrets
import string
from datetime import datetime, timedelta, timezone

import streamlit as st

logger = logging.getLogger(__name__)

import _bootstrap  # noqa: F401
from core import auth, db

st.set_page_config(page_title="Admin · Sponsor OS", page_icon="🛠️", layout="wide")
auth.require_role("admin")

st.title("🛠️ Admin")

# --- Invite codes ------------------------------------------------------------
st.subheader("Invite codes")
st.caption("Generate a code, send it to the new member, they sign up on Home.")

with st.form("new_invite"):
    form_cols = st.columns([2, 1, 1])
    role_choice = form_cols[0].selectbox("Role for this code", auth.ROLES, index=1)
    max_uses = form_cols[1].number_input("Max uses", min_value=1, max_value=50, value=1)
    valid_days = form_cols[2].number_input("Valid for (days)", min_value=1, max_value=365, value=30)
    if st.form_submit_button("Generate code"):
        alphabet = string.ascii_uppercase + string.digits
        code = "ACM-" + "".join(secrets.choice(alphabet) for _ in range(6))
        expires = (datetime.now(timezone.utc) + timedelta(days=int(valid_days))).isoformat()
        try:
            db.get_client().table("invite_codes").insert(
                {"code": code, "role": role_choice, "max_uses": int(max_uses), "expires_at": expires}
            ).execute()
            st.success(f"New **{role_choice}** code (copy and share it): `{code}`")
        except Exception:  # noqa: BLE001
            logger.exception("Invite code creation failed")
            st.error("Couldn't create the code — check your connection and try again.")

codes = db.fetch_invite_codes()
if codes:
    st.dataframe(
        [
            {
                "Code": item.get("code"),
                "Role": item.get("role"),
                "Used": f"{item.get('uses', 0)}/{item.get('max_uses', 1)}",
                "Expires": str(item.get("expires_at") or "never")[:10],
            }
            for item in codes
        ],
        use_container_width=True,
        hide_index=True,
    )

# --- Members -----------------------------------------------------------------
st.subheader("Members")
profiles = db.fetch_profiles()
if profiles:
    st.dataframe(
        [
            {
                "Name": person.get("name"),
                "Role": person.get("role"),
                "Committee": person.get("committee") or "—",
                "Joined": str(person.get("created_at"))[:10],
            }
            for person in profiles
        ],
        use_container_width=True,
        hide_index=True,
    )
    with st.form("change_role"):
        change_cols = st.columns([2, 1, 1])
        by_label = {f"{p.get('name')} ({p.get('role')})": p for p in profiles}
        picked = change_cols[0].selectbox("Member", list(by_label))
        new_role = change_cols[1].selectbox("New role", auth.ROLES)
        if st.form_submit_button("Change role"):
            try:
                db.get_client().table("profiles").update({"role": new_role}).eq(
                    "id", by_label[picked]["id"]
                ).execute()
                st.success(f"{picked} is now **{new_role}**.")
                st.rerun()
            except Exception:  # noqa: BLE001
                logger.exception("Role change failed")
                st.error("Couldn't change the role — try again.")
else:
    st.caption("No members yet.")

# --- Scout pipeline ------------------------------------------------------------
st.subheader("Scout pipeline")

run = db.fetch_latest_scout_run()
if run is None:
    st.info("Scout hasn't run yet. It runs every Monday morning automatically once GitHub Actions is set up.")
else:
    status_icon = {"success": "✅", "partial": "🟡", "failed": "🔴", "running": "⏳"}.get(run["status"], "❔")
    st.markdown(f"**Last run:** {status_icon} `{run['status']}` — started {str(run['started_at'])[:16].replace('T', ' ')} UTC")
    stats = run.get("stats") or {}
    if stats:
        stat_cols = st.columns(4)
        stat_cols[0].metric("Pages scraped", stats.get("pages_ok", 0),
                            help="Rival-fest pages fetched successfully this run.")
        stat_cols[1].metric("Evidence saved", stats.get("evidence_written", 0),
                            help="Verified proof items written to the database.")
        stat_cols[2].metric("New brands", stats.get("brands_new", 0),
                            help="Brands seen for the first time this run.")
        stat_cols[3].metric("Hallucinations blocked", stats.get("rejected_not_on_page", 0),
                            help="AI suggestions rejected because the brand wasn't actually on the page.")
    if run.get("log"):
        with st.expander("Run log"):
            st.code(run["log"], language="text")

actions_url = db.get_secret("GITHUB_ACTIONS_URL")
if actions_url:
    st.link_button("▶️ Run Scout now (GitHub Actions)", actions_url)
else:
    st.caption("Tip: set GITHUB_ACTIONS_URL in Streamlit secrets to get a one-click "
               "'Run Scout now' button here. Until then: GitHub → Actions → Scout refresh → Run workflow.")

# --- Rival-fest seeds ------------------------------------------------------------
st.subheader("Rival-fest seed list")
st.caption("Public fest websites Scout checks for sponsor evidence. Jaipur/Rajasthan "
           "sources are tagged 📍 regional — those brands are warmer leads for us.")

seeds = db.fetch_scout_seeds()
if seeds:
    st.dataframe(
        [
            {
                "Fest": seed.get("name"),
                "URL": seed.get("url"),
                "Regional": "📍 yes" if seed.get("region_match") else "no",
                "Active": "✅" if seed.get("enabled") else "⏸️ off",
                "Notes": seed.get("notes") or "",
            }
            for seed in seeds
        ],
        use_container_width=True,
        hide_index=True,
        column_config={"URL": st.column_config.LinkColumn("URL")},
    )
    with st.form("toggle_seed"):
        toggle_cols = st.columns([3, 1])
        seed_by_label = {f"{seed['name']} ({'on' if seed['enabled'] else 'off'})": seed for seed in seeds}
        picked_seed = toggle_cols[0].selectbox("Seed", list(seed_by_label))
        if st.form_submit_button("Toggle on/off"):
            seed_row = seed_by_label[picked_seed]
            try:
                db.get_client().table("scout_seeds").update(
                    {"enabled": not seed_row["enabled"]}
                ).eq("id", seed_row["id"]).execute()
                st.rerun()
            except Exception:  # noqa: BLE001
                logger.exception("Seed toggle failed")
                st.error("Couldn't update the seed — try again.")
else:
    st.caption("No seeds yet — the first Scout run imports the starter list automatically.")

with st.form("add_seed"):
    st.markdown("**Add a fest**")
    add_cols = st.columns([2, 3, 1])
    seed_name = add_cols[0].text_input("Fest name")
    seed_url = add_cols[1].text_input("Website (public page)", placeholder="https://…")
    seed_regional = add_cols[2].toggle("📍 Regional", value=True,
                                       help="Is this a Jaipur/Rajasthan fest?")
    if st.form_submit_button("Add seed"):
        if not seed_name.strip() or not seed_url.strip().startswith("http"):
            st.error("Give the fest a name and a full URL starting with https://")
        else:
            try:
                db.get_client().table("scout_seeds").insert(
                    {
                        "name": seed_name.strip(),
                        "url": seed_url.strip(),
                        "region_match": seed_regional,
                        "added_by": (auth.current_user() or {}).get("id"),
                    }
                ).execute()
                st.success(f"Added {seed_name}. Scout will include it on its next run.")
                st.rerun()
            except Exception:  # noqa: BLE001
                logger.exception("Seed add failed")
                st.error("Couldn't add it — is that URL already in the list?")

st.caption(
    "🧪 Demo data cleanup (run in the Supabase SQL editor when going live): "
    "`delete from leads where is_demo; delete from brands where is_demo;`"
)
