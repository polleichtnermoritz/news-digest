"""Fetches every verified source concurrently; one failing source never kills the run."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import httpx

from digest.fetch.arxiv import fetch_arxiv, fetch_huggingface_daily_papers
from digest.fetch.common import SourceFetchResult, make_client
from digest.fetch.guardian import MissingApiKey, fetch_guardian
from digest.fetch.pages import (
    fetch_anthropic_news,
    fetch_mistral_news,
    fetch_parlamentskorrespondenz,
    fetch_ris_bgbl,
)
from digest.fetch.rss import fetch_rss
from digest.models import Access, Item, Source

Fetcher = Callable[[httpx.AsyncClient, Source], Awaitable[list[Item]]]

# Sources that need bespoke handling, keyed by their exact name in sources.yaml.
# Everything else falls back to its declared `access` type.
_FETCHER_BY_NAME: dict[str, Fetcher] = {
    "The Guardian": fetch_guardian,
    "Hugging Face Daily Papers": fetch_huggingface_daily_papers,
    "arXiv cs.AI": fetch_arxiv,
    "arXiv cs.LG": fetch_arxiv,
    "arXiv cs.CL": fetch_arxiv,
    "arXiv cs.SE": fetch_arxiv,
    "Anthropic": fetch_anthropic_news,
    "Mistral": fetch_mistral_news,
    "RIS Bundesgesetzblatt": fetch_ris_bgbl,
    "Parlamentskorrespondenz": fetch_parlamentskorrespondenz,
}

_FETCHER_BY_ACCESS: dict[Access, Fetcher] = {
    Access.RSS: fetch_rss,
}

MAX_CONCURRENT_FETCHES = 10
PER_SOURCE_TIMEOUT = 30.0


def _resolve_fetcher(source: Source) -> Fetcher | None:
    return _FETCHER_BY_NAME.get(source.name) or _FETCHER_BY_ACCESS.get(source.access)


async def _fetch_one(
    client: httpx.AsyncClient, source: Source, semaphore: asyncio.Semaphore
) -> SourceFetchResult:
    if not source.verified:
        return SourceFetchResult(source.name, skipped=True, error="not verified, skipped")

    fetcher = _resolve_fetcher(source)
    if fetcher is None:
        return SourceFetchResult(
            source.name, skipped=True, error=f"no fetcher implemented for access={source.access}"
        )

    async with semaphore:
        try:
            items = await asyncio.wait_for(fetcher(client, source), timeout=PER_SOURCE_TIMEOUT)
        except MissingApiKey as exc:
            return SourceFetchResult(source.name, skipped=True, error=str(exc))
        except TimeoutError:
            return SourceFetchResult(source.name, error=f"timed out after {PER_SOURCE_TIMEOUT}s")
        except httpx.HTTPError as exc:
            return SourceFetchResult(source.name, error=f"HTTP error: {exc!r}")
        except Exception as exc:  # noqa: BLE001 - one bad source must not kill the run
            return SourceFetchResult(source.name, error=f"unexpected error: {exc!r}")

    return SourceFetchResult(source.name, items=items)


async def fetch_all(sources: list[Source]) -> list[SourceFetchResult]:
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)
    async with make_client() as client:
        return await asyncio.gather(
            *(_fetch_one(client, source, semaphore) for source in sources)
        )


__all__ = ["fetch_all", "SourceFetchResult"]
