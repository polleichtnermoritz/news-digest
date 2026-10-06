"""Loads config/settings.yaml into a validated Settings model."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"


class TopNPerGroup(BaseModel):
    tech: int
    geopolitics: int
    science: int
    laws_minimum: int


class Models(BaseModel):
    ranking: str
    summarize: str


class EnrichSettings(BaseModel):
    max_words: int
    timeout_seconds: int


class ClusterSettings(BaseModel):
    title_similarity_threshold: int


class StateSettings(BaseModel):
    seen_retention_days: int


class PodcastSettings(BaseModel):
    enabled: bool
    voice: str
    story_count: int
    episode_retention_days: int


class Settings(BaseModel):
    delivery_hour_vienna: int
    site_base_url: str
    top_n_per_group: TopNPerGroup
    models: Models
    token_cap_per_run: int
    enrich: EnrichSettings
    cluster: ClusterSettings
    state: StateSettings
    podcast: PodcastSettings


def load_settings(path: Path = DEFAULT_SETTINGS_PATH) -> Settings:
    raw = yaml.safe_load(path.read_text())
    return Settings.model_validate(raw)
