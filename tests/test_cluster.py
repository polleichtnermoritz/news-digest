from datetime import UTC, datetime

from digest.cluster import canonical_hash, canonical_url, cluster_items
from digest.models import Group, Item, Tier


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


def test_canonical_url_ignores_trailing_slash_and_case() -> None:
    assert canonical_url("https://Example.com/a/b/") == canonical_url("https://example.com/a/b")


def test_same_story_from_three_outlets_becomes_one_cluster() -> None:
    items = [
        make_item(
            id="a",
            url="https://orf.at/story-1",
            title="Austria passes new budget law",
            source="ORF.at",
            tier=Tier.JOURNALISM,
            weight=2,
        ),
        make_item(
            id="b",
            url="https://derstandard.at/story-1",
            title="Austria passes new budget law today",
            source="Der Standard",
            tier=Tier.JOURNALISM,
            weight=2,
        ),
        make_item(
            id="c",
            url="https://parlament.gv.at/pk/story-1",
            title="Austria passes new budget law",
            source="Parlamentskorrespondenz",
            tier=Tier.PRIMARY,
            weight=3,
        ),
    ]

    clusters = cluster_items(items, threshold=90)

    assert len(clusters) == 1
    cluster = clusters[0]
    assert len(cluster.also_covered_by) == 2
    # Primary tier + highest weight wins the lead slot even though it wasn't first.
    assert cluster.lead.source == "Parlamentskorrespondenz"
    covering_sources = {i.source for i in cluster.also_covered_by} | {cluster.lead.source}
    assert covering_sources == {"ORF.at", "Der Standard", "Parlamentskorrespondenz"}


def test_dissimilar_titles_stay_separate_clusters() -> None:
    items = [
        make_item(id="a", url="https://a.example/1", title="Austria passes new budget law"),
        make_item(id="b", url="https://b.example/1", title="Heavy rainfall floods Vienna streets"),
    ]
    assert len(cluster_items(items, threshold=90)) == 2


def test_clustering_is_scoped_to_topic_group() -> None:
    # Same title, but different topic groups -- must not merge.
    items = [
        make_item(
            id="a", url="https://a.example/1", title="New model released", group=Group.SCIENCE
        ),
        make_item(id="b", url="https://b.example/1", title="New model released", group=Group.TECH),
    ]
    assert len(cluster_items(items, threshold=90)) == 2


def test_running_twice_on_the_same_items_yields_identical_cluster_ids() -> None:
    items = [
        make_item(id="a", url="https://orf.at/story-1", title="Austria passes new budget law"),
        make_item(
            id="b",
            url="https://derstandard.at/story-1",
            title="Austria passes new budget law today",
            source="Der Standard",
        ),
    ]

    first_run = cluster_items(items, threshold=90)
    second_run = cluster_items(items, threshold=90)

    assert [c.id for c in first_run] == [c.id for c in second_run]


def test_canonical_hash_is_stable_for_equivalent_urls() -> None:
    assert canonical_hash("https://example.com/a/") == canonical_hash("https://EXAMPLE.com/a")
