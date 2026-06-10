"""Lead Board — the home workspace: search, filter, inspect evidence, take action."""
from __future__ import annotations

from typing import Any

import streamlit as st

import _bootstrap  # noqa: F401
from core import auth, db
from core.scoring import evidence_strength, strength_label

st.set_page_config(page_title="Lead Board · Sponsor OS", page_icon="📋", layout="wide")
role = auth.require_role("lead_board")
read_only = not auth.can_write(role, "leads")

STATUS_CHIP: dict[str, str] = {
    "new": "🆕 New",
    "contacted": "📤 Contacted",
    "replied": "💬 Replied",
    "meeting": "📅 Meeting",
    "signed": "✅ Signed",
    "ghosted": "👻 Ghosted",
    "rejected": "❌ Rejected",
}
SOURCE_LABEL: dict[str, str] = {
    "rival_fest_site": "Rival fest website",
    "news": "News article",
    "instagram": "Instagram post",
    "poster_logo": "Logo spotted on a poster",
}

st.title("📋 Lead Board")
st.caption(
    "Every brand we might pitch, ranked by Evidence Score — how much public proof "
    "we found that they sponsor events like ours. Tap a row to see the proof."
)

leads: list[dict[str, Any]] = db.fetch_leads()
if not leads:
    st.info("No leads yet. Run the seed script (admin) or wait for the first Scout run.")
    st.stop()

# --- Filters ---------------------------------------------------------------
industries = sorted({(lead.get("brands") or {}).get("industry") or "Unknown" for lead in leads})
filter_cols = st.columns([2, 2, 2, 1])
search = filter_cols[0].text_input("Search brand", placeholder="e.g. boAt")
status_filter = filter_cols[1].multiselect("Status", list(STATUS_CHIP), format_func=lambda s: STATUS_CHIP[s])
industry_filter = filter_cols[2].multiselect("Industry", industries)
hide_demo = filter_cols[3].toggle(
    "Hide demo", value=False,
    help="Demo rows are fake practice data — never contact those brands.",
)


def _visible(lead: dict[str, Any]) -> bool:
    brand = lead.get("brands") or {}
    if search and search.lower() not in str(brand.get("name", "")).lower():
        return False
    if status_filter and lead.get("status") not in status_filter:
        return False
    if industry_filter and (brand.get("industry") or "Unknown") not in industry_filter:
        return False
    if hide_demo and lead.get("is_demo"):
        return False
    return True


visible = [lead for lead in leads if _visible(lead)]
if not visible:
    st.warning("No leads match those filters.")
    st.stop()

rows = [
    {
        "Brand": ("🧪 " if lead.get("is_demo") else "") + str((lead.get("brands") or {}).get("name", "?")),
        "Score": round(float(lead.get("evidence_score") or 0)),
        "Status": STATUS_CHIP.get(str(lead.get("status")), str(lead.get("status"))),
        "Industry": (lead.get("brands") or {}).get("industry") or "—",
        "Owner": (lead.get("profiles") or {}).get("name") or "Unassigned",
        "Priority": str(lead.get("priority", "medium")).title(),
    }
    for lead in visible
]

event = st.dataframe(
    rows,
    use_container_width=True,
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
    column_config={
        "Score": st.column_config.ProgressColumn(
            "Evidence Score",
            help="0–100: how much public proof we found that this brand sponsors student events. Higher = warmer lead.",
            min_value=0, max_value=100, format="%d",
        ),
    },
)

selected_rows = event.selection.rows if event.selection else []
if not selected_rows:
    st.caption("👆 Tap a row to open the lead's detail panel.")
    st.stop()

lead = visible[selected_rows[0]]
brand = lead.get("brands") or {}
is_demo = bool(lead.get("is_demo"))

# --- Detail panel ------------------------------------------------------------
st.divider()
header_cols = st.columns([3, 1])
header_cols[0].subheader(("🧪 " if is_demo else "") + str(brand.get("name", "Lead")))
header_cols[1].metric(
    "Evidence Score", round(float(lead.get("evidence_score") or 0)),
    help="0–100, built from how recent the proof is, how trustworthy each source is, and how many pieces of proof exist.",
)
if is_demo:
    st.warning(
        "🧪 **This is demo data** — a fake practice lead. Do NOT contact this brand "
        "based on it. Deck generation is disabled for demo leads."
    )
if brand.get("website"):
    st.caption(f"🌐 [{brand['website']}]({brand['website']}) · {brand.get('industry') or ''}")

st.markdown("**The proof** — every score is backed by public sources you can check:")
evidence_rows = db.fetch_evidence(int(lead["brand_id"])) if lead.get("brand_id") else []
if not evidence_rows:
    st.caption("No evidence collected yet for this brand.")
for item in evidence_rows:
    label = SOURCE_LABEL.get(str(item.get("source_type")), str(item.get("source_type")))
    snippet = item.get("snippet") or ""
    signal = strength_label(evidence_strength(item))
    region_tag = " · 📍 Jaipur-region source" if item.get("region_match") else ""
    st.markdown(
        f"- **{label}** ({signal}{region_tag}) — {snippet} "
        f"[View source ↗]({item.get('source_url')})"
    )

with st.expander("❓ How is this score calculated?"):
    st.markdown(
        "Each piece of proof is weighted by **where it came from** (a rival fest's "
        "own sponsor page counts most, then news, then posters and Instagram), "
        "**how fresh it is** (proof loses half its weight every 6 months), and "
        "**how confident we are** in the match. One solid proof scores about 35; "
        "more independent proofs push the score up — but it never reaches 100, "
        "because no amount of evidence makes a sponsorship certain. "
        "This is a transparent rule, not an AI prediction."
    )

if not read_only:
    action_cols = st.columns(3)
    if action_cols[0].button("🙋 Assign to me", use_container_width=True):
        try:
            db.get_client().table("leads").update(
                {"owner_id": (auth.current_user() or {}).get("id")}
            ).eq("id", lead["id"]).execute()
            st.success("This lead is yours now.")
            st.rerun()
        except Exception:  # noqa: BLE001
            st.error("Couldn't assign — check your connection and try again.")
    action_cols[1].button(
        "🎨 Generate Deck", use_container_width=True, disabled=True,
        help="Demo leads can't generate decks." if is_demo else "Coming in Phase 3.",
    )
    action_cols[2].button(
        "📝 Log Outcome", use_container_width=True, disabled=True,
        help="Coming in Phase 5.",
    )
else:
    st.caption("You have read-only access — browsing and evidence links only.")
