"""Site-specific scrapers for sources with no usable feed (access: page).

Each of these targets one site's current HTML structure and will need
updating if that site redesigns. Only sources confirmed reachable without a
headless browser are implemented here — see config/sources.yaml for ones
still blocked by bot-protection or client-side rendering.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from lxml import html

from digest.fetch.common import clean_teaser, item_id
from digest.models import Item, Source

ANTHROPIC_BASE_URL = "https://www.anthropic.com"
MISTRAL_BASE_URL = "https://mistral.ai"
RIS_BASE_URL = "https://www.ris.bka.gv.at"

# Column indices into each row of the Parlament filter API response (see
# fetch_parlamentskorrespondenz). The API returns rows as plain positional
# arrays, not objects, and its own header metadata has duplicate/None
# feld_name entries that can't disambiguate these columns by name — so we
# pin them by position instead, confirmed against a live response on
# 2026-10-06.
PK_COLUMN_TITLE = 6
PK_COLUMN_SUBTITLE = 7
PK_COLUMN_ISO_DATE = 11
PK_COLUMN_URL = 14


def _text(element: Any) -> str:
    # lxml-stubs types cssselect() results as generic _Element, which lacks
    # text_content(); the runtime objects are always HtmlElement.
    return str(element.text_content())


async def fetch_anthropic_news(client: httpx.AsyncClient, source: Source) -> list[Item]:
    response = await client.get(source.url)
    response.raise_for_status()
    doc = html.fromstring(response.text)

    items = []
    seen_urls: set[str] = set()
    for anchor in doc.cssselect('a[href^="/news/"]'):
        href = anchor.get("href")
        if not href or href == "/news" or href in seen_urls:
            continue
        seen_urls.add(href)

        spans = anchor.cssselect("span")
        title = _text(spans[-1]).strip() if spans else ""
        if not title:
            continue

        time_els = anchor.cssselect("time")
        published_at = _parse_month_day_year(_text(time_els[0]).strip()) if time_els else None

        items.append(
            Item(
                id=item_id(ANTHROPIC_BASE_URL + href),
                url=ANTHROPIC_BASE_URL + href,
                title=title,
                source=source.name,
                group=source.group,
                tier=source.tier,
                language=source.language,
                published_at=published_at or datetime.now(UTC),
                teaser=clean_teaser(title),
                weight=source.weight,
            )
        )
    return items


async def fetch_mistral_news(client: httpx.AsyncClient, source: Source) -> list[Item]:
    response = await client.get(source.url)
    response.raise_for_status()
    doc = html.fromstring(response.text)

    items = []
    seen_urls: set[str] = set()
    for article in doc.cssselect("article[data-title]"):
        anchors = article.cssselect('a[href^="/news/"]')
        if not anchors:
            continue
        href = anchors[0].get("href")
        if not href or href in seen_urls or href.endswith("/news/") or "?" in href:
            continue
        seen_urls.add(href)

        title_els = article.cssselect('[class*="post-title"]')
        title = _text(title_els[0]).strip() if title_els else article.get("data-title", "")
        if not title:
            continue

        items.append(
            Item(
                id=item_id(MISTRAL_BASE_URL + href),
                url=MISTRAL_BASE_URL + href,
                title=title,
                source=source.name,
                group=source.group,
                tier=source.tier,
                language=source.language,
                # The listing page carries no visible publish date; using
                # fetch time is an approximation until M3 enrich visits the
                # article itself.
                published_at=datetime.now(UTC),
                teaser=clean_teaser(title),
                weight=source.weight,
            )
        )
    return items


async def fetch_ris_bgbl(client: httpx.AsyncClient, source: Source) -> list[Item]:
    response = await client.get(source.url)
    response.raise_for_status()
    doc = html.fromstring(response.text)

    items = []
    for row in doc.cssselect("tr.bocListDataRow"):
        eli_links = row.cssselect('a[href^="/eli/"]')
        if not eli_links:
            continue
        link = eli_links[0]
        href = (link.get("href") or "").split("?")[0]
        if not href:
            continue
        title = link.get("title") or _text(link).strip()

        date_els = row.cssselect(".bocListCommandText")
        published_at = _parse_dotted_date(_text(date_els[0]).strip()) if date_els else None

        items.append(
            Item(
                id=item_id(RIS_BASE_URL + href),
                url=RIS_BASE_URL + href,
                title=title,
                source=source.name,
                group=source.group,
                tier=source.tier,
                language=source.language,
                published_at=published_at or datetime.now(UTC),
                teaser=clean_teaser(title),
                weight=source.weight,
            )
        )
    return items


async def fetch_parlamentskorrespondenz(client: httpx.AsyncClient, source: Source) -> list[Item]:
    response = await client.get(source.url)
    response.raise_for_status()
    payload = response.json()

    items = []
    for row in payload.get("rows", []):
        if len(row) <= PK_COLUMN_URL:
            continue
        title = row[PK_COLUMN_TITLE].strip()
        url = row[PK_COLUMN_URL]
        if not title or not url:
            continue

        try:
            published_at = datetime.fromisoformat(row[PK_COLUMN_ISO_DATE]).replace(tzinfo=UTC)
        except ValueError:
            published_at = datetime.now(UTC)

        subtitle = row[PK_COLUMN_SUBTITLE].strip() if row[PK_COLUMN_SUBTITLE] else title

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
                teaser=clean_teaser(subtitle),
                weight=source.weight,
            )
        )
    return items


def _parse_month_day_year(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, "%b %d, %Y").replace(tzinfo=UTC)
    except ValueError:
        return None


def _parse_dotted_date(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, "%d.%m.%Y").replace(tzinfo=UTC)
    except ValueError:
        return None
