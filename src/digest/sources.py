"""Loads config/sources.yaml into validated Source models."""

from __future__ import annotations

from pathlib import Path

import yaml

from digest.models import Source
from digest.settings import PROJECT_ROOT

DEFAULT_SOURCES_PATH = PROJECT_ROOT / "config" / "sources.yaml"


def load_sources(path: Path = DEFAULT_SOURCES_PATH) -> list[Source]:
    raw = yaml.safe_load(path.read_text())
    return [Source.model_validate(entry) for entry in raw["sources"]]
