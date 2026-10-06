"""Summarizes each kept cluster (2-4 sentences + "why it matters") in the
lead item's original language, and writes one short German overview
paragraph per group connecting that group's kept stories. Law-feed items
(primary-tier geopolitics sources) also get jurisdiction/status/effective
date extracted. Every call is grounded only in the fetched text -- the
system prompt explicitly forbids speculation -- and a BudgetExceeded stops
summarization early without raising, so already-kept clusters without a
Summary still appear in the digest headline-only, per the plan's guardrail.
"""

from __future__ import annotations

from datetime import date

import anthropic
from pydantic import BaseModel

from digest.llm import BudgetExceeded, CostTracker, call_structured, make_client
from digest.models import Cluster, Group, LawFields, LawStatus, ScoredCluster, Summary, Tier
from digest.settings import Settings

LANGUAGE_NAMES = {"de": "German", "en": "English"}

MAX_TEXT_CHARS_IN_PROMPT = 12_000  # ~2000 words of extracted text, generously bounded


class SummaryOutput(BaseModel):
    text: str
    why_it_matters: str


class LawSummaryOutput(BaseModel):
    text: str
    why_it_matters: str
    jurisdiction: str
    status: LawStatus
    effective_date: date | None = None


class OverviewOutput(BaseModel):
    overview_de: str


def is_law_feed_item(cluster: Cluster) -> bool:
    return cluster.lead.tier == Tier.PRIMARY and cluster.lead.group == Group.GEOPOLITICS


def _language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code)


def _article_text(cluster: Cluster) -> str:
    lead = cluster.lead
    text = lead.extracted_text or lead.teaser
    return text[:MAX_TEXT_CHARS_IN_PROMPT]


def _summary_prompt(cluster: Cluster) -> str:
    lead = cluster.lead
    other_sources = ", ".join(item.source for item in cluster.also_covered_by)
    lines = [f"Title: {lead.title}", f"Source text:\n{_article_text(cluster)}"]
    if other_sources:
        lines.append(f"Also covered by: {other_sources}")
    return "\n\n".join(lines)


async def summarize_cluster(
    client: anthropic.AsyncAnthropic, cost: CostTracker, cluster: Cluster, settings: Settings
) -> Summary | None:
    lead = cluster.lead
    language = _language_name(lead.language)
    base_system = (
        "You write concise summaries for a personal news digest. Use only the "
        "text given below -- never speculate or add outside knowledge. If "
        f"something is unclear from the source, say so explicitly in {language} "
        f"rather than guessing. Write your response in {language}."
    )

    if is_law_feed_item(cluster):
        system = (
            base_system
            + " This item is from a primary legal/parliamentary source: also "
            "extract the jurisdiction (e.g. 'Austria' or 'European Union'), its "
            "status, and an effective date only if one is explicitly stated."
        )
        law_output = await call_structured(
            client,
            cost,
            model=settings.models.summarize,
            system=system,
            user_content=_summary_prompt(cluster),
            output_format=LawSummaryOutput,
        )
        if law_output is None:
            return None
        return Summary(
            cluster_id=cluster.id,
            text=law_output.text,
            why_it_matters=law_output.why_it_matters,
            language=lead.language,
            law=LawFields(
                jurisdiction=law_output.jurisdiction,
                status=law_output.status,
                effective_date=law_output.effective_date,
            ),
        )

    output = await call_structured(
        client,
        cost,
        model=settings.models.summarize,
        system=base_system,
        user_content=_summary_prompt(cluster),
        output_format=SummaryOutput,
    )
    if output is None:
        return None
    return Summary(
        cluster_id=cluster.id,
        text=output.text,
        why_it_matters=output.why_it_matters,
        language=lead.language,
    )


async def summarize_clusters(
    clusters: list[Cluster],
    settings: Settings,
    client: anthropic.AsyncAnthropic | None = None,
    cost: CostTracker | None = None,
) -> dict[str, Summary]:
    own_client = client is None
    client = client or make_client()
    cost = cost or CostTracker(token_cap=settings.token_cap_per_run)

    summaries: dict[str, Summary] = {}
    try:
        for cluster in clusters:
            try:
                summary = await summarize_cluster(client, cost, cluster, settings)
            except BudgetExceeded:
                break
            if summary is not None:
                summaries[cluster.id] = summary
    finally:
        if own_client:
            await client.close()

    return summaries


def _overview_prompt(
    group: Group, scored: list[ScoredCluster], summaries: dict[str, Summary]
) -> str:
    lines = [f"Group: {group.value}", "Today's kept stories:"]
    for sc in scored:
        summary = summaries.get(sc.cluster.id)
        headline = summary.text if summary else sc.cluster.lead.teaser
        lines.append(f"- {sc.cluster.lead.title}: {headline}")
    return "\n".join(lines)


async def generate_group_overview(
    client: anthropic.AsyncAnthropic,
    cost: CostTracker,
    group: Group,
    scored: list[ScoredCluster],
    summaries: dict[str, Summary],
    settings: Settings,
) -> str | None:
    if not scored:
        return None
    output = await call_structured(
        client,
        cost,
        model=settings.models.summarize,
        system=(
            "You write a short German-language overview paragraph connecting "
            "today's top stories in one topic group of a news digest. Use only "
            "the headlines and summaries given below -- never speculate."
        ),
        user_content=_overview_prompt(group, scored, summaries),
        output_format=OverviewOutput,
    )
    return output.overview_de if output else None


async def generate_overviews(
    scored_by_group: dict[Group, list[ScoredCluster]],
    summaries: dict[str, Summary],
    settings: Settings,
    client: anthropic.AsyncAnthropic | None = None,
    cost: CostTracker | None = None,
) -> dict[Group, str]:
    own_client = client is None
    client = client or make_client()
    cost = cost or CostTracker(token_cap=settings.token_cap_per_run)

    overviews: dict[Group, str] = {}
    try:
        for group, scored in scored_by_group.items():
            try:
                overview = await generate_group_overview(
                    client, cost, group, scored, summaries, settings
                )
            except BudgetExceeded:
                break
            if overview is not None:
                overviews[group] = overview
    finally:
        if own_client:
            await client.close()

    return overviews
