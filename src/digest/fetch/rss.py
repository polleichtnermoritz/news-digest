"""Generic RSS/Atom fetcher, used for every source with access: rss."""

from __future__ import annotations

import feedparser
import httpx

from digest.fetch.common import clean_teaser, entry_published_at, item_id
from digest.models import Item, Source


async def fetch_rss(client: httpx.AsyncClient, source: Source) -> list[Item]:
    response = await client.get(source.url)
    response.raise_for_status()

    parsed = feedparser.parse(response.content)
    items = []
    for entry in parsed.entries:
        link = entry.get("link")
        if not link:
            continue
        title = entry.get("title", "").strip()
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
