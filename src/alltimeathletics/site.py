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

    # Index page — also surface the 5 most recent WRs across the whole catalogue
    # to make the homepage feel alive (item #11 on the roadmap).
    recent_wrs = _recent_wrs_across_events(
        event_meta, ev_by_slug={ev.slug: ev for ev in EVENTS}, n=5
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
            recent_wrs=recent_wrs,
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


def _recent_wrs_across_events(
    event_meta: dict[str, dict[str, Any]],
    ev_by_slug: dict[str, Any],
    n: int = 5,
) -> list[dict[str, Any]]:
    """Top ``n`` most-recent WRs across the whole catalogue."""
    rows: list[dict[str, Any]] = []
    for slug, meta in event_meta.items():
        for wr in meta["wr_progression"]:
            ev = ev_by_slug.get(slug)
            if ev is None:
                continue
            rows.append(
                {
                    "slug": slug,
                    "label": ev.label,
                    "sex": ev.sex,
                    "date": wr["date"],
                    "mark_raw": wr["mark_raw"],
                    "name": wr["name"],
                    "country": wr["country"],
                    "venue": wr["venue"],
                }
            )
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows[:n]


# ---------------------------------------------------------------- charts --


def _render_wr_chart_svg(wrs: list[dict[str, Any]], family: str) -> str:
    """Tiny inline SVG of WR progression (date → mark, step plot).

    Empty string when there's <2 WRs to plot. The chart is intentionally
    minimal: no axes labels, no tooltips, no library — just a polyline +
    a couple of reference dots. The big table below has all the details.
    """
    if len(wrs) < 2:
        return ""
    descending = family in _DESC_FAMILIES
    # Coordinates (px) inside a 480x140 viewport, with margin for labels.
    W, H = 480, 140
    M_L, M_R, M_T, M_B = 36, 8, 12, 24
    xs = [date.fromisoformat(w["date"]).toordinal() for w in wrs]
    ys = [w["mark_value"] for w in wrs]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    if x_max == x_min or y_max == y_min:
        return ""

    def sx(x: int) -> float:
        return M_L + (W - M_L - M_R) * (x - x_min) / (x_max - x_min)

    def sy(y: float) -> float:
        # Flip so "better" is up: for time events, lower mark is better,
        # so smaller y → higher pixel.
        norm = (y - y_min) / (y_max - y_min)
        if not descending:
            norm = 1 - norm
        return M_T + (H - M_T - M_B) * (1 - norm)

    # Step path: horizontal until the next WR, then vertical drop.
    pts: list[str] = []
    for i, (x, y) in enumerate(zip(xs, ys, strict=True)):
        if i == 0:
            pts.append(f"M {sx(x):.1f},{sy(y):.1f}")
        else:
            pts.append(f"H {sx(x):.1f}")
            pts.append(f"V {sy(y):.1f}")
    # Extend the last value to today so the latest WR doesn't end mid-chart.
    today_ord = date.today().toordinal()
    if today_ord > x_max:
        pts.append(f"H {sx(today_ord):.1f}")  # off-scale; we'll rescale below
    path = " ".join(pts)

    dots = "".join(
        f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="3" />' for x, y in zip(xs, ys, strict=True)
    )
    # Y-axis labels: first and last mark.
    first_label = f'<text x="0" y="{sy(ys[0]):.1f}" dy="4" class="ax">{wrs[0]["mark_raw"]}</text>'
    last_label = f'<text x="0" y="{sy(ys[-1]):.1f}" dy="4" class="ax">{wrs[-1]["mark_raw"]}</text>'
    # X-axis labels: first and last year only.
    first_year = wrs[0]["date"][:4]
    last_year = wrs[-1]["date"][:4]
    x_first = (
        f'<text x="{sx(xs[0]):.1f}" y="{H - 6}" text-anchor="middle" '
        f'class="ax">{first_year}</text>'
    )
    x_last = (
        f'<text x="{sx(xs[-1]):.1f}" y="{H - 6}" text-anchor="middle" '
        f'class="ax">{last_year}</text>'
    )
    return (
        f'<svg viewBox="0 0 {W} {H}" class="wr-chart" '
        f'aria-label="World-record progression: {len(wrs)} marks from '
        f'{first_year} to {last_year}">'
        f'<path d="{path}" fill="none" />'
        f'{dots}'
        f'{first_label}{last_label}{x_first}{x_last}'
        "</svg>"
    )


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
