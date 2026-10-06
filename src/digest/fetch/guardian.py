"""Fetcher for the Guardian Open Platform API (access: api)."""

from __future__ import annotations

import os
from datetime import datetime

import httpx

from digest.fetch.common import clean_teaser, item_id
from digest.models import Item, Source

GUARDIAN_API_KEY_ENV = "GUARDIAN_API_KEY"


class MissingApiKey(Exception):
    pass


async def fetch_guardian(client: httpx.AsyncClient, source: Source) -> list[Item]:
    api_key = os.environ.get(GUARDIAN_API_KEY_ENV)
    if not api_key:
        raise MissingApiKey(f"{GUARDIAN_API_KEY_ENV} is not set")

    response = await client.get(
        source.url,
        params={
            "api-key": api_key,
            "order-by": "newest",
            "show-fields": "trailText",
            "page-size": 50,
        },
    )
    response.raise_for_status()

    results = response.json()["response"]["results"]
    items = []
    for result in results:
        url = result["webUrl"]
        title = result["webTitle"].strip()
        items.append(
            Item(
                id=item_id(url),
                url=url,
                title=title,
                source=source.name,
                group=source.group,
                tier=source.tier,
                language=source.language,
                published_at=datetime.fromisoformat(
                    result["webPublicationDate"].replace("Z", "+00:00")
                ),
                teaser=clean_teaser(result.get("fields", {}).get("trailText")) or title,
                weight=source.weight,
            )
        )
    return items


def guardian_configured() -> bool:
    return bool(os.environ.get(GUARDIAN_API_KEY_ENV))


__all__ = ["fetch_guardian", "guardian_configured", "MissingApiKey", "GUARDIAN_API_KEY_ENV"]
