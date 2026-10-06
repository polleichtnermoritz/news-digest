from __future__ import annotations

from digest.models import Digest, Group
from digest.notify.base import Notifier
from digest.notify.telegram import TelegramConfigError, TelegramNotifier

GROUP_SHORT_LABELS = {Group.TECH: "Tech", Group.GEOPOLITICS: "Geo", Group.SCIENCE: "Papers"}


def format_success_message(digest: Digest, page_url: str) -> str:
    counts = " · ".join(
        f"{len(group.items)} {GROUP_SHORT_LABELS[group.group]}" for group in digest.groups
    )
    date_str = digest.date.strftime("%d.%m.%Y")
    return f"\U0001f4f0 Digest {date_str} — {counts}\n{page_url}"


def format_error_message(error: str) -> str:
    return f"⚠️ news-digest failed: {error}"


__all__ = [
    "Notifier",
    "TelegramNotifier",
    "TelegramConfigError",
    "format_success_message",
    "format_error_message",
]
