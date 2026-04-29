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
import shutil
from collections import defaultdict
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

    # Per-event meta (summary card, sections, WR progression) — used by both
    # the per-event JSON (for client-side rendering) and the template (for
    # server-rendered headlines that show before Tabulator boots).
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
        df, ev_by_slug={ev.slug: ev for ev in EVENTS}, n=10
    )

    flags_json = json.dumps(flag_emoji_map(), separators=(",", ":"))

    (out_dir / "index.html").write_text(
        env.get_template("index.html").render(
            **common,
            events_by_sex=events_by_sex,
            counts=counts,
            n_rows_total=manifest["n_rows"],
            n_events_total=manifest["n_events"],
            parquet_size_mb=f"{parquet_bytes / (1024 * 1024):.1f}",
            recent_additions=recent_additions,
            flag=ioc_to_emoji,
        )
    )

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
                wr_chart_svg=_render_wr_chart_svg(meta["wr_progression"], ev.family),
                flag=ioc_to_emoji,
                flags_json=flags_json,
            )
        )

    n_pages = sum(len(events_by_sex[s]) for s in ("men", "women", "mixed"))
    print(f"Rendered {n_pages} event pages to {out_dir}")


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
    - ``summary``: rank-1 holder + mark + date + venue + 10th-place gap
      + median age of top-100
    - ``descending``: True if higher mark = better (field events)
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

    # Summary card numbers.
    summary: dict[str, Any] = {}
    if main_section is not None:
        main_clean = sub.filter(
            (pl.col("section") == main_section) & pl.col("mark_value").is_not_null()
        ).sort("mark_value", descending=descending)
        if not main_clean.is_empty():
            top = main_clean.row(0, named=True)
            summary["top_name"] = top["name"]
            summary["top_country"] = top["country"]
            summary["top_mark_raw"] = top["mark_raw"]
            summary["top_mark_value"] = top["mark_value"]
            summary["top_date"] = str(top["date"]) if top["date"] is not None else None
            summary["top_venue"] = top["venue"]
        if main_clean.height >= 10:
            tenth = main_clean.row(9, named=True)
            summary["tenth_mark_raw"] = tenth["mark_raw"]
            summary["tenth_gap"] = (
                tenth["mark_value"] - main_clean["mark_value"][0]
                if not descending
                else main_clean["mark_value"][0] - tenth["mark_value"]
            )
        # Median age of athletes in the top 100 of the main section.
        ages = (
            main_clean.head(100)
            .filter(pl.col("dob").is_not_null() & pl.col("date").is_not_null())
            .with_columns(
                ((pl.col("date") - pl.col("dob")).dt.total_days() / 365.25).alias("age")
            )
        )
        if not ages.is_empty():
            # ``ages["age"]`` is a Float64 series — median always returns a
            # float — but ty can't narrow Series.median() from its general
            # union return type. Round-trip through str to satisfy the checker.
            median = ages["age"].median()
            if median is not None:
                summary["median_age_top100"] = round(float(str(median)), 1)

    return {
        "main_section": main_section,
        "sections": sections,
        "wr_progression": wr_rows,
        "summary": summary,
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


def _render_wr_chart_svg(wrs: list[dict[str, Any]], family: str) -> str:
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
    - Each dot carries a ``data-wr`` attribute consumed by the page's JS
      to render a styled tooltip on hover. The native ``<title>`` element
      is *also* present so screen readers + non-JS users still get the
      details.
    """
    # ``family`` no longer flips the y-axis (both families share the
    # natural convention) but it still drives intermediate Y-tick formatting
    # (track times → ``mm:ss``, field marks → metres with one decimal).
    family_for_format = family
    if len(wrs) < 2:
        return ""
    # Coordinates (px) inside a 560x180 viewport, with margins for labels
    # on the left and below.
    W, H = 560, 180
    M_L, M_R, M_T, M_B = 48, 12, 14, 28
    xs = [date.fromisoformat(w["date"]).toordinal() for w in wrs]
    ys = [w["mark_value"] for w in wrs]
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
        f'text-anchor="end" class="ax-grid">{_format_y_tick(yv, family_for_format)}</text>'
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

    # Dots with styled-tooltip data + a native <title> as fallback.
    dots = "".join(
        '<circle class="wr-dot" cx="{x:.1f}" cy="{y:.1f}" r="4" '
        'data-wr="{data}" tabindex="0">'
        '<title>{tip}</title>'
        '</circle>'.format(
            x=sx(xs[i]),
            y=sy(ys[i]),
            data=json.dumps(wrs[i]).replace('"', "&quot;"),
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
        f'text-anchor="end" class="ax">{_y_axis_label(wrs, ys, y_min)}</text>'
        f'<text x="{plot_left - 4}" y="{sy(y_max):.1f}" dy="4" '
        f'text-anchor="end" class="ax">{_y_axis_label(wrs, ys, y_max)}</text>'
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
