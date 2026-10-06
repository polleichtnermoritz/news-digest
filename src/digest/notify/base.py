"""Notifier interface: one method, send a short text message.

Concrete implementations plug in behind this so the pipeline can notify
success or failure without caring which channel is behind it. Telegram is
the only implementation for now -- CallMeBot (the plan's original primary
channel) proved too unreliable during M0.5's spike test, which is why
Telegram became primary; it was never reliable enough to be worth adding
back as a fallback.
"""

from __future__ import annotations

from typing import Protocol


class Notifier(Protocol):
    async def send(self, text: str) -> None: ...
