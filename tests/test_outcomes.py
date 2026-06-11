"""Tests for the Loop: status mapping, funnel math, undo window, memory hygiene."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import core.llm as llm
from core.outcomes import (
    EVENT_LABELS,
    EVENT_TO_STATUS,
    can_void,
    event_to_status,
    funnel_stats,
    ranker_progress,
)
from core.pitch_memory import POSITIVE_EVENTS, build_style_block, compose_deck_text

NOW = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)


# --- event -> status mapping (mirrors SQL event_to_status) -------------------
def test_every_outcome_event_has_status_and_label() -> None:
    events = {"sent", "replied", "meeting", "signed", "ghosted", "rejected"}
    assert set(EVENT_TO_STATUS) == events
    assert set(EVENT_LABELS) == events


def test_sent_maps_to_contacted() -> None:
    assert event_to_status("sent") == "contacted"
    with pytest.raises(KeyError):
        event_to_status("opened_meeting")  # the brief's typo must stay dead


# --- funnel math ----------------------------------------------------------------
def _outcome(lead: int, event: str, voided: bool = False, value: float | None = None) -> dict:
    return {"lead_id": lead, "event": event, "voided": voided, "deal_value": value}


def test_funnel_counts_unique_leads_not_rows() -> None:
    stats = funnel_stats([_outcome(1, "sent"), _outcome(1, "replied"),
                          _outcome(1, "replied"), _outcome(2, "sent")])
    assert stats["contacted"] == 2
    assert stats["replied"] == 1
    assert stats["reply_rate"] == 50.0


def test_funnel_excludes_voided_rows() -> None:
    stats = funnel_stats([_outcome(1, "signed", value=50000),
                          _outcome(2, "signed", voided=True, value=99999)])
    assert stats["signed"] == 1
    assert stats["signed_value"] == 50000


def test_signed_implies_contacted_even_without_sent_row() -> None:
    stats = funnel_stats([_outcome(5, "signed", value=10000)])
    assert stats["contacted"] == 1 and stats["close_rate"] == 100.0


def test_empty_funnel_has_no_rates() -> None:
    stats = funnel_stats([])
    assert stats["logged_total"] == 0 and stats["reply_rate"] is None


# --- Catch 2: undo window ----------------------------------------------------------
def _row(minutes_ago: float, by: str = "me", voided: bool = False) -> dict:
    return {"logged_by": by, "voided": voided,
            "logged_at": (NOW - timedelta(minutes=minutes_ago)).isoformat()}


def test_own_recent_outcome_is_voidable() -> None:
    assert can_void(_row(9), "me", "sponsorship", NOW)


def test_window_closes_at_ten_minutes() -> None:
    assert not can_void(_row(11), "me", "sponsorship", NOW)


def test_cannot_void_someone_elses_entry() -> None:
    assert not can_void(_row(1, by="them"), "me", "sponsorship", NOW)


def test_admin_can_void_anything_anytime() -> None:
    assert can_void(_row(99999, by="them"), "me", "admin", NOW)


def test_already_voided_is_not_voidable_again() -> None:
    assert not can_void(_row(1, voided=True), "me", "admin", NOW)


# --- ranker progress ------------------------------------------------------------------
def test_progress_caps_at_one_and_flips_message_at_fifty() -> None:
    fraction, caption = ranker_progress(23)
    assert fraction == pytest.approx(23 / 50) and "23 / 50" in caption
    fraction, caption = ranker_progress(73)
    assert fraction == 1.0 and "can be" in caption


# --- pitch memory hygiene ----------------------------------------------------------------
def test_positive_events_match_label_scheme() -> None:
    assert POSITIVE_EVENTS == {"replied", "meeting", "signed"}


def test_compose_deck_text_takes_language_not_facts_table() -> None:
    text = compose_deck_text({"hook": "A hook.", "story": "A story.",
                              "email_body": "An email.", "title_line": "SKIPPED",
                              "why_us_bullets": [{"text": "SKIPPED"}]})
    assert "A hook." in text and "An email." in text
    assert "SKIPPED" not in text


def test_style_block_orders_tone_not_facts() -> None:
    block = build_style_block(["Winning snippet one", "x" * 999])
    assert "NEVER copy facts" in block
    assert "[1] Winning snippet one" in block
    assert len(block) < 1200  # snippets capped at 300 chars each


def test_empty_memory_means_empty_block() -> None:
    assert build_style_block([]) == ""


# --- graceful embeddings ---------------------------------------------------------------------
def test_embed_text_returns_none_on_failure_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(text: str) -> list[float]:
        raise ConnectionError("quota")

    monkeypatch.setattr(llm, "EMBED_CALL", boom)
    assert llm.embed_text("anything") is None


def test_embed_text_skips_empty_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "EMBED_CALL", lambda t: [0.1] * 768)
    assert llm.embed_text("   ") is None
    assert llm.embed_text("real text") == [0.1] * 768
