"""Telegram notifier: sends a message via the Telegram Bot API, using
TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (see config/settings.yaml note in
CLAUDE.md -- Telegram is primary, not CallMeBot, per the M0.5 decision).
"""

from __future__ import annotations

import os

import httpx

TELEGRAM_API_BASE = "https://api.telegram.org"
REQUEST_TIMEOUT = 15.0


class TelegramConfigError(Exception):
    pass


class TelegramNotifier:
    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        if not self.bot_token or not self.chat_id:
            raise TelegramConfigError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
        self._client = client  # injectable for tests; a real send() opens its own otherwise

    async def send(self, text: str) -> None:
        url = f"{TELEGRAM_API_BASE}/bot{self.bot_token}/sendMessage"
        data = {"chat_id": self.chat_id, "text": text}

        if self._client is not None:
            response = await self._client.post(url, data=data)
            response.raise_for_status()
            return

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(url, data=data)
            response.raise_for_status()
