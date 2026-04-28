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

Use::

    uv run python -m alltimeathletics.site --out site/
"""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

import fire
import polars as pl
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from alltimeathletics.events import EVENTS

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "templates"
STATIC_SRC = REPO_ROOT / "static"
DATA_SRC = REPO_ROOT / "data"
PARQUET_NAME = "alltime_athletics.parquet"


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
    _write_per_event_json(df, out_data / "events")

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

    # Index page
    (out_dir / "index.html").write_text(
        env.get_template("index.html").render(
            **common,
            events_by_sex=events_by_sex,
            counts=counts,
            n_rows_total=manifest["n_rows"],
            n_events_total=manifest["n_events"],
            parquet_size_mb=f"{parquet_bytes / (1024 * 1024):.1f}",
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
        (event_dir / f"{ev.slug}.html").write_text(
            template.render(
                **common,
                event=ev,
                family=ev.family,
                row_count=n,
                event_meta_json=json.dumps(
                    {"slug": ev.slug, "label": ev.label, "family": ev.family}
                ),
            )
        )

    n_pages = sum(len(events_by_sex[s]) for s in ("men", "women", "mixed"))
    print(f"Rendered {n_pages} event pages to {out_dir}")


def _write_per_event_json(df: pl.DataFrame, out_dir: Path) -> None:
    """One JSON file per event slug for the static frontend.

    Drops fields constant across every row of a given event
    (event/event_slug/sex/legality/family). ``source_url`` is kept because it
    now varies per row — the trailing ``#<anchor>`` deep-links to the section
    on Larsson's page that the row came from. To keep payloads small we also
    drop the page prefix and just store the fragment (e.g. ``#1``); the
    template stitches it back onto ``event.url`` in the browser.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    drop_cols = ["event", "event_slug", "sex", "legality", "family"]
    for slug in df.select("event_slug").unique().to_series().to_list():
        sub = (
            df.filter(pl.col("event_slug") == slug)
            .drop(drop_cols)
            .with_columns(
                pl.col("dob").cast(pl.Utf8),
                pl.col("date").cast(pl.Utf8),
                pl.col("source_url")
                .str.extract(r"(#\d+)$", 1)
                .fill_null("")
                .alias("source_url"),
            )
        )
        records = sub.to_dicts()
        (out_dir / f"{slug}.json").write_text(
            json.dumps(records, separators=(",", ":"), default=str)
        )


def main() -> None:
    fire.Fire(render)


if __name__ == "__main__":
    main()
