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


class FakeStream:
    def __init__(self, response_or_exc: FakeResponse | Exception) -> None:
        self._response_or_exc = response_or_exc

    async def get_final_message(self) -> FakeResponse:
        if isinstance(self._response_or_exc, Exception):
            raise self._response_or_exc
        return self._response_or_exc


class FakeStreamManager:
    def __init__(self, response_or_exc: FakeResponse | Exception) -> None:
        self._stream = FakeStream(response_or_exc)

    async def __aenter__(self) -> FakeStream:
        return self._stream

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class FakeMessages:
    def __init__(self, script: ResponseScript) -> None:
        self.script = script
        self.calls: list[dict[str, Any]] = []

    def stream(self, **kwargs: Any) -> FakeStreamManager:
        index = len(self.calls)
        self.calls.append(kwargs)
        item = self.script(index, kwargs) if callable(self.script) else self.script[index]
        return FakeStreamManager(item)


class FakeAnthropicClient:
    def __init__(self, script: ResponseScript) -> None:
        self.messages = FakeMessages(script)

    async def close(self) -> None:
        pass

    async def __aenter__(self) -> FakeAnthropicClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()
