"""Render the static GitHub Pages site from the canonical parquet.

Outputs (under the ``--out`` directory)::

    index.html
    event/<slug>.html               one page per event
    static/                         vendored CSS/JS + custom stylesheet
    data/alltime_athletics.parquet  copied straight through
    data/manifest.json              copied straight through
    data/events/<slug>.json         derived from the parquet at render time

Per-event JSON is generated here (not committed) to keep the repo small —
the ~80 MB of JSON would otherwise grow with every weekly data refresh.

For each event we also pre-compute a small ``meta`` block that powers the
template's summary card, section chips, and WR-progression chart — see
``_compute_event_meta``. Doing the analysis at build time keeps the
client-side JS dumb (it just renders) and means the heavy ``mark_value``
sort runs once, not per page-view.

Use::

    uv run python -m alltimeathletics.site --out site/
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import fire
import polars as pl
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from alltimeathletics.events import EVENTS
from alltimeathletics.flags import flag_emoji_map, ioc_to_emoji

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "templates"
STATIC_SRC = REPO_ROOT / "static"
DATA_SRC = REPO_ROOT / "data"
PARQUET_NAME = "alltime_athletics.parquet"

# Field/combined events sort high-to-low; everything else low-to-high.
_DESC_FAMILIES = frozenset({"field_distance", "field_distance_wind", "combined_points"})

_TRACK_FAMILIES = frozenset({"track_time", "track_time_wind"})
_FIELD_FAMILIES = frozenset({"field_distance", "field_distance_wind"})


def _track_distance_m(label: str) -> float:
    """Approximate distance (in metres) parsed from a track/relay event label.

    Used purely to order events on the homepage from short to long. Returns
    a best-effort number; the exact value doesn't matter as long as it
    compares correctly against other track distances.
    """
    s = label.lower()
    if "marathon" in s and "half" not in s:
        return 42195.0
    if "half-marathon" in s:
        return 21097.5
    if "one hour" in s:
        # One hour run lives in the long-distance band; placing it just
        # past the half-marathon keeps it next to similarly-paced events.
        return 21000.0
    if m := re.search(r"(\d+(?:\.\d+)?)\s*km", s):
        return float(m.group(1)) * 1000.0
    if m := re.search(r"(\d+(?:\.\d+)?)\s*miles?\b", s):
        return float(m.group(1)) * 1609.344
    if m := re.search(r"(\d+(?:\.\d+)?)\s*yards?\b", s):
        return float(m.group(1)) * 0.9144
    if m := re.search(r"(\d+)\s*(?:metres|m\b)", s):
        return float(m.group(1))
    return 0.0


def _event_sort_key(ev: Any) -> tuple[int, float, str]:
    """Order homepage events: track (by distance) → field → combined → relay."""
    if ev.family in _TRACK_FAMILIES:
        return (0, _track_distance_m(ev.label), ev.label)
    if ev.family in _FIELD_FAMILIES:
        # Field jumps/throws — keep them grouped, ordered by label for stability.
        return (1, 0.0, ev.label)
    if ev.family == "combined_points":
        return (2, 0.0, ev.label)
    # Relays last, ordered by leg distance (4x100 → 4x1500 → 4xMile).
    return (3, _track_distance_m(ev.label), ev.label)


def _hash_static_assets(static_dir: Path) -> str:
    """Short content hash of the CSS so the cache key flips when CSS changes.

    Tabulator + js bundles are vendored and rarely change; only the
    handwritten stylesheet really matters for cache invalidation, so we
    only hash that. Returns 8 hex chars — enough to avoid collisions in
    practice without bloating every link href.
    """
    css = (static_dir / "style.css").read_bytes()
    return hashlib.sha256(css).hexdigest()[:8]


def _build_example_queries() -> list[dict[str, str]]:
    """Pre-canned SQL examples for the playground dropdown.

    First entry is the default that loads on page open. Queries assume
    the ``perf`` view (the parquet aliased) and use the legal / All-time
    section idiom defined above.
    """
    return [
        {
            "group": "Records",
            "title": "Current world records (latest first)",
            "sql": (
                "-- One row per event: the current world record, latest broken on top.\n"
                "SELECT event, sex, mark_raw, name, country, venue, date\n"
                "FROM perf\n"
                "WHERE rank = 1\n"
                "  AND legality = 'legal'\n"
                "  AND family <> 'relay'\n"
                "  AND section LIKE 'All-time%'\n"
                "ORDER BY date DESC;"
            ),
        },
        {
            "group": "Records",
            "title": "Longest-standing world records",
            "sql": (
                "-- WRs that have stood the longest, oldest first.\n"
                "SELECT event, sex, mark_raw, name, country, venue, date,\n"
                "       DATE_DIFF('year', date, CURRENT_DATE) AS years_old\n"
                "FROM perf\n"
                "WHERE rank = 1\n"
                "  AND legality = 'legal'\n"
                "  AND family <> 'relay'\n"
                "  AND section LIKE 'All-time%'\n"
                "ORDER BY date ASC\n"
                "LIMIT 25;"
            ),
        },
        {
            "group": "Records",
            "title": "World records set in 2024 or later",
            "sql": (
                "-- The most recent crop of WRs.\n"
                "SELECT event, sex, mark_raw, name, country, venue, date\n"
                "FROM perf\n"
                "WHERE rank = 1\n"
                "  AND legality = 'legal'\n"
                "  AND section LIKE 'All-time%'\n"
                "  AND date >= DATE '2024-01-01'\n"
                "ORDER BY date DESC;"
            ),
        },
        {
            "group": "Records",
            "title": "World records by decade set",
            "sql": (
                "-- How many of today's WRs were set in each decade?\n"
                "SELECT (EXTRACT(year FROM date) / 10)::INT * 10 AS decade,\n"
                "       COUNT(*) AS wrs_still_standing\n"
                "FROM perf\n"
                "WHERE rank = 1\n"
                "  AND legality = 'legal'\n"
                "  AND family <> 'relay'\n"
                "  AND section LIKE 'All-time%'\n"
                "GROUP BY decade\n"
                "ORDER BY decade;"
            ),
        },
        {
            "group": "Records",
            "title": "Athletes holding multiple current WRs",
            "sql": (
                "-- Anyone whose name shows up at the top of more than one event.\n"
                "SELECT name, sex, country,\n"
                "       COUNT(*) AS records,\n"
                "       STRING_AGG(event, ', ' ORDER BY event) AS events\n"
                "FROM perf\n"
                "WHERE rank = 1\n"
                "  AND legality = 'legal'\n"
                "  AND family <> 'relay'\n"
                "  AND section LIKE 'All-time%'\n"
                "GROUP BY name, sex, country\n"
                "HAVING COUNT(*) > 1\n"
                "ORDER BY records DESC, name;"
            ),
        },
        {
            "group": "Records",
            "title": "Countries with the most current WRs",
            "sql": (
                "-- Which countries hold the most world records right now?\n"
                "SELECT country, COUNT(*) AS world_records\n"
                "FROM perf\n"
                "WHERE rank = 1\n"
                "  AND legality = 'legal'\n"
                "  AND family <> 'relay'\n"
                "  AND section LIKE 'All-time%'\n"
                "GROUP BY country\n"
                "ORDER BY world_records DESC, country;"
            ),
        },
        {
            "group": "Men vs women",
            "title": "Men vs women WR gap, per event",
            "sql": (
                "-- Relative percentage gap between the men's and women's WR.\n"
                "WITH wr AS (\n"
                "  SELECT event, sex, mark_value, mark_raw\n"
                "  FROM perf\n"
                "  WHERE rank = 1 AND legality = 'legal'\n"
                "    AND family <> 'relay'\n"
                "    AND section LIKE 'All-time%'\n"
                ")\n"
                "SELECT m.event,\n"
                "       m.mark_raw AS men,\n"
                "       w.mark_raw AS women,\n"
                "       ROUND(100.0 * ABS(m.mark_value - w.mark_value) /\n"
                "             GREATEST(m.mark_value, w.mark_value), 2) AS gap_pct\n"
                "FROM wr m\n"
                "JOIN wr w USING (event)\n"
                "WHERE m.sex = 'men' AND w.sex = 'women'\n"
                "ORDER BY gap_pct DESC;"
            ),
        },
        {
            "group": "Men vs women",
            "title": "Closest #1 vs #2 in each event",
            "sql": (
                "-- Most-contested events: tightest margin between the two best ever.\n"
                "WITH t AS (\n"
                "  SELECT event, sex, rank, mark_value, mark_raw, name\n"
                "  FROM perf\n"
                "  WHERE rank IN (1, 2)\n"
                "    AND legality = 'legal'\n"
                "    AND family <> 'relay'\n"
                "    AND section LIKE 'All-time%'\n"
                ")\n"
                "SELECT a.event, a.sex,\n"
                "       a.mark_raw AS top1, a.name AS top1_name,\n"
                "       b.mark_raw AS top2, b.name AS top2_name,\n"
                "       ROUND(100.0 * ABS(a.mark_value - b.mark_value) /\n"
                "             GREATEST(a.mark_value, b.mark_value), 3) AS gap_pct\n"
                "FROM t a\n"
                "JOIN t b USING (event, sex)\n"
                "WHERE a.rank = 1 AND b.rank = 2\n"
                "ORDER BY gap_pct ASC\n"
                "LIMIT 25;"
            ),
        },
        {
            "group": "Athletes",
            "title": "Most versatile athletes (top-50 across events)",
            "sql": (
                "-- Athletes ranked top-50 in the most distinct events.\n"
                "SELECT name, country, sex,\n"
                "       COUNT(DISTINCT event) AS events_in_top50,\n"
                "       STRING_AGG(DISTINCT event, ', ' ORDER BY event) AS events\n"
                "FROM perf\n"
                "WHERE rank <= 50\n"
                "  AND legality = 'legal'\n"
                "  AND family <> 'relay'\n"
                "  AND section LIKE 'All-time%'\n"
                "GROUP BY name, country, sex\n"
                "ORDER BY events_in_top50 DESC, name\n"
                "LIMIT 30;"
            ),
        },
        {
            "group": "Athletes",
            "title": "Most all-time top-10 marks (any event)",
            "sql": (
                "-- Who shows up most in all-time top-10s? Big number = era of dominance.\n"
                "SELECT name, country,\n"
                "       COUNT(*) AS top10_marks,\n"
                "       COUNT(DISTINCT event) AS in_events\n"
                "FROM perf\n"
                "WHERE rank <= 10\n"
                "  AND legality = 'legal'\n"
                "  AND family <> 'relay'\n"
                "  AND section LIKE 'All-time%'\n"
                "GROUP BY name, country\n"
                "ORDER BY top10_marks DESC, name\n"
                "LIMIT 25;"
            ),
        },
        {
            "group": "Athletes",
            "title": "Youngest athletes to set a current WR",
            "sql": (
                "-- Age at the moment they set the still-standing WR.\n"
                "SELECT name, sex, event, mark_raw, dob, date,\n"
                "       ROUND((date - dob) / 365.25, 2) AS age_at_record\n"
                "FROM perf\n"
                "WHERE rank = 1\n"
                "  AND legality = 'legal'\n"
                "  AND family <> 'relay'\n"
                "  AND section LIKE 'All-time%'\n"
                "  AND dob IS NOT NULL\n"
                "ORDER BY age_at_record ASC\n"
                "LIMIT 15;"
            ),
        },
        {
            "group": "Athletes",
            "title": "Oldest athletes to set a current WR",
            "sql": (
                "-- The other end of the curve.\n"
                "SELECT name, sex, event, mark_raw, dob, date,\n"
                "       ROUND((date - dob) / 365.25, 2) AS age_at_record\n"
                "FROM perf\n"
                "WHERE rank = 1\n"
                "  AND legality = 'legal'\n"
                "  AND family <> 'relay'\n"
                "  AND section LIKE 'All-time%'\n"
                "  AND dob IS NOT NULL\n"
                "ORDER BY age_at_record DESC\n"
                "LIMIT 15;"
            ),
        },
        {
            "group": "Events",
            "title": "Biggest #1 vs #2 gap, men only",
            "sql": (
                "-- Most untouchable men's records: how far ahead is #1?\n"
                "WITH t AS (\n"
                "  SELECT event, rank, mark_value, mark_raw, name\n"
                "  FROM perf\n"
                "  WHERE sex = 'men'\n"
                "    AND rank IN (1, 2)\n"
                "    AND legality = 'legal'\n"
                "    AND family <> 'relay'\n"
                "    AND section LIKE 'All-time%'\n"
                ")\n"
                "SELECT a.event,\n"
                "       a.mark_raw AS top1, a.name AS top1_name,\n"
                "       b.mark_raw AS top2, b.name AS top2_name,\n"
                "       ROUND(100.0 * ABS(a.mark_value - b.mark_value) /\n"
                "             GREATEST(a.mark_value, b.mark_value), 2) AS gap_pct\n"
                "FROM t a JOIN t b USING (event)\n"
                "WHERE a.rank = 1 AND b.rank = 2\n"
                "ORDER BY gap_pct DESC;"
            ),
        },
        {
            "group": "Events",
            "title": "Sub-10s 100m runs by year (men)",
            "sql": (
                "-- The pace of 100m progress: how many sub-10 runs per calendar year?\n"
                "SELECT EXTRACT(year FROM date)::INT AS year,\n"
                "       COUNT(*) AS sub10_runs\n"
                "FROM perf\n"
                "WHERE event_slug = 'm_100ok'\n"
                "  AND mark_value < 10.00\n"
                "  AND legality = 'legal'\n"
                "  AND section LIKE 'All-time%'\n"
                "GROUP BY year\n"
                "ORDER BY year;"
            ),
        },
        {
            "group": "Events",
            "title": "Sub-2:05 marathons (men)",
            "sql": (
                "-- All sub-2:05 men's marathons in the all-time list.\n"
                "SELECT name, country, mark_raw, venue, date\n"
                "FROM perf\n"
                "WHERE event_slug = 'mmaraok'\n"
                "  AND mark_value < 7500   -- 2:05:00 in seconds\n"
                "  AND legality = 'legal'\n"
                "  AND section LIKE 'All-time%'\n"
                "ORDER BY mark_value ASC;"
            ),
        },
        {
            "group": "Events",
            "title": "Country dominance: men's marathon top 100",
            "sql": (
                "-- Which nations own the men's marathon all-time top 100?\n"
                "SELECT country, COUNT(*) AS top100_marks\n"
                "FROM perf\n"
                "WHERE event_slug = 'mmaraok'\n"
                "  AND rank <= 100\n"
                "  AND legality = 'legal'\n"
                "  AND section LIKE 'All-time%'\n"
                "GROUP BY country\n"
                "ORDER BY top100_marks DESC;"
            ),
        },
        {
            "group": "Geography",
            "title": "Top countries across all-time top-100 lists",
            "sql": (
                "-- Sum of all-time top-100 entries across every event.\n"
                "SELECT country, COUNT(*) AS top100_entries\n"
                "FROM perf\n"
                "WHERE rank <= 100\n"
                "  AND legality = 'legal'\n"
                "  AND family <> 'relay'\n"
                "  AND section LIKE 'All-time%'\n"
                "GROUP BY country\n"
                "ORDER BY top100_entries DESC\n"
                "LIMIT 30;"
            ),
        },
        {
            "group": "Geography",
            "title": "Venues where the most current WRs were set",
            "sql": (
                "-- Where do records get broken? Cities with the most WRs still on the books.\n"
                "SELECT venue, COUNT(*) AS records_set\n"
                "FROM perf\n"
                "WHERE rank = 1\n"
                "  AND legality = 'legal'\n"
                "  AND family <> 'relay'\n"
                "  AND section LIKE 'All-time%'\n"
                "  AND venue IS NOT NULL\n"
                "GROUP BY venue\n"
                "ORDER BY records_set DESC, venue\n"
                "LIMIT 25;"
            ),
        },
        {
            "group": "Combined",
            "title": "Decathlon all-time top 25 (men)",
            "sql": (
                "-- All-time best decathlon scores.\n"
                "SELECT rank, mark_raw AS points, name, country, venue, date\n"
                "FROM perf\n"
                "WHERE event_slug = 'mdecaok'\n"
                "  AND legality = 'legal'\n"
                "  AND section LIKE 'All-time%'\n"
                "ORDER BY rank ASC\n"
                "LIMIT 25;"
            ),
        },
        {
            "group": "Combined",
            "title": "Heptathlon all-time top 25 (women)",
            "sql": (
                "-- All-time best heptathlon scores.\n"
                "SELECT rank, mark_raw AS points, name, country, venue, date\n"
                "FROM perf\n"
                "WHERE event_slug = 'whepaok'\n"
                "  AND legality = 'legal'\n"
                "  AND section LIKE 'All-time%'\n"
                "ORDER BY rank ASC\n"
                "LIMIT 25;"
            ),
        },
    ]


def render(*, out: str = "site", site_root: str = "/") -> None:
    """Render the static site into ``out`` directory."""
    out_dir = Path(out)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    # Copy static assets; copy parquet + manifest into data/ but build per-event
    # JSON from the parquet (don't try to copy data/events — it isn't checked in).
    shutil.copytree(STATIC_SRC, out_dir / "static")
    out_data = out_dir / "data"
    out_data.mkdir()
    shutil.copy2(DATA_SRC / PARQUET_NAME, out_data / PARQUET_NAME)
    shutil.copy2(DATA_SRC / "manifest.json", out_data / "manifest.json")

    df = pl.read_parquet(out_data / PARQUET_NAME)

    # Pre-compute athlete slugs once and attach to the dataframe — both the
    # per-event JSON (for in-table name links) and the per-athlete pages
    # need them, and we want one canonical mapping. Relays don't get
    # athlete pages (the "name" is a team like "USA"); their slug is "".
    df = df.with_columns(_athlete_slug_expr().alias("athlete_slug"))

    # Per-event meta (sections, WR progression) — used by both the per-event
    # JSON (for client-side rendering) and the template (for server-rendered
    # headlines that show before Tabulator boots).
    event_meta: dict[str, dict[str, Any]] = {
        slug: _compute_event_meta(df, slug) for slug in df["event_slug"].unique().to_list()
    }
    _write_per_event_json(df, out_data / "events", event_meta)

    manifest = json.loads((DATA_SRC / "manifest.json").read_text())

    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=True,
        undefined=StrictUndefined,
    )

    # Cache-bust the stylesheet on its content hash, not on scraped_at.
    # Otherwise CSS changes shipped between data refreshes never invalidate
    # in users' browsers (and locally during development).
    static_version = _hash_static_assets(STATIC_SRC)

    common: dict[str, Any] = {
        "scraped_at": manifest["scraped_at"][:10],
        "built_at": datetime.now(UTC).date().isoformat(),
        "site_root": site_root,
        "static_root": f"{site_root}static/",
        "static_version": static_version,
    }

    counts = {e["slug"]: e["n_rows"] for e in manifest["events"]}
    events_by_sex: dict[str, list[Any]] = defaultdict(list)
    for ev in EVENTS:
        if counts.get(ev.slug, 0) > 0:
            events_by_sex[ev.sex].append(ev)
    # Order the homepage list short-track → long-track → field → combined → relay
    # rather than relying on the catalogue's authoring order.
    for sex in events_by_sex:
        events_by_sex[sex].sort(key=_event_sort_key)

    parquet_bytes = (out_data / PARQUET_NAME).stat().st_size

    # Index page — surface the most recently-dated performances across the
    # whole catalogue so the homepage shows what's new (item #11 redux:
    # users care more about "what got added this week" than "the latest WR
    # which might be years old"). Larsson doesn't tag rows with an
    # added-at timestamp, so the most recent perf ``date`` is the cleanest
    # proxy for "fresh entries".
    recent_additions = _recent_additions(df, ev_by_slug={ev.slug: ev for ev in EVENTS}, n=5)

    flags_json = json.dumps(flag_emoji_map(), separators=(",", ":"))

    parquet_size_mb_str = f"{parquet_bytes / (1024 * 1024):.1f}"

    (out_dir / "index.html").write_text(
        env.get_template("index.html").render(
            **common,
            active_tab="events",
            events_by_sex=events_by_sex,
            counts=counts,
            n_rows_total=manifest["n_rows"],
            n_events_total=manifest["n_events"],
            recent_additions=recent_additions,
            flag=ioc_to_emoji,
        )
    )

    # SQL playground — single page, lazy-loads DuckDB-WASM + the parquet
    # only when the user clicks Run on this page. Examples are grouped by
    # theme so first-time visitors see useful starting points; the first
    # entry is the default that loads on page open. Filter idioms used
    # across queries:
    #
    # - rank = 1                  → top of each sub-list
    # - section LIKE 'All-time%'  → keep only the canonical sub-list per
    #                               event (skips altitude / indoor / etc.)
    # - legality = 'legal'        → drops wind-aided / drug-annulled lists
    # - family != 'relay'         → individual events only (relays have
    #                               team names instead of an athlete)
    example_queries = _build_example_queries()
    (out_dir / "sql.html").write_text(
        env.get_template("sql.html").render(
            **common,
            active_tab="sql",
            example_queries=example_queries,
            parquet_size_mb=parquet_size_mb_str,
        )
    )

    # Download page — single source of truth for "where do I get the data".
    # Parquet + manifest sizes come from disk; CSV variants are produced by
    # the release workflow on a runner and only ever live on the GitHub
    # Release (not in this repo), so we surface their typical sizes.
    manifest_kb = (out_data / "manifest.json").stat().st_size / 1024
    release_base = "https://github.com/thomascamminady/alltimeathletics/releases/latest/download"
    download_files = [
        {
            "name": "alltime_athletics.parquet",
            "url": f"{release_base}/alltime_athletics.parquet",
            "size": f"{parquet_size_mb_str} MB",
            "desc": "Full dataset, columnar — best for polars / pandas / DuckDB.",
        },
        {
            "name": "alltime_athletics.csv",
            "url": f"{release_base}/alltime_athletics.csv",
            "size": "~70 MB",
            "desc": "Full dataset as plain CSV — works anywhere, large.",
        },
        {
            "name": "alltime_athletics.csv.gz",
            "url": f"{release_base}/alltime_athletics.csv.gz",
            "size": "~10 MB",
            "desc": "gzip-compressed CSV — same content, smaller download.",
        },
        {
            "name": "manifest.json",
            "url": f"{release_base}/manifest.json",
            "size": f"{manifest_kb:.0f} KB",
            "desc": "Per-event row counts and parser diagnostics.",
        },
    ]
    (out_dir / "download.html").write_text(
        env.get_template("download.html").render(
            **common,
            active_tab="download",
            n_rows_total=manifest["n_rows"],
            n_events_total=manifest["n_events"],
            files=download_files,
        )
    )

    (out_dir / "about.html").write_text(
        env.get_template("about.html").render(**common, active_tab="about")
    )

    # Event-slug → label, used on the athlete page so we can show "100m"
    # next to a row instead of just its slug.
    event_labels = {ev.slug: ev.label for ev in EVENTS}
    event_sex = {ev.slug: ev.sex for ev in EVENTS}
    event_family = {ev.slug: ev.family for ev in EVENTS}
    event_descending = {ev.slug: ev.family in _DESC_FAMILIES for ev in EVENTS}
    wr_values: dict[str, float] = {
        slug: float(meta["wr_progression"][-1]["mark_value"])
        for slug, meta in event_meta.items()
        if meta.get("wr_progression")
    }

    # Per-event pages
    event_dir = out_dir / "event"
    event_dir.mkdir()
    template = env.get_template("event.html")
    for ev in EVENTS:
        n = counts.get(ev.slug, 0)
        if n == 0:
            continue
        meta = event_meta[ev.slug]
        (event_dir / f"{ev.slug}.html").write_text(
            template.render(
                **common,
                active_tab="events",
                event=ev,
                family=ev.family,
                row_count=n,
                meta=meta,
                event_meta_json=json.dumps(
                    {"slug": ev.slug, "label": ev.label, "family": ev.family}
                ),
                flag=ioc_to_emoji,
                flags_json=flags_json,
            )
        )

    # Per-event analytics pages — the visualisation hub. Each event gets
    # its own page with WR progression, best-of-year line, entries-per-year
    # bars, mark-vs-age scatter, and a stats panel.
    analytics_dir = out_dir / "analytics"
    analytics_dir.mkdir()
    analytics_template = env.get_template("analytics.html")
    for ev in EVENTS:
        n = counts.get(ev.slug, 0)
        if n == 0:
            continue
        meta = event_meta[ev.slug]
        analytics = _compute_event_analytics(df, ev.slug, meta)
        (analytics_dir / f"{ev.slug}.html").write_text(
            analytics_template.render(
                **common,
                active_tab="events",
                event=ev,
                family=ev.family,
                row_count=n,
                meta=meta,
                analytics=analytics,
                year_bars_svg=_render_year_bars_svg(analytics["entries_per_year"]),
                flag=ioc_to_emoji,
                flags_json=flags_json,
            )
        )

    # Per-athlete pages — one HTML per (name, country, dob) so the name
    # column in event tables can link to a complete career view.
    n_athletes, athlete_index_records = _render_athlete_pages(
        df,
        out_dir / "athlete",
        common=common,
        event_labels=event_labels,
        event_sex=event_sex,
        event_family=event_family,
        event_descending=event_descending,
        wr_values=wr_values,
        flags_json=flags_json,
    )

    # Athlete index — JSON sidecar + HTML page.
    athlete_dir = out_dir / "athlete"
    (athlete_dir / "index.json").write_text(
        json.dumps(athlete_index_records, separators=(",", ":"), default=str)
    )
    (athlete_dir / "index.html").write_text(
        env.get_template("athlete_index.html").render(
            **common,
            active_tab="athletes",
            n_athletes=n_athletes,
            flags_json=flags_json,
        )
    )

    n_pages = sum(len(events_by_sex[s]) for s in ("men", "women", "mixed"))
    print(
        f"Rendered {n_pages} event pages, {n_pages} analytics pages, "
        f"{n_athletes} athlete pages, and 1 athlete index to {out_dir} "
        f"({manifest['n_rows']:,} performances)"
    )


# ---------------------------------------------------------------- athletes --


def _compute_athlete_analytics(
    all_entries: list[dict[str, Any]],
    event_labels: Mapping[str, str],
    event_family: Mapping[str, str],
    event_descending: Mapping[str, bool],
) -> dict[str, Any]:
    """Derive summary stats for one athlete's analytics panel."""
    if not all_entries:
        return {}
    slug_counts: Counter[str] = Counter(e["event_slug"] for e in all_entries)
    primary_slug = slug_counts.most_common(1)[0][0]
    primary_entries = [e for e in all_entries if e["event_slug"] == primary_slug]
    family = event_family.get(primary_slug, "track_time")
    descending = event_descending.get(primary_slug, False)

    years = [int(e["date"][:4]) for e in all_entries if e.get("date")]
    career_span = (min(years), max(years)) if years else None

    ranks = [e["rank"] for e in all_entries if e.get("rank") is not None]
    best_rank = min(ranks) if ranks else None

    valued = [e for e in primary_entries if e.get("mark_value") is not None]
    best_entry: dict[str, Any] | None = None
    if valued:
        best_entry = (max if descending else min)(valued, key=lambda e: e["mark_value"])

    return {
        "primary_slug": primary_slug,
        "primary_label": event_labels.get(primary_slug, primary_slug),
        "family": family,
        "descending": descending,
        "career_span": career_span,
        "best_rank": best_rank,
        "best_entry": best_entry,
        "n_primary_entries": len(primary_entries),
        "n_events": len(slug_counts),
    }


