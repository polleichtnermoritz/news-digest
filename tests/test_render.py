from datetime import UTC, date, datetime
from pathlib import Path

from lxml import html as lxml_html

from digest.models import Cluster, Group, Item, LawFields, LawStatus, ScoredCluster, Summary, Tier
from digest.render import build_digest, render_site


def make_item(**overrides: object) -> Item:
    defaults: dict[str, object] = dict(
        id="item",
        url="https://example.com/a",
        title="Some title",
        source="Example Source",
        group=Group.TECH,
        tier=Tier.JOURNALISM,
        language="en",
        published_at=datetime(2026, 10, 6, 8, 0, tzinfo=UTC),
        teaser="A short teaser.",
        weight=2,
    )
    defaults.update(overrides)
    return Item.model_validate(defaults)


def make_scored(
    cluster_id: str,
    group: Group = Group.TECH,
    tier: Tier = Tier.JOURNALISM,
    kept: bool = True,
    final_score: float = 5.0,
    also_covered_by: list[Item] | None = None,
) -> ScoredCluster:
    lead = make_item(
        id=f"item-{cluster_id}", url=f"https://example.com/{cluster_id}", group=group, tier=tier
    )
    cluster = Cluster(id=cluster_id, group=group, lead=lead, also_covered_by=also_covered_by or [])
    return ScoredCluster(
        cluster=cluster,
        llm_score=min(final_score, 10.0),
        reason="a reason",
        final_score=final_score,
        kept=kept,
    )


def test_build_digest_sorts_kept_by_final_score_descending() -> None:
    low = make_scored("low", final_score=2.0)
    high = make_scored("high", final_score=9.0)
    summaries = {
        "low": Summary(cluster_id="low", text="t", why_it_matters="w", language="en"),
        "high": Summary(cluster_id="high", text="t", why_it_matters="w", language="en"),
    }

    digest = build_digest(date(2026, 10, 6), [low, high], summaries, overviews={})

    tech_group = next(g for g in digest.groups if g.group == Group.TECH)
    assert [item.cluster.id for item in tech_group.items] == ["high", "low"]


def test_build_digest_handles_missing_summary_gracefully() -> None:
    sc = make_scored("a")
    digest = build_digest(date(2026, 10, 6), [sc], summaries={}, overviews={})

    item = next(g for g in digest.groups if g.group == Group.TECH).items[0]
    assert item.summary is None


def test_build_digest_puts_dropped_clusters_in_also_considered() -> None:
    kept = make_scored("kept", kept=True)
    dropped = make_scored("dropped", kept=False)

    digest = build_digest(date(2026, 10, 6), [kept, dropped], summaries={}, overviews={})

    tech_group = next(g for g in digest.groups if g.group == Group.TECH)
    assert [i.cluster.id for i in tech_group.items] == ["kept"]
    assert [sc.cluster.id for sc in tech_group.also_considered] == ["dropped"]


def test_build_digest_caps_also_considered_to_keep_page_size_bounded() -> None:
    # Regression test: an uncapped "also considered" list blew the page past
    # the plan's 200 KB budget on a cold-start day with hundreds of dropped
    # clusters in one group. The list must be capped regardless of backlog
    # size, while still reporting the real total for the UI.
    dropped = [make_scored(f"dropped-{i}", kept=False, final_score=float(i)) for i in range(50)]

    digest = build_digest(date(2026, 10, 6), dropped, summaries={}, overviews={})

    tech_group = next(g for g in digest.groups if g.group == Group.TECH)
    assert len(tech_group.also_considered) == 20
    assert tech_group.also_considered_total == 50
    # Keeps the highest-scored near-misses, not an arbitrary slice.
    assert tech_group.also_considered[0].cluster.id == "dropped-49"


