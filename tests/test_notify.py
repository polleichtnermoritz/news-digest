from collections.abc import Callable
from datetime import date

import httpx
import pytest

from digest.models import Digest, GroupDigest
from digest.models import Group as GroupEnum
from digest.notify import format_error_message, format_success_message
from digest.notify.telegram import TelegramConfigError, TelegramNotifier


def test_format_success_message_matches_plan_format() -> None:
    # Build directly with real item counts since RenderedItem needs a full
    # Cluster -- construct via model_construct to skip validation, since
    # only len() matters to the formatter.
    digest = Digest(
        date=date(2026, 10, 6),
        groups=[
            GroupDigest.model_construct(
                group=GroupEnum.TECH, overview_de="", items=[object()] * 8, also_considered=[]
            ),
            GroupDigest.model_construct(
                group=GroupEnum.GEOPOLITICS,
                overview_de="",
                items=[object()] * 6,
                also_considered=[],
            ),
            GroupDigest.model_construct(
                group=GroupEnum.SCIENCE, overview_de="", items=[object()] * 5, also_considered=[]
            ),
        ],
    )

    page_url = "https://example.github.io/news-digest/digest/2026-10-06.html"
    message = format_success_message(digest, page_url)

    assert "06.10.2026" in message
    assert "8 Tech" in message
    assert "6 Geo" in message
    assert "5 Papers" in message
    assert page_url in message


def test_format_error_message_is_short_and_labeled() -> None:
    message = format_error_message("ConnectionError: boom")
    assert message.startswith("⚠️")
    assert "ConnectionError: boom" in message


def test_telegram_notifier_requires_config() -> None:
    with pytest.raises(TelegramConfigError):
        TelegramNotifier(bot_token=None, chat_id=None)


def client_with_handler(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_telegram_notifier_sends_via_bot_api() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.read().decode()
        return httpx.Response(200, json={"ok": True})

    async with client_with_handler(handler) as http_client:
        notifier = TelegramNotifier(bot_token="TESTTOKEN", chat_id="12345", client=http_client)
        await notifier.send("hello world")

    assert "bot TESTTOKEN".replace(" ", "") in captured["url"].replace("%20", "")
    assert "chat_id=12345" in captured["body"]
    assert "hello" in captured["body"]


async def test_telegram_notifier_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})

    async with client_with_handler(handler) as http_client:
        notifier = TelegramNotifier(bot_token="BADTOKEN", chat_id="12345", client=http_client)
        with pytest.raises(httpx.HTTPStatusError):
            await notifier.send("hello")
