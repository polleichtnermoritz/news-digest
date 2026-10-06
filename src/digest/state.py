"""Tracks which clusters have already been sent, in state/seen.json.

The file maps a Cluster.id to the ISO date it was first sent. Entries older
than `seen_retention_days` (config/settings.yaml) are pruned on each run so
the file doesn't grow forever.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from digest.models import Cluster
from digest.settings import PROJECT_ROOT

DEFAULT_SEEN_PATH = PROJECT_ROOT / "state" / "seen.json"

SeenState = dict[str, str]


def load_seen(path: Path = DEFAULT_SEEN_PATH) -> SeenState:
    if not path.exists():
        return {}
    data: SeenState = json.loads(path.read_text())
    return data


def save_seen(seen: SeenState, path: Path = DEFAULT_SEEN_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(seen, indent=2, sort_keys=True) + "\n")


def prune_seen(seen: SeenState, retention_days: int, today: date) -> SeenState:
    cutoff = today - timedelta(days=retention_days)
    return {
        cluster_id: seen_date
        for cluster_id, seen_date in seen.items()
        if date.fromisoformat(seen_date) >= cutoff
    }


def unseen_clusters(clusters: list[Cluster], seen: SeenState) -> list[Cluster]:
    return [cluster for cluster in clusters if cluster.id not in seen]


def mark_seen(seen: SeenState, clusters: list[Cluster], today: date) -> SeenState:
    updated = dict(seen)
    for cluster in clusters:
        updated[cluster.id] = today.isoformat()
    return updated
