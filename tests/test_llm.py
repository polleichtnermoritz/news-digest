from pathlib import Path

import anthropic
import httpx2
import pydantic
import pytest
from pydantic import BaseModel

from digest.llm import BudgetExceeded, CostTracker, call_structured
from tests.fakes import FakeAnthropicClient, FakeResponse


class Dummy(BaseModel):
    value: str


def test_cost_tracker_accumulates_across_calls() -> None:
    cost = CostTracker(token_cap=1_000_000)
    cost.record("claude-haiku-4-5", 100, 50)
    cost.record("claude-haiku-4-5", 200, 75)

    assert cost.calls == 2
    assert cost.input_tokens == 300
    assert cost.output_tokens == 125
    assert cost.total_tokens == 425


def test_cost_tracker_estimated_cost_uses_pricing_table() -> None:
    cost = CostTracker(token_cap=1_000_000)
    cost.record("claude-haiku-4-5", 1_000_000, 1_000_000)
    # $1.00 input + $5.00 output per the pricing table
    assert cost.estimated_cost_usd() == pytest.approx(6.00)


def test_cost_tracker_over_budget() -> None:
    cost = CostTracker(token_cap=100)
    assert not cost.over_budget
    cost.record("claude-haiku-4-5", 60, 60)
    assert cost.over_budget


def test_cost_tracker_write_log(tmp_path: Path) -> None:
    cost = CostTracker(token_cap=1000)
    cost.record("claude-haiku-4-5", 10, 10)
    log_path = tmp_path / "cost_log.jsonl"

    cost.write_log(log_path)
    cost.write_log(log_path)  # appends, doesn't overwrite

    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 2


async def test_call_structured_success() -> None:
    client = FakeAnthropicClient([FakeResponse(Dummy(value="ok"))])
    cost = CostTracker(token_cap=1_000_000)

    result = await call_structured(
        client, cost, "claude-haiku-4-5", "system", "user", Dummy
    )

    assert result == Dummy(value="ok")
    assert cost.calls == 1


def _make_validation_error() -> pydantic.ValidationError:
    try:
        pydantic.TypeAdapter(Dummy).validate_json("{not valid json")
    except pydantic.ValidationError as exc:
        return exc
    raise AssertionError("expected a ValidationError")


async def test_call_structured_retries_on_truncated_json_then_succeeds() -> None:
    # Regression test: a response whose JSON got cut off mid-string (e.g.
    # max_tokens too low for a large batch) raises ValidationError straight
    # out of client.messages.parse(), before we ever see a usable response
    # object -- this must be treated as a retryable attempt, not crash.
    client = FakeAnthropicClient([_make_validation_error(), FakeResponse(Dummy(value="ok"))])
    cost = CostTracker(token_cap=1_000_000)

    result = await call_structured(
        client, cost, "claude-haiku-4-5", "system", "user", Dummy, retries=1
    )

    assert result == Dummy(value="ok")


async def test_call_structured_retries_once_on_none_then_succeeds() -> None:
    client = FakeAnthropicClient(
        [FakeResponse(None), FakeResponse(Dummy(value="ok"))]
    )
    cost = CostTracker(token_cap=1_000_000)

    result = await call_structured(
        client, cost, "claude-haiku-4-5", "system", "user", Dummy, retries=1
    )

    assert result == Dummy(value="ok")
    assert cost.calls == 2


async def test_call_structured_gives_up_after_exhausting_retries() -> None:
    client = FakeAnthropicClient([FakeResponse(None), FakeResponse(None)])
    cost = CostTracker(token_cap=1_000_000)

    result = await call_structured(
        client, cost, "claude-haiku-4-5", "system", "user", Dummy, retries=1
    )

    assert result is None
    assert cost.calls == 2


async def test_call_structured_raises_budget_exceeded_without_calling() -> None:
    client = FakeAnthropicClient([FakeResponse(Dummy(value="should not be reached"))])
    cost = CostTracker(token_cap=10)
    cost.record("claude-haiku-4-5", 20, 20)  # already over the cap

    with pytest.raises(BudgetExceeded):
        await call_structured(client, cost, "claude-haiku-4-5", "system", "user", Dummy)

    assert client.messages.calls == []


async def test_call_structured_retries_on_rate_limit_then_succeeds() -> None:
    rate_limit_error = anthropic.RateLimitError(
        message="rate limited", response=_fake_httpx_response(429), body=None
    )
    client = FakeAnthropicClient([rate_limit_error, FakeResponse(Dummy(value="ok"))])
    cost = CostTracker(token_cap=1_000_000)

    result = await call_structured(
        client, cost, "claude-haiku-4-5", "system", "user", Dummy, retries=1
    )

    assert result == Dummy(value="ok")


async def test_call_structured_reraises_authentication_error_immediately() -> None:
    auth_error = anthropic.AuthenticationError(
        message="bad key", response=_fake_httpx_response(401), body=None
    )
    client = FakeAnthropicClient([auth_error, FakeResponse(Dummy(value="unreachable"))])
    cost = CostTracker(token_cap=1_000_000)

    with pytest.raises(anthropic.AuthenticationError):
        await call_structured(
            client, cost, "claude-haiku-4-5", "system", "user", Dummy, retries=3
        )

    assert len(client.messages.calls) == 1  # never retried past the auth error


def _fake_httpx_response(status_code: int) -> httpx2.Response:
    return httpx2.Response(status_code, request=httpx2.Request("POST", "https://example.com"))