def _athlete_slug(name: str | None, country: str | None, dob: date | None) -> str:
    """Stable slug for an athlete. Empty for relays/unknowns.

    Identity is ``(name, country, dob)``. We accent-fold the name and keep
    only ``[a-z0-9-]``; the country and dob suffixes disambiguate athletes
    who share the same display name. When ``dob`` is missing (a chunk of
    the older entries don't have one) we use ``"u"`` as a placeholder.
    Two distinct athletes with identical ``(name, country, "u")`` will
    collide — accept it, it's vanishingly rare and the page still shows
    every entry.
    """
    if not name:
        return ""
    base = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-zA-Z0-9]+", "-", base).strip("-").lower()
    if not base:
        return ""
    parts = [base, (country or "xxx").lower()]
    parts.append(dob.strftime("%Y%m%d") if dob else "u")
    return "-".join(parts)


def _athlete_slug_expr() -> pl.Expr:
    """Polars expression that materialises ``athlete_slug`` per row.

    Implemented via ``map_elements`` because the slug requires Unicode
    folding + regex, which polars can't do natively. Relays return ``""``.
    """

    def _slug(row: dict[str, Any]) -> str:
        if row["family"] == "relay":
            return ""
        return _athlete_slug(row["name"], row["country"], row["dob"])

    return pl.struct(["family", "name", "country", "dob"]).map_elements(_slug, return_dtype=pl.Utf8)


