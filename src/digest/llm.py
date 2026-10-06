"""Thin wrapper around the Anthropic SDK: structured-output calls, a running
token/cost budget, and a cost log. Network-level retries (429/5xx/connection
errors) are handled by the SDK itself (`max_retries` on the client); this
module only adds the retry-once-on-invalid-structured-output behavior the
plan asks for, which the SDK can't do for us since it's about the parsed
*content*, not the transport.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import anthropic
from pydantic import BaseModel

from digest.settings import PROJECT_ROOT

DEFAULT_COST_LOG_PATH = PROJECT_ROOT / "out" / "cost_log.jsonl"

# Approximate list pricing in USD per million tokens, (input, output).
# Verify against https://www.anthropic.com/pricing before relying on this
# for real budgeting -- the plan itself flags this as a pre-launch check.
PRICING_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (2.00, 10.00),
}

class BudgetExceeded(Exception):
    """Raised when a call would push the run past settings.token_cap_per_run."""


def make_client() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(max_retries=3)


@dataclass
class CostTracker:
    token_cap: int
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    by_model: dict[str, tuple[int, int]] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def over_budget(self) -> bool:
        return self.total_tokens >= self.token_cap

    def record(self, model: str, input_tokens: int, output_tokens: int) -> None:
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        prev_in, prev_out = self.by_model.get(model, (0, 0))
        self.by_model[model] = (prev_in + input_tokens, prev_out + output_tokens)

    def estimated_cost_usd(self) -> float:
        total = 0.0
        for model, (in_tok, out_tok) in self.by_model.items():
            in_price, out_price = PRICING_PER_MTOK.get(model, (0.0, 0.0))
            total += in_tok / 1_000_000 * in_price + out_tok / 1_000_000 * out_price
        return total

    def summary(self) -> dict[str, object]:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd(), 4),
            "by_model": {
                model: {"input_tokens": i, "output_tokens": o}
                for model, (i, o) in self.by_model.items()
            },
        }

    def write_log(self, path: Path = DEFAULT_COST_LOG_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"run_at": datetime.now(UTC).isoformat(), **self.summary()}
        with path.open("a") as f:
            f.write(json.dumps(entry) + "\n")


async def call_structured[T: BaseModel](
    client: anthropic.AsyncAnthropic,
    cost: CostTracker,
    model: str,
    system: str,
    user_content: str,
    output_format: type[T],
    max_tokens: int = 4096,
    retries: int = 1,
) -> T | None:
    """Calls the API with a Pydantic output_format, retrying once (by
    default) if the model's response doesn't parse or a transient error
    survives the SDK's own retries. Returns None on any recoverable failure
    -- never raises -- so callers can fall back gracefully; raises
    BudgetExceeded if the cap is already spent, or re-raises immediately on
    an auth/permission/not-found error, since those won't fix themselves
    and silently returning None for the rest of the run would just hide a
    misconfigured key."""
    if cost.over_budget:
        raise BudgetExceeded(f"token cap of {cost.token_cap} already reached")

    for _ in range(retries + 1):
        try:
            response = await client.messages.parse(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_content}],
                output_format=output_format,
            )
        except (
            anthropic.AuthenticationError,
            anthropic.PermissionDeniedError,
            anthropic.NotFoundError,
        ):
            raise  # config/credential problems won't fix themselves on retry
        except (anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APIConnectionError):
            continue

        cost.record(model, response.usage.input_tokens, response.usage.output_tokens)
        if response.parsed_output is not None:
            return response.parsed_output

    return None
