"""Authentication and role gating: login, invite-code signup, page guards.

Pure permission logic (ROLES, PAGE_ACCESS, has_access, can_write) lives at the
top with no I/O so pytest can cover it without a database.
"""
from __future__ import annotations

import logging
from typing import Any

import streamlit as st

from core.db import get_client

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pure permission matrix (covered by tests/test_auth_gating.py)
# ---------------------------------------------------------------------------
ROLES: tuple[str, ...] = ("admin", "sponsorship", "analyst", "viewer")

PAGE_ACCESS: dict[str, frozenset[str]] = {
    "home": frozenset(ROLES),
    "lead_board": frozenset({"admin", "sponsorship", "analyst"}),
    "tier_simulator": frozenset({"admin", "sponsorship", "analyst"}),
    "deck_generator": frozenset({"admin", "sponsorship"}),
    "outcomes": frozenset({"admin", "sponsorship"}),
    "admin": frozenset({"admin"}),
}

WRITE_ACCESS: dict[str, frozenset[str]] = {
    "admin": frozenset(
        {
            "brands", "evidence", "leads", "tiers", "pricing_posteriors",
            "decks", "outcomes", "pitch_memory", "profiles", "invite_codes",
        }
    ),
    "sponsorship": frozenset({"leads", "decks", "outcomes"}),
    "analyst": frozenset(),
    "viewer": frozenset(),
}


def has_access(role: str | None, page: str) -> bool:
    """Return True if `role` may open `page`. Unknown roles/pages are denied."""
    if role is None:
        return False
    return role in PAGE_ACCESS.get(page, frozenset())


def can_write(role: str | None, table: str) -> bool:
    """Return True if `role` may INSERT/UPDATE `table` (mirrors RLS, for UI gating)."""
    if role is None:
        return False
    return table in WRITE_ACCESS.get(role, frozenset())


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
def current_user() -> dict[str, Any] | None:
    """The logged-in user dict ({id, email}) or None."""
    return st.session_state.get("user")


def current_role() -> str | None:
    """The logged-in user's role, or None (not logged in / profile pending)."""
    return st.session_state.get("role")


def current_name() -> str:
    """Display name for the logged-in user."""
    return str(st.session_state.get("name", "there"))


def logout() -> None:
    """Sign out, clear the persisted cookie, clear all session auth state."""
    clear_persisted_session()
    try:
        get_client().auth.sign_out()
    except Exception as exc:  # noqa: BLE001 — logout must always succeed locally
        logger.warning("Supabase sign_out failed: %s", exc)
    for key in ("user", "role", "name", "sb_client"):
        st.session_state.pop(key, None)


# ---------------------------------------------------------------------------
# Session persistence (Phase 6): ONLY the refresh token, 7-day cookie.
# The access token never leaves memory. unsafe_allow_html is banned app-wide
# (enforced by tests) so the XSS surface this depends on stays closed.
# ---------------------------------------------------------------------------
REFRESH_COOKIE = "sos_refresh"


def _cookies():  # noqa: ANN202 — stx CookieManager or None
    """Per-session CookieManager; None when unavailable (tests, bare mode)."""
    try:
        import extra_streamlit_components as stx

        if "sos_cookie_mgr" not in st.session_state:
            st.session_state.sos_cookie_mgr = stx.CookieManager(key="sos_cookies")
        return st.session_state.sos_cookie_mgr
    except Exception:  # noqa: BLE001 — persistence is an enhancement
        return None


def persist_refresh_token(token: str | None) -> None:
    """Best-effort: remember the refresh token for 7 days."""
    from datetime import datetime, timedelta, timezone

    manager = _cookies()
    if manager is None or not token:
        return
    try:
        manager.set(REFRESH_COOKIE, token,
                    expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                    key="sos_set_refresh")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not persist session cookie: %s", exc)


def clear_persisted_session() -> None:
    manager = _cookies()
    if manager is None:
        return
    try:
        manager.delete(REFRESH_COOKIE, key="sos_del_refresh")
    except Exception:  # noqa: BLE001 — cookie may simply not exist
        pass


def restore_from_cookie() -> bool:
    """Try to resume a session from the cookie. Tolerates the component not
    being mounted yet (first script run) — it simply returns False that run."""
    if current_user() is not None:
        return False
    manager = _cookies()
    if manager is None:
        return False
    try:
        token = manager.get(REFRESH_COOKIE)
    except Exception:  # noqa: BLE001
        return False
    if not token:
        return False
    client = get_client()
    try:
        result = client.auth.refresh_session(token)
    except Exception as exc:  # noqa: BLE001 — expired/rotated-away token
        logger.info("Cookie session restore failed: %s", exc)
        clear_persisted_session()
        return False
    if result.session is None or result.user is None:
        return False
    client.postgrest.auth(result.session.access_token)
    profile = _load_profile(result.user.id)
    _store_session(result.user,
                   profile.get("role") if profile else None,
                   profile.get("name") if profile else None)
    # Supabase rotates refresh tokens on use — persist the NEW one.
    persist_refresh_token(result.session.refresh_token)
    return True


