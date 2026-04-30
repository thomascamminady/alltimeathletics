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

import json
import re
import shutil
import unicodedata
from collections import defaultdict
from collections.abc import Mapping
from datetime import date
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
_DESC_FAMILIES = frozenset(
    {"field_distance", "field_distance_wind", "combined_points"}
)


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
        slug: _compute_event_meta(df, slug)
        for slug in df["event_slug"].unique().to_list()
    }
    _write_per_event_json(df, out_data / "events", event_meta)

    manifest = json.loads((DATA_SRC / "manifest.json").read_text())

    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=True,
        undefined=StrictUndefined,
    )

    common: dict[str, Any] = {
        "scraped_at": manifest["scraped_at"][:10],
        "site_root": site_root,
        "static_root": f"{site_root}static/",
    }

    counts = {e["slug"]: e["n_rows"] for e in manifest["events"]}
    events_by_sex: dict[str, list[Any]] = defaultdict(list)
    for ev in EVENTS:
        if counts.get(ev.slug, 0) > 0:
            events_by_sex[ev.sex].append(ev)

    parquet_bytes = (out_data / PARQUET_NAME).stat().st_size

    # Index page — surface the most recently-dated performances across the
    # whole catalogue so the homepage shows what's new (item #11 redux:
    # users care more about "what got added this week" than "the latest WR
    # which might be years old"). Larsson doesn't tag rows with an
    # added-at timestamp, so the most recent perf ``date`` is the cleanest
    # proxy for "fresh entries".
    recent_additions = _recent_additions(
        df, ev_by_slug={ev.slug: ev for ev in EVENTS}, n=5
    )

    flags_json = json.dumps(flag_emoji_map(), separators=(",", ":"))

    parquet_size_mb_str = f"{parquet_bytes / (1024 * 1024):.1f}"

    (out_dir / "index.html").write_text(
        env.get_template("index.html").render(
            **common,
            events_by_sex=events_by_sex,
            counts=counts,
            n_rows_total=manifest["n_rows"],
            n_events_total=manifest["n_events"],
            parquet_size_mb=parquet_size_mb_str,
            recent_additions=recent_additions,
            flag=ioc_to_emoji,
        )
    )

    # SQL playground — single page, lazy-loads DuckDB-WASM + the parquet
    # only when the user clicks Run on this page. The pre-filled query is
    # deliberately short so first-time visitors see something useful in
    # one read: the current world record per event, sorted by how recently
    # it was set. Filters keep the answer to one row per event:
    #
    # - rank = 1                  → top of each sub-list
    # - section LIKE 'All-time%'  → keep only the canonical sub-list per
    #                               event (skips altitude / indoor / etc.)
    # - legality = 'legal'        → drops wind-aided / drug-annulled lists
    # - family != 'relay'         → individual events only (relays have
    #                               team names instead of an athlete)
    example_query = (
        "-- Current world records by event, freshest on top.\n"
        "-- Each row is the all-time #1 mark for that event,\n"
        "-- sorted by the date it was set.\n"
        "SELECT event, sex, mark_raw, name, country, venue, date\n"
        "FROM perf\n"
        "WHERE rank = 1\n"
        "  AND legality = 'legal'\n"
        "  AND family <> 'relay'\n"
        "  AND section LIKE 'All-time%'\n"
        "ORDER BY date DESC;"
    )
    (out_dir / "sql.html").write_text(
        env.get_template("sql.html").render(
            **common,
            example_query=example_query,
            parquet_size_mb=parquet_size_mb_str,
        )
    )

    # Event-slug → label, used on the athlete page so we can show "100m"
    # next to a row instead of just its slug.
    event_labels = {ev.slug: ev.label for ev in EVENTS}
    event_sex = {ev.slug: ev.sex for ev in EVENTS}

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
                event=ev,
                family=ev.family,
                row_count=n,
                meta=meta,
                analytics=analytics,
                wr_chart_svg_absolute=_render_wr_chart_svg(
                    meta["wr_progression"], ev.family, mode="absolute"
                ),
                wr_chart_svg_delta=_render_wr_chart_svg(
                    meta["wr_progression"], ev.family, mode="delta"
                ),
                wr_chart_svg_percent=_render_wr_chart_svg(
                    meta["wr_progression"], ev.family, mode="percent"
                ),
                year_line_svg=_render_year_line_svg(
                    analytics["best_per_year"], ev.family, meta["descending"]
                ),
                year_bars_svg=_render_year_bars_svg(analytics["entries_per_year"]),
                age_scatter_svg=_render_age_scatter_svg(
                    analytics["age_scatter"], ev.family, meta["descending"]
                ),
                flag=ioc_to_emoji,
                flags_json=flags_json,
            )
        )

    # Per-athlete pages — one HTML per (name, country, dob) so the name
    # column in event tables can link to a complete career view.
    n_athletes = _render_athlete_pages(
        df,
        out_dir / "athlete",
        env=env,
        common=common,
        event_labels=event_labels,
        event_sex=event_sex,
        flags_json=flags_json,
    )

    n_pages = sum(len(events_by_sex[s]) for s in ("men", "women", "mixed"))
    print(
        f"Rendered {n_pages} event pages, {n_pages} analytics pages, "
        f"and {n_athletes} athlete pages to {out_dir}"
    )


