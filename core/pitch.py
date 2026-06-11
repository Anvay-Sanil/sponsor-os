"""Pitch v1 orchestration: research → narrative → render → save.

Headless module (no Streamlit): the Deck Generator page passes a Supabase
client and a stage callback in. Every stage degrades gracefully:
research fails → narrative from stored evidence only; palette fails → ACM
colors; storage fails → the download still works from memory.

EVIDENCE TRACEABILITY (user catch, 2026-06-11): every why-us bullet carries
the evidence_id it is based on. Bullets citing unknown ids are dropped after
one corrective retry; if none survive, deterministic bullets are built
verbatim from stored evidence — claims are anchored to proof, not vibes.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, Field, field_validator

from core import llm, pricing
from core.chapter_facts import CHAPTER_FACTS, missing_facts
from core.pitch_memory import build_style_block, fetch_style_examples
from core.deck_render import render_deck
from core.palette import extract_palette

logger = logging.getLogger(__name__)

SOURCE_LABEL = {"rival_fest_site": "a rival fest's website", "news": "news coverage",
                "instagram": "a public Instagram post", "poster_logo": "an event poster"}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class BrandResearch(BaseModel):
    """Internal research artifact — verbose models get truncated, not rejected
    (a validation retry costs a Gemini call, and the free tier allows 5/min)."""

    summary: str = Field(max_length=600)
    audience: str = Field(default="", max_length=600)
    india_activity: str = Field(default="", max_length=600)
    tone: str = Field(default="", max_length=600)

    @field_validator("*", mode="before")
    @classmethod
    def _truncate(cls, value: object) -> object:
        return value[:580] if isinstance(value, str) else value


class EvidenceBullet(BaseModel):
    text: str = Field(min_length=10, max_length=160)
    evidence_id: int

    @field_validator("text", mode="before")
    @classmethod
    def _clamp(cls, value: object) -> object:
        return value[:160] if isinstance(value, str) else value


# Slide-fit limits. Over-length output is CLAMPED, never rejected — a
# truncated title is cosmetic, a failed deck is a failure (and every
# rejection-retry costs scarce free-tier quota). Under-length still rejects:
# too-short means genuinely broken output.
_NARRATIVE_MAX = {"title_line": 60, "hook": 200, "story": 600,
                  "cta_line": 120, "email_subject": 80, "email_body": 1500}


class DeckNarrative(BaseModel):
    title_line: str = Field(min_length=5, max_length=60)
    hook: str = Field(min_length=20, max_length=200)
    story: str = Field(min_length=50, max_length=600)
    why_us_bullets: list[EvidenceBullet] = Field(min_length=1, max_length=4)
    cta_line: str = Field(min_length=10, max_length=120)
    email_subject: str = Field(min_length=5, max_length=80)
    email_body: str = Field(min_length=100, max_length=1500)

    @field_validator(*_NARRATIVE_MAX, mode="before")
    @classmethod
    def _clamp(cls, value: object, info) -> object:  # noqa: ANN001 — pydantic API
        if isinstance(value, str):
            return value[: _NARRATIVE_MAX[info.field_name]]
        return value


@dataclass
class PitchResult:
    pptx_bytes: bytes
    email_text: str
    narrative_json: dict[str, Any]
    storage_path: str | None
    version: int
    is_test: bool


# ---------------------------------------------------------------------------
# Pure helpers (covered by tests/test_pitch.py)
# ---------------------------------------------------------------------------
def suggest_tier(evidence_score: float) -> str:
    """v1 rule: ≥70 Gold, ≥45 Silver, else Community. Title stays a human call."""
    if evidence_score >= 70:
        return "Gold"
    if evidence_score >= 45:
        return "Silver"
    return "Community"


def valid_bullets(bullets: list[EvidenceBullet], allowed_ids: set[int]) -> list[EvidenceBullet]:
    """Keep only bullets whose evidence_id exists on this lead's brand."""
    return [bullet for bullet in bullets if bullet.evidence_id in allowed_ids]


def fallback_bullets(evidence: list[dict[str, Any]], limit: int = 3) -> list[EvidenceBullet]:
    """Deterministic, always-anchored bullets built verbatim from evidence."""
    bullets: list[EvidenceBullet] = []
    for item in evidence[:limit]:
        snippet = (item.get("snippet") or "").replace("[DEMO DATA] ", "").strip()
        label = SOURCE_LABEL.get(str(item.get("source_type")), "a public source")
        text = f"Documented on {label}: {snippet}"[:160]
        if len(text) >= 10:
            bullets.append(EvidenceBullet(text=text, evidence_id=int(item["id"])))
    return bullets


