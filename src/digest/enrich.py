"""Enriches each cluster's lead item with the full article text.

Fetches the lead's URL, respects robots.txt, and extracts the main text
with trafilatura, capped at a configured word count. Paper sources
(arXiv, Hugging Face Daily Papers) already carry the full abstract as their
teaser from the fetch stage and are left alone. Any failure along the way
-- robots.txt disallows us, the fetch times out or errors, or trafilatura
finds nothing to extract (e.g. a paywalled page with no visible body) --
leaves the cluster untouched, so the teaser is what M4 falls back to. We
never try to work around a disallow or a paywall.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
import trafilatura

from digest.fetch.common import USER_AGENT, make_client
from digest.models import Cluster
from digest.settings import EnrichSettings

PAPER_SOURCES = {"Hugging Face Daily Papers"}
MAX_CONCURRENT_ENRICH = 10
ROBOTS_TIMEOUT = 5.0


def is_paper_source(source_name: str) -> bool:
    return source_name.startswith("arXiv") or source_name in PAPER_SOURCES


def truncate_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words])


async def _robots_allow(client: httpx.AsyncClient, url: str) -> bool:
    parts = urlsplit(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        response = await client.get(robots_url, timeout=ROBOTS_TIMEOUT)
    except httpx.HTTPError:
        return True  # can't check -> fail open, a real fetch attempt will still 4xx if blocked

    if response.status_code >= 400:
        return True

    parser = RobotFileParser()
    parser.parse(response.text.splitlines())
    return parser.can_fetch(USER_AGENT, url)


async def enrich_cluster(
    client: httpx.AsyncClient, cluster: Cluster, settings: EnrichSettings
) -> Cluster:
    lead = cluster.lead
    if is_paper_source(lead.source):
        return cluster

    try:
        if not await _robots_allow(client, lead.url):
            return cluster
        response = await client.get(lead.url, timeout=settings.timeout_seconds)
        response.raise_for_status()
    except httpx.HTTPError:
        return cluster

    extracted = trafilatura.extract(response.text)
    if not extracted or not extracted.strip():
        return cluster

    lead.extracted_text = truncate_words(extracted, settings.max_words)
    return cluster


async def enrich_all(clusters: list[Cluster], settings: EnrichSettings) -> list[Cluster]:
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_ENRICH)

    async def _bounded(cluster: Cluster) -> Cluster:
        async with semaphore:
            return await enrich_cluster(client, cluster, settings)

    async with make_client() as client:
        return await asyncio.gather(*(_bounded(cluster) for cluster in clusters))
