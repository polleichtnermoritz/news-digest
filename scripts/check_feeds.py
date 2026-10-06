"""Verifies every source in config/sources.yaml.

For each source, checks whether its URL is reachable and, for RSS sources,
whether the response actually parses as a feed with entries. Prints a report
and (with --write) rewrites sources.yaml: dead entries are dropped, and the
`access` field is corrected based on what was actually observed.

Usage:
    uv run python scripts/check_feeds.py            # report only
    uv run python scripts/check_feeds.py --write     # report + update sources.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import feedparser
import httpx
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCES_PATH = PROJECT_ROOT / "config" / "sources.yaml"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0 Safari/537.36 news-digest-feed-checker/0.1"
)
TIMEOUT = 15.0


@dataclass
class CheckResult:
    name: str
    declared_access: str
    status: str  # "ok" | "dead" | "needs_review"
    observed_access: str  # "rss" | "api" | "page" | "unknown"
    detail: str


async def check_source(client: httpx.AsyncClient, source: dict[str, Any]) -> CheckResult:
    name = source["name"]
    url = source["url"]
    declared_access = source["access"]

    try:
        response = await client.get(url, timeout=TIMEOUT, follow_redirects=True)
    except httpx.HTTPError as exc:
        return CheckResult(name, declared_access, "dead", "unknown", f"request failed: {exc!r}")

    if response.status_code == 404:
        return CheckResult(name, declared_access, "dead", "unknown", "HTTP 404")

    if response.status_code == 401 and declared_access == "api":
        return CheckResult(
            name, declared_access, "ok", "api", "HTTP 401 (reachable, needs API key)"
        )

    if response.status_code >= 400:
        return CheckResult(
            name, declared_access, "needs_review", "unknown", f"HTTP {response.status_code}"
        )

    content_type = response.headers.get("content-type", "")

    if declared_access == "rss":
        parsed = feedparser.parse(response.content)
        if parsed.bozo and not parsed.entries:
            return CheckResult(
                name,
                declared_access,
                "needs_review",
                "page",
                f"HTTP {response.status_code} but did not parse as a feed "
                f"(bozo_exception={parsed.get('bozo_exception')!r}); may need a new feed URL",
            )
        if not parsed.entries:
            return CheckResult(
                name, declared_access, "needs_review", "rss", "parsed as feed but zero entries"
            )
        return CheckResult(
            name, declared_access, "ok", "rss", f"{len(parsed.entries)} entries parsed"
        )

    if declared_access == "api":
        return CheckResult(
            name, declared_access, "ok", "api", f"HTTP {response.status_code}, {content_type}"
        )

    if declared_access == "page":
        return CheckResult(
            name, declared_access, "ok", "page", f"HTTP {response.status_code}, {content_type}"
        )

    return CheckResult(name, declared_access, "needs_review", "unknown", "unhandled access type")


async def check_all(sources: list[dict[str, Any]]) -> list[CheckResult]:
    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(headers=headers) as client:
        tasks = [check_source(client, source) for source in sources]
        return await asyncio.gather(*tasks)


def print_report(results: list[CheckResult]) -> None:
    ok = [r for r in results if r.status == "ok"]
    review = [r for r in results if r.status == "needs_review"]
    dead = [r for r in results if r.status == "dead"]

    print(f"\n{len(ok)} ok, {len(review)} need review, {len(dead)} dead\n")

    for label, group in (("OK", ok), ("NEEDS REVIEW", review), ("DEAD", dead)):
        if not group:
            continue
        print(f"--- {label} ---")
        for r in group:
            marker = "" if r.observed_access == r.declared_access else (
                f" [declared={r.declared_access} -> observed={r.observed_access}]"
            )
            print(f"  {r.name}{marker}: {r.detail}")
        print()


def update_sources_file(
    sources: list[dict[str, Any]], results: list[CheckResult]
) -> list[dict[str, Any]]:
    by_name = {r.name: r for r in results}
    updated = []
    for source in sources:
        result = by_name[source["name"]]
        if result.status == "dead":
            continue
        source = dict(source)
        source["verified"] = result.status == "ok"
        if result.observed_access != "unknown":
            source["access"] = result.observed_access
        updated.append(source)
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help="rewrite sources.yaml with the verification results"
    )
    args = parser.parse_args()

    raw = yaml.safe_load(SOURCES_PATH.read_text())
    sources = raw["sources"]

    results = asyncio.run(check_all(sources))
    print_report(results)

    dead_count = sum(1 for r in results if r.status == "dead")

    if args.write:
        updated = update_sources_file(sources, results)
        SOURCES_PATH.write_text(
            yaml.safe_dump({"sources": updated}, sort_keys=False, allow_unicode=True)
        )
        print(f"Wrote {len(updated)} sources to {SOURCES_PATH} ({dead_count} dropped as dead).")

    if dead_count and not args.write:
        sys.exit(1)


if __name__ == "__main__":
    main()