def next_deck_path(lead_id: int, existing_decks: int) -> str:
    """Versioned storage path — regeneration never overwrites (user request)."""
    return f"{lead_id}/deck_v{existing_decks + 1}.pptx"


def assemble_email(narrative: DeckNarrative, is_test: bool) -> str:
    prefix = "[TEST — DO NOT SEND]\n\n" if is_test else ""
    signature = f"\n\n— {CHAPTER_FACTS['chapter_name']}\n{CHAPTER_FACTS['contact_email']}"
    return f"{prefix}Subject: {narrative.email_subject}\n\n{narrative.email_body}{signature}"


# ---------------------------------------------------------------------------
# LLM stages
# ---------------------------------------------------------------------------
def research_brand(brand: dict[str, Any], evidence: list[dict[str, Any]]) -> BrandResearch | None:
    """Profile the brand via the normal provider chain; None means "skip
    research", never an error.

    NOTE on grounding: the brief asks for search-grounded Gemini here, but
    grounding for 2.x models is unsupported by the EOL'd google-generativeai
    SDK (and gemini-1.5/2.0 free quota is gone — verified live 2026-06-11).
    Grounding returns with the google-genai SDK migration planned for Phase 6.
    """
    prompt = (
        "Research this brand for a student-fest sponsorship pitch. Be concise. Reply "
        'with ONLY JSON: {"summary": str, "audience": str, "india_activity": str, '
        '"tone": str}, each value under 2 sentences.\n'
        f"Brand: {brand.get('name')} | Website: {brand.get('website') or 'unknown'} | "
        f"Industry: {brand.get('industry') or 'unknown'}\n"
        "Evidence we hold: "
        + "; ".join((item.get("snippet") or "")[:120] for item in evidence[:5])
    )
    try:
        return llm.extract_json(prompt, BrandResearch)
    except llm.LLMUnavailableError:
        return None


def build_narrative(brand: dict[str, Any], research: BrandResearch | None,
                    evidence: list[dict[str, Any]], tier_name: str,
                    style_block: str = "") -> DeckNarrative | None:
    """Narrative JSON with evidence-anchored bullets. None only if all LLMs fail
    AND no deterministic fallback is possible."""
    allowed_ids = {int(item["id"]) for item in evidence if item.get("id") is not None}
    evidence_block = json.dumps(
        [{"id": int(item["id"]), "source": item.get("source_type"),
          "snippet": (item.get("snippet") or "")[:160]} for item in evidence[:6]]
    )
    research_block = research.model_dump_json() if research else "{}"
    prompt = (
        "Write sponsorship-pitch copy for a college fest deck. Reply with ONLY JSON "
        "matching: {\"title_line\": str<=60, \"hook\": str<=200, \"story\": str<=600, "
        "\"why_us_bullets\": [{\"text\": str<=160, \"evidence_id\": int}] (1-4 items), "
        "\"cta_line\": str<=120, \"email_subject\": str<=80, \"email_body\": str (120-200 words)}.\n"
        "HARD RULES: every why_us_bullets item MUST cite the id of one EVIDENCE item "
        "below and only restate what that snippet supports — no invented claims, no "
        "made-up numbers. PERSPECTIVE: WE are the student chapter in FACTS (the "
        "sender); the email is written BY us TO the brand's marketing team — open "
        "with 'Hi {brand} team,'. Never write as the brand. No bracket placeholders "
        "like [Name]. Email: warm, concise, zero hype-words, ends asking for a "
        "15-minute call. Never promise anything not in FACTS.\n"
        f"BRAND: {brand.get('name')} ({brand.get('industry') or 'unknown industry'})\n"
        f"RESEARCH: {research_block}\nEVIDENCE: {evidence_block}\n"
        f"FACTS: {json.dumps(CHAPTER_FACTS)}\nSUGGESTED TIER: {tier_name}"
        f"{style_block}"
    )
    try:
        narrative = llm.extract_json(prompt, DeckNarrative)
    except llm.LLMUnavailableError:
        narrative = None
    if narrative is not None:
        kept = valid_bullets(narrative.why_us_bullets, allowed_ids)
        if not kept:
            retry = prompt + (
                "\nYour previous bullets cited evidence ids that do not exist. Use ONLY "
                f"these ids: {sorted(allowed_ids)}."
            )
            try:
                narrative_retry = llm.extract_json(retry, DeckNarrative)
            except llm.LLMUnavailableError:
                narrative_retry = None
            if narrative_retry is not None:
                kept = valid_bullets(narrative_retry.why_us_bullets, allowed_ids)
                narrative = narrative_retry if kept else narrative
        narrative.why_us_bullets = kept or fallback_bullets(evidence)
        if narrative.why_us_bullets:
            return narrative
    return None