def _friendly_auth_error(exc: Exception) -> str:
    """Translate Supabase auth errors into junior-readable messages."""
    text = str(exc).lower()
    if "invalid login credentials" in text:
        return "Email or password is wrong. If you forgot your password, ask an admin to reset it."
    if "already registered" in text or "already exists" in text:
        return "This email already has an account — use the Log in tab instead."
    if "database error" in text:
        # Transient GoTrue 500 seen on freshly provisioned projects (e.g. while
        # the auth service restarts after a settings change). Retrying works.
        return (
            "The signup service had a brief hiccup — wait 30 seconds and try once. "
            "If it already said this once before, try the Log in tab: your account "
            "may have been created anyway."
        )
    if "password" in text and ("weak" in text or "at least" in text or "short" in text):
        return "Password is too short — use at least 6 characters."
    if "rate" in text or "429" in text:
        return "Too many tries — wait a minute and try again."
    logger.exception("Unrecognized auth error")
    return "Something went wrong talking to the login service. Try again in a moment."


def _store_session(user: Any, role: str | None, name: str | None) -> None:
    """Cache the authenticated identity in st.session_state."""
    st.session_state.user = {"id": user.id, "email": user.email}
    st.session_state.role = role
    st.session_state.name = name or (user.email or "member").split("@")[0]


def _load_profile(user_id: str) -> dict[str, Any] | None:
    """Fetch the caller's profile row, or None if not yet created."""
    try:
        response = (
            get_client().table("profiles").select("*").eq("id", user_id).execute()
        )
        return response.data[0] if response.data else None
    except Exception as exc:  # noqa: BLE001
        logger.error("Profile lookup failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Login / signup flows
# ---------------------------------------------------------------------------
def login(email: str, password: str) -> tuple[bool, str]:
    """Sign in with email + password. Returns (ok, message)."""
    client = get_client()
    try:
        result = client.auth.sign_in_with_password(
            {"email": email.strip(), "password": password}
        )
    except Exception as exc:  # noqa: BLE001 — supabase-py raises several types
        return False, _friendly_auth_error(exc)
    if result.session is None or result.user is None:
        return False, "Login didn't complete. Try again."
    # Belt-and-braces: make sure data queries carry the user's token.
    client.postgrest.auth(result.session.access_token)
    profile = _load_profile(result.user.id)
    _store_session(
        result.user,
        profile.get("role") if profile else None,
        profile.get("name") if profile else None,
    )
    persist_refresh_token(result.session.refresh_token)
    return True, "ok"


def signup(invite_code: str, name: str, email: str, password: str) -> tuple[bool, str]:
    """Create an account gated by an invite code. Returns (ok, message)."""
    client = get_client()
    code = invite_code.strip()
    if not code:
        return False, "Enter the invite code you were given."

    # 1. Validate the code BEFORE creating the auth user.
    try:
        check = client.rpc("check_invite", {"p_code": code}).execute()
    except Exception as exc:  # noqa: BLE001
        return False, _friendly_auth_error(exc)
    if not check.data:
        return False, "That invite code is not valid (wrong, used up, or expired). Ask your admin for a new one."

    # 2. Create the auth user — email confirmation is OFF, so we get a session.
    try:
        result = client.auth.sign_up({"email": email.strip(), "password": password})
    except Exception as exc:  # noqa: BLE001
        return False, _friendly_auth_error(exc)
    if result.session is None or result.user is None:
        return False, (
            "Account created but couldn't log in automatically. An admin must turn "
            "OFF 'Confirm email' in Supabase Auth settings (see README), then log in here."
        )
    client.postgrest.auth(result.session.access_token)
    persist_refresh_token(result.session.refresh_token)

    # 3. Redeem atomically: creates the profile with the code's role.
    return _redeem(client, code, name, result.user)


def redeem_pending(invite_code: str) -> tuple[bool, str]:
    """Retry profile creation for a logged-in user whose redemption failed."""
    user = current_user()
    if user is None:
        return False, "Log in first."
    client = get_client()

    class _U:  # minimal shim matching the attributes _redeem needs
        id = user["id"]
        email = user["email"]

    return _redeem(client, invite_code.strip(), st.session_state.get("name", ""), _U())


def _redeem(client: Any, code: str, name: str, user: Any) -> tuple[bool, str]:
    """Call redeem_invite and store the resulting role. Returns (ok, message)."""
    try:
        redeemed = client.rpc(
            "redeem_invite", {"p_code": code, "p_name": name.strip() or None}
        ).execute()
    except Exception as exc:  # noqa: BLE001
        if "INVITE_INVALID" in str(exc):
            # Code was consumed between check and redeem: user exists but has no
            # profile ("pending" state). Home shows a re-enter-code screen.
            _store_session(user, None, name)
            return False, (
                "That invite code was just used up by someone else. You're signed in "
                "but not activated — enter a fresh code below."
            )
        return False, _friendly_auth_error(exc)
    role = str(redeemed.data) if redeemed.data else None
    _store_session(user, role, name)
    return True, f"Welcome! Your access level is: {role}."


# ---------------------------------------------------------------------------
# Page guard
# ---------------------------------------------------------------------------
def require_role(page: str) -> str:
    """Gate a page. Returns the caller's role, or stops rendering with a friendly note."""
    user = current_user()
    if user is None:
        st.info("Please log in on the **Home** page first.")
        st.stop()
    role = current_role()
    if role is None:
        st.warning("Your account isn't activated yet — go to **Home** and enter your invite code.")
        st.stop()
    if not has_access(role, page):
        st.info(
            "This page isn't part of your access level "
            f"(you are **{role}**). If you think you need it, ask an admin."
        )
        st.stop()
    return role
