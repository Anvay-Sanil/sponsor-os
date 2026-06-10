"""Deck Generator — pick a lead, watch the stages, download deck + email.

House rule, forever: the email is shown for human review and editing.
There is no send button. Sending is a human act.
"""
from __future__ import annotations

import streamlit as st

import _bootstrap  # noqa: F401
from core import auth, db, pitch
from core.chapter_facts import missing_facts

st.set_page_config(page_title="Deck Generator · Sponsor OS", page_icon="🎨", layout="wide")
auth.require_role("deck_generator")

st.title("🎨 Deck Generator")
st.caption("Pick a lead → we research the brand, write the story, and build a "
           "branded deck with a ready-to-edit email. About 30 seconds.")

facts_gap = missing_facts()
if facts_gap:
    st.error(
        "**Real-lead decks are locked.** These chapter facts still have placeholders: "
        f"`{'`, `'.join(facts_gap)}`. An admin must fill in real numbers in "
        "`core/chapter_facts.py` (real gate counts, real follower numbers — never "
        "inflate). Demo leads still work below as 🧪 test decks."
    )

leads = db.fetch_leads()
selectable = [lead for lead in leads if not (facts_gap and not lead.get("is_demo"))]
if not selectable:
    st.info("No leads available. Run Scout or seed demo data first.")
    st.stop()


def _label(lead: dict) -> str:
    brand = lead.get("brands") or {}
    flag = "🧪 TEST · " if lead.get("is_demo") else ""
    return f"{flag}{brand.get('name', '?')} — score {round(float(lead.get('evidence_score') or 0))}"


labels = {_label(lead): lead for lead in selectable}
preselect = 0
wanted_id = st.session_state.get("deck_lead_id")
if wanted_id is not None:
    for index, lead in enumerate(labels.values()):
        if lead["id"] == wanted_id:
            preselect = index
            break

picked = st.selectbox("Lead", list(labels), index=preselect)
lead = labels[picked]
brand = lead.get("brands") or {}
is_demo = bool(lead.get("is_demo"))
if is_demo:
    st.warning("🧪 Demo lead: the output is a **practice deck**, watermarked "
               "'TEST — DO NOT SEND'. Use it to learn the flow, never for outreach.")

if st.button("Generate deck", type="primary", use_container_width=True):
    full_brand = {"id": lead["brand_id"], "name": brand.get("name"),
                  "website": brand.get("website"), "industry": brand.get("industry")}
    evidence = db.fetch_evidence(int(lead["brand_id"]))
    tiers = db.fetch_tiers()
    try:
        with st.status("Working…", expanded=True) as status:
            result = pitch.generate_pitch(
                db.get_client(), lead, full_brand, evidence, tiers,
                user_id=(auth.current_user() or {}).get("id", ""),
                on_stage=lambda stage: status.update(label=stage),
            )
            status.update(label="Done!", state="complete")
        st.session_state.deck_result = result
        st.session_state.deck_brand_name = brand.get("name", "brand")
    except RuntimeError as exc:
        st.error(str(exc))
    except Exception:  # noqa: BLE001 — never show a junior a traceback
        st.error("Something unexpected went wrong building this deck. Try once more; "
                 "if it repeats, tell an admin.")

result = st.session_state.get("deck_result")
if result is not None:
    brand_name = st.session_state.get("deck_brand_name", "brand")
    prefix = "TEST_" if result.is_test else ""
    st.success("Deck ready" + ("" if result.storage_path else
                               " (saved locally only — cloud copy failed, download below still works)"))
    st.download_button(
        f"⬇️ Download {prefix}{brand_name}_deck_v{result.version}.pptx",
        data=result.pptx_bytes,
        file_name=f"{prefix}{brand_name}_sponsorship_deck_v{result.version}.pptx".replace(" ", "_"),
        mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        use_container_width=True,
    )
    st.text_area("The email — yours to edit", value=result.email_text, height=320)
    st.caption("**Review and edit before sending. Never send unread AI output.**")