def _reach_extras(client: Any, tiers: list[dict[str, Any]], suggested: str,
                  ) -> tuple[dict[str, str] | None, str | None]:
    """Per-tier reach cells + the suggested tier's ROI memo from cached
    posteriors. (None, None) when the model isn't fitted — decks keep their
    pre-Phase-4 'indicative' behavior."""
    posteriors, _ = pricing.fetch_posteriors(client)
    if not posteriors:
        return None, None
    reaches = {str(tier.get("name")): pricing.reach_cell(tier.get("components_json") or {}, posteriors)
               for tier in tiers}
    memo = None
    for tier in tiers:
        if str(tier.get("name")) == suggested and tier.get("base_price"):
            samples, _unmodeled = pricing.tier_reach_samples(
                tier.get("components_json") or {}, posteriors)
            if samples is not None:
                memo = pricing.deck_roi_memo(suggested, float(tier["base_price"]),
                                             pricing.reach_summary(samples))
            break
    return reaches, memo


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def generate_pitch(client: Any, lead: dict[str, Any], brand: dict[str, Any],
                   evidence: list[dict[str, Any]], tiers: list[dict[str, Any]],
                   user_id: str, on_stage: Callable[[str], None] = lambda _: None,
                   ) -> PitchResult:
    """Run the full pipeline for one lead. Raises RuntimeError with a
    junior-readable message only when no deck can be produced at all."""
    is_test = bool(lead.get("is_demo"))
    if not is_test and missing_facts():
        raise RuntimeError(
            "Real-lead decks are locked until chapter facts are filled in: "
            + ", ".join(missing_facts())
        )
    if not evidence:
        raise RuntimeError("This lead has no evidence on file — run Scout first.")

    on_stage("Researching brand…")
    research = research_brand(brand, evidence)

    on_stage("Reading brand colors…")
    palette = extract_palette(brand.get("website"))
    if palette.get("source") == "brand":
        try:
            client.table("brands").update({"palette_json": palette}).eq("id", brand["id"]).execute()
        except Exception:  # noqa: BLE001 — cache write is best-effort
            logger.info("Could not cache palette for brand %s", brand.get("id"))

    on_stage("Writing the story…")
    tier_name = suggest_tier(float(lead.get("evidence_score") or 0))
    # Winning-language memory (Phase 5): style only, demo-filtered for real leads.
    style_query = f"{brand.get('industry') or ''} {research.summary if research else brand.get('name')}"
    style_block = build_style_block(
        fetch_style_examples(client, style_query, include_test=is_test))
    narrative = build_narrative(brand, research, evidence, tier_name, style_block)
    if narrative is None:
        raise RuntimeError("The AI providers are busy right now — try again in a few minutes.")

    on_stage("Building slides…")
    title = narrative.title_line if not is_test else f"TEST · {narrative.title_line}"[:60]
    narrative.title_line = title
    tier_reaches, roi_memo = _reach_extras(client, tiers, tier_name)
    pptx_bytes = render_deck(narrative, str(brand.get("name")), palette, tiers,
                             tier_name, CHAPTER_FACTS, is_test,
                             tier_reaches=tier_reaches, roi_memo=roi_memo)
    email_text = assemble_email(narrative, is_test)

    on_stage("Saving…")
    storage_path: str | None = None
    version = 1
    try:
        existing = client.table("decks").select("id").eq("lead_id", lead["id"]).execute().data or []
        version = len(existing) + 1
        storage_path = next_deck_path(int(lead["id"]), len(existing))
        client.storage.from_("decks").upload(
            storage_path, pptx_bytes,
            {"content-type": "application/vnd.openxmlformats-officedocument.presentationml.presentation"},
        )
        client.table("decks").insert(
            {"lead_id": lead["id"], "generated_by": user_id,
             "narrative_json": narrative.model_dump(), "pptx_storage_path": storage_path,
             "email_text": email_text}
        ).execute()
    except Exception as exc:  # noqa: BLE001 — download still works from memory
        logger.warning("Deck save to Supabase failed (download unaffected): %s", exc)
        storage_path = None

    return PitchResult(pptx_bytes=pptx_bytes, email_text=email_text,
                       narrative_json=narrative.model_dump(), storage_path=storage_path,
                       version=version, is_test=is_test)
