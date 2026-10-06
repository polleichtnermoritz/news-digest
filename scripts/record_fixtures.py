"""Runs a real fetch -> prefilter -> cluster pass and saves the resulting
clusters to a fixture file, so rank/summarize prompts can be tuned by
replaying `digest --from-fixture <path>` without re-fetching all 41 sources
each time.

Usage:
    uv run python scripts/record_fixtures.py
    uv run python scripts/record_fixtures.py --out tests/fixtures/runs/custom.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date
from pathlib import Path

from digest.cluster import cluster_items
from digest.fetch import fetch_all
from digest.models import Cluster
from digest.prefilter import load_filters, prefilter
from digest.settings import PROJECT_ROOT, load_settings
from digest.sources import load_sources

DEFAULT_RUNS_DIR = PROJECT_ROOT / "tests" / "fixtures" / "runs"


async def record(out_path: Path) -> list[Cluster]:
    settings = load_settings()
    sources = load_sources()
    results = await fetch_all(sources)
    items = [item for r in results for item in r.items]
    filtered = prefilter(items, load_filters())
    clusters = cluster_items(filtered, threshold=settings.cluster.title_similarity_threshold)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps([c.model_dump(mode="json") for c in clusters], indent=2))
    return clusters


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_RUNS_DIR / f"{date.today().isoformat()}.json"
    )
    args = parser.parse_args()

    clusters = asyncio.run(record(args.out))
    print(f"Recorded {len(clusters)} clusters to {args.out}")


if __name__ == "__main__":
    main()
