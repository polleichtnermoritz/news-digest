from datetime import datetime

from digest.models import Cluster, Group, Item, Tier


def make_item(**overrides: object) -> Item:
    defaults: dict[str, object] = dict(
        id="item-1",
        url="https://example.com/a",
        title="Example title",
        source="Example Source",
        group=Group.TECH,
        tier=Tier.JOURNALISM,
        language="en",
        published_at=datetime(2026, 10, 6, 8, 0),
        teaser="A short teaser.",
    )
    defaults.update(overrides)
    return Item.model_validate(defaults)


def test_item_roundtrips_through_json() -> None:
    item = make_item()
    assert Item.model_validate_json(item.model_dump_json()) == item


def test_cluster_tracks_also_covered_by() -> None:
    lead = make_item(id="lead")
    other = make_item(id="other", source="Other Source")
    cluster = Cluster(id="cluster-1", group=Group.TECH, lead=lead, also_covered_by=[other])

    assert cluster.lead.id == "lead"
    assert [i.id for i in cluster.also_covered_by] == ["other"]
