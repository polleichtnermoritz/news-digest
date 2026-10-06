"""Test doubles for the Anthropic SDK surface our code touches, so tests
never hit the real API."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class FakeUsage:
    def __init__(self, input_tokens: int = 10, output_tokens: int = 10) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeResponse:
    def __init__(
        self, parsed_output: Any, input_tokens: int = 10, output_tokens: int = 10
    ) -> None:
        self.parsed_output = parsed_output
        self.usage = FakeUsage(input_tokens, output_tokens)


ResponseScript = list[FakeResponse | Exception] | Callable[[int, dict[str, Any]], Any]


class FakeMessages:
    def __init__(self, script: ResponseScript) -> None:
        self.script = script
        self.calls: list[dict[str, Any]] = []

    async def parse(self, **kwargs: Any) -> FakeResponse:
        index = len(self.calls)
        self.calls.append(kwargs)

        item = self.script(index, kwargs) if callable(self.script) else self.script[index]
        if isinstance(item, Exception):
            raise item
        return item


class FakeAnthropicClient:
    def __init__(self, script: ResponseScript) -> None:
        self.messages = FakeMessages(script)

    async def close(self) -> None:
        pass

    async def __aenter__(self) -> FakeAnthropicClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()
