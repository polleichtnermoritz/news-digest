"""Renders a Digest into static HTML via Jinja2: one dated page per day
under site/digest/, an index.html pointing at the latest + an archive
list, and a shared stylesheet. Mobile-first, no external JS/fonts,
`noindex` since free GitHub Pages requires a public repo.
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from digest.models import (
    Digest,
    Group,
    GroupDigest,
    PodcastEpisode,
    RenderedItem,
    ScoredCluster,
    Summary,
)
from digest.settings import PROJECT_ROOT

TEMPLATES_DIR = PROJECT_ROOT / "templates"
DEFAULT_SITE_DIR = PROJECT_ROOT / "site"

GROUP_ORDER = [Group.TECH, Group.GEOPOLITICS, Group.SCIENCE]
GROUP_LABELS = {Group.TECH: "Tech", Group.GEOPOLITICS: "Geopolitics", Group.SCIENCE: "Science"}

# "Also considered" is for auditing the top near-misses, not a full dump of
# the backlog -- unbounded, a cold-start day (hundreds of dropped clusters
# per group) blows well past the plan's 200 KB page budget.
ALSO_CONSIDERED_LIMIT = 20


def build_digest(
    run_date: date,
    scored: list[ScoredCluster],
    summaries: dict[str, Summary],
    overviews: dict[Group, str],
) -> Digest:
    groups = []
    for group in GROUP_ORDER:
        group_scored = [sc for sc in scored if sc.cluster.group == group]
        kept = sorted(
            (sc for sc in group_scored if sc.kept), key=lambda s: s.final_score, reverse=True
        )
        dropped = sorted(
            (sc for sc in group_scored if not sc.kept),
            key=lambda s: s.final_score,
            reverse=True,
        )

        items = [
            RenderedItem(cluster=sc.cluster, summary=summaries.get(sc.cluster.id)) for sc in kept
        ]
        groups.append(
            GroupDigest(
                group=group,
                overview_de=overviews.get(group, ""),
                items=items,
                also_considered=dropped[:ALSO_CONSIDERED_LIMIT],
                also_considered_total=len(dropped),
            )
        )
    return Digest(date=run_date, groups=groups)


def _make_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["group_label"] = lambda g: GROUP_LABELS[g]
    return env


def render_site(
    digest: Digest,
    site_dir: Path = DEFAULT_SITE_DIR,
    episode: PodcastEpisode | None = None,
    podcast_enabled: bool = False,
) -> Path:
    env = _make_env()
    digest_dir = site_dir / "digest"
    digest_dir.mkdir(parents=True, exist_ok=True)

    page_path = digest_dir / f"{digest.date.isoformat()}.html"
    page_path.write_text(
        env.get_template("digest.html.j2").render(digest=digest, episode=episode)
    )

    archive_dates = sorted((p.stem for p in digest_dir.glob("*.html")), reverse=True)
    index_html = env.get_template("index.html.j2").render(
        latest_date=archive_dates[0],
        archive_dates=archive_dates,
        podcast_enabled=podcast_enabled,
    )
    (site_dir / "index.html").write_text(index_html)

    shutil.copyfile(TEMPLATES_DIR / "styles.css", site_dir / "styles.css")

    return page_path
