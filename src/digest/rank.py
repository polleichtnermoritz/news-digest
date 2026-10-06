"""Scores every cluster for relevance/importance in one cheap LLM pass per
topic group, computes a final_score (LLM score x source weight + recency +
coverage bonus), and selects the top N per group -- guaranteeing a minimum
number of law/primary-source slots in geopolitics. Every scored cluster,
kept or dropped, is returned (and written to out/scored.json by the caller)
so ranking can be audited.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import anthropic
from pydantic import BaseModel, Field

from digest.llm import CostTracker, call_structured, make_client
from digest.models import Cluster, Group, ScoredCluster, Tier
from digest.settings import PROJECT_ROOT, Settings
from digest.topics import TopicConfig, load_topics

DEFAULT_SCORED_PATH = PROJECT_ROOT / "out" / "scored.json"

RECENCY_WINDOW_HOURS = 36.0
RECENCY_MAX_BONUS = 2.0
COVERAGE_BONUS_PER_OUTLET = 0.5
COVERAGE_BONUS_MAX = 2.0

MAX_TEASER_CHARS_IN_PROMPT = 200
RANK_MAX_ATTEMPTS = 2


class RankedItem(BaseModel):
    index: int
    score: float = Field(ge=0, le=10)
    reason: str


class RankingResponse(BaseModel):
    rankings: list[RankedItem]


def _recency_bonus(published_at: datetime, now: datetime) -> float:
    hours_old = max(0.0, (now - published_at).total_seconds() / 3600)
    fraction_fresh = max(0.0, (RECENCY_WINDOW_HOURS - hours_old) / RECENCY_WINDOW_HOURS)
    return fraction_fresh * RECENCY_MAX_BONUS


def _coverage_bonus(cluster: Cluster) -> float:
    return min(COVERAGE_BONUS_MAX, len(cluster.also_covered_by) * COVERAGE_BONUS_PER_OUTLET)


def _build_prompt(clusters: list[Cluster], topic: TopicConfig, now: datetime) -> str:
    lines = [
        f"Topic: {topic.description}",
        f"Keywords of interest: {', '.join(topic.keywords)}",
        "",
        "Score each numbered story 0-10 for relevance to the topic above and",
        "real-world importance. Give a one-line reason for each. Every index",
        "below must appear exactly once in your response.",
        "",
    ]
    for i, cluster in enumerate(clusters):
        lead = cluster.lead
        hours_old = max(0.0, (now - lead.published_at).total_seconds() / 3600)
        teaser = lead.teaser[:MAX_TEASER_CHARS_IN_PROMPT]
        lines.append(
            f"[{i}] (tier={lead.tier.value}, {hours_old:.0f}h old, "
            f"{len(cluster.also_covered_by)} other outlets) {lead.title} -- {teaser}"
        )
    return "\n".join(lines)


def _is_complete(response: RankingResponse, n: int) -> bool:
    indices = {item.index for item in response.rankings}
    return indices == set(range(n))


async def _rank_group(
    client: anthropic.AsyncAnthropic,
    cost: CostTracker,
    clusters: list[Cluster],
    topic: TopicConfig,
    settings: Settings,
) -> dict[str, tuple[float, str]]:
    """Returns cluster.id -> (llm_score, reason). Falls back to a neutral
    score for every cluster if the model never returns a complete ranking."""
    now = datetime.now(UTC)
    prompt = _build_prompt(clusters, topic, now)

    for _ in range(RANK_MAX_ATTEMPTS):
        response = await call_structured(
            client,
            cost,
            model=settings.models.ranking,
            system="You rank news and research items for a daily digest. Base scores only "
            "on the text given; never invent facts not present in the titles/teasers.",
            user_content=prompt,
            output_format=RankingResponse,
            retries=0,
        )
        if response is not None and _is_complete(response, len(clusters)):
            by_index = {item.index: item for item in response.rankings}
            return {
                clusters[i].id: (by_index[i].score, by_index[i].reason)
                for i in range(len(clusters))
            }

    return {
        cluster.id: (5.0, "ranking unavailable, neutral fallback score") for cluster in clusters
    }


def _select_kept(
    scored: list[ScoredCluster], top_n: int, min_primary_tier: int = 0
) -> set[str]:
    ordered = sorted(scored, key=lambda s: s.final_score, reverse=True)
    kept_ids = {s.cluster.id for s in ordered[:top_n]}

    if min_primary_tier <= 0:
        return kept_ids

    kept_primary_count = sum(
        1 for s in ordered[:top_n] if s.cluster.lead.tier == Tier.PRIMARY
    )
    shortfall = min_primary_tier - kept_primary_count
    if shortfall <= 0:
        return kept_ids

    promotable = [
        s for s in ordered[top_n:] if s.cluster.lead.tier == Tier.PRIMARY
    ]
    droppable = sorted(
        (s for s in ordered[:top_n] if s.cluster.lead.tier != Tier.PRIMARY),
        key=lambda s: s.final_score,
    )
    for candidate in promotable[:shortfall]:
        if not droppable:
            break
        kept_ids.discard(droppable.pop(0).cluster.id)
        kept_ids.add(candidate.cluster.id)

    return kept_ids


_TOP_N_BY_GROUP_ATTR = {
    Group.TECH: "tech",
    Group.GEOPOLITICS: "geopolitics",
    Group.SCIENCE: "science",
}


async def rank_clusters(
    clusters: list[Cluster],
    settings: Settings,
    client: anthropic.AsyncAnthropic | None = None,
    cost: CostTracker | None = None,
) -> list[ScoredCluster]:
    own_client = client is None
    client = client or make_client()
    cost = cost or CostTracker(token_cap=settings.token_cap_per_run)
    topics = load_topics()
    now = datetime.now(UTC)

    try:
        all_scored: list[ScoredCluster] = []
        for group in Group:
            group_clusters = [c for c in clusters if c.group == group]
            if not group_clusters:
                continue

            scores = await _rank_group(client, cost, group_clusters, topics[group], settings)

            scored_group = []
            for cluster in group_clusters:
                llm_score, reason = scores[cluster.id]
                final_score = (
                    llm_score * cluster.lead.weight
                    + _recency_bonus(cluster.lead.published_at, now)
                    + _coverage_bonus(cluster)
                )
                scored_group.append(
                    ScoredCluster(
                        cluster=cluster,
                        llm_score=llm_score,
                        reason=reason,
                        final_score=final_score,
                        kept=False,
                    )
                )

            top_n = getattr(settings.top_n_per_group, _TOP_N_BY_GROUP_ATTR[group])
            min_primary = settings.top_n_per_group.laws_minimum if group == Group.GEOPOLITICS else 0
            kept_ids = _select_kept(scored_group, top_n, min_primary)
            for sc in scored_group:
                sc.kept = sc.cluster.id in kept_ids

            all_scored.extend(scored_group)
    finally:
        if own_client:
            await client.close()

    return all_scored


def write_scored_json(scored: list[ScoredCluster], path: Path = DEFAULT_SCORED_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([sc.model_dump(mode="json") for sc in scored], indent=2))
