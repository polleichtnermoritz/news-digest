"""Code-only pre-filter: drops routine law-feed items and empty-teaser items before clustering."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from digest.models import Group, Item, Tier
from digest.settings import PROJECT_ROOT

DEFAULT_FILTERS_PATH = PROJECT_ROOT / "config" / "filters.yaml"


class FilterConfig(BaseModel):
    drop_if_empty_teaser: bool
    law_feed_drop_keywords: dict[str, list[str]]


def load_filters(path: Path = DEFAULT_FILTERS_PATH) -> FilterConfig:
    raw = yaml.safe_load(path.read_text())
    return FilterConfig.model_validate(raw)


def _is_law_feed_item(item: Item) -> bool:
    return item.tier == Tier.PRIMARY and item.group == Group.GEOPOLITICS


def _matches_drop_keyword(item: Item, config: FilterConfig) -> bool:
    keywords = config.law_feed_drop_keywords.get(item.language, [])
    title_lower = item.title.lower()
    return any(keyword.lower() in title_lower for keyword in keywords)


def prefilter(items: list[Item], config: FilterConfig) -> list[Item]:
    kept = []
    for item in items:
        if config.drop_if_empty_teaser and not item.teaser.strip():
            continue
        if _is_law_feed_item(item) and _matches_drop_keyword(item, config):
            continue
        kept.append(item)
    return kept
