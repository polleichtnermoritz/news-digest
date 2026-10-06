from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from digest.cluster import cluster_items
from digest.models import Group, Item, Tier
from digest.state import load_seen, mark_seen, prune_seen, save_seen, unseen_clusters


def make_item(**overrides: object) -> Item:
    defaults: dict[str, object] = dict(
        id="item",
        url="https://example.com/a",
        title="Some title",
        source="Example Source",
        group=Group.GEOPOLITICS,
        tier=Tier.JOURNALISM,
        language="en",
        published_at=datetime(2026, 10, 6, 8, 0, tzinfo=UTC),
        teaser="A short teaser.",
        weight=2,
    )
    defaults.update(overrides)
    return Item.model_validate(defaults)


def test_running_twice_on_the_same_fixtures_yields_zero_new_clusters(tmp_path: Path) -> None:
    seen_path = tmp_path / "seen.json"
    items = [
        make_item(id="a", url="https://orf.at/story-1", title="Austria passes new budget law"),
        make_item(
            id="b",
            url="https://derstandard.at/story-1",
            title="Austria passes new budget law today",
            source="Der Standard",
        ),
    ]
    today = date(2026, 10, 6)

    # Run 1: fresh state, everything is new.
    clusters = cluster_items(items)
    seen = load_seen(seen_path)
    new_clusters = unseen_clusters(clusters, seen)
    assert len(new_clusters) == len(clusters) == 1

    seen = mark_seen(seen, new_clusters, today)
    save_seen(seen, seen_path)

    # Run 2: same fixtures, same clusters -- all already seen.
    clusters_again = cluster_items(items)
    seen_again = load_seen(seen_path)
    new_clusters_again = unseen_clusters(clusters_again, seen_again)
    assert new_clusters_again == []


def test_prune_seen_drops_entries_past_retention(tmp_path: Path) -> None:
    today = date(2026, 10, 6)
    seen = {
        "recent": (today - timedelta(days=5)).isoformat(),
        "stale": (today - timedelta(days=40)).isoformat(),
    }

    pruned = prune_seen(seen, retention_days=30, today=today)

    assert pruned == {"recent": seen["recent"]}


def test_load_seen_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_seen(tmp_path / "does-not-exist.json") == {}
