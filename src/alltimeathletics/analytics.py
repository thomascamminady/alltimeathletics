"""Per-event and per-athlete analytics computed at build time.

Pure data transforms over the canonical parquet — no rendering, no I/O —
so they are straightforward to unit-test in isolation."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

import polars as pl

_DESC_FAMILIES = frozenset({"field_distance", "field_distance_wind", "combined_points"})


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
            "all_perfs": [],
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
    # Among ties on the year's best mark, pick the earliest-set (then A–Z by
    # name) holder so the chosen holder is deterministic across runs.
    holders = (
        holders.sort(["date", "name"], nulls_last=True)
        .unique(subset=["year"], keep="first")
        .sort("year")
    )
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
        .sort(
            ["mark_value", "date", "name"],
            descending=[descending, False, False],
            nulls_last=True,
        )
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
    # Deterministic tiebreaker: among equal marks the earliest-set performance
    # wins, then alphabetical by name. Only ``mark_value`` follows ``descending``;
    # ``date`` and ``name`` are always ascending. Without this, ties (common —
    # e.g. several 9.86 in the 100m) would pick a non-deterministic holder.
    sorted_main = canonical.sort(
        ["mark_value", "date", "name"],
        descending=[descending, False, False],
        nulls_last=True,
    )
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
            # Break count ties alphabetically so the displayed top-8 is stable.
            .sort(["count", "country"], descending=[True, False])
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
            .sort(
                ["mark_value", "date", "name"],
                descending=[descending, False, False],
                nulls_last=True,
            )
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
                # Break count ties alphabetically (name, then slug) so the
                # displayed top-8 and ``max_count`` reference are stable.
                .sort(["count", "name", "athlete_slug"], descending=[True, False, False])
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

    # ---- all canonical performances (for the "all performances" layer) -----
    # Keep the top N best marks so the combined chart has a dense point cloud
    # without bloating the analytics page. Capped at 2000 to bound page weight
    # for the deepest events (mile, marathon both go well past 5000 rows).
    ALL_PERFS_CAP = 2000
    all_perfs_df = sorted_main.head(ALL_PERFS_CAP) if not sorted_main.is_empty() else canonical
    all_perfs: list[dict[str, Any]] = [
        {
            "mark_value": r["mark_value"],
            "mark_raw": r["mark_raw"],
            "name": r["name"],
            "country": r["country"],
            "venue": r["venue"],
            "date": str(r["date"]),
        }
        for r in all_perfs_df.select(
            "mark_value", "mark_raw", "name", "country", "venue", "date"
        ).to_dicts()
    ]

    return {
        "best_per_year": best_per_year,
        "all_perfs": all_perfs,
        "entries_per_year": entries_per_year,
        "age_scatter": age_scatter,
        "top_countries": top_countries,
        "decade_leaders": decade_leaders,
        "top_athletes": top_athletes,
        "summary": summary,
    }


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