# ---- per-process worker state (initialised once per worker by _athlete_worker_init) ----

_W_EVENT_LABELS: dict[str, str] = {}
_W_EVENT_FAMILY: dict[str, str] = {}
_W_EVENT_DESCENDING: dict[str, bool] = {}
_W_WR_VALUES: dict[str, float] = {}
_W_COMMON: dict[str, Any] = {}
_W_FLAGS_JSON: str = ""
_W_OUT_DIR: str = ""
_W_TEMPLATE: Any = None
_W_FLAG_FN: Any = None


def _athlete_worker_init(
    event_labels: dict[str, str],
    event_family: dict[str, str],
    event_descending: dict[str, bool],
    wr_values: dict[str, float],
    template_dir: str,
    common: dict[str, Any],
    flags_json: str,
    out_dir: str,
) -> None:
    global _W_EVENT_LABELS, _W_EVENT_FAMILY, _W_EVENT_DESCENDING, _W_WR_VALUES
    global _W_COMMON, _W_FLAGS_JSON, _W_OUT_DIR, _W_TEMPLATE, _W_FLAG_FN
    from jinja2 import Environment, FileSystemLoader, StrictUndefined  # noqa: PLC0415

    from alltimeathletics.flags import ioc_to_emoji  # noqa: PLC0415

    _W_EVENT_LABELS = event_labels
    _W_EVENT_FAMILY = event_family
    _W_EVENT_DESCENDING = event_descending
    _W_WR_VALUES = wr_values
    _W_COMMON = common
    _W_FLAGS_JSON = flags_json
    _W_OUT_DIR = out_dir
    _W_FLAG_FN = ioc_to_emoji
    _W_TEMPLATE = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=True,
        undefined=StrictUndefined,
    ).get_template("athlete.html")


