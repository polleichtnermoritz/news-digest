from datetime import UTC, date, datetime

from digest.llm import CostTracker
from digest.models import Cluster, Group, Item, LawStatus, ScoredCluster, Tier
from digest.settings import (
    ClusterSettings,
    EnrichSettings,
    Models,
    PodcastSettings,
    Settings,
    StateSettings,
    TopNPerGroup,
)
from digest.summarize import (
    LawSummaryOutput,
    SummaryOutput,
    generate_overviews,
    is_law_feed_item,
    summarize_clusters,
)
from tests.fakes import FakeAnthropicClient, FakeResponse


def make_settings() -> Settings:
    return Settings(
        delivery_hour_vienna=7,
        site_base_url="https://example.github.io/news-digest",
        top_n_per_group=TopNPerGroup(tech=8, geopolitics=8, science=5, laws_minimum=2),
        models=Models(ranking="claude-haiku-4-5", summarize="claude-haiku-4-5"),
        token_cap_per_run=1_000_000,
        enrich=EnrichSettings(max_words=2000, timeout_seconds=10),
        cluster=ClusterSettings(title_similarity_threshold=90),
        state=StateSettings(seen_retention_days=30),
        podcast=PodcastSettings(
            enabled=True, voice="en-US-GuyNeural", story_count=6, episode_retention_days=30
        ),
    )


def make_cluster(
    cluster_id: str = "a",
    group: Group = Group.TECH,
    tier: Tier = Tier.JOURNALISM,
    language: str = "en",
    extracted_text: str | None = None,
) -> Cluster:
    lead = Item(
        id=f"item-{cluster_id}",
        url=f"https://example.com/{cluster_id}",
        title=f"Story {cluster_id}",
        source="Example Source",
        group=group,
        tier=tier,
        language=language,
        published_at=datetime(2026, 10, 6, 8, 0, tzinfo=UTC),
        teaser="A short teaser.",
        extracted_text=extracted_text,
    )
    return Cluster(id=cluster_id, group=group, lead=lead, also_covered_by=[])


def test_is_law_feed_item_requires_primary_tier_and_geopolitics() -> None:
    assert is_law_feed_item(make_cluster(group=Group.GEOPOLITICS, tier=Tier.PRIMARY))
    assert not is_law_feed_item(make_cluster(group=Group.GEOPOLITICS, tier=Tier.JOURNALISM))
    assert not is_law_feed_item(make_cluster(group=Group.TECH, tier=Tier.PRIMARY))


async def test_summarize_cluster_uses_extracted_text_when_available() -> None:
    cluster = make_cluster(extracted_text="Full enriched article body.")
    response = FakeResponse(SummaryOutput(text="A summary.", why_it_matters="It matters."))
    client = FakeAnthropicClient([response])
    cost = CostTracker(token_cap=1_000_000)

    summaries = await summarize_clusters([cluster], make_settings(), client=client, cost=cost)

    assert summaries["a"].text == "A summary."
    assert summaries["a"].why_it_matters == "It matters."
    assert summaries["a"].law is None
    assert "Full enriched article body." in client.messages.calls[0]["messages"][0]["content"]


async def test_summarize_cluster_falls_back_to_teaser_when_not_enriched() -> None:
    cluster = make_cluster(extracted_text=None)
    response = FakeResponse(SummaryOutput(text="A summary.", why_it_matters="It matters."))
    client = FakeAnthropicClient([response])
    cost = CostTracker(token_cap=1_000_000)

    await summarize_clusters([cluster], make_settings(), client=client, cost=cost)

    assert "A short teaser." in client.messages.calls[0]["messages"][0]["content"]


async def test_summarize_requests_law_fields_for_primary_geopolitics() -> None:
    cluster = make_cluster(group=Group.GEOPOLITICS, tier=Tier.PRIMARY, language="de")
    law_output = LawSummaryOutput(
        text="Zusammenfassung.",
        why_it_matters="Warum es wichtig ist.",
        jurisdiction="Austria",
        status=LawStatus.PASSED,
        effective_date=date(2026, 11, 1),
    )
    client = FakeAnthropicClient([FakeResponse(law_output)])
    cost = CostTracker(token_cap=1_000_000)

    summaries = await summarize_clusters([cluster], make_settings(), client=client, cost=cost)

    summary = summaries["a"]
    assert summary.language == "de"
    assert summary.law is not None
    assert summary.law.jurisdiction == "Austria"
    assert summary.law.status == LawStatus.PASSED
    assert summary.law.effective_date == date(2026, 11, 1)


async def test_summarize_requests_response_in_the_lead_items_language() -> None:
    cluster = make_cluster(language="de")
    client = FakeAnthropicClient(
        [FakeResponse(SummaryOutput(text="Text.", why_it_matters="Wichtig."))]
    )
    cost = CostTracker(token_cap=1_000_000)

    await summarize_clusters([cluster], make_settings(), client=client, cost=cost)

    system_prompt = client.messages.calls[0]["system"]
    assert "German" in system_prompt


async def test_summarize_skips_cluster_when_model_returns_nothing_parseable() -> None:
    cluster = make_cluster()
    # Both attempts (default retries=1 inside call_structured) return None.
    client = FakeAnthropicClient([FakeResponse(None), FakeResponse(None)])
    cost = CostTracker(token_cap=1_000_000)

    summaries = await summarize_clusters([cluster], make_settings(), client=client, cost=cost)

    assert summaries == {}


async def test_summarize_clusters_stops_gracefully_when_budget_exceeded() -> None:
    cluster_a = make_cluster("a")
    cluster_b = make_cluster("b")
    cost = CostTracker(token_cap=100)
    cost.record("claude-haiku-4-5", 60, 60)  # already over budget before we start

    client = FakeAnthropicClient(
        [FakeResponse(SummaryOutput(text="unreachable", why_it_matters="unreachable"))]
    )

    summaries = await summarize_clusters(
        [cluster_a, cluster_b], make_settings(), client=client, cost=cost
    )

    assert summaries == {}
    assert client.messages.calls == []


async def test_generate_overviews_one_call_per_group_with_kept_clusters() -> None:
    cluster = make_cluster(group=Group.TECH)
    scored = ScoredCluster(cluster=cluster, llm_score=8.0, reason="r", final_score=8.0, kept=True)
    scored_by_group = {Group.TECH: [scored]}

    from digest.summarize import OverviewOutput

    overview = OverviewOutput(overview_de="Ein deutscher Absatz.")
    client = FakeAnthropicClient([FakeResponse(overview)])
    cost = CostTracker(token_cap=1_000_000)

    overviews = await generate_overviews(
        scored_by_group, summaries={}, settings=make_settings(), client=client, cost=cost
    )

    assert overviews[Group.TECH] == "Ein deutscher Absatz."
    assert len(client.messages.calls) == 1


async def test_generate_overviews_skips_empty_groups() -> None:
    client = FakeAnthropicClient([])
    cost = CostTracker(token_cap=1_000_000)

    overviews = await generate_overviews(
        {Group.TECH: []}, summaries={}, settings=make_settings(), client=client, cost=cost
    )

    assert overviews == {}
    assert client.messages.calls == []
