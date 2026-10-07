from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from digest.models import Cluster, Group, Item, PodcastEpisode, ScoredCluster, Summary, Tier
from digest.podcast import (
    build_feed_xml,
    build_script,
    load_episodes,
    prune_episodes,
    render_podcast,
    save_episodes,
    select_stories,
    spoken_date,
)
from digest.settings import (
    ClusterSettings,
    EnrichSettings,
    Models,
    PodcastSettings,
    Settings,
    StateSettings,
    TopNPerGroup,
)


def make_settings(**podcast_overrides: object) -> Settings:
    podcast = dict(enabled=True, voice="en-US-GuyNeural", story_count=6, episode_retention_days=30)
    podcast.update(podcast_overrides)
    return Settings(
        delivery_hour_vienna=7,
        site_base_url="https://example.github.io/news-digest",
        top_n_per_group=TopNPerGroup(tech=8, geopolitics=8, science=5, laws_minimum=2),
        models=Models(ranking="claude-haiku-4-5", summarize="claude-haiku-4-5"),
        token_cap_per_run=1_000_000,
        enrich=EnrichSettings(max_words=2000, timeout_seconds=10),
        cluster=ClusterSettings(title_similarity_threshold=90),
        state=StateSettings(seen_retention_days=30),
        podcast=PodcastSettings(**podcast),  # type: ignore[arg-type]
    )


def make_scored(
    cluster_id: str,
    group: Group = Group.TECH,
    kept: bool = True,
    final_score: float = 5.0,
    title: str | None = None,
    teaser: str = "A teaser.",
    source: str = "Example Source",
) -> ScoredCluster:
    lead = Item(
        id=f"item-{cluster_id}",
        url=f"https://example.com/{cluster_id}",
        title=title or f"Story {cluster_id}",
        source=source,
        group=group,
        tier=Tier.JOURNALISM,
        language="en",
        published_at=datetime(2026, 10, 7, 8, 0, tzinfo=UTC),
        teaser=teaser,
        weight=2,
    )
    cluster = Cluster(id=cluster_id, group=group, lead=lead, also_covered_by=[])
    return ScoredCluster(
        cluster=cluster, llm_score=final_score, reason="r", final_score=final_score, kept=kept
    )


def test_spoken_date_formats_with_ordinal_suffix() -> None:
    assert spoken_date(date(2026, 10, 7)) == "Wednesday, October 7th"
    assert spoken_date(date(2026, 10, 1)) == "Thursday, October 1st"
    assert spoken_date(date(2026, 10, 2)) == "Friday, October 2nd"
    assert spoken_date(date(2026, 10, 3)) == "Saturday, October 3rd"
    assert spoken_date(date(2026, 10, 11)) == "Sunday, October 11th"  # not "11st"
    assert spoken_date(date(2026, 10, 13)) == "Tuesday, October 13th"  # not "13rd"


def test_select_stories_picks_top_n_kept_across_groups_by_score() -> None:
    scored = [
        make_scored("a", group=Group.TECH, final_score=9.0),
        make_scored("b", group=Group.GEOPOLITICS, final_score=8.0),
        make_scored("c", group=Group.SCIENCE, final_score=7.0),
        make_scored("d", group=Group.TECH, final_score=1.0),
        make_scored("e", group=Group.TECH, final_score=10.0, kept=False),  # dropped, excluded
    ]

    selected = select_stories(scored, n=3)

    assert [sc.cluster.id for sc in selected] == ["a", "b", "c"]


def test_select_stories_excludes_raw_papers_but_keeps_science_journalism() -> None:
    scored = [
        make_scored(
            "paper", group=Group.SCIENCE, final_score=10.0, source="arXiv cs.AI"
        ),
        make_scored(
            "hf-paper",
            group=Group.SCIENCE,
            final_score=9.5,
            source="Hugging Face Daily Papers",
        ),
        make_scored(
            "quanta", group=Group.SCIENCE, final_score=6.0, source="Quanta Magazine"
        ),
        make_scored("tech", group=Group.TECH, final_score=5.0),
    ]

    selected = select_stories(scored, n=6)

    assert [sc.cluster.id for sc in selected] == ["quanta", "tech"]


