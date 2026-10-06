"""Shared helpers for all fetchers: HTTP client config and the per-source result type."""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from digest.models import Item

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")

MAX_TEASER_CHARS = 500

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0 Safari/537.36 news-digest-fetcher/0.1"
)
REQUEST_TIMEOUT = 15.0


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
    )


def item_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def clean_teaser(raw: str | None) -> str:
    if not raw:
        return ""
    text = _WHITESPACE_RE.sub(" ", _TAG_RE.sub(" ", raw)).strip()
    return text[:MAX_TEASER_CHARS]


def struct_time_to_datetime(struct: time.struct_time | None) -> datetime | None:
    if struct is None:
        return None
    return datetime(*struct[:6], tzinfo=UTC)


def entry_published_at(entry: dict[str, Any]) -> datetime:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    return struct_time_to_datetime(parsed) or datetime.now(UTC)


@dataclass
class SourceFetchResult:
    source_name: str
    items: list[Item] = field(default_factory=list)
    error: str | None = None
    skipped: bool = False
