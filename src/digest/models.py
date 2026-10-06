"""Pydantic data models shared across the pipeline stages."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Group(StrEnum):
    TECH = "tech"
    GEOPOLITICS = "geopolitics"
    SCIENCE = "science"


class Tier(StrEnum):
    PRIMARY = "primary"
    JOURNALISM = "journalism"
    THINK_TANK = "think_tank"


class LawStatus(StrEnum):
    PROPOSED = "proposed"
    PASSED = "passed"
    IN_FORCE = "in_force"


class Item(BaseModel):
    """A single fetched article or paper, normalized across all sources."""

    id: str
    url: str
    title: str
    source: str
    group: Group
    tier: Tier
    language: str
    published_at: datetime
    teaser: str
    weight: int = Field(ge=1, le=3, default=1)
    extracted_text: str | None = None


class Cluster(BaseModel):
    """One or more Items judged to be the same story; `lead` is shown, the rest linked."""

    id: str
    group: Group
    lead: Item
    also_covered_by: list[Item] = Field(default_factory=list)


class ScoredCluster(BaseModel):
    """A Cluster after the LLM ranking pass, kept or dropped from the digest."""

    cluster: Cluster
    llm_score: float = Field(ge=0, le=10)
    reason: str
    final_score: float
    kept: bool


class LawFields(BaseModel):
    jurisdiction: str
    status: LawStatus
    effective_date: date | None = None


class Summary(BaseModel):
    """The LLM-written summary for one kept cluster."""

    cluster_id: str
    text: str
    why_it_matters: str
    language: str
    law: LawFields | None = None


class GroupDigest(BaseModel):
    group: Group
    overview_de: str
    summaries: list[Summary] = Field(default_factory=list)
    also_considered: list[ScoredCluster] = Field(default_factory=list)


class Digest(BaseModel):
    """The full rendered output for one day."""

    date: date
    groups: list[GroupDigest] = Field(default_factory=list)