# ---------------------------------------------------------------- athletes --


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

    return pl.struct(["family", "name", "country", "dob"]).map_elements(
        _slug, return_dtype=pl.Utf8
    )


def _render_athlete_pages(
    df: pl.DataFrame,
    out_dir: Path,
    *,
    env: Environment,
    common: dict[str, Any],
    event_labels: Mapping[str, str],
    event_sex: Mapping[str, str],
    flags_json: str,
) -> int:
    """Render one HTML per athlete with all their entries inlined.

    Per-athlete data is small (median 4 rows, p99 ~150) so we inline it
    into the HTML rather than spawning a 29k-file JSON sidecar. The
    template wires the inline JSON into Tabulator the same way the event
    page does, so sorting/filtering still feels identical.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    template = env.get_template("athlete.html")

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

    for slug, meta in meta_by_slug.items():
        entries = entries_by_slug[slug]
        # Newest first by default; the table is sortable so this is just
        # the initial view.
        entries.sort(
            key=lambda e: (e["date"] or "0000-00-00", e["event_label"]),
            reverse=True,
        )
        athlete = {
            **meta,
            "n_entries": len(entries),
            "n_events": len(events_by_slug[slug]),
            "sexes_label": ", ".join(sorted(sexes_by_slug[slug])),
        }
        (out_dir / f"{slug}.html").write_text(
            template.render(
                **common,
                athlete=athlete,
                entries_json=json.dumps(entries, separators=(",", ":")),
                flags_json=flags_json,
                flag=ioc_to_emoji,
            )
        )

    return len(meta_by_slug)


# ---------------------------------------------------------------- analytics --


def _compute_event_analytics(
    df: pl.DataFrame, slug: str, meta: dict[str, Any]
) -> dict[str, Any]:
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
    best_agg = (
        pl.col("mark_value").max() if descending else pl.col("mark_value").min()
    ).alias("best")
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
        {"year": int(r["year"]), "count": int(r["len"])}
        for r in counts_by_year.to_dicts()
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
        .with_columns(
            ((pl.col("date") - pl.col("dob")).dt.total_days() / 365.25).alias("age")
        )
    )
    age_scatter: list[dict[str, Any]] = [
        {
            "age": round(float(r["age"]), 2),
            "mark_value": r["mark_value"],
            "mark_raw": r["mark_raw"],
            "name": r["name"],
            "date": str(r["date"]),
        }
        for r in age_scatter_df.select(
            "age", "mark_value", "mark_raw", "name", "date"
        ).to_dicts()
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
    summary["n_athletes"] = (
        canonical.select(["name", "country", "dob"]).unique().height
    )
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

    return {
        "best_per_year": best_per_year,
        "entries_per_year": entries_per_year,
        "age_scatter": age_scatter,
        "top_countries": top_countries,
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


def _render_year_line_svg(
    points: list[dict[str, Any]], family: str, descending: bool
) -> str:
    """Best-mark-per-year line chart. Returns ``""`` when there's <2 years."""
    if len(points) < 2:
        return ""
    W, H = 560, 200
    M_L, M_R, M_T, M_B = 56, 12, 14, 28
    plot_left, plot_right = M_L, W - M_R
    plot_top, plot_bot = M_T, H - M_B

    years = [p["year"] for p in points]
    ys = [p["mark_value"] for p in points]
    y_min, y_max = min(ys), max(ys)
    if y_max == y_min:
        # Pad a touch so we have something to draw.
        y_max = y_min + 1.0
    x_min, x_max = min(years), max(years)
    if x_min == x_max:
        return ""

    def sx(yr: float) -> float:
        return _scale(yr, x_min, x_max, plot_left, plot_right)

    def sy(v: float) -> float:
        # Lower mark_value sits lower on screen — same convention as WR chart.
        return _scale(v, y_min, y_max, plot_bot, plot_top)

    # Decade gridlines + labels
    decade_years = _decade_ticks(int(x_min), int(x_max))
    grid_x = "".join(
        f'<line class="grid" x1="{sx(yr):.1f}" y1="{plot_top}" '
        f'x2="{sx(yr):.1f}" y2="{plot_bot}" />'
        for yr in decade_years
    )
    label_x = "".join(
        f'<text x="{sx(yr):.1f}" y="{H - 8}" text-anchor="middle" '
        f'class="ax-grid">{yr}</text>'
        for yr in decade_years
        if abs(yr - x_min) >= 4 and abs(yr - x_max) >= 4
    )

    # Quartile Y gridlines + labels
    y_quarters = [y_min + (y_max - y_min) * f for f in (0.25, 0.5, 0.75)]
    grid_y = "".join(
        f'<line class="grid" x1="{plot_left}" y1="{sy(v):.1f}" '
        f'x2="{plot_right}" y2="{sy(v):.1f}" />'
        for v in y_quarters
    )
    label_y = "".join(
        f'<text x="{plot_left - 4}" y="{sy(v):.1f}" dy="4" '
        f'text-anchor="end" class="ax-grid">{_format_y_tick(v, family)}</text>'
        for v in y_quarters
    )

    # Bounding box
    box = (
        f'<line class="grid grid-axis" x1="{plot_left}" y1="{plot_top}" '
        f'x2="{plot_right}" y2="{plot_top}" />'
        f'<line class="grid grid-axis" x1="{plot_left}" y1="{plot_bot}" '
        f'x2="{plot_right}" y2="{plot_bot}" />'
    )

    # Path through points
    pts = " ".join(f"{sx(p['year']):.1f},{sy(p['mark_value']):.1f}" for p in points)

    # Dots with native title hover
    dots = "".join(
        '<circle class="wr-dot" cx="{x:.1f}" cy="{y:.1f}" r="3.2">'
        '<title>{tip}</title></circle>'.format(
            x=sx(p["year"]),
            y=sy(p["mark_value"]),
            tip=f"{p['year']}: {p['mark_raw']} — {p['name']} ({p['country']})",
        )
        for p in points
    )

    # Endpoint y-labels (best of first year, best of last year)
    first = points[0]
    last = points[-1]
    label_extremes = (
        f'<text x="{plot_left - 4}" y="{sy(first["mark_value"]):.1f}" dy="4" '
        f'text-anchor="end" class="ax">{first["mark_raw"]}</text>'
        f'<text x="{plot_left - 4}" y="{sy(last["mark_value"]):.1f}" dy="4" '
        f'text-anchor="end" class="ax">{last["mark_raw"]}</text>'
    )
    # X-axis endpoint labels
    x_first = (
        f'<text x="{sx(x_min):.1f}" y="{H - 8}" text-anchor="middle" '
        f'class="ax">{x_min}</text>'
    )
    x_last = (
        f'<text x="{sx(x_max):.1f}" y="{H - 8}" text-anchor="middle" '
        f'class="ax">{x_max}</text>'
    )
    del descending  # used for y-axis direction; same convention covers both
    return (
        f'<svg viewBox="0 0 {W} {H}" class="wr-chart" preserveAspectRatio="xMidYMid meet" '
        f'role="img" aria-label="Best mark per year, {x_min}-{x_max}">'
        f"{box}{grid_x}{grid_y}{label_x}{label_y}"
        f'<polyline class="wr-line" fill="none" points="{pts}" />'
        f"{dots}{label_extremes}{x_first}{x_last}"
        "</svg>"
    )


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
        f'<text x="{sx(yr):.1f}" y="{H - 8}" text-anchor="middle" '
        f'class="ax-grid">{yr}</text>'
        for yr in decade_years
    )
    x_first = (
        f'<text x="{sx(x_min):.1f}" y="{H - 8}" text-anchor="middle" '
        f'class="ax">{x_min}</text>'
    )
    x_last = (
        f'<text x="{sx(x_max):.1f}" y="{H - 8}" text-anchor="middle" '
        f'class="ax">{x_max}</text>'
    )
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


