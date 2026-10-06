from datetime import UTC, datetime, timedelta
from pathlib import Path

from digest.llm import CostTracker
from digest.models import Cluster, Group, Item, ScoredCluster, Tier
from digest.rank import (
    RankedItem,
    RankingResponse,
    _select_kept,
    rank_clusters,
    write_scored_json,
)
from digest.settings import (
    ClusterSettings,
    EnrichSettings,
    Models,
    Settings,
    StateSettings,
    TopNPerGroup,
)
from tests.fakes import FakeAnthropicClient, FakeResponse

OLD_ENOUGH_TO_HAVE_NO_RECENCY_BONUS = datetime.now(UTC) - timedelta(hours=1000)


def make_settings(**top_n_overrides: int) -> Settings:
    top_n = dict(tech=8, geopolitics=8, science=5, laws_minimum=2)
    top_n.update(top_n_overrides)
    return Settings(
        delivery_hour_vienna=7,
        top_n_per_group=TopNPerGroup(**top_n),
        models=Models(ranking="claude-haiku-4-5", summarize="claude-haiku-4-5"),
        token_cap_per_run=1_000_000,
        enrich=EnrichSettings(max_words=2000, timeout_seconds=10),
        cluster=ClusterSettings(title_similarity_threshold=90),
        state=StateSettings(seen_retention_days=30),
    )


def make_cluster(
    cluster_id: str,
    group: Group = Group.GEOPOLITICS,
    tier: Tier = Tier.JOURNALISM,
    weight: int = 2,
    also_covered_by: int = 0,
) -> Cluster:
    lead = Item(
        id=f"item-{cluster_id}",
        url=f"https://example.com/{cluster_id}",
        title=f"Story {cluster_id}",
        source="Example Source",
        group=group,
        tier=tier,
        language="en",
        published_at=OLD_ENOUGH_TO_HAVE_NO_RECENCY_BONUS,
        teaser="A teaser.",
        weight=weight,
    )
    others = [
        Item(
            id=f"other-{cluster_id}-{i}",
            url=f"https://example.com/{cluster_id}/{i}",
            title=f"Story {cluster_id}",
            source=f"Other Source {i}",
            group=group,
            tier=tier,
            language="en",
            published_at=OLD_ENOUGH_TO_HAVE_NO_RECENCY_BONUS,
            teaser="A teaser.",
            weight=weight,
        )
        for i in range(also_covered_by)
    ]
    return Cluster(id=cluster_id, group=group, lead=lead, also_covered_by=others)


def ranking_response_for(clusters: list[Cluster], scores: dict[str, float]) -> FakeResponse:
    rankings = [
        RankedItem(index=i, score=scores[c.id], reason=f"reason for {c.id}")
        for i, c in enumerate(clusters)
    ]
    return FakeResponse(RankingResponse(rankings=rankings))


async def test_final_score_combines_llm_score_weight_and_coverage() -> None:
    clusters = [make_cluster("a", weight=3, also_covered_by=2)]
    client = FakeAnthropicClient([ranking_response_for(clusters, {"a": 8.0})])
    cost = CostTracker(token_cap=1_000_000)

    scored = await rank_clusters(clusters, make_settings(), client=client, cost=cost)

    assert len(scored) == 1
    sc = scored[0]
    # llm_score(8) * weight(3) + coverage_bonus(2 outlets * 0.5 = 1.0) + ~0 recency
    assert sc.final_score == 8.0 * 3 + 1.0
    assert sc.kept is True


async def test_max_tokens_scales_with_group_size() -> None:
    # Regression test: a fixed max_tokens truncated large groups' JSON
    # output mid-string in production (see commit message). The request's
    # max_tokens must grow with the number of clusters being ranked.
    clusters = [make_cluster(str(i)) for i in range(500)]
    client = FakeAnthropicClient([ranking_response_for(clusters, {c.id: 5.0 for c in clusters})])
    cost = CostTracker(token_cap=10_000_000)

    await rank_clusters(clusters, make_settings(), client=client, cost=cost)

    assert client.messages.calls[0]["max_tokens"] >= 500 * 40


async def test_top_n_selection_keeps_only_the_highest_scored() -> None:
    clusters = [make_cluster(str(i), weight=1) for i in range(5)]
    scores = {c.id: float(i) for i, c in enumerate(clusters)}  # 0,1,2,3,4
    client = FakeAnthropicClient([ranking_response_for(clusters, scores)])
    cost = CostTracker(token_cap=1_000_000)

    scored = await rank_clusters(
        clusters, make_settings(geopolitics=2, laws_minimum=0), client=client, cost=cost
    )

    kept_ids = {sc.cluster.id for sc in scored if sc.kept}
    assert kept_ids == {"3", "4"}


async def test_law_minimum_promotes_primary_tier_even_if_low_scored() -> None:
    # Two high-scoring journalism items and one low-scoring primary (law) item.
    journalism_a = make_cluster("journalism-a", tier=Tier.JOURNALISM, weight=1)
    journalism_b = make_cluster("journalism-b", tier=Tier.JOURNALISM, weight=1)
    law_item = make_cluster("law", tier=Tier.PRIMARY, weight=1)
    clusters = [journalism_a, journalism_b, law_item]
    scores = {"journalism-a": 9.0, "journalism-b": 8.0, "law": 1.0}
    client = FakeAnthropicClient([ranking_response_for(clusters, scores)])
    cost = CostTracker(token_cap=1_000_000)

    scored = await rank_clusters(
        clusters, make_settings(geopolitics=2, laws_minimum=1), client=client, cost=cost
    )

    kept_ids = {sc.cluster.id for sc in scored if sc.kept}
    assert "law" in kept_ids
    assert len(kept_ids) == 2
    # The lowest-scoring non-primary kept item is the one bumped out.
    assert "journalism-b" not in kept_ids
    assert "journalism-a" in kept_ids


async def test_falls_back_to_neutral_score_when_ranking_response_is_incomplete() -> None:
    clusters = [make_cluster("a"), make_cluster("b")]
    # Missing index 1 both times -> incomplete on every attempt.
    incomplete = FakeResponse(RankingResponse(rankings=[RankedItem(index=0, score=9, reason="x")]))
    client = FakeAnthropicClient([incomplete, incomplete])
    cost = CostTracker(token_cap=1_000_000)

    scored = await rank_clusters(clusters, make_settings(), client=client, cost=cost)

    assert all(sc.llm_score == 5.0 for sc in scored)
    assert all("fallback" in sc.reason for sc in scored)


def test_select_kept_with_no_law_minimum_is_plain_top_n() -> None:
    scored = [
        ScoredCluster(
            cluster=make_cluster(str(i)),
            llm_score=float(i),
            reason="r",
            final_score=float(i),
            kept=False,
        )
        for i in range(4)
    ]
    kept = _select_kept(scored, top_n=2, min_primary_tier=0)
    assert kept == {"2", "3"}


def test_write_scored_json_includes_both_kept_and_dropped(tmp_path: Path) -> None:
    scored = [
        ScoredCluster(
            cluster=make_cluster("kept"), llm_score=9.0, reason="good", final_score=9.0, kept=True
        ),
        ScoredCluster(
            cluster=make_cluster("dropped"),
            llm_score=1.0,
            reason="meh",
            final_score=1.0,
            kept=False,
        ),
    ]
    path = tmp_path / "scored.json"

    write_scored_json(scored, path)

    import json

    data = json.loads(path.read_text())
    assert len(data) == 2
    assert {d["kept"] for d in data} == {True, False}
