"""CLI entry point for the daily digest pipeline."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter, defaultdict
from pathlib import Path

from digest.cluster import cluster_items
from digest.enrich import enrich_all, is_paper_source
from digest.fetch import fetch_all
from digest.fetch.common import SourceFetchResult
from digest.llm import CostTracker
from digest.llm import make_client as make_llm_client
from digest.models import Cluster, Group, Item, ScoredCluster, Summary
from digest.prefilter import load_filters, prefilter
from digest.rank import rank_clusters, write_scored_json
from digest.settings import load_settings
from digest.sources import load_sources
from digest.state import load_seen, unseen_clusters
from digest.summarize import generate_overviews, summarize_clusters

ENRICH_SAMPLE_PER_GROUP = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="digest", description="Daily news digest pipeline")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch and pre-filter every source and report counts, without calling any LLM",
    )
    parser.add_argument(
        "--stage",
        choices=["fetch", "prefilter", "cluster", "enrich", "rank", "summarize", "render"],
        help="run a single pipeline stage in isolation",
    )
    parser.add_argument("--date", help="run as if today were this ISO date (YYYY-MM-DD)")
    parser.add_argument("--from-fixture", help="replay a recorded fixture instead of fetching live")
    return parser


def _print_fetch_report(results: list[SourceFetchResult]) -> None:
    ok = [r for r in results if not r.skipped and r.error is None]
    skipped = [r for r in results if r.skipped]
    failed = [r for r in results if not r.skipped and r.error is not None]

    print(f"\nFetched {len(ok)} sources ok, {len(skipped)} skipped, {len(failed)} failed:\n")
    for r in ok:
        print(f"  {r.source_name}: {len(r.items)} items")
    for r in skipped:
        print(f"  {r.source_name}: skipped ({r.error})")
    for r in failed:
        print(f"  {r.source_name}: FAILED ({r.error})")


def _print_prefilter_report(before: list[Item], after: list[Item]) -> None:
    before_counts = Counter(item.source for item in before)
    after_counts = Counter(item.source for item in after)

    print(f"\nPre-filter: {len(before)} -> {len(after)} items\n")
    for source_name in sorted(before_counts):
        b, a = before_counts[source_name], after_counts.get(source_name, 0)
        marker = f" ({b - a} dropped)" if b != a else ""
        print(f"  {source_name}: {b} -> {a}{marker}")


def _print_cluster_report(
    clusters: list[Cluster], seen_count: int, new_clusters: list[Cluster]
) -> None:
    by_group = Counter(cluster.group for cluster in clusters)
    print(f"\nClustering: {len(clusters)} clusters ({dict(by_group)})\n")

    multi_source = [c for c in clusters if c.also_covered_by]
    print(f"  {len(multi_source)} clusters have more than one outlet")
    for c in multi_source[:5]:
        outlets = [c.lead.source, *(i.source for i in c.also_covered_by)]
        print(f"    \"{c.lead.title[:70]}\" <- {outlets}")

    print(
        f"\nState (state/seen.json, read-only in --dry-run): "
        f"{seen_count} previously-sent cluster ids loaded, "
        f"{len(new_clusters)} of today's {len(clusters)} clusters are new"
    )


def _sample_for_enrich(clusters: list[Cluster], per_group: int) -> list[Cluster]:
    sample = []
    counts: dict[Group, int] = {}
    for cluster in clusters:
        if is_paper_source(cluster.lead.source):
            continue  # already has the full abstract as teaser, nothing to demo
        count = counts.get(cluster.group, 0)
        if count >= per_group:
            continue
        counts[cluster.group] = count + 1
        sample.append(cluster)
    return sample


def _print_enrich_report(sample: list[Cluster], total_new: int) -> None:
    print(
        f"\nEnrich (sample of {len(sample)} out of {total_new} new clusters, up to "
        f"{ENRICH_SAMPLE_PER_GROUP} per group -- full enrichment waits until after M4 ranking "
        f"keeps only the top N, so --dry-run doesn't hit hundreds of external sites per run):\n"
    )
    for cluster in sample:
        lead = cluster.lead
        if lead.extracted_text:
            word_count = len(lead.extracted_text.split())
            print(f'  "{lead.title[:60]}" ({lead.source}): {word_count} words extracted')
        else:
            print(f'  "{lead.title[:60]}" ({lead.source}): fell back to teaser')


async def run_dry_run() -> None:
    settings = load_settings()
    print("Loaded settings:")
    print(settings.model_dump_json(indent=2))

    sources = load_sources()
    results = await fetch_all(sources)
    _print_fetch_report(results)

    all_items = [item for r in results for item in r.items]
    filters = load_filters()
    filtered_items = prefilter(all_items, filters)
    _print_prefilter_report(all_items, filtered_items)

    clusters = cluster_items(filtered_items, threshold=settings.cluster.title_similarity_threshold)
    seen = load_seen()
    new_clusters = unseen_clusters(clusters, seen)
    _print_cluster_report(clusters, len(seen), new_clusters)

    sample = _sample_for_enrich(new_clusters, ENRICH_SAMPLE_PER_GROUP)
    enriched_sample = await enrich_all(sample, settings.enrich)
    _print_enrich_report(enriched_sample, len(new_clusters))


def _print_rank_report(scored: list[ScoredCluster]) -> None:
    kept = [sc for sc in scored if sc.kept]
    by_group = Counter(sc.cluster.group for sc in kept)
    print(f"\nRank: {len(scored)} scored, {len(kept)} kept ({dict(by_group)})")
    print("  out/scored.json written with every scored cluster (kept and dropped)")
    for sc in sorted(kept, key=lambda s: s.final_score, reverse=True)[:5]:
        print(f'    [{sc.final_score:.1f}] "{sc.cluster.lead.title[:60]}" -- {sc.reason}')


def _print_summarize_report(kept: list[Cluster], summaries: dict[str, Summary]) -> None:
    print(f"\nSummarize: {len(summaries)}/{len(kept)} kept clusters got a full summary")
    for _cluster_id, summary in list(summaries.items())[:3]:
        print(f'  "{summary.text[:100]}"')
        print(f"    why it matters: {summary.why_it_matters[:100]}")
        if summary.law:
            print(f"    law: {summary.law.jurisdiction}, {summary.law.status.value}")


def _print_cost_report(cost: CostTracker) -> None:
    print("\nCost:")
    print(json.dumps(cost.summary(), indent=2))


async def run_from_fixture(path: Path) -> None:
    settings = load_settings()
    raw = json.loads(path.read_text())
    clusters = [Cluster.model_validate(c) for c in raw]
    print(f"Loaded {len(clusters)} clusters from fixture {path}")

    seen = load_seen()
    new_clusters = unseen_clusters(clusters, seen)
    print(f"{len(new_clusters)} of {len(clusters)} are new (not already in state/seen.json)")

    cost = CostTracker(token_cap=settings.token_cap_per_run)
    async with make_llm_client() as client:
        scored = await rank_clusters(new_clusters, settings, client=client, cost=cost)
        write_scored_json(scored)
        _print_rank_report(scored)

        kept = [sc.cluster for sc in scored if sc.kept]
        await enrich_all(kept, settings.enrich)

        summaries = await summarize_clusters(kept, settings, client=client, cost=cost)
        _print_summarize_report(kept, summaries)

        scored_by_group: dict[Group, list[ScoredCluster]] = defaultdict(list)
        for sc in scored:
            if sc.kept:
                scored_by_group[sc.cluster.group].append(sc)
        overviews = await generate_overviews(
            scored_by_group, summaries, settings, client=client, cost=cost
        )

    print("\nGerman overviews:")
    for group, text in overviews.items():
        print(f"  [{group.value}] {text}")

    cost.write_log()
    _print_cost_report(cost)


def app() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.from_fixture:
        asyncio.run(run_from_fixture(Path(args.from_fixture)))
        return

    if args.dry_run:
        asyncio.run(run_dry_run())
        return

    parser.error("no pipeline stages are implemented yet; use --dry-run or --from-fixture")


if __name__ == "__main__":
    app()
