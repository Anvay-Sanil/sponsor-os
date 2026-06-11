"""Outcomes — one tap logs what happened and updates the lead, atomically.

Every tap teaches Sponsor OS which brands and pitches work. Mis-taps have a
10-minute undo. Demo leads are fair practice; training data filters them out.
"""
from __future__ import annotations

import streamlit as st

import _bootstrap  # noqa: F401
from core import auth, db, outcomes, pitch_memory

st.set_page_config(page_title="Outcomes · Sponsor OS", page_icon="📝", layout="wide")
role = auth.require_role("outcomes")

st.title("📝 Outcomes")
st.caption("Sent a pitch? Got a reply? Tap it here — one tap logs it AND updates "
           "the Lead Board. This is how the system learns what works.")

leads = db.fetch_leads()
if not leads:
    st.info("No leads yet.")
    st.stop()


def _label(lead: dict) -> str:
    brand = lead.get("brands") or {}
    flag = "🧪 " if lead.get("is_demo") else ""
    return f"{flag}{brand.get('name', '?')} — currently {lead.get('status')}"


labels = {_label(lead): lead for lead in leads}
preselect = 0
wanted = st.session_state.get("outcome_lead_id")
if wanted is not None:
    for index, lead in enumerate(labels.values()):
        if lead["id"] == wanted:
            preselect = index
            break

picked = st.selectbox("Lead", list(labels), index=preselect)
lead = labels[picked]
if lead.get("is_demo"):
    st.caption("🧪 Demo lead — perfect for practicing. Practice taps never enter the AI's training data.")

with st.expander("Add details (optional)"):
    deal_value = st.number_input("Deal value (₹) — for Signed", min_value=0, value=0, step=5000)
    notes = st.text_input("Note", placeholder="e.g. spoke to Priya from marketing")

st.markdown("**What happened?**")
columns = st.columns(3)
for index, (event, label) in enumerate(outcomes.EVENT_LABELS.items()):
    if columns[index % 3].button(label, use_container_width=True,
                                 type="primary" if event == "signed" else "secondary"):
        ok, message = outcomes.log_outcome(
            db.get_client(), int(lead["id"]), event,
            deal_value=float(deal_value) or None if event == "signed" else None,
            notes=notes or None,
        )
        if ok:
            # Best-effort: remember winning language. Never blocks the log.
            pitch_memory.embed_winning_deck(db.get_client(), int(lead["id"]),
                                            event, bool(lead.get("is_demo")))
            st.success(message)
            if event == "signed":
                st.balloons()
        else:
            st.error(message)

# --- Recent feed with undo ----------------------------------------------------
st.divider()
st.subheader("Recently logged")
recent = [o for o in db.fetch_outcomes(15) if not o.get("voided")]
if not recent:
    st.caption("Nothing logged yet — the funnel on Home fills up from here.")
user_id = (auth.current_user() or {}).get("id", "")
for outcome in recent[:10]:
    brand_name = ((outcome.get("leads") or {}).get("brands") or {}).get("name", "?")
    demo_flag = "🧪 " if (outcome.get("leads") or {}).get("is_demo") else ""
    logger_name = (outcome.get("logger") or {}).get("name") or "someone"
    value_text = f" · ₹{float(outcome['deal_value']):,.0f}" if outcome.get("deal_value") else ""
    line_cols = st.columns([6, 1])
    line_cols[0].markdown(
        f"{outcomes.EVENT_LABELS.get(str(outcome['event']), outcome['event'])} — "
        f"{demo_flag}**{brand_name}**{value_text} · by {logger_name} · "
        f"{str(outcome['logged_at'])[:16].replace('T', ' ')}"
    )
    if outcomes.can_void(outcome, user_id, role):
        if line_cols[1].button("↩️ Undo", key=f"void_{outcome['id']}"):
            ok, message = outcomes.void_outcome(db.get_client(), int(outcome["id"]))
            (st.success if ok else st.error)(message)
            if ok:
                st.rerun()
st.caption("Mis-tap? You can undo your own entry for 10 minutes; admins can undo anything.")
