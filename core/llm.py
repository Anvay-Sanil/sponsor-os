"""The single LLM gateway: Groq (Llama 3.3 70B) primary, Gemini fallback.

Every LLM call in Sponsor OS goes through `extract_json()`:
  * strict JSON output validated against a pydantic model;
  * on validation failure: ONE retry on the same provider with the error fed
    back; second failure returns None — the caller logs and skips the item,
    the batch never crashes;
  * on transport failure / rate limit (429): fall through to the next
    provider after a short sleep;
  * when every configured provider fails at transport level:
    LLMUnavailableError — jobs catch it, checkpoint, and exit cleanly so the
    next run resumes (all writes are idempotent upserts).

No Streamlit imports here — this module runs headless in GitHub Actions.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Callable, TypeVar

from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

load_dotenv()

logger = logging.getLogger(__name__)

GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
# Free-tier reality, verified live 2026-06-11: gemini-2.0-flash has ZERO quota
# ("limit: 0"), and gemini-2.5-flash allows only 20 requests/DAY — useless as a
# fallback. flash-lite carries the workable free budget; Gemini is our backup
# provider, so budget beats polish here.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
RATE_LIMIT_SLEEP_SECONDS = 2.5  # free tiers allow ~30 req/min; stay well under

ModelT = TypeVar("ModelT", bound=BaseModel)


class LLMUnavailableError(RuntimeError):
    """Every configured provider failed at transport level (e.g. both 429)."""


# text-embedding-004 was retired by Google (404, verified live 2026-06-11);
# gemini-embedding-001 is the GA replacement. output_dimensionality=768 is
# REQUIRED — its native 3072 dims would not fit pitch_memory vector(768).
EMBED_MODEL = os.environ.get("GEMINI_EMBED_MODEL", "gemini-embedding-001")
EMBED_DIMS = 768


def _call_embed(text: str) -> list[float]:
    from google.genai import types

    result = _gemini_client().models.embed_content(
        model=EMBED_MODEL,
        contents=text[:8000],
        config=types.EmbedContentConfig(output_dimensionality=EMBED_DIMS),
    )
    return list(result.embeddings[0].values)


# Tests monkeypatch this; embed_text below stays graceful either way.
EMBED_CALL = _call_embed


def embed_text(text: str) -> list[float] | None:
    """768-dim Gemini embedding, or None on ANY failure.

    Embeddings are an enhancement (pitch language memory) — no provider
    fallback exists (Groq has no embeddings API) and no caller may ever block
    or crash on a failed embed. One attempt, log, move on.
    """
    if not text or not text.strip():
        return None
    try:
        vector = EMBED_CALL(text)
        return [float(value) for value in vector] if vector else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Embedding failed (skipping, not fatal): %s", exc)
        return None


def _call_groq(prompt: str, system: str) -> str:
    from groq import Groq  # imported lazily: jobs-only dependency path

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise LLMUnavailableError("GROQ_API_KEY not configured")
    client = Groq(api_key=api_key)
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    return response.choices[0].message.content or ""


_GENAI_CLIENT = None  # module-level singleton: a GC'd google-genai Client
#                       closes its httpx transport and kills in-flight calls.


def _gemini_client():  # noqa: ANN202 — google-genai Client, lazily imported
    global _GENAI_CLIENT
    if _GENAI_CLIENT is None:
        from google import genai  # supported SDK (google-generativeai is EOL)

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise LLMUnavailableError("GEMINI_API_KEY not configured")
        _GENAI_CLIENT = genai.Client(api_key=api_key)
    return _GENAI_CLIENT


def _call_gemini(prompt: str, system: str) -> str:
    from google.genai import types

    response = _gemini_client().models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            temperature=0,
        ),
    )
    return response.text or ""


# Ordered provider chain. Tests monkeypatch this dict to inject fakes.
PROVIDER_CALLS: dict[str, Callable[[str, str], str]] = {
    "groq": _call_groq,
    "gemini": _call_gemini,
}


def _strip_fences(raw: str) -> str:
    """Remove markdown code fences some models wrap around JSON."""
    return re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw.strip())


def _validate(raw: str, model_cls: type[ModelT]) -> tuple[ModelT | None, str]:
    """Parse + validate. Returns (instance, "") or (None, error_description)."""
    try:
        return model_cls.model_validate(json.loads(_strip_fences(raw))), ""
    except (json.JSONDecodeError, ValidationError) as exc:
        return None, str(exc)[:600]


def _looks_rate_limited(message: str) -> bool:
    lowered = message.lower()
    return "429" in lowered or "rate" in lowered and "limit" in lowered or "quota" in lowered


def extract_json(
    prompt: str,
    model_cls: type[ModelT],
    *,
    system: str = "You are a precise data extraction engine. Reply with ONLY valid JSON matching the requested schema — no prose, no markdown.",
    sleep_seconds: float = RATE_LIMIT_SLEEP_SECONDS,
    rate_limit_backoff_seconds: float = 30.0,
) -> ModelT | None:
    """Run `prompt` through the provider chain, returning a validated model.

    Returns None when the output never validated (caller skips the item).
    If the whole pass failed and at least one failure was a rate limit
    (Gemini free tier allows only 5 requests/minute), waits once and makes a
    second pass. Raises LLMUnavailableError when nothing is reachable.
    """
    result = _provider_pass(prompt, model_cls, system, sleep_seconds)
    if isinstance(result, _AllDown) and result.rate_limited:
        logger.info("All providers rate-limited; backing off %.0fs for one retry pass.",
                    rate_limit_backoff_seconds)
        time.sleep(rate_limit_backoff_seconds)
        result = _provider_pass(prompt, model_cls, system, sleep_seconds)
    if isinstance(result, _AllDown):
        raise LLMUnavailableError("All LLM providers failed: " + " | ".join(result.errors))
    return result.value


class _AllDown:
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        self.rate_limited = any(_looks_rate_limited(error) for error in errors)


class _Outcome:
    def __init__(self, value: ModelT | None) -> None:
        self.value = value


def _provider_pass(prompt: str, model_cls: type[ModelT], system: str,
                   sleep_seconds: float) -> "_Outcome | _AllDown":
    """One walk down the provider chain. Returns _Outcome on any verdict
    (parsed model or validated-twice-failed None), _AllDown if unreachable."""
    transport_errors: list[str] = []
    for name, call in PROVIDER_CALLS.items():
        try:
            raw = call(prompt, system)
        except Exception as exc:  # noqa: BLE001 — any provider error => next provider
            logger.warning("Provider %s failed (%s); trying next.", name, exc)
            transport_errors.append(f"{name}: {exc}")
            time.sleep(sleep_seconds)
            continue

        parsed, error = _validate(raw, model_cls)
        if parsed is not None:
            time.sleep(sleep_seconds)
            return _Outcome(parsed)

        # One retry on the SAME provider with the validation error fed back.
        logger.info("Provider %s returned invalid JSON; retrying with feedback.", name)
        retry_prompt = (
            f"{prompt}\n\nYour previous reply was rejected with this validation "
            f"error:\n{error}\nReply again with ONLY corrected, valid JSON."
        )
        time.sleep(sleep_seconds)
        try:
            raw_retry = call(retry_prompt, system)
        except Exception as exc:  # noqa: BLE001
            transport_errors.append(f"{name} (retry): {exc}")
            time.sleep(sleep_seconds)
            continue

        parsed_retry, error_retry = _validate(raw_retry, model_cls)
        time.sleep(sleep_seconds)
        if parsed_retry is not None:
            return _Outcome(parsed_retry)
        logger.warning("Provider %s failed validation twice (%s); skipping item.", name, error_retry)
        return _Outcome(None)  # schema failure twice => skip item, don't burn quota

    return _AllDown(transport_errors)