def _render_age_scatter_svg(
    points: list[dict[str, Any]], family: str, descending: bool
) -> str:
    """Scatter of mark vs athlete age at performance."""
    if len(points) < 5:
        return ""
    W, H = 560, 220
    M_L, M_R, M_T, M_B = 56, 12, 14, 28
    plot_left, plot_right = M_L, W - M_R
    plot_top, plot_bot = M_T, H - M_B

    ages = [p["age"] for p in points]
    ys = [p["mark_value"] for p in points]
    a_min = max(14.0, min(ages) - 1)
    a_max = min(50.0, max(ages) + 1)
    y_min, y_max = min(ys), max(ys)
    # Pad y-range slightly so dots aren't pinned to the axis.
    pad = (y_max - y_min) * 0.05 or 1
    y_min, y_max = y_min - pad, y_max + pad

    def sx(a: float) -> float:
        return _scale(a, a_min, a_max, plot_left, plot_right)

    def sy(v: float) -> float:
        return _scale(v, y_min, y_max, plot_bot, plot_top)

    # Age gridlines every 5 years
    age_ticks = list(range(int(a_min // 5) * 5, int(a_max) + 1, 5))
    age_ticks = [t for t in age_ticks if a_min <= t <= a_max]
    grid_x = "".join(
        f'<line class="grid" x1="{sx(t):.1f}" y1="{plot_top}" '
        f'x2="{sx(t):.1f}" y2="{plot_bot}" />'
        for t in age_ticks
    )
    label_x = "".join(
        f'<text x="{sx(t):.1f}" y="{H - 8}" text-anchor="middle" '
        f'class="ax-grid">{t}</text>'
        for t in age_ticks
    )

    # Y gridlines at quartiles
    y_quarters = [y_min + (y_max - y_min) * f for f in (0.25, 0.5, 0.75)]
    grid_y = "".join(
        f'<line class="grid" x1="{plot_left}" y1="{sy(v):.1f}" '
        f'x2="{plot_right}" y2="{sy(v):.1f}" />'
        for v in y_quarters
    )
    label_y = "".join(
        f'<text x="{plot_left - 4}" y="{sy(v):.1f}" dy="4" '
        f'text-anchor="end" class="ax-grid">{_format_y_tick(v, family)}</text>'
        for v in y_quarters
    )

    box = (
        f'<line class="grid grid-axis" x1="{plot_left}" y1="{plot_top}" '
        f'x2="{plot_right}" y2="{plot_top}" />'
        f'<line class="grid grid-axis" x1="{plot_left}" y1="{plot_bot}" '
        f'x2="{plot_right}" y2="{plot_bot}" />'
    )

    # Translucent dots — overlap is informative.
    dots = "".join(
        '<circle class="scatter-dot" cx="{x:.1f}" cy="{y:.1f}" r="2.4">'
        '<title>{tip}</title></circle>'.format(
            x=sx(p["age"]),
            y=sy(p["mark_value"]),
            tip=(
                f"{p['mark_raw']} @ age {p['age']:.1f} — {p['name']} ({p['date'][:4]})"
            ),
        )
        for p in points
    )

    # Best-mark endpoint label
    extreme = max(ys) if descending else min(ys)
    label_best = (
        f'<text x="{plot_left - 4}" y="{sy(extreme):.1f}" dy="4" '
        f'text-anchor="end" class="ax">{_format_y_tick(extreme, family)}</text>'
    )
    # X-axis label
    x_axis_label = (
        f'<text x="{(plot_left + plot_right) / 2:.1f}" y="{H - 2}" '
        f'text-anchor="middle" class="ax-grid">age (years)</text>'
    )
    return (
        f'<svg viewBox="0 0 {W} {H}" class="wr-chart" preserveAspectRatio="xMidYMid meet" '
        f'role="img" aria-label="Mark vs age scatter ({len(points)} points)">'
        f"{box}{grid_x}{grid_y}{label_x}{label_y}{dots}{label_best}{x_axis_label}"
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
    sec_counts = (
        sub.group_by("section").len().rename({"len": "n"}).sort("n", descending=True)
    )
    sections = [
        {"name": row["section"], "n": int(row["n"])} for row in sec_counts.to_dicts()
    ]
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
            cum = (
                pl.col("mark_value").cum_max()
                if descending
                else pl.col("mark_value").cum_min()
            )
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


# ---------------------------------------------------------------- charts --


def _render_wr_chart_svg(
    wrs: list[dict[str, Any]],
    family: str,
    *,
    mode: str = "absolute",
) -> str:
    """Inline SVG of WR progression — date on X, mark on Y, step plot.

    Empty string when there's <2 WRs to plot. Visual conventions:

    - Y axis is *natural*: lower mark_value sits lower on screen for both
      families. That means time events (lower=better) draw a line that
      *descends* over time as records improve, while field events
      (higher=better) draw a line that ascends. Both feel right because
      the labels alongside the axis tell you which direction is "better".
    - Step path holds horizontally between WRs, then steps to the new mark
      at the date the new WR was set.
    - The last horizontal segment extends to today so the most recent WR
      doesn't visually "end" the moment it was set.
    - Each dot has a native SVG ``<title>`` so hovering reveals the holder.

    ``mode`` controls what the Y values represent:

    - ``"absolute"``  (default): the raw ``mark_value`` (seconds, metres,
      points). Y-axis labels show ``mark_raw`` strings.
    - ``"delta"``: each mark expressed as how-much-worse-than-the-current
      WR. The current WR sits at 0; older WRs are positive (slower /
      shorter / fewer points). Units match the family.
    - ``"percent"``: same as ``delta`` but as a percentage of the current
      WR. Useful for cross-event comparison.
    """
    if len(wrs) < 2:
        return ""
    descending = family in _DESC_FAMILIES
    # The "current" WR is the latest entry — chronologically last.
    current_value = wrs[-1]["mark_value"]
    raw_values = [w["mark_value"] for w in wrs]

    if mode == "absolute":
        ys = raw_values
        # Y-extreme labels: the actual mark_raw text on the worst & best rows.
        extreme_label_fn = lambda v: _y_axis_label(wrs, raw_values, v)  # noqa: E731
        intermediate_fmt = lambda v: _format_y_tick(v, family)  # noqa: E731
    elif mode == "delta":
        # Positive = how much worse than current.
        if descending:
            ys = [current_value - v for v in raw_values]
        else:
            ys = [v - current_value for v in raw_values]

        def _delta_fmt(v: float) -> str:
            if abs(v) < 1e-9:
                return "0"
            return ("+" if v > 0 else "") + _format_y_tick(abs(v), family)

        extreme_label_fn = _delta_fmt
        intermediate_fmt = _delta_fmt
    elif mode == "percent":
        if descending:
            ys = [100 * (current_value - v) / current_value for v in raw_values]
        else:
            ys = [100 * (v - current_value) / current_value for v in raw_values]

        def _pct_fmt(v: float) -> str:
            if abs(v) < 1e-9:
                return "0%"
            return f"{'+' if v > 0 else ''}{v:.2f}%"

        extreme_label_fn = _pct_fmt
        intermediate_fmt = _pct_fmt
    else:
        raise ValueError(f"unknown mode {mode!r}")

    # Coordinates (px) inside a 560x180 viewport, with margins for labels
    # on the left and below.
    W, H = 560, 180
    M_L, M_R, M_T, M_B = 56, 12, 14, 28
    xs = [date.fromisoformat(w["date"]).toordinal() for w in wrs]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    today_ord = date.today().toordinal()
    x_max_chart = max(x_max, today_ord)
    if x_max_chart == x_min or y_max == y_min:
        return ""

    def sx(x: int) -> float:
        return M_L + (W - M_L - M_R) * (x - x_min) / (x_max_chart - x_min)

    def sy(y: float) -> float:
        # Lower mark value → lower y position on screen.
        norm = (y - y_min) / (y_max - y_min)
        return M_T + (H - M_T - M_B) * (1 - norm)

    # Step path: horizontal segment ending at the next WR's date, then a
    # vertical step to that WR's value.
    pts: list[str] = [f"M {sx(xs[0]):.1f},{sy(ys[0]):.1f}"]
    for x, y in list(zip(xs, ys, strict=True))[1:]:
        pts.append(f"H {sx(x):.1f}")
        pts.append(f"V {sy(y):.1f}")
    # Extend last mark out to today so the current WR doesn't appear to end.
    if today_ord > x_max:
        pts.append(f"H {sx(today_ord):.1f}")
    path = " ".join(pts)

    # Plot area
    plot_top, plot_bot = M_T, H - M_B
    plot_left, plot_right = M_L, W - M_R

    # Decade-aligned X gridlines (every 10 years from the first decade boundary
    # at/after x_min, ending at the last boundary at/before x_max_chart).
    first_year_int = date.fromordinal(x_min).year
    last_year_int = date.fromordinal(x_max_chart).year
    decade_lo = ((first_year_int + 9) // 10) * 10  # next decade boundary
    decade_hi = (last_year_int // 10) * 10
    decade_years = list(range(decade_lo, decade_hi + 1, 10))
    x_grid_lines = "".join(
        f'<line class="grid" x1="{sx(date(y, 1, 1).toordinal()):.1f}" '
        f'y1="{plot_top}" x2="{sx(date(y, 1, 1).toordinal()):.1f}" '
        f'y2="{plot_bot}" />'
        for y in decade_years
    )
    x_tick_labels = "".join(
        f'<text x="{sx(date(y, 1, 1).toordinal()):.1f}" y="{H - 8}" '
        f'text-anchor="middle" class="ax-grid">{y}</text>'
        for y in decade_years
        # Skip endpoints — first/last year labels are drawn separately.
        if abs(y - int(wrs[0]["date"][:4])) >= 5
        and abs(y - int(wrs[-1]["date"][:4])) >= 5
    )

    # Y gridlines at quartiles of the value range (skip extremes; those are
    # drawn from the first/last WR labels).
    y_quarters = [y_min + (y_max - y_min) * f for f in (0.25, 0.5, 0.75)]
    y_grid_lines = "".join(
        f'<line class="grid" x1="{plot_left}" y1="{sy(yv):.1f}" '
        f'x2="{plot_right}" y2="{sy(yv):.1f}" />'
        for yv in y_quarters
    )
    y_tick_labels = "".join(
        f'<text x="{plot_left - 4}" y="{sy(yv):.1f}" dy="4" '
        f'text-anchor="end" class="ax-grid">{intermediate_fmt(yv)}</text>'
        for yv in y_quarters
    )

    # Bounding box
    grid_box = (
        f'<line class="grid grid-axis" x1="{plot_left}" y1="{plot_top}" '
        f'x2="{plot_right}" y2="{plot_top}" />'
        f'<line class="grid grid-axis" x1="{plot_left}" y1="{plot_bot}" '
        f'x2="{plot_right}" y2="{plot_bot}" />'
    )
    grid = grid_box + x_grid_lines + y_grid_lines

    # Dots with native <title> hover.
    dots = "".join(
        '<circle class="wr-dot" cx="{x:.1f}" cy="{y:.1f}" r="4" tabindex="0">'
        '<title>{tip}</title>'
        '</circle>'.format(
            x=sx(xs[i]),
            y=sy(ys[i]),
            tip=(
                f"{wrs[i]['mark_raw']} — {wrs[i]['name']} ({wrs[i]['country']}) "
                f"— {wrs[i]['venue']}, {wrs[i]['date']}"
            ),
        )
        for i in range(len(wrs))
    )

    # Y-axis labels: best (extreme) mark + worst (other extreme).
    # Positioning: text is right-aligned to the chart's left margin.
    y_label_extremes = (
        f'<text x="{plot_left - 4}" y="{sy(y_min):.1f}" dy="4" '
        f'text-anchor="end" class="ax">{extreme_label_fn(y_min)}</text>'
        f'<text x="{plot_left - 4}" y="{sy(y_max):.1f}" dy="4" '
        f'text-anchor="end" class="ax">{extreme_label_fn(y_max)}</text>'
    )
    # X-axis labels: first year, last year.
    first_year = wrs[0]["date"][:4]
    last_year = wrs[-1]["date"][:4]
    x_first = (
        f'<text x="{sx(xs[0]):.1f}" y="{H - 8}" text-anchor="middle" '
        f'class="ax">{first_year}</text>'
    )
    x_last = (
        f'<text x="{sx(xs[-1]):.1f}" y="{H - 8}" text-anchor="middle" '
        f'class="ax">{last_year}</text>'
    )
    return (
        f'<svg viewBox="0 0 {W} {H}" class="wr-chart" preserveAspectRatio="xMidYMid meet" '
        f'role="img" '
        f'aria-label="World-record progression: {len(wrs)} marks from '
        f'{first_year} to {last_year}">'
        f'{grid}'
        f'{y_tick_labels}{x_tick_labels}'
        f'<path class="wr-line" d="{path}" fill="none" />'
        f'{dots}'
        f'{y_label_extremes}{x_first}{x_last}'
        "</svg>"
    )


def _y_axis_label(wrs: list[dict[str, Any]], ys: list[float], target: float) -> str:
    """Return the ``mark_raw`` for the row whose ``mark_value`` equals ``target``."""
    for w, y in zip(wrs, ys, strict=True):
        if y == target:
            return w["mark_raw"]
    return f"{target:g}"


def _format_y_tick(value: float, family: str) -> str:
    """Format an intermediate Y-axis tick.

    Track times need to be turned back into ``[h:]mm:ss``; field events
    just want metres with a sensible precision.
    """
    if family in _DESC_FAMILIES:
        # Field/combined: one decimal, suppress trailing zero on integers
        # to keep tick labels short.
        return f"{value:.2f}".rstrip("0").rstrip(".")
    if value < 60:
        return f"{value:.2f}"
    if value < 3600:
        m, s = divmod(value, 60)
        return f"{int(m)}:{s:05.2f}"
    h, rem = divmod(value, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{int(s):02d}"


# ---------------------------------------------------------------- json --


def _write_per_event_json(
    df: pl.DataFrame,
    out_dir: Path,
    event_meta: dict[str, dict[str, Any]],
) -> None:
    """One JSON file per event slug for the static frontend.

    Drops fields that are constant across every row (event/event_slug/sex/
    legality/family/source_url) and adds two derived fields:

    - ``is_main``: True if this row is in the canonical (rank-1) section
    - ``is_wr``: True if this row was a world record at some point

    Tabulator's ajaxURL fetches these directly. The two booleans are what
    the section-chip filter and the "WRs only" toggle pivot on.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    drop_cols = ["event", "event_slug", "sex", "legality", "family", "source_url"]
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
