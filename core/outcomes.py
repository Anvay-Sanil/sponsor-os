"""Outcome logging and funnel math. The learning loop's front door.

Writes go through the atomic `log_outcome` / `void_outcome` Postgres RPCs
(migration 004): one tap = outcomes row + lead status in ONE transaction, so
flaky Wi-Fi can never leave the tables disagreeing. The pure parts here
(mapping, funnel, void-window) are mirrored for UI display and tested.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Mirrors public.event_to_status() in SQL — keep the two in sync.
EVENT_TO_STATUS: dict[str, str] = {
    "sent": "contacted",
    "replied": "replied",
    "meeting": "meeting",
    "signed": "signed",
    "ghosted": "ghosted",
    "rejected": "rejected",
}

EVENT_LABELS: dict[str, str] = {
    "sent": "📤 Sent",
    "replied": "💬 Reply",
    "meeting": "📅 Meeting",
    "signed": "✅ Signed",
    "ghosted": "👻 Ghosted",
    "rejected": "❌ Rejected",
}

VOID_WINDOW_MINUTES = 10
RANKER_THRESHOLD = 50  # real outcomes needed before Smart Ranking activates


def event_to_status(event: str) -> str:
    """Lead status implied by an outcome event. KeyError on unknown = loud."""
    return EVENT_TO_STATUS[event]


def can_void(outcome: dict[str, Any], user_id: str, role: str | None,
             now: datetime | None = None) -> bool:
    """UI mirror of the RPC rule: own outcome within 10 minutes, admin anytime."""
    if outcome.get("voided"):
        return False
    if role == "admin":
        return True
    if str(outcome.get("logged_by")) != str(user_id):
        return False
    logged_at = outcome.get("logged_at")
    if logged_at is None:
        return False
    if isinstance(logged_at, str):
        logged_at = datetime.fromisoformat(logged_at.replace("Z", "+00:00"))
    now = now or datetime.now(timezone.utc)
    return now - logged_at <= timedelta(minutes=VOID_WINDOW_MINUTES)


def funnel_stats(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    """Sent→replied→meeting→signed funnel over NON-VOIDED outcomes.

    Counts unique leads per stage (a lead that got 3 replies is one warm lead,
    not three) and sums signed deal values.
    """
    live = [o for o in outcomes if not o.get("voided")]
    leads_by_event: dict[str, set] = {event: set() for event in EVENT_TO_STATUS}
    signed_value = 0.0
    for outcome in live:
        event = str(outcome.get("event"))
        if event in leads_by_event:
            leads_by_event[event].add(outcome.get("lead_id"))
        if event == "signed" and outcome.get("deal_value"):
            signed_value += float(outcome["deal_value"])
    contacted = leads_by_event["sent"] | leads_by_event["replied"] | \
        leads_by_event["meeting"] | leads_by_event["signed"]
    replied = leads_by_event["replied"] | leads_by_event["meeting"] | leads_by_event["signed"]
    meeting = leads_by_event["meeting"] | leads_by_event["signed"]
    signed = leads_by_event["signed"]

    def rate(part: set, whole: set) -> float | None:
        return round(100 * len(part) / len(whole), 1) if whole else None

    return {
        "logged_total": len(live),
        "contacted": len(contacted),
        "replied": len(replied),
        "meeting": len(meeting),
        "signed": len(signed),
        "reply_rate": rate(replied, contacted),
        "close_rate": rate(signed, contacted),
        "signed_value": signed_value,
    }


def ranker_progress(real_outcome_count: int) -> tuple[float, str]:
    """Progress toward the 50-outcome Smart Ranking threshold, for st.progress."""
    fraction = min(real_outcome_count / RANKER_THRESHOLD, 1.0)
    if real_outcome_count >= RANKER_THRESHOLD:
        caption = (f"{real_outcome_count} real outcomes logged — Smart Ranking can be "
                   "trained! (Admin: run notebooks/train_ranker.ipynb)")
    else:
        caption = (f"{real_outcome_count} / {RANKER_THRESHOLD} real outcomes logged "
                   "toward Smart Ranking. Every tap teaches the system.")
    return fraction, caption


# ---------------------------------------------------------------------------
# RPC wrappers (thin; all atomicity lives in SQL)
# ---------------------------------------------------------------------------
def log_outcome(client: Any, lead_id: int, event: str,
                deal_value: float | None = None, notes: str | None = None,
                ) -> tuple[bool, str]:
    """Log one outcome atomically. Returns (ok, junior-readable message)."""
    try:
        client.rpc("log_outcome", {
            "p_lead_id": lead_id, "p_event": event,
            "p_deal_value": deal_value, "p_notes": notes,
        }).execute()
        return True, f"Logged {EVENT_LABELS.get(event, event)} — lead status updated too."
    except Exception as exc:  # noqa: BLE001
        logger.error("log_outcome failed: %s", exc)
        if "NOT_ALLOWED" in str(exc):
            return False, "Your role can't log outcomes — ask a sponsorship member."
        return False, "Couldn't save that — check your connection and tap once more."


def void_outcome(client: Any, outcome_id: int) -> tuple[bool, str]:
    """Undo a mis-tap. Returns (ok, message)."""
    try:
        client.rpc("void_outcome", {"p_outcome_id": outcome_id}).execute()
        return True, "Undone. The lead's status has been rolled back."
    except Exception as exc:  # noqa: BLE001
        if "VOID_WINDOW_CLOSED" in str(exc):
            return False, "The 10-minute undo window has passed — ask an admin to fix it."
        logger.error("void_outcome failed: %s", exc)
        return False, "Couldn't undo — try again."
