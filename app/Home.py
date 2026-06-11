"""Sponsor OS — Home: login, invite-code signup, role routing, pipeline summary."""
from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="Sponsor OS", page_icon="🤝", layout="wide")

import _bootstrap  # noqa: F401, E402 — repo root on sys.path (Streamlit Cloud)
from core import auth, db  # noqa: E402

STATUS_LABELS: dict[str, str] = {
    "new": "🆕 New",
    "contacted": "📤 Contacted",
    "replied": "💬 Replied",
    "meeting": "📅 Meeting",
    "signed": "✅ Signed",
    "ghosted": "👻 Ghosted",
    "rejected": "❌ Rejected",
}

ROLE_HINTS: dict[str, str] = {
    "admin": "You can do everything: manage members, invite codes, and all pipeline pages.",
    "sponsorship": "Your pages: **Lead Board**, **Deck Generator**, **Outcomes**, **Tier Simulator**.",
    "analyst": "Your pages: **Lead Board** (read-only) and **Tier Simulator**, plus these dashboards.",
    "viewer": "You have the dashboards on this page. The team updates them as outreach happens.",
}


def _login_screen() -> None:
    """Login + invite-code signup, shown when nobody is logged in."""
    st.title("🤝 Sponsor OS")
    st.caption("ACM SIGAI MUJ — sponsorship pipeline. Log in or use your invite code.")

    tab_login, tab_signup = st.tabs(["Log in", "Sign up with invite code"])

    with tab_login, st.form("login_form"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Log in", use_container_width=True):
            ok, message = auth.login(email, password)
            if ok:
                st.rerun()
            st.error(message)

    with tab_signup, st.form("signup_form"):
        st.caption("You need an invite code from your admin — there is no open signup.")
        code = st.text_input("Invite code", placeholder="ACM-XXXXXX")
        name = st.text_input("Your name")
        email_new = st.text_input("Email")
        password_new = st.text_input("Password (6+ characters)", type="password")
        if st.form_submit_button("Create my account", use_container_width=True):
            ok, message = auth.signup(code, name, email_new, password_new)
            if ok:
                st.success(message)
                st.rerun()
            st.error(message)


def _pending_screen() -> None:
    """Logged in but no profile yet (redeem raced/failed) — re-enter a code."""
    st.title("🤝 Sponsor OS")
    st.warning("You're signed in but not activated yet. Enter a valid invite code to finish.")
    with st.form("redeem_form"):
        code = st.text_input("Invite code", placeholder="ACM-XXXXXX")
        if st.form_submit_button("Activate my account"):
            ok, message = auth.redeem_pending(code)
            if ok:
                st.success(message)
                st.rerun()
            st.error(message)
    if st.button("Log out"):
        auth.logout()
        st.rerun()


def _dashboard(role: str) -> None:
    """Pipeline summary — the viewer's whole UI, everyone else's landing page."""
    st.title(f"👋 Hi {auth.current_name()}!")
    st.caption(f"Access level: **{role}** — {ROLE_HINTS.get(role, '')}")

    counts = db.lead_status_counts()
    total = sum(counts.values())
    st.subheader("Pipeline at a glance")
    if total == 0:
        st.info("No leads yet. Once Scout runs (or demo data is seeded), numbers appear here.")
    else:
        columns = st.columns(4)
        columns[0].metric(
            "Total leads", total,
            help="Every brand we're tracking as a possible sponsor.",
        )
        columns[1].metric(
            "In conversation", counts.get("replied", 0) + counts.get("meeting", 0),
            help="Brands that replied or agreed to a meeting.",
        )
        columns[2].metric(
            "Signed 🎉", counts.get("signed", 0),
            help="Confirmed sponsors. The goal!",
        )
        columns[3].metric(
            "Awaiting first touch", counts.get("new", 0),
            help="Leads nobody has contacted yet — pick one from the Lead Board.",
        )
        st.caption("Status mix: " + " · ".join(
            f"{STATUS_LABELS.get(status, status)} {count}"
            for status, count in sorted(counts.items())
        ))

    # --- The Loop: real-outcome funnel + progress toward Smart Ranking ---
    from core import outcomes as outcomes_core

    all_outcomes = db.fetch_outcomes(500)
    real = [o for o in all_outcomes if not (o.get("leads") or {}).get("is_demo")]
    funnel = outcomes_core.funnel_stats(real)
    st.subheader("How outreach is going")
    if funnel["logged_total"] == 0:
        st.caption("No real outcomes logged yet — every pitch you log on the "
                   "Outcomes page shows up here. (🧪 practice taps are excluded.)")
    else:
        funnel_cols = st.columns(4)
        funnel_cols[0].metric("Contacted", funnel["contacted"],
                              help="Leads we've actually reached out to.")
        funnel_cols[1].metric("Replied", funnel["replied"],
                              help=f"Reply rate: {funnel['reply_rate'] or 0}% of contacted.")
        funnel_cols[2].metric("Meetings", funnel["meeting"],
                              help="Leads that agreed to talk.")
        funnel_cols[3].metric(
            "Signed 🎉", funnel["signed"],
            help=(f"Close rate: {funnel['close_rate'] or 0}%. "
                  f"Total value: ₹{funnel['signed_value']:,.0f}"),
        )
    fraction, caption = outcomes_core.ranker_progress(len(real))
    st.progress(fraction)
    st.caption(caption)

    with st.sidebar:
        st.markdown(f"**{auth.current_name()}** · `{role}`")
        if st.button("Log out", use_container_width=True):
            auth.logout()
            st.rerun()


user = auth.current_user()
if user is None:
    _login_screen()
elif auth.current_role() is None:
    _pending_screen()
else:
    _dashboard(auth.current_role() or "viewer")
