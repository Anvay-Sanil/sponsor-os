"""Tests for core.llm — provider fallback, validation retry, clean failure."""
from __future__ import annotations

import pytest
from pydantic import BaseModel

import core.llm as llm


class Item(BaseModel):
    name: str
    count: int


VALID = '{"name": "boAt", "count": 3}'
INVALID = '{"name": "boAt"}'  # missing count


def _fixed(reply: str):
    def call(prompt: str, system: str) -> str:
        return reply
    return call


def _fail(exc: Exception):
    def call(prompt: str, system: str) -> str:
        raise exc
    return call


def _must_not_be_called(prompt: str, system: str) -> str:
    raise AssertionError("second provider must not be consulted on schema failure")


def _run(monkeypatch: pytest.MonkeyPatch, providers: dict) -> Item | None:
    monkeypatch.setattr(llm, "PROVIDER_CALLS", providers)
    return llm.extract_json("extract", Item, sleep_seconds=0)


def test_valid_first_try(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _run(monkeypatch, {"p1": _fixed(VALID)})
    assert result == Item(name="boAt", count=3)


def test_markdown_fences_are_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _run(monkeypatch, {"p1": _fixed(f"```json\n{VALID}\n```")})
    assert result is not None and result.name == "boAt"


def test_retry_with_error_feedback_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []

    def flaky(prompt: str, system: str) -> str:
        prompts.append(prompt)
        return INVALID if len(prompts) == 1 else VALID

    result = _run(monkeypatch, {"p1": flaky})
    assert result is not None and result.count == 3
    assert len(prompts) == 2
    assert "rejected" in prompts[1]  # the validation error was fed back


def test_double_validation_failure_skips_item(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _run(monkeypatch, {"p1": _fixed(INVALID), "p2": _must_not_be_called})
    assert result is None  # log-and-skip, never crash, never burn provider 2


def test_transport_failure_falls_back_to_next_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    providers = {"p1": _fail(ConnectionError("429 rate limited")), "p2": _fixed(VALID)}
    result = _run(monkeypatch, providers)
    assert result is not None and result.name == "boAt"


def test_all_providers_down_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    providers = {"p1": _fail(ConnectionError("down")), "p2": _fail(TimeoutError("down"))}
    monkeypatch.setattr(llm, "PROVIDER_CALLS", providers)
    with pytest.raises(llm.LLMUnavailableError):
        llm.extract_json("extract", Item, sleep_seconds=0)


def test_rate_limited_pass_backs_off_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gemini free tier = 5 req/min: a fully rate-limited pass gets ONE retry pass."""
    calls: list[int] = []

    def flaky_quota(prompt: str, system: str) -> str:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("429 quota exceeded, retry in 22s")
        return VALID

    monkeypatch.setattr(llm, "PROVIDER_CALLS", {"p1": flaky_quota})
    result = llm.extract_json("extract", Item, sleep_seconds=0, rate_limit_backoff_seconds=0)
    assert result is not None and result.name == "boAt"
    assert len(calls) == 2


def test_plain_outage_gets_no_backoff_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def hard_down(prompt: str, system: str) -> str:
        calls.append(1)
        raise ConnectionError("connection refused")

    monkeypatch.setattr(llm, "PROVIDER_CALLS", {"p1": hard_down})
    with pytest.raises(llm.LLMUnavailableError):
        llm.extract_json("extract", Item, sleep_seconds=0, rate_limit_backoff_seconds=0)
    assert len(calls) == 1  # no second pass for non-rate-limit outages
