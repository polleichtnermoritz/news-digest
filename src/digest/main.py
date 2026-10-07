"""CLI entry point for the daily digest pipeline."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic

from digest.cluster import cluster_items
from digest.enrich import enrich_all, is_paper_source
from digest.fetch import fetch_all
from digest.fetch.common import SourceFetchResult
from digest.llm import CostTracker
from digest.llm import make_client as make_llm_client
from digest.models import Cluster, Digest, Group, Item, PodcastEpisode, ScoredCluster, Summary
from digest.notify import TelegramNotifier, format_error_message, format_success_message
from digest.podcast import render_podcast
from digest.prefilter import load_filters, prefilter
from digest.rank import rank_clusters, write_scored_json
from digest.render import DEFAULT_SITE_DIR, build_digest, render_site
from digest.settings import PROJECT_ROOT, Settings, load_settings
from digest.sources import load_sources
from digest.state import load_seen, mark_seen, prune_seen, save_seen, unseen_clusters
from digest.summarize import generate_overviews, summarize_clusters

ENRICH_SAMPLE_PER_GROUP = 3
VIENNA_TZ = ZoneInfo("Europe/Vienna")


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
    parser.add_argument(
        "--run",
        action="store_true",
        help="run the full live pipeline: fetch, rank, summarize, render, and update state. "
        "This is what the scheduled GitHub Actions workflow calls -- it costs real LLM money.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="with --run, skip the Vienna delivery-hour gate (used for workflow_dispatch)",
    )
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


async def rank_enrich_summarize_render(
    new_clusters: list[Cluster],
    settings: Settings,
    client: anthropic.AsyncAnthropic,
    cost: CostTracker,
    run_date: date,
) -> tuple[list[ScoredCluster], Digest, PodcastEpisode | None]:
    """Shared tail of the pipeline: rank -> write scored.json -> enrich the
    kept leads -> summarize -> German overview -> render -> podcast episode.
    Used by both --from-fixture (read-only w.r.t. state/seen.json) and --run
    (which marks the kept clusters seen afterwards)."""
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

    episode = await render_podcast(scored, summaries, run_date, settings, DEFAULT_SITE_DIR)
    _print_podcast_report(episode)

    digest = build_digest(run_date, scored, summaries, overviews)
    (PROJECT_ROOT / "out" / "digest.json").write_text(digest.model_dump_json(indent=2))
    page_path = render_site(digest, episode=episode, podcast_enabled=settings.podcast.enabled)
    print(f"\nRendered {page_path.relative_to(PROJECT_ROOT)}")

    return scored, digest, episode


def _print_podcast_report(episode: PodcastEpisode | None) -> None:
    if episode is None:
        print("\nPodcast: skipped (disabled, or no kept stories)")
        return
    print(
        f"\nPodcast: {episode.title} -> {episode.mp3_path} "
        f"({episode.mp3_bytes / 1024:.0f} KB), feed.xml updated"
    )


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
        await rank_enrich_summarize_render(
            new_clusters, settings, client, cost, datetime.now(UTC).date()
        )


def should_run_now(vienna_hour: int, delivery_hour: int, force: bool) -> bool:
    return force or vienna_hour == delivery_hour


def _write_github_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a") as f:
        f.write(f"{name}={value}\n")


async def run_live(force: bool = False) -> bool:
    """The production entry point the scheduled workflow calls: fetches
    live, calls the real LLM, renders site/, and marks kept clusters seen.
    Gated to settings.delivery_hour_vienna unless --force (used for both
    workflow_dispatch and local manual runs), so the DST-safe double cron
    can fire twice a day and only do real work once."""
    settings = load_settings()
    vienna_hour = datetime.now(VIENNA_TZ).hour
    if not should_run_now(vienna_hour, settings.delivery_hour_vienna, force):
        print(
            f"Vienna local hour is {vienna_hour:02d}:00, delivery hour is "
            f"{settings.delivery_hour_vienna:02d}:00 -- skipping (use --force to run anyway)."
        )
        _write_github_output("ran", "false")
        return False

    print(f"Running at Vienna local hour {vienna_hour:02d}:00\n")

    try:
        sources = load_sources()
        results = await fetch_all(sources)
        _print_fetch_report(results)

        all_items = [item for r in results for item in r.items]
        filtered_items = prefilter(all_items, load_filters())
        _print_prefilter_report(all_items, filtered_items)

        clusters = cluster_items(
            filtered_items, threshold=settings.cluster.title_similarity_threshold
        )
        seen = load_seen()
        new_clusters = unseen_clusters(clusters, seen)
        _print_cluster_report(clusters, len(seen), new_clusters)

        run_date = datetime.now(UTC).date()
        cost = CostTracker(token_cap=settings.token_cap_per_run)
        async with make_llm_client() as client:
            scored, digest, episode = await rank_enrich_summarize_render(
                new_clusters, settings, client, cost, run_date
            )

        kept_clusters = [sc.cluster for sc in scored if sc.kept]
        seen = mark_seen(seen, kept_clusters, run_date)
        seen = prune_seen(seen, settings.state.seen_retention_days, run_date)
        save_seen(seen)
        print(
            f"\nState: marked {len(kept_clusters)} published clusters as seen, "
            f"{len(seen)} total after pruning"
        )
    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"[:300]
        await _try_notify(format_error_message(error_text))
        _write_github_output("ran", "false")
        raise

    page_url = f"{settings.site_base_url}/digest/{run_date.isoformat()}.html"
    message = format_success_message(digest, page_url)
    if episode is not None:
        message += f"\n\U0001f3a7 Podcast: {settings.site_base_url}/feed.xml"
    await _try_notify(message)

    _write_github_output("ran", "true")
    return True


async def _try_notify(text: str) -> None:
    """Best-effort: a notification failure must never crash the run (the
    pipeline already succeeded or already failed by the time we send one),
    but it also must not pass silently -- print it either way."""
    try:
        await TelegramNotifier().send(text)
        print(f"\nNotified: {text}")
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
        print(f"\nWARNING: failed to send notification: {exc!r}")


def app() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.from_fixture:
        asyncio.run(run_from_fixture(Path(args.from_fixture)))
        return

    if args.run:
        asyncio.run(run_live(force=args.force))
        return

    if args.dry_run:
        asyncio.run(run_dry_run())
        return

    parser.error("no pipeline stages are implemented yet; use --dry-run, --from-fixture, or --run")


if __name__ == "__main__":
    app()
