"""Fetchers for the two paper sources: arXiv (Atom API) and Hugging Face Daily Papers (JSON API)."""

from __future__ import annotations

from datetime import UTC, datetime

import feedparser
import httpx

from digest.fetch.common import clean_teaser, entry_published_at, item_id
from digest.models import Item, Source

ARXIV_QUERY_SUFFIX = "&sortBy=submittedDate&sortOrder=descending&max_results=50"


async def fetch_arxiv(client: httpx.AsyncClient, source: Source) -> list[Item]:
    response = await client.get(source.url + ARXIV_QUERY_SUFFIX)
    response.raise_for_status()

    parsed = feedparser.parse(response.content)
    items = []
    for entry in parsed.entries:
        link = entry.get("id") or entry.get("link")
        if not link:
            continue
        title = " ".join(entry.get("title", "").split())
        items.append(
            Item(
                id=item_id(link),
                url=link,
                title=title,
                source=source.name,
                group=source.group,
                tier=source.tier,
                language=source.language,
                published_at=entry_published_at(entry),
                teaser=clean_teaser(entry.get("summary")) or clean_teaser(title),
                weight=source.weight,
            )
        )
    return items


async def fetch_huggingface_daily_papers(client: httpx.AsyncClient, source: Source) -> list[Item]:
    response = await client.get(source.url)
    response.raise_for_status()

    items = []
    for entry in response.json():
        paper = entry.get("paper", {})
        paper_id = paper.get("id")
        if not paper_id:
            continue
        url = f"https://huggingface.co/papers/{paper_id}"
        published_raw = entry.get("publishedAt") or paper.get("publishedAt")
        published_at = (
            datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
            if published_raw
            else datetime.now(UTC)
        )
        title = entry.get("title", paper.get("title", "")).strip()
        items.append(
            Item(
                id=item_id(url),
                url=url,
                title=title,
                source=source.name,
                group=source.group,
                tier=source.tier,
                language=source.language,
                published_at=published_at,
                teaser=clean_teaser(entry.get("summary") or paper.get("summary")) or title,
                weight=source.weight,
            )
        )
    return items
