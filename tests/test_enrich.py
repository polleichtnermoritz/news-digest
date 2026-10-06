from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest

from digest.enrich import enrich_cluster, is_paper_source, truncate_words
from digest.models import Cluster, Group, Item, Tier
from digest.settings import EnrichSettings

SETTINGS = EnrichSettings(max_words=2000, timeout_seconds=10)

ARTICLE_HTML = """
<html><body><article>
<p>{paragraph}</p>
</article></body></html>
"""

ALLOW_ALL_ROBOTS = "User-agent: *\nAllow: /\n"
DISALLOW_ROBOTS = "User-agent: *\nDisallow: /paywalled\n"


def make_item(**overrides: object) -> Item:
    defaults: dict[str, object] = dict(
        id="item",
        url="https://example.com/article",
        title="Some title",
        source="Example Source",
        group=Group.TECH,
        tier=Tier.JOURNALISM,
        language="en",
        published_at=datetime(2026, 10, 6, 8, 0, tzinfo=UTC),
        teaser="A short teaser.",
        weight=2,
    )
    defaults.update(overrides)
    return Item.model_validate(defaults)


def make_cluster(lead: Item) -> Cluster:
    return Cluster(id="cluster-1", group=lead.group, lead=lead, also_covered_by=[])


def client_with_handler(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_enrich_success_sets_extracted_text() -> None:
    long_paragraph = " ".join(["word"] * 50)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ALLOW_ALL_ROBOTS)
        return httpx.Response(200, text=ARTICLE_HTML.format(paragraph=long_paragraph))

    cluster = make_cluster(make_item())
    async with client_with_handler(handler) as client:
        result = await enrich_cluster(client, cluster, SETTINGS)

    assert result.lead.extracted_text is not None
    assert "word" in result.lead.extracted_text


async def test_enrich_truncates_to_max_words() -> None:
    long_paragraph = " ".join(["word"] * 50)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ALLOW_ALL_ROBOTS)
        return httpx.Response(200, text=ARTICLE_HTML.format(paragraph=long_paragraph))

    cluster = make_cluster(make_item())
    short_settings = EnrichSettings(max_words=5, timeout_seconds=10)
    async with client_with_handler(handler) as client:
        result = await enrich_cluster(client, cluster, short_settings)

    assert result.lead.extracted_text is not None
    assert len(result.lead.extracted_text.split()) == 5


async def test_enrich_respects_robots_disallow_and_never_fetches_the_page() -> None:
    requested_paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=DISALLOW_ROBOTS)
        return httpx.Response(200, text=ARTICLE_HTML.format(paragraph="should never be fetched"))

    lead = make_item(url="https://example.com/paywalled/article")
    cluster = make_cluster(lead)
    async with client_with_handler(handler) as client:
        result = await enrich_cluster(client, cluster, SETTINGS)

    assert result.lead.extracted_text is None
    assert "/paywalled/article" not in requested_paths


async def test_enrich_falls_back_to_teaser_on_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ALLOW_ALL_ROBOTS)
        raise httpx.ReadTimeout("simulated timeout")

    cluster = make_cluster(make_item())
    async with client_with_handler(handler) as client:
        result = await enrich_cluster(client, cluster, SETTINGS)

    assert result.lead.extracted_text is None
    assert result.lead.teaser == "A short teaser."


async def test_enrich_falls_back_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ALLOW_ALL_ROBOTS)
        return httpx.Response(404, text="not found")

    cluster = make_cluster(make_item())
    async with client_with_handler(handler) as client:
        result = await enrich_cluster(client, cluster, SETTINGS)

    assert result.lead.extracted_text is None


async def test_enrich_falls_back_when_extraction_is_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ALLOW_ALL_ROBOTS)
        return httpx.Response(200, text="<html><body></body></html>")

    cluster = make_cluster(make_item())
    async with client_with_handler(handler) as client:
        result = await enrich_cluster(client, cluster, SETTINGS)

    assert result.lead.extracted_text is None


async def test_enrich_skips_paper_sources_without_any_http_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"should not have fetched {request.url}")

    lead = make_item(
        source="arXiv cs.AI",
        group=Group.SCIENCE,
        tier=Tier.PRIMARY,
        teaser="The full abstract text.",
    )
    cluster = make_cluster(lead)
    async with client_with_handler(handler) as client:
        result = await enrich_cluster(client, cluster, SETTINGS)

    assert result.lead.extracted_text is None
    assert result.lead.teaser == "The full abstract text."


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("arXiv cs.AI", True),
        ("Hugging Face Daily Papers", True),
        ("ORF.at", False),
    ],
)
def test_is_paper_source(name: str, expected: bool) -> None:
    assert is_paper_source(name) is expected


def test_truncate_words_leaves_short_text_untouched() -> None:
    text = "one two three"
    assert truncate_words(text, max_words=10) == text


def test_truncate_words_cuts_to_the_limit() -> None:
    text = " ".join(str(i) for i in range(10))
    assert truncate_words(text, max_words=3) == "0 1 2"
