"""CLI entry point for the daily digest pipeline."""

from __future__ import annotations

import argparse

from digest.settings import load_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="digest", description="Daily news digest pipeline")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="load config and report what would run, without calling any LLM or network",
    )
    parser.add_argument(
        "--stage",
        choices=["fetch", "prefilter", "cluster", "enrich", "rank", "summarize", "render"],
        help="run a single pipeline stage in isolation",
    )
    parser.add_argument("--date", help="run as if today were this ISO date (YYYY-MM-DD)")
    parser.add_argument("--from-fixture", help="replay a recorded fixture instead of fetching live")
    return parser


def app() -> None:
    parser = build_parser()
    args = parser.parse_args()
    settings = load_settings()

    if args.dry_run:
        print("Loaded settings:")
        print(settings.model_dump_json(indent=2))
        return

    parser.error("no pipeline stages are implemented yet; use --dry-run")


if __name__ == "__main__":
    app()
