"""Invariant: legal performances never exceed 100% of the world record.

The athlete-page chart plots ``% of WR`` against time, with the y-axis
anchored at 100. Anything plotted above 100 is — by construction — a
non-legal mark (later DQ, wind-aided, or beaten by an annulled performance
that itself was the WR at scrape time). The chart deliberately still draws
those dots so the data is honest, but the axis caps at 100.

This test asserts the same invariant on the parquet: any row that we'd
treat as "legal" (canonical/main section, no DQ annotation) must compute
to a percent-of-WR ≤ 100. The WR is the same one the site uses — the
last entry in the WR progression on the canonical section.

Genuine, documented over-100 cases (e.g. an INEOS-style assisted attempt
that somehow leaked into the canonical section because Larsson catalogued
it there) belong in ``ALLOWED_OVER_100``. Don't grow that allowlist
casually — every entry should reference a real-world reason.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from alltimeathletics.events import EVENTS

# Mirrors ``site._DESC_FAMILIES``. Keep in sync.
_DESC_FAMILIES = frozenset({"field_distance", "field_distance_wind", "combined_points"})

REPO_ROOT = Path(__file__).resolve().parents[1]
PARQUET = REPO_ROOT / "data" / "alltime_athletics.parquet"

# Tolerance for float division noise. 1e-6 is plenty — we're comparing
# pct values around 100, so even a relative epsilon of 1e-9 would suffice.
EPSILON = 1e-6

# Documented exceptions: (event_slug, name, mark_raw) → reason. Add a row
# here only when the over-100 mark genuinely belongs on the legal list per
# Larsson AND the rule "no legal mark ≥ WR" can't be tightened to exclude
# it. Keep the reason concrete.
ALLOWED_OVER_100: dict[tuple[str, str, str], str] = {}


@pytest.fixture(scope="module")
def df() -> pl.DataFrame:
    if not PARQUET.exists():
        pytest.skip(f"{PARQUET} not present; run `make scrape`")
    return pl.read_parquet(PARQUET)


def _main_section_per_slug(df: pl.DataFrame) -> dict[str, str]:
    """Section name that holds rank=1 for each event slug.

    Matches ``site._compute_event_meta``'s definition of the canonical
    list, so the WR we derive here lines up with what the site shows.
    """
    rank1 = df.filter(pl.col("rank") == 1).select("event_slug", "section")
    return {row["event_slug"]: row["section"] for row in rank1.to_dicts()}


def _wr_per_slug(df: pl.DataFrame, main_section: dict[str, str]) -> dict[str, float]:
    """Latest-WR mark_value per slug, computed exactly like the site does.

    The site picks ``wr_progression[-1]["mark_value"]``; for the purpose of
    this test we just need the extremum over the canonical section's
    non-DQ rows, which is the same value.
    """
    family_by_slug = {ev.slug: ev.family for ev in EVENTS}
    wrs: dict[str, float] = {}
    for slug, section in main_section.items():
        sub = df.filter(
            (pl.col("event_slug") == slug)
            & (pl.col("section") == section)
            & pl.col("mark_value").is_not_null()
            & (pl.col("mark_annotation").is_null() | (pl.col("mark_annotation") != "*"))
        )
        if sub.is_empty():
            continue
        descending = family_by_slug.get(slug, "track_time") in _DESC_FAMILIES
        agg = sub["mark_value"].max() if descending else sub["mark_value"].min()
        if agg is not None:
            wrs[slug] = float(agg)
    return wrs


def test_legal_performances_never_exceed_100_pct_of_wr(df: pl.DataFrame) -> None:
    main_section = _main_section_per_slug(df)
    wrs = _wr_per_slug(df, main_section)
    family_by_slug = {ev.slug: ev.family for ev in EVENTS}

    # Build the "legal" view: canonical section, non-DQ, mark_value present.
    legal_rows = []
    for slug, section in main_section.items():
        if slug not in wrs:
            continue
        sub = df.filter(
            (pl.col("event_slug") == slug)
            & (pl.col("section") == section)
            & pl.col("mark_value").is_not_null()
            & (pl.col("mark_annotation").is_null() | (pl.col("mark_annotation") != "*"))
        )
        if sub.is_empty():
            continue
        wr = wrs[slug]
        descending = family_by_slug.get(slug, "track_time") in _DESC_FAMILIES
        for row in sub.to_dicts():
            mark = row["mark_value"]
            pct = (mark / wr) * 100.0 if descending else (wr / mark) * 100.0
            if pct > 100.0 + EPSILON:
                key = (slug, row["name"], row["mark_raw"])
                if key in ALLOWED_OVER_100:
                    continue
                legal_rows.append(
                    {
                        "slug": slug,
                        "name": row["name"],
                        "mark_raw": row["mark_raw"],
                        "date": str(row["date"]),
                        "pct": round(pct, 4),
                        "wr": wr,
                    }
                )

    assert not legal_rows, (
        f"{len(legal_rows)} legal performances compute to >100% of WR. Either "
        f"the parquet has a section/annotation regression, or these belong in "
        f"ALLOWED_OVER_100 with a documented reason. Sample: {legal_rows[:5]}"
    )
