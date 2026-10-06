from datetime import UTC, datetime

from digest.models import Group, Item, Tier
from digest.prefilter import FilterConfig, prefilter

CONFIG = FilterConfig(
    drop_if_empty_teaser=True,
    law_feed_drop_keywords={"de": ["Berichtigung", "Kundmachung"], "en": ["correction"]},
)


def make_item(**overrides: object) -> Item:
    defaults: dict[str, object] = dict(
        id="item-1",
        url="https://example.com/a",
        title="Some title",
        source="Example Source",
        group=Group.TECH,
        tier=Tier.JOURNALISM,
        language="en",
        published_at=datetime(2026, 10, 6, 8, 0, tzinfo=UTC),
        teaser="A short teaser.",
    )
    defaults.update(overrides)
    return Item.model_validate(defaults)


def test_drops_items_with_empty_teaser() -> None:
    items = [make_item(teaser=""), make_item(id="item-2", teaser="has content")]
    assert [item.id for item in prefilter(items, CONFIG)] == ["item-2"]


def test_drops_primary_law_feed_items_matching_keyword() -> None:
    items = [
        make_item(
            title="Berichtigung der Verordnung",
            tier=Tier.PRIMARY,
            group=Group.GEOPOLITICS,
            language="de",
        ),
        make_item(
            id="item-2",
            title="Neues Gesetz beschlossen",
            tier=Tier.PRIMARY,
            group=Group.GEOPOLITICS,
            language="de",
        ),
    ]
    assert [item.id for item in prefilter(items, CONFIG)] == ["item-2"]


def test_keyword_filter_does_not_apply_outside_primary_geopolitics() -> None:
    # Same "correction"-like title, but from a journalism source -- must survive.
    items = [
        make_item(
            title="Guide correction notes for readers", tier=Tier.JOURNALISM, group=Group.TECH
        )
    ]
    assert [item.id for item in prefilter(items, CONFIG)] == ["item-1"]