def _athlete_worker_task(
    task: tuple[str, dict[str, Any], list[dict[str, Any]], int, str],
) -> dict[str, Any]:
    """Render one athlete page and return its index record.

    Runs inside a worker process; reads only from module-level worker globals
    set by ``_athlete_worker_init``.  Writes the HTML file directly so we
    avoid pickling the rendered string back to the main process.
    """
    slug, meta, entries, n_events, sexes_label = task
    entries.sort(
        key=lambda e: (e["date"] or "0000-00-00", e["event_label"]),
        reverse=True,
    )
    for i, e in enumerate(entries):
        e["idx"] = i
    analytics = _compute_athlete_analytics(
        entries, _W_EVENT_LABELS, _W_EVENT_FAMILY, _W_EVENT_DESCENDING
    )
    _athlete_slugs = {e["event_slug"] for e in entries}
    wr_json = json.dumps(
        {s: v for s, v in _W_WR_VALUES.items() if s in _athlete_slugs},
        separators=(",", ":"),
    )
    event_family_json = json.dumps(
        {s: f for s, f in _W_EVENT_FAMILY.items() if s in _athlete_slugs},
        separators=(",", ":"),
    )
    event_descending_json = json.dumps(
        {s: d for s, d in _W_EVENT_DESCENDING.items() if s in _athlete_slugs},
        separators=(",", ":"),
    )
    athlete = {
        **meta,
        "n_entries": len(entries),
        "n_events": n_events,
        "sexes_label": sexes_label,
    }
    html = _W_TEMPLATE.render(
        **_W_COMMON,
        active_tab="athletes",
        athlete=athlete,
        entries_json=json.dumps(entries, separators=(",", ":")),
        analytics=analytics,
        wr_json=wr_json,
        event_family_json=event_family_json,
        event_descending_json=event_descending_json,
        flags_json=_W_FLAGS_JSON,
        flag=_W_FLAG_FN,
    )
    Path(_W_OUT_DIR, f"{slug}.html").write_text(html)
    best = analytics.get("best_entry") or {}
    cs = analytics.get("career_span")
    return {
        "slug": slug,
        "name": meta["name"],
        "country": meta["country"],
        "dob": meta["dob"],
        "primary_label": analytics.get("primary_label"),
        "best_mark_raw": best.get("mark_raw"),
        "best_mark_value": best.get("mark_value"),
        "best_rank": analytics.get("best_rank"),
        "career_start": cs[0] if cs else None,
        "career_end": cs[1] if cs else None,
        "n_events": analytics.get("n_events", 1),
        "n_entries": len(entries),
    }


