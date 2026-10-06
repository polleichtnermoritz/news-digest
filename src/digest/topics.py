"""Loads config/topics.yaml: topic descriptions and keywords for the ranking prompt."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from digest.models import Group
from digest.settings import PROJECT_ROOT

DEFAULT_TOPICS_PATH = PROJECT_ROOT / "config" / "topics.yaml"


class TopicConfig(BaseModel):
    description: str
    keywords: list[str]


def load_topics(path: Path = DEFAULT_TOPICS_PATH) -> dict[Group, TopicConfig]:
    raw = yaml.safe_load(path.read_text())
    return {Group(key): TopicConfig.model_validate(value) for key, value in raw.items()}