def test_render_site_writes_valid_html_with_expected_content(tmp_path: Path) -> None:
    also_covered = [make_item(id="other", url="https://other.example/a", source="Other Outlet")]
    sc = make_scored("a", group=Group.GEOPOLITICS, tier=Tier.PRIMARY, also_covered_by=also_covered)
    summaries = {
        "a": Summary(
            cluster_id="a",
            text="Summary text.",
            why_it_matters="It matters a lot.",
            language="en",
            law=LawFields(jurisdiction="Austria", status=LawStatus.PASSED),
        )
    }
    digest = build_digest(
        date(2026, 10, 6), [sc], summaries, overviews={Group.GEOPOLITICS: "German overview."}
    )

    page_path = render_site(digest, site_dir=tmp_path / "site")

    assert page_path == tmp_path / "site" / "digest" / "2026-10-06.html"
    html = page_path.read_text()

    # Parses as well-formed HTML with no structural errors.
    doc = lxml_html.fromstring(html)
    assert doc.cssselect("html")[0].get("lang") == "en"
    assert doc.cssselect('meta[name="robots"]')[0].get("content") == "noindex"

    assert "Some title" in html
    assert 'href="https://example.com/a"' in html
    assert "Summary text." in html
    assert "It matters a lot." in html
    assert "Other Outlet" in html
    assert "Austria" in html
    assert "passed" in html
    assert "German overview." in html

    assert (tmp_path / "site" / "styles.css").exists()
    assert (tmp_path / "site" / "index.html").exists()


def test_render_site_escapes_untrusted_text(tmp_path: Path) -> None:
    sc = make_scored("a")
    sc.cluster.lead.title = "<script>alert(1)</script>"
    digest = build_digest(date(2026, 10, 6), [sc], summaries={}, overviews={})

    page_path = render_site(digest, site_dir=tmp_path / "site")
    html = page_path.read_text()

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_render_site_builds_archive_across_multiple_runs(tmp_path: Path) -> None:
    site_dir = tmp_path / "site"
    day1 = build_digest(date(2026, 10, 5), [make_scored("a")], {}, {})
    day2 = build_digest(date(2026, 10, 6), [make_scored("b")], {}, {})

    render_site(day1, site_dir=site_dir)
    render_site(day2, site_dir=site_dir)

    index_html = (site_dir / "index.html").read_text()
    assert "2026-10-05" in index_html
    assert "2026-10-06" in index_html
    assert 'href="digest/2026-10-06.html"' in index_html  # latest


def test_render_site_page_stays_under_200kb_even_on_a_cold_start_backlog(
    tmp_path: Path,
) -> None:
    # Worst case: every group fully kept (8/8/5, matching settings.yaml) with
    # real-length summaries, plus a huge dropped backlog per group (as a
    # first-ever run with an empty state/seen.json produces).
    scored = []
    summaries = {}
    long_text = " ".join(["word"] * 60)
    for group, kept_n, dropped_n in [
        (Group.TECH, 8, 500),
        (Group.GEOPOLITICS, 8, 700),
        (Group.SCIENCE, 5, 600),
    ]:
        for i in range(kept_n):
            cid = f"{group.value}-kept-{i}"
            sc = make_scored(cid, group=group, kept=True, final_score=10.0 - i * 0.1)
            scored.append(sc)
            summaries[cid] = Summary(
                cluster_id=cid, text=long_text, why_it_matters=long_text, language="en"
            )
        for i in range(dropped_n):
            scored.append(
                make_scored(
                    f"{group.value}-dropped-{i}", group=group, kept=False, final_score=float(i)
                )
            )

    digest = build_digest(date(2026, 10, 6), scored, summaries, overviews={})
    page_path = render_site(digest, site_dir=tmp_path / "site")

    size_kb = page_path.stat().st_size / 1024
    assert size_kb < 200, f"page was {size_kb:.0f} KB, over the 200 KB budget"


def test_render_site_shows_empty_state_for_a_group_with_no_kept_items(tmp_path: Path) -> None:
    sc = make_scored("a", group=Group.TECH)
    digest = build_digest(date(2026, 10, 6), [sc], {}, {})

    page_path = render_site(digest, site_dir=tmp_path / "site")
    html = page_path.read_text()

    assert "No stories today." in html  # geopolitics and science are empty
