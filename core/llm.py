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
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
RATE_LIMIT_SLEEP_SECONDS = 2.5  # free tiers allow ~30 req/min; stay well under

ModelT = TypeVar("ModelT", bound=BaseModel)


class LLMUnavailableError(RuntimeError):
    """Every configured provider failed at transport level (e.g. both 429)."""


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


def _call_gemini(prompt: str, system: str) -> str:
    import google.generativeai as genai  # lazy: jobs-only dependency path

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise LLMUnavailableError("GEMINI_API_KEY not configured")
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(GEMINI_MODEL, system_instruction=system)
    response = model.generate_content(
        prompt,
        generation_config={"response_mime_type": "application/json", "temperature": 0},
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


def extract_json(
    prompt: str,
    model_cls: type[ModelT],
    *,
    system: str = "You are a precise data extraction engine. Reply with ONLY valid JSON matching the requested schema — no prose, no markdown.",
    sleep_seconds: float = RATE_LIMIT_SLEEP_SECONDS,
) -> ModelT | None:
    """Run `prompt` through the provider chain, returning a validated model.

    Returns None when the output never validated (caller skips the item).
    Raises LLMUnavailableError when no provider could be reached at all.
    """
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
            return parsed

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
            return parsed_retry
        logger.warning("Provider %s failed validation twice (%s); skipping item.", name, error_retry)
        return None  # schema failure twice => skip this item, don't burn quota

    raise LLMUnavailableError("All LLM providers failed: " + " | ".join(transport_errors))
