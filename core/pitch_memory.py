"""Pitch language memory: embed winning deck language, retrieve it for new decks.

Rules (user-approved 2026-06-11):
* Embed the LATEST deck generated BEFORE the positive outcome — with versioned
  decks, crediting v1 when v3 earned the reply would poison the memory.
* Demo practice is welcome but labeled: demo-deck rows get a `test_` prefix
  and real-lead retrieval filters them out (match_pitch_memory RPC).
* Retrieved snippets are STYLE examples only — the injection block orders the
  model to match tone, never copy facts; Phase 3's evidence-anchoring gate is
  the second line of defense.
* Everything here is best-effort: a failed embed never blocks an outcome log.
"""
from __future__ import annotations

import logging
from typing import Any

from core import llm

logger = logging.getLogger(__name__)

POSITIVE_EVENTS = frozenset({"replied", "meeting", "signed"})
SNIPPET_CAP = 300
MATCH_COUNT = 3


def compose_deck_text(narrative_json: dict[str, Any]) -> str:
    """The reusable language of a deck: hook, story, email — not the facts table."""
    parts = [str(narrative_json.get(key) or "") for key in ("hook", "story", "email_body")]
    return "\n".join(part for part in parts if part).strip()


def build_style_block(snippets: list[str]) -> str:
    """Prompt block for narrative generation. Pure; empty list -> empty string."""
    if not snippets:
        return ""
    numbered = "\n".join(f"[{index + 1}] {snippet[:SNIPPET_CAP]}"
                         for index, snippet in enumerate(snippets))
    return (
        "\nSTYLE EXAMPLES — language from our past pitches that earned replies. "
        "Match their tone, warmth and rhythm ONLY. NEVER copy facts, numbers, "
        "brand names, or claims from them; all facts must come from EVIDENCE "
        f"and FACTS above.\n{numbered}\n"
    )


def fetch_style_examples(client: Any, query_text: str, include_test: bool) -> list[str]:
    """Top winning snippets by pgvector similarity. [] on any failure."""
    vector = llm.embed_text(query_text)
    if vector is None:
        return []
    try:
        rows = client.rpc("match_pitch_memory", {
            "p_query": vector, "p_count": MATCH_COUNT, "p_include_test": include_test,
        }).execute().data or []
        return [str(row["snippet"]) for row in rows if row.get("snippet")]
    except Exception as exc:  # noqa: BLE001 — memory is an enhancement
        logger.warning("pitch_memory retrieval failed: %s", exc)
        return []


def embed_winning_deck(client: Any, lead_id: int, event: str, is_demo: bool,
                       before: str | None = None) -> bool:
    """Embed the latest deck generated before `before` (default: now).

    Idempotent (skips decks already in memory). Returns True when a row was
    written; False is always fine — callers never depend on it.
    """
    if event not in POSITIVE_EVENTS:
        return False
    try:
        query = client.table("decks").select("id, narrative_json").eq("lead_id", lead_id)
        if before:
            query = query.lte("created_at", before)
        decks = query.order("created_at", desc=True).limit(1).execute().data or []
        if not decks or not decks[0].get("narrative_json"):
            return False
        deck = decks[0]
        existing = (client.table("pitch_memory").select("id")
                    .eq("source_deck_id", deck["id"]).limit(1).execute().data)
        if existing:
            return False
        text = compose_deck_text(deck["narrative_json"])
        vector = llm.embed_text(text)
        if vector is None or not text:
            return False
        client.table("pitch_memory").insert({
            "text": text,
            "embedding": vector,
            "outcome_label": ("test_" if is_demo else "") + event,
            "source_deck_id": deck["id"],
        }).execute()
        return True
    except Exception as exc:  # noqa: BLE001 — never block the outcome log
        logger.warning("embed_winning_deck skipped (lead %s): %s", lead_id, exc)
        return False


def backfill_missing(client: Any, limit: int = 10) -> int:
    """Weekly safety net (runs in the Scout job, service key): embed winning
    decks that the best-effort online path missed. Quota-capped per run."""
    embedded = 0
    try:
        outcomes = (
            client.table("outcomes")
            .select("lead_id, event, logged_at, leads(is_demo)")
            .in_("event", sorted(POSITIVE_EVENTS)).eq("voided", False)
            .order("logged_at", desc=True).limit(50).execute().data or []
        )
        for outcome in outcomes:
            if embedded >= limit:
                break
            is_demo = bool((outcome.get("leads") or {}).get("is_demo"))
            if embed_winning_deck(client, int(outcome["lead_id"]), str(outcome["event"]),
                                  is_demo, before=outcome.get("logged_at")):
                embedded += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("pitch_memory backfill aborted: %s", exc)
    return embedded
