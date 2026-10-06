from pathlib import Path

import httpx
import pytest

from digest.fetch.arxiv import fetch_arxiv, fetch_huggingface_daily_papers
from digest.fetch.guardian import MissingApiKey, fetch_guardian
from digest.fetch.pages import (
    fetch_anthropic_news,
    fetch_mistral_news,
    fetch_parlamentskorrespondenz,
    fetch_ris_bgbl,
)
from digest.fetch.rss import fetch_rss
from digest.models import Access, Group, Source, Tier

FIXTURES = Path(__file__).parent / "fixtures" / "feeds"


def make_source(**overrides: object) -> Source:
    defaults: dict[str, object] = dict(
        name="Test Source",
        group=Group.TECH,
        tier=Tier.JOURNALISM,
        language="en",
        weight=2,
        access=Access.RSS,
        url="https://example.com/feed",
        verified=True,
    )
    defaults.update(overrides)
    return Source.model_validate(defaults)


def client_for_fixture(path: Path, content_type: str = "application/xml") -> httpx.AsyncClient:
    body = path.read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": content_type})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_fetch_rss_parses_entries() -> None:
    source = make_source(name="ORF.at", group=Group.GEOPOLITICS, language="de")
    async with client_for_fixture(FIXTURES / "orf_sample.xml") as client:
        items = await fetch_rss(client, source)

    assert len(items) > 0
    assert all(item.source == "ORF.at" for item in items)
    assert all(item.teaser for item in items)


async def test_fetch_arxiv_uses_abstract_as_teaser() -> None:
    source = make_source(
        name="arXiv cs.AI", group=Group.SCIENCE, tier=Tier.PRIMARY, url="https://export.arxiv.org/api/query?search_query=cat:cs.AI"
    )
    async with client_for_fixture(FIXTURES / "arxiv_sample.xml") as client:
        items = await fetch_arxiv(client, source)

    assert len(items) > 0
    assert all(item.teaser for item in items)
    assert all("arxiv.org" in item.url for item in items)


async def test_fetch_huggingface_daily_papers() -> None:
    source = make_source(
        name="Hugging Face Daily Papers",
        group=Group.SCIENCE,
        tier=Tier.PRIMARY,
        access=Access.API,
        url="https://huggingface.co/api/daily_papers",
    )
    async with client_for_fixture(
        FIXTURES / "hf_daily_papers_sample.json", content_type="application/json"
    ) as client:
        items = await fetch_huggingface_daily_papers(client, source)

    assert len(items) == 2
    assert all(item.url.startswith("https://huggingface.co/papers/") for item in items)


async def test_fetch_anthropic_news() -> None:
    source = make_source(
        name="Anthropic",
        group=Group.SCIENCE,
        tier=Tier.PRIMARY,
        access=Access.PAGE,
        url="https://www.anthropic.com/news",
    )
    async with client_for_fixture(
        FIXTURES / "anthropic_news_sample.html", content_type="text/html"
    ) as client:
        items = await fetch_anthropic_news(client, source)

    assert len(items) == 2
    assert items[0].title == "Expanding the Cyber Verification Program"
    assert items[0].url == "https://www.anthropic.com/news/cyber-verification-program"
    assert items[0].published_at.year == 2026


async def test_fetch_mistral_news() -> None:
    source = make_source(
        name="Mistral",
        group=Group.SCIENCE,
        tier=Tier.PRIMARY,
        access=Access.PAGE,
        url="https://mistral.ai/news/",
    )
    async with client_for_fixture(
        FIXTURES / "mistral_news_sample.html", content_type="text/html"
    ) as client:
        items = await fetch_mistral_news(client, source)

    assert len(items) == 2
    assert items[0].title == (
        "Agentic Search. More accurate and efficient results from your AI systems."
    )
    assert items[0].url == "https://mistral.ai/news/agentic-search/"


async def test_fetch_ris_bgbl() -> None:
    source = make_source(
        name="RIS Bundesgesetzblatt",
        group=Group.GEOPOLITICS,
        tier=Tier.PRIMARY,
        language="de",
        access=Access.PAGE,
        url="https://www.ris.bka.gv.at/Ergebnis.wxe?Abfrage=BgblAuth",
    )
    async with client_for_fixture(
        FIXTURES / "ris_bgbl_sample.html", content_type="text/html"
    ) as client:
        items = await fetch_ris_bgbl(client, source)

    assert len(items) == 2
    assert items[0].title == "Universitätsbibliothekspersonal-Ausbildungsverordnung"
    assert items[0].published_at.isoformat().startswith("2026-10-02")
    assert items[1].title.startswith("Berichtigung")


async def test_fetch_parlamentskorrespondenz() -> None:
    source = make_source(
        name="Parlamentskorrespondenz",
        group=Group.GEOPOLITICS,
        tier=Tier.PRIMARY,
        language="de",
        access=Access.API,
        url="https://www.parlament.gv.at/Filter/api/filter/data/110?js=eval",
    )
    async with client_for_fixture(
        FIXTURES / "parlament_sample.json", content_type="application/json"
    ) as client:
        items = await fetch_parlamentskorrespondenz(client, source)

    assert len(items) == 3
    assert items[0].url.startswith("https://www.parlament.gv.at/")
    assert items[0].published_at.year == 2026


async def test_fetch_guardian_without_key_raises() -> None:
    source = make_source(
        name="The Guardian", access=Access.API, url="https://content.guardianapis.com/search"
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(MissingApiKey):
            await fetch_guardian(client, source)
