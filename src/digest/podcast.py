"""Turns the day's top stories into a short audio episode: picks the
highest-scored kept clusters across all three groups (not a per-group
quota -- "today's 6 best stories", not an even split), builds a spoken-
style script deterministically from the already-written summaries (no
extra LLM call, and no risk of an LLM "naturalizing" pass quietly drifting
from the grounded text), synthesizes it with edge-tts, and maintains a
podcast RSS feed so any podcast app can subscribe to it.

Episode history lives in state/podcast_episodes.json (mirroring
state/seen.json) since the workflow only checks out `main`, not
`gh-pages` -- the feed is rebuilt fresh from that history every run.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import edge_tts
from jinja2 import Environment, FileSystemLoader, select_autoescape

from digest.enrich import is_paper_source
from digest.models import PodcastEpisode, ScoredCluster, Summary
from digest.render import TEMPLATES_DIR
from digest.settings import PROJECT_ROOT, PodcastSettings, Settings

DEFAULT_EPISODES_PATH = PROJECT_ROOT / "state" / "podcast_episodes.json"

_ORDINAL_SUFFIXES = {1: "st", 2: "nd", 3: "rd"}


def _ordinal(day: int) -> str:
    if 10 <= day % 100 <= 20:
        suffix = "th"
    else:
        suffix = _ORDINAL_SUFFIXES.get(day % 10, "th")
    return f"{day}{suffix}"


def spoken_date(d: date) -> str:
    return f"{d.strftime('%A, %B')} {_ordinal(d.day)}"


def select_stories(scored: list[ScoredCluster], n: int) -> list[ScoredCluster]:
    """Top N kept stories by score, excluding raw papers (arXiv, Hugging
    Face Daily Papers) -- dense academic-abstract prose is hard to follow
    as audio with no text to glance back at. Science *journalism* (Quanta,
    Nature News) and AI-lab announcement posts are written in normal prose
    and stay eligible."""
    eligible = [sc for sc in scored if sc.kept and not is_paper_source(sc.cluster.lead.source)]
    return sorted(eligible, key=lambda sc: sc.final_score, reverse=True)[:n]


def build_script(
    stories: list[ScoredCluster], summaries: dict[str, Summary], run_date: date
) -> str:
    story_count = len(stories)
    lines = [
        f"Here's your news digest for {spoken_date(run_date)}. "
        f"{story_count} stories today."
    ]

    for i, sc in enumerate(stories):
        lead = sc.cluster.lead
        summary = summaries.get(sc.cluster.id)
        body = summary.text if summary else lead.teaser
        why = summary.why_it_matters if summary else ""

        if i == 0:
            transition = "First up:"
        elif i == story_count - 1:
            transition = "Finally:"
        else:
            transition = "Next:"

        paragraph = f"{transition} {lead.title}. {body}"
        if why:
            paragraph += f" Why it matters: {why}"
        lines.append(paragraph)

    lines.append("That's your digest. Thanks for listening.")
    return "\n\n".join(lines)


async def synthesize(script: str, voice: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    communicate = edge_tts.Communicate(script, voice)
    await communicate.save(str(out_path))


def load_episodes(path: Path = DEFAULT_EPISODES_PATH) -> list[PodcastEpisode]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text())
    return [PodcastEpisode.model_validate(e) for e in raw]


def save_episodes(episodes: list[PodcastEpisode], path: Path = DEFAULT_EPISODES_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([e.model_dump(mode="json") for e in episodes], indent=2))


def prune_episodes(
    episodes: list[PodcastEpisode], retention_days: int, today: date
) -> list[PodcastEpisode]:
    cutoff = today - timedelta(days=retention_days)
    return [e for e in episodes if e.date >= cutoff]


def _make_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(["xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def build_feed_xml(episodes: list[PodcastEpisode], settings: Settings) -> str:
    env = _make_env()
    template = env.get_template("podcast_feed.xml.j2")
    # Newest first, RSS convention.
    ordered = sorted(episodes, key=lambda e: e.date, reverse=True)
    return template.render(episodes=ordered, settings=settings)


async def render_podcast(
    scored: list[ScoredCluster],
    summaries: dict[str, Summary],
    run_date: date,
    settings: Settings,
    site_dir: Path,
    episodes_path: Path = DEFAULT_EPISODES_PATH,
) -> PodcastEpisode | None:
    podcast_settings: PodcastSettings = settings.podcast
    if not podcast_settings.enabled:
        return None

    stories = select_stories(scored, podcast_settings.story_count)
    if not stories:
        return None

    script = build_script(stories, summaries, run_date)

    mp3_relative = f"podcast/{run_date.isoformat()}.mp3"
    mp3_path = site_dir / mp3_relative
    await synthesize(script, podcast_settings.voice, mp3_path)

    episode = PodcastEpisode(
        date=run_date,
        title=f"News Digest — {run_date.strftime('%d.%m.%Y')}",
        mp3_path=mp3_relative,
        mp3_bytes=mp3_path.stat().st_size,
    )

    episodes = load_episodes(episodes_path)
    episodes = [e for e in episodes if e.date != run_date] + [episode]
    episodes = prune_episodes(episodes, podcast_settings.episode_retention_days, run_date)
    save_episodes(episodes, episodes_path)

    feed_xml = build_feed_xml(episodes, settings)
    (site_dir / "feed.xml").write_text(feed_xml)

    return episode
