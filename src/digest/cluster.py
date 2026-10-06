"""Groups the same story from multiple outlets into one Cluster.

Two items become one cluster if either their canonical URL matches exactly
(same article, e.g. refetched across runs) or their titles are similar
enough (rapidfuzz token_sort_ratio >= threshold) within the same topic
group. The lead item is the one with the highest-ranked source tier, then
weight, then most recent publish time; everything else becomes
"also covered by".
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from urllib.parse import urlsplit, urlunsplit

from rapidfuzz import fuzz

from digest.models import Cluster, Group, Item, Tier

DEFAULT_TITLE_SIMILARITY_THRESHOLD = 90

_TIER_RANK: dict[Tier, int] = {
    Tier.PRIMARY: 3,
    Tier.JOURNALISM: 2,
    Tier.THINK_TANK: 1,
}


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def canonical_hash(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode()).hexdigest()[:16]


def _pick_lead(items: list[Item]) -> Item:
    return max(items, key=lambda item: (_TIER_RANK[item.tier], item.weight, item.published_at))


def _dedupe_by_source(items: list[Item]) -> list[Item]:
    seen_sources: set[str] = set()
    deduped = []
    for item in items:
        if item.source in seen_sources:
            continue
        seen_sources.add(item.source)
        deduped.append(item)
    return deduped


def cluster_items(
    items: list[Item], threshold: int = DEFAULT_TITLE_SIMILARITY_THRESHOLD
) -> list[Cluster]:
    # Pass 1: merge exact same-URL items (e.g. the same article refetched).
    by_url: dict[str, list[Item]] = defaultdict(list)
    for item in items:
        by_url[canonical_hash(item.url)].append(item)
    url_groups = list(by_url.values())

    # Pass 2: within each topic group, merge url-groups whose representative
    # title is similar enough to an existing bucket's.
    buckets_by_group: dict[Group, list[list[Item]]] = defaultdict(list)
    for group_items in url_groups:
        topic = group_items[0].group
        rep_title = group_items[0].title
        buckets = buckets_by_group[topic]

        best_idx, best_score = None, 0.0
        for idx, bucket in enumerate(buckets):
            score = fuzz.token_sort_ratio(rep_title, bucket[0].title)
            if score > best_score:
                best_idx, best_score = idx, score

        if best_idx is not None and best_score >= threshold:
            buckets[best_idx].extend(group_items)
        else:
            buckets.append(group_items)

    clusters = []
    for topic, buckets in buckets_by_group.items():
        for bucket_items in buckets:
            representatives = _dedupe_by_source(bucket_items)
            lead = _pick_lead(representatives)
            also_covered_by = [item for item in representatives if item is not lead]
            clusters.append(
                Cluster(
                    id=canonical_hash(lead.url),
                    group=topic,
                    lead=lead,
                    also_covered_by=also_covered_by,
                )
            )
    return clusters
