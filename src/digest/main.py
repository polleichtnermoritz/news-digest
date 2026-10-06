"""CLI entry point for the daily digest pipeline."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter

from digest.cluster import cluster_items
from digest.fetch import fetch_all
from digest.fetch.common import SourceFetchResult
from digest.models import Cluster, Item
from digest.prefilter import load_filters, prefilter
from digest.settings import load_settings
from digest.sources import load_sources
from digest.state import load_seen, unseen_clusters


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


def app() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.dry_run:
        asyncio.run(run_dry_run())
        return

    parser.error("no pipeline stages are implemented yet; use --dry-run")


if __name__ == "__main__":
    app()
