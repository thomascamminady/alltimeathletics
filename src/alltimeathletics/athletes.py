"""Per-athlete career pages, rendered in a process pool."""

from __future__ import annotations

import json
import os
import re
import unicodedata
from collections.abc import Mapping
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from alltimeathletics.analytics import _compute_athlete_analytics
from alltimeathletics.paths import TEMPLATE_DIR


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
    # sort_keys so these maps serialise identically regardless of dict
    # iteration order — keeps athlete pages byte-reproducible across builds.
    _athlete_slugs = {e["event_slug"] for e in entries}
    wr_json = json.dumps(
        {s: v for s, v in _W_WR_VALUES.items() if s in _athlete_slugs},
        separators=(",", ":"),
        sort_keys=True,
    )
    event_family_json = json.dumps(
        {s: f for s, f in _W_EVENT_FAMILY.items() if s in _athlete_slugs},
        separators=(",", ":"),
        sort_keys=True,
    )
    event_descending_json = json.dumps(
        {s: d for s, d in _W_EVENT_DESCENDING.items() if s in _athlete_slugs},
        separators=(",", ":"),
        sort_keys=True,
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
    main_section_by_slug: Mapping[str, str],
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
                # True iff this entry sits on the canonical (rank-1) sub-list
                # for its event. The chart only computes % of WR for these
                # rows — sub-lists like "indoor performances" can't be
                # compared against the outdoor WR.
                "is_main": r["section"] == main_section_by_slug.get(r["event_slug"]),
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