def _render_athlete_pages(
    df: pl.DataFrame,
    out_dir: Path,
    *,
    common: dict[str, Any],
    event_labels: Mapping[str, str],
    event_sex: Mapping[str, str],
    event_family: Mapping[str, str],
    event_descending: Mapping[str, bool],
    wr_values: Mapping[str, float],
    flags_json: str,
) -> tuple[int, list[dict[str, Any]]]:
    """Render one HTML per athlete with all their entries inlined.

    Per-athlete data is small (median 4 rows, p99 ~150) so we inline it
    into the HTML rather than spawning a 29k-file JSON sidecar. The
    template wires the inline JSON into Tabulator the same way the event
    page does, so sorting/filtering still feels identical.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    athletes_df = df.filter(pl.col("athlete_slug") != "")
    rows = athletes_df.select(
        "athlete_slug",
        "name",
        "country",
        "dob",
        "event_slug",
        "section",
        "rank",
        "mark_raw",
        "mark_value",
        "mark_annotation",
        "wind",
        "venue",
        "date",
        "position",
    ).to_dicts()

    entries_by_slug: dict[str, list[dict[str, Any]]] = {}
    events_by_slug: dict[str, set[str]] = {}
    sexes_by_slug: dict[str, set[str]] = {}
    meta_by_slug: dict[str, dict[str, Any]] = {}
    for r in rows:
        slug = r["athlete_slug"]
        if slug not in meta_by_slug:
            meta_by_slug[slug] = {
                "slug": slug,
                "name": r["name"],
                "country": r["country"],
                "dob": str(r["dob"]) if r["dob"] is not None else None,
            }
            entries_by_slug[slug] = []
            events_by_slug[slug] = set()
            sexes_by_slug[slug] = set()
        events_by_slug[slug].add(r["event_slug"])
        sexes_by_slug[slug].add(event_sex.get(r["event_slug"], "?"))
        entries_by_slug[slug].append(
            {
                "event_slug": r["event_slug"],
                "event_label": event_labels.get(r["event_slug"], r["event_slug"]),
                "section": r["section"],
                "rank": r["rank"],
                "mark_raw": r["mark_raw"],
                "mark_value": r["mark_value"],
                "wind": r["wind"],
                "venue": r["venue"],
                "date": str(r["date"]) if r["date"] is not None else None,
                "position": r["position"],
            }
        )

    tasks = [
        (
            slug,
            meta_by_slug[slug],
            entries_by_slug[slug],
            len(events_by_slug[slug]),
            ", ".join(sorted(sexes_by_slug[slug])),
        )
        for slug in meta_by_slug
    ]
    init_args = (
        dict(event_labels),
        dict(event_family),
        dict(event_descending),
        dict(wr_values),
        str(TEMPLATE_DIR),
        common,
        flags_json,
        str(out_dir),
    )
    n_workers = os.cpu_count() or 4
    chunksize = max(1, len(tasks) // (n_workers * 8))
    with ProcessPoolExecutor(
        max_workers=n_workers,
        initializer=_athlete_worker_init,
        initargs=init_args,
    ) as pool:
        index_records = list(pool.map(_athlete_worker_task, tasks, chunksize=chunksize))

    return len(meta_by_slug), index_records


# ---------------------------------------------------------------- analytics --


def _compute_event_analytics(df: pl.DataFrame, slug: str, meta: dict[str, Any]) -> dict[str, Any]:
    """Pre-compute every analytics-page input from the canonical sub-list.

    All charts pivot on the canonical (rank-1) section to keep the picture
    consistent — sub-lists like "altitude marks" or "manual timing" would
    otherwise distort the year-by-year and depth views.
    """
    main_section = meta["main_section"]
    descending = meta["descending"]

    if main_section is None:
        return {
            "best_per_year": [],
            "entries_per_year": [],
            "age_scatter": [],
            "summary": {},
        }

    canonical = df.filter(
        (pl.col("event_slug") == slug)
        & (pl.col("section") == main_section)
        & pl.col("mark_value").is_not_null()
        & pl.col("date").is_not_null()
        # Drop "*" (later-DQ) marks from year-over-year and stats — keeping
        # them would let stripped marks distort the depth picture.
        & (pl.col("mark_annotation").is_null() | (pl.col("mark_annotation") != "*"))
    )

    # ---- best-of-year line ---------------------------------------
    # For each year, the single best mark (min for time, max for field).
    best_agg = (pl.col("mark_value").max() if descending else pl.col("mark_value").min()).alias(
        "best"
    )
    by_year = (
        canonical.with_columns(pl.col("date").dt.year().alias("year"))
        .group_by("year")
        .agg(best_agg)
        .sort("year")
    )
    # Attach the holder's display info per year (first match for ties).
    holders = canonical.with_columns(pl.col("date").dt.year().alias("year")).join(
        by_year, left_on=["year", "mark_value"], right_on=["year", "best"], how="inner"
    )
    holders = holders.unique(subset=["year"], keep="first").sort("year")
    best_per_year: list[dict[str, Any]] = [
        {
            "year": int(r["year"]),
            "mark_value": r["mark_value"],
            "mark_raw": r["mark_raw"],
            "name": r["name"],
            "country": r["country"],
            "venue": r["venue"],
            "date": str(r["date"]),
        }
        for r in holders.to_dicts()
    ]

    # ---- entries-per-year bars -----------------------------------
    counts_by_year = (
        canonical.with_columns(pl.col("date").dt.year().alias("year"))
        .group_by("year")
        .len()
        .sort("year")
    )
    entries_per_year: list[dict[str, Any]] = [
        {"year": int(r["year"]), "count": int(r["len"])} for r in counts_by_year.to_dicts()
    ]

    # ---- mark-vs-age scatter -------------------------------------
    # Cap at the top N performances per event — past that the dots just
    # overlap and inflate page weight without adding signal. The cap
    # keeps the analytics page under ~150 KB even for the deepest lists
    # (some events have 5k+ marks).
    SCATTER_CAP = 800
    age_scatter_df = (
        canonical.filter(pl.col("dob").is_not_null())
        .sort("mark_value", descending=descending)
        .head(SCATTER_CAP)
        .with_columns(((pl.col("date") - pl.col("dob")).dt.total_days() / 365.25).alias("age"))
    )
    age_scatter: list[dict[str, Any]] = [
        {
            "age": round(float(r["age"]), 2),
            "mark_value": r["mark_value"],
            "mark_raw": r["mark_raw"],
            "name": r["name"],
            "date": str(r["date"]),
        }
        for r in age_scatter_df.select("age", "mark_value", "mark_raw", "name", "date").to_dicts()
        if 12.0 <= r["age"] <= 65.0  # filter obvious dob/date typos
    ]

    # ---- summary stats panel -------------------------------------
    summary: dict[str, Any] = {}
    sorted_main = canonical.sort("mark_value", descending=descending)
    if not sorted_main.is_empty():
        top = sorted_main.row(0, named=True)
        summary["top_name"] = top["name"]
        summary["top_country"] = top["country"]
        summary["top_mark_raw"] = top["mark_raw"]
        summary["top_date"] = str(top["date"])
        summary["top_venue"] = top["venue"]
    if sorted_main.height >= 10:
        tenth = sorted_main.row(9, named=True)
        summary["tenth_mark_raw"] = tenth["mark_raw"]
        gap = tenth["mark_value"] - sorted_main["mark_value"][0]
        summary["tenth_gap"] = -gap if descending else gap
    if sorted_main.height >= 100:
        hundredth = sorted_main.row(99, named=True)
        summary["hundredth_mark_raw"] = hundredth["mark_raw"]
        gap = hundredth["mark_value"] - sorted_main["mark_value"][0]
        summary["hundredth_gap"] = -gap if descending else gap
    # Median age top 100
    if age_scatter_df.height > 0:
        top100 = sorted_main.head(100).filter(pl.col("dob").is_not_null())
        if not top100.is_empty():
            ages = top100.with_columns(
                ((pl.col("date") - pl.col("dob")).dt.total_days() / 365.25).alias("age")
            )["age"]
            median = ages.median()
            if median is not None:
                summary["median_age_top100"] = round(float(str(median)), 1)
    # Distinct athletes on the canonical list
    summary["n_athletes"] = canonical.select(["name", "country", "dob"]).unique().height
    # First & last year the event was contested at top-N level
    if not by_year.is_empty():
        summary["first_year"] = int(str(by_year["year"].min() or 0))
        summary["last_year"] = int(str(by_year["year"].max() or 0))
    summary["n_canonical"] = canonical.height

    # ---- top countries (top 100) -------------------------------
    # Compact "national depth" indicator: how many athletes from each
    # country appear in the all-time top 100. Tells you at a glance which
    # nations dominate this event. Empty list when the canonical list has
    # <10 entries (then "top 100" is meaningless).
    top_countries: list[dict[str, Any]] = []
    if sorted_main.height >= 10:
        n_pool = min(100, sorted_main.height)
        country_counts = (
            sorted_main.head(n_pool)
            .filter(pl.col("country").is_not_null())
            .group_by("country")
            .len()
            .rename({"len": "count"})
            .sort("count", descending=True)
        )
        top_countries = [
            {
                "country": r["country"],
                "count": int(r["count"]),
                "share": int(r["count"]) / n_pool,
            }
            for r in country_counts.head(8).to_dicts()
        ]

    # ---- decade leaders ----------------------------------------
    # Best canonical mark per decade with its holder. Strong narrative
    # for "how the event evolved generation-by-generation".
    decade_leaders: list[dict[str, Any]] = []
    if not canonical.is_empty():
        decade_df = (
            canonical.with_columns(((pl.col("date").dt.year() // 10) * 10).alias("decade"))
            .sort("mark_value", descending=descending)
            .unique(subset=["decade"], keep="first")
            .sort("decade")
        )
        decade_leaders = [
            {
                "decade": int(r["decade"]),
                "mark_raw": r["mark_raw"],
                "name": r["name"],
                "country": r["country"],
                "athlete_slug": r.get("athlete_slug"),
                "date": str(r["date"]),
                "venue": r["venue"],
            }
            for r in decade_df.to_dicts()
        ]

    # ---- most prolific athletes (canonical top 100) ------------
    # How many top-100 marks each athlete owns. Skip relays — the
    # "name" column is a national team there, not an athlete page.
    top_athletes: list[dict[str, Any]] = []
    if sorted_main.height >= 10 and meta.get("family") != "relay":
        n_pool = min(100, sorted_main.height)
        pool = sorted_main.head(n_pool).filter(
            pl.col("name").is_not_null() & (pl.col("athlete_slug") != "")
        )
        if not pool.is_empty():
            athlete_counts = (
                pool.group_by(["name", "country", "athlete_slug"])
                .len()
                .rename({"len": "count"})
                .filter(pl.col("count") > 1)
                .sort("count", descending=True)
                .head(8)
            )
            if not athlete_counts.is_empty():
                max_count = int(athlete_counts["count"][0])
                top_athletes = [
                    {
                        "name": r["name"],
                        "country": r["country"],
                        "athlete_slug": r["athlete_slug"],
                        "count": int(r["count"]),
                        "share": int(r["count"]) / max_count,
                    }
                    for r in athlete_counts.to_dicts()
                ]

    return {
        "best_per_year": best_per_year,
        "entries_per_year": entries_per_year,
        "age_scatter": age_scatter,
        "top_countries": top_countries,
        "decade_leaders": decade_leaders,
        "top_athletes": top_athletes,
        "summary": summary,
    }


# ---------------------------------------------------------------- charts (svg) --


def _scale(v: float, vmin: float, vmax: float, lo: float, hi: float) -> float:
    """Linear scale ``v`` from ``[vmin, vmax]`` into ``[lo, hi]``."""
    if vmax == vmin:
        return (lo + hi) / 2
    return lo + (hi - lo) * (v - vmin) / (vmax - vmin)


def _decade_ticks(year_min: int, year_max: int) -> list[int]:
    """Decade-aligned tick positions inside ``[year_min, year_max]``."""
    lo = ((year_min + 9) // 10) * 10
    hi = (year_max // 10) * 10
    return list(range(lo, hi + 1, 10))


def _render_year_bars_svg(points: list[dict[str, Any]]) -> str:
    """Entries-per-year vertical bars."""
    if len(points) < 2:
        return ""
    W, H = 560, 160
    M_L, M_R, M_T, M_B = 40, 12, 12, 28
    plot_left, plot_right = M_L, W - M_R
    plot_top, plot_bot = M_T, H - M_B

    years = [p["year"] for p in points]
    counts = [p["count"] for p in points]
    x_min, x_max = min(years), max(years)
    c_max = max(counts)
    if x_min == x_max or c_max == 0:
        return ""

    def sx(yr: float) -> float:
        return _scale(yr, x_min, x_max, plot_left, plot_right)

    def sy(c: float) -> float:
        return _scale(c, 0, c_max, plot_bot, plot_top)

    span = x_max - x_min + 1
    # Bars sit centered on each year; width is slightly less than the slot.
    slot_w = (plot_right - plot_left) / max(span, 1)
    bar_w = max(slot_w * 0.85, 1.0)

    bars = "".join(
        '<rect class="bar" x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" '
        'height="{h:.2f}"><title>{tip}</title></rect>'.format(
            x=sx(p["year"]) - bar_w / 2,
            y=sy(p["count"]),
            w=bar_w,
            h=plot_bot - sy(p["count"]),
            tip=f"{p['year']}: {p['count']} performance{'s' if p['count'] != 1 else ''}",
        )
        for p in points
    )

    # Decade x-axis labels
    decade_years = _decade_ticks(int(x_min), int(x_max))
    label_x = "".join(
        f'<text x="{sx(yr):.1f}" y="{H - 8}" text-anchor="middle" class="ax-grid">{yr}</text>'
        for yr in decade_years
    )
    x_first = (
        f'<text x="{sx(x_min):.1f}" y="{H - 8}" text-anchor="middle" class="ax">{x_min}</text>'
    )
    x_last = f'<text x="{sx(x_max):.1f}" y="{H - 8}" text-anchor="middle" class="ax">{x_max}</text>'
    # Y-axis: just min (0) and max
    label_y = (
        f'<text x="{plot_left - 4}" y="{sy(0):.1f}" dy="4" '
        f'text-anchor="end" class="ax">0</text>'
        f'<text x="{plot_left - 4}" y="{sy(c_max):.1f}" dy="4" '
        f'text-anchor="end" class="ax">{c_max}</text>'
    )
    box = (
        f'<line class="grid grid-axis" x1="{plot_left}" y1="{plot_bot}" '
        f'x2="{plot_right}" y2="{plot_bot}" />'
    )
    return (
        f'<svg viewBox="0 0 {W} {H}" class="wr-chart" preserveAspectRatio="xMidYMid meet" '
        f'role="img" aria-label="Entries per year, {x_min}-{x_max}">'
        f"{box}{bars}{label_x}{label_y}{x_first}{x_last}"
        "</svg>"
    )


# ---------------------------------------------------------------- meta --


def _compute_event_meta(df: pl.DataFrame, slug: str) -> dict[str, Any]:
    """Pre-compute the data the event-page template + JS need.

    Returns a dict with:

    - ``main_section``: name of the rank-1 section (the canonical list)
    - ``sections``: ``[{name, n}]`` ordered most-rows first
    - ``wr_progression``: list of rows that were a WR at some point in
      the canonical list (chronological order)
    - ``wr_indices``: parquet-row indices flagged as WRs (for the
      "Show WRs only" button)
    - ``descending``: True if higher mark = better (field events)
    - ``n_wrs``: number of historical WRs in the canonical list
    """
    sub = df.filter(pl.col("event_slug") == slug)
    family = sub["family"][0] if not sub.is_empty() else "track_time"
    descending = family in _DESC_FAMILIES

    # Pick the "main" section as the one containing rank=1 — robust against
    # Larsson's varying section labels ("All-time men's best 100m" vs
    # "main list" vs "Main list"). All other sections are sub-lists.
    rank1 = sub.filter(pl.col("rank") == 1)
    main_section = rank1["section"][0] if not rank1.is_empty() else None

    # Section catalogue (ordered most-populated first; main section pinned first).
    sec_counts = sub.group_by("section").len().rename({"len": "n"}).sort("n", descending=True)
    sections = [{"name": row["section"], "n": int(row["n"])} for row in sec_counts.to_dicts()]
    if main_section is not None:
        sections.sort(key=lambda s: 0 if s["name"] == main_section else 1)

    # WR progression on the main section, excluding "*" (later DQ).
    wr_rows: list[dict[str, Any]] = []
    if main_section is not None:
        main = sub.filter(
            (pl.col("section") == main_section)
            & pl.col("mark_value").is_not_null()
            & pl.col("date").is_not_null()
            & (pl.col("mark_annotation").is_null() | (pl.col("mark_annotation") != "*"))
        ).sort("date")
        if not main.is_empty():
            cum = pl.col("mark_value").cum_max() if descending else pl.col("mark_value").cum_min()
            running = main.with_columns(cum.alias("_best"))
            # Strict improvement: keep first occurrence of each new running
            # best. For tied marks (e.g. Bolt 9.69 ×2) only the first is a WR;
            # later equals are not progressions. ``unique(keep="first")``
            # preserves doc order because we sorted by date above.
            wrs = running.filter(pl.col("mark_value") == pl.col("_best")).unique(
                subset=["_best"], keep="first"
            )
            wr_rows = [
                {
                    "date": str(r["date"]),
                    "mark_value": r["mark_value"],
                    "mark_raw": r["mark_raw"],
                    "name": r["name"],
                    "country": r["country"],
                    "venue": r["venue"],
                }
                for r in wrs.sort("date").to_dicts()
            ]

    return {
        "main_section": main_section,
        "sections": sections,
        "wr_progression": wr_rows,
        "descending": descending,
        "family": family,
        "n_wrs": len(wr_rows),
    }


def _recent_additions(
    df: pl.DataFrame,
    ev_by_slug: dict[str, Any],
    n: int = 12,
) -> list[dict[str, Any]]:
    """Most recent meets the database picked up — one row per (date, venue).

    Larsson doesn't tag rows with an "added at" timestamp or with a meet
    name, so we proxy "recent meets" by the most recent ``date + venue``
    pairs in the canonical sub-lists. That gives a clean "what was new
    this week" panel like alltime-athletics.com's homepage shows.

    The ``ev_by_slug`` argument is unused here but kept in the signature
    so the call site doesn't have to change as we evolve the panel.
    """
    del ev_by_slug
    main_per_slug = (
        df.filter(pl.col("rank") == 1)
        .group_by("event_slug")
        .agg(pl.col("section").first().alias("main_section"))
    )
    canonical = df.join(main_per_slug, on="event_slug", how="inner").filter(
        pl.col("section") == pl.col("main_section")
    )
    meets = (
        canonical.filter(pl.col("date").is_not_null() & pl.col("venue").is_not_null())
        .group_by(["date", "venue"])
        .agg(
            pl.len().alias("n_perfs"),
            pl.col("event_slug").n_unique().alias("n_events"),
        )
        .sort("date", descending=True)
        .head(n)
    )
    return [
        {
            "date": str(m["date"]),
            "venue": m["venue"],
            "n_perfs": int(m["n_perfs"]),
            "n_events": int(m["n_events"]),
        }
        for m in meets.to_dicts()
    ]


# ---------------------------------------------------------------- json --


def _write_per_event_json(
    df: pl.DataFrame,
    out_dir: Path,
    event_meta: dict[str, dict[str, Any]],
) -> None:
    """One JSON file per event slug for the static frontend.

    Drops fields that are constant across every row (event/event_slug/sex/
    legality/family) and adds two derived fields:

    - ``is_main``: True if this row is in the canonical (rank-1) section
    - ``is_wr``: True if this row was a world record at some point

    ``source_url`` and ``source_line`` are dropped from the per-event JSON
    too — neither is consumed by the frontend table, and ``source_line``
    in particular (the raw scraped Larsson line) is the bulkiest field
    per row, so trimming both meaningfully shrinks the payload Tabulator
    has to download. They remain available in the canonical parquet.

    Tabulator's ajaxURL fetches these directly. The two booleans are what
    the section-chip filter and the "WRs only" toggle pivot on.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    drop_cols = [
        "event",
        "event_slug",
        "sex",
        "legality",
        "family",
        "source_url",
        "source_line",
    ]
    for slug in df.select("event_slug").unique().to_series().to_list():
        meta = event_meta[slug]
        wr_keys = {(w["name"], w["date"], w["mark_raw"]) for w in meta["wr_progression"]}
        main_section = meta["main_section"]
        sub = (
            df.filter(pl.col("event_slug") == slug)
            .drop(drop_cols)
            .with_columns(
                pl.col("dob").cast(pl.Utf8),
                pl.col("date").cast(pl.Utf8),
            )
        )
        records = sub.to_dicts()
        for r in records:
            r["is_main"] = r["section"] == main_section
            r["is_wr"] = (r["name"], r["date"], r["mark_raw"]) in wr_keys
        (out_dir / f"{slug}.json").write_text(
            json.dumps(records, separators=(",", ":"), default=str)
        )


def main() -> None:
    fire.Fire(render)


if __name__ == "__main__":
    main()