def test_build_script_includes_all_stories_in_order_with_transitions() -> None:
    stories = [
        make_scored("a", title="First Story"),
        make_scored("b", title="Middle Story"),
        make_scored("c", title="Last Story"),
    ]
    summaries = {
        "a": Summary(cluster_id="a", text="Summary A.", why_it_matters="Matters A.", language="en"),
        "c": Summary(cluster_id="c", text="Summary C.", why_it_matters="Matters C.", language="en"),
        # "b" has no summary -- must fall back to teaser.
    }

    script = build_script(stories, summaries, date(2026, 10, 7))

    assert script.startswith("Here's your news digest for Wednesday, October 7th. 3 stories today.")
    assert "First up: First Story. Summary A. Why it matters: Matters A." in script
    assert "Next: Middle Story. A teaser." in script
    assert "Finally: Last Story. Summary C. Why it matters: Matters C." in script
    assert script.endswith("That's your digest. Thanks for listening.")


def test_prune_episodes_drops_entries_past_retention() -> None:
    episodes = [
        PodcastEpisode(
            date=date(2026, 9, 1), title="old", mp3_path="podcast/old.mp3", mp3_bytes=1
        ),
        PodcastEpisode(
            date=date(2026, 10, 6), title="new", mp3_path="podcast/new.mp3", mp3_bytes=1
        ),
    ]

    pruned = prune_episodes(episodes, retention_days=30, today=date(2026, 10, 7))

    assert [e.title for e in pruned] == ["new"]


def test_save_and_load_episodes_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "episodes.json"
    episodes = [
        PodcastEpisode(
            date=date(2026, 10, 7), title="Ep 1", mp3_path="podcast/x.mp3", mp3_bytes=123
        )
    ]

    save_episodes(episodes, path)
    loaded = load_episodes(path)

    assert loaded == episodes


def test_load_episodes_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_episodes(tmp_path / "nope.json") == []


def test_build_feed_xml_contains_enclosure_and_is_newest_first() -> None:
    episodes = [
        PodcastEpisode(
            date=date(2026, 10, 5), title="Older", mp3_path="podcast/a.mp3", mp3_bytes=100
        ),
        PodcastEpisode(
            date=date(2026, 10, 7), title="Newer", mp3_path="podcast/b.mp3", mp3_bytes=200
        ),
    ]

    xml = build_feed_xml(episodes, make_settings())

    assert "<rss" in xml and "itunes" in xml
    assert 'url="https://example.github.io/news-digest/podcast/b.mp3"' in xml
    assert 'length="200"' in xml
    assert xml.index("Newer") < xml.index("Older")  # newest-first ordering


class _FakeCommunicate:
    saved_to: list[str] = []

    def __init__(self, text: str, voice: str) -> None:
        self.text = text
        self.voice = voice

    async def save(self, path: str) -> None:
        _FakeCommunicate.saved_to.append(path)
        Path(path).write_bytes(b"\xff\xfb" + b"\x00" * 50)  # fake mp3-ish bytes


async def test_render_podcast_writes_mp3_and_feed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("digest.podcast.edge_tts.Communicate", _FakeCommunicate)

    scored = [make_scored("a", final_score=9.0)]
    summaries = {"a": Summary(cluster_id="a", text="Text.", why_it_matters="Why.", language="en")}
    site_dir = tmp_path / "site"

    episode = await render_podcast(
        scored,
        summaries,
        date(2026, 10, 7),
        make_settings(),
        site_dir,
        episodes_path=tmp_path / "episodes.json",
    )

    assert episode is not None
    assert (site_dir / "podcast" / "2026-10-07.mp3").exists()
    assert (site_dir / "feed.xml").exists()
    assert episode.mp3_bytes > 0


async def test_render_podcast_returns_none_when_disabled(tmp_path: Path) -> None:
    settings = make_settings(enabled=False)
    episode = await render_podcast([], {}, date(2026, 10, 7), settings, tmp_path / "site")
    assert episode is None


async def test_render_podcast_returns_none_when_no_kept_stories(tmp_path: Path) -> None:
    scored = [make_scored("a", kept=False)]
    episode = await render_podcast(
        scored, {}, date(2026, 10, 7), make_settings(), tmp_path / "site"
    )
    assert episode is None
