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
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fire
import polars as pl
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from alltimeathletics.analytics import (
    _DESC_FAMILIES,
    _compute_event_analytics,
    _compute_event_meta,
    _recent_additions,
)
from alltimeathletics.athletes import _athlete_slug_expr, _render_athlete_pages
from alltimeathletics.charts import _render_year_bars_svg
from alltimeathletics.events import EVENTS
from alltimeathletics.flags import flag_emoji_map, ioc_to_emoji
from alltimeathletics.paths import DATA_SRC, PARQUET_NAME, REPO_ROOT, STATIC_SRC, TEMPLATE_DIR
from alltimeathletics.sql_examples import _build_example_queries

# Field/combined events sort high-to-low; everything else low-to-high.

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


def _git_commit_hash() -> str | None:
    """Short SHA of the commit being built, or ``None`` if unavailable.

    Tries the GitHub Actions ``GITHUB_SHA`` env var first (always present
    in CI, doesn't require git to be installed), then falls back to
    ``git rev-parse HEAD`` for local builds. Returns 7 hex chars to match
    GitHub's default short-SHA convention.
    """
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha[:7]
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return result.stdout.strip()[:7]
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None


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

    # Skip "career" pages for athletes with fewer than this many entries.
    # A one-entry career page is just a single row already visible in the
    # event table the user came from — strictly worse than the link they
    # clicked. Empty the slug for filtered athletes so:
    #   - per-event JSON: the name renders as plain text (no broken link)
    #   - athlete-page renderer: no task is generated
    #   - athlete index: the row never gets emitted
    # Single source of truth, downstream code stays unchanged.
    MIN_ATHLETE_ENTRIES = 2
    df = df.with_columns(
        pl.when(pl.col("athlete_slug").len().over("athlete_slug") >= MIN_ATHLETE_ENTRIES)
        .then(pl.col("athlete_slug"))
        .otherwise(pl.lit(""))
        .alias("athlete_slug")
    )

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
        "built_commit": _git_commit_hash(),
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
    # Canonical (rank-1) section per slug. Used by the athlete page so
    # ``% of WR`` is only computed for rows in the canonical sub-list —
    # comparing, say, an indoor mile against the outdoor WR is apples-to-
    # oranges and was the bug this map closes.
    main_section_by_slug: dict[str, str] = {
        slug: meta["main_section"] for slug, meta in event_meta.items() if meta.get("main_section")
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
        main_section_by_slug=main_section_by_slug,
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
