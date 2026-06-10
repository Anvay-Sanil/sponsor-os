"""Admin — invite codes, member roles; Scout controls arrive in Phase 2."""
from __future__ import annotations

import secrets
import string
from datetime import datetime, timedelta, timezone

import streamlit as st

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
                st.error("Couldn't change the role — try again.")
else:
    st.caption("No members yet.")

# --- Scout (Phase 2) -----------------------------------------------------------
st.subheader("Scout pipeline")
st.info(
    "🔧 Coming in **Phase 2**: edit the rival-fest seed list, see the last Scout "
    "run's status and log, and a link to trigger a manual run on GitHub Actions."
)
st.caption(
    "🧪 Demo data cleanup (run in the Supabase SQL editor when going live): "
    "`delete from leads where is_demo; delete from brands where is_demo;`"
)
