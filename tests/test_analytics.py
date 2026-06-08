"""Tests for :func:`alltimeathletics.site._compute_event_analytics`.

The analytics page picks a single "representative" row for several cards
(top mark, decade leaders, best-per-year, ...). When several athletes share a
mark — ties are common, e.g. multiple 9.86 in the 100m — a mark-only sort is
not guaranteed stable, so the chosen holder used to be non-deterministic.

These tests pin the agreed tiebreaker: among equal marks the *earliest-set*
performance wins (then alphabetical by name), and two calls always agree.
"""

from __future__ import annotations

from datetime import date

import polars as pl

from alltimeathletics.site import _compute_event_analytics

# Columns the function reads off the canonical frame.
_SCHEMA: dict[str, pl.DataType] = {
    "event_slug": pl.String(),
    "section": pl.String(),
    "rank": pl.Int64(),
    "mark_raw": pl.String(),
    "mark_value": pl.Float64(),
    "mark_annotation": pl.String(),
    "name": pl.String(),
    "country": pl.String(),
    "dob": pl.Date(),
    "venue": pl.String(),
    "date": pl.Date(),
    "athlete_slug": pl.String(),
}


def _row(
    *,
    mark_value: float,
    mark_raw: str,
    name: str,
    when: date,
    country: str = "USA",
    venue: str = "Somewhere",
    dob: date = date(1990, 1, 1),
    athlete_slug: str = "",
) -> dict[str, object]:
    """One canonical (rank-1 section) performance row."""
    return {
        "event_slug": "evt",
        "section": "main list",
        "rank": 1,
        "mark_raw": mark_raw,
        "mark_value": mark_value,
        "mark_annotation": None,
        "name": name,
        "country": country,
        "dob": dob,
        "venue": venue,
        "date": when,
        "athlete_slug": athlete_slug or f"{name.lower().replace(' ', '-')}-usa",
    }


def _frame(rows: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=_SCHEMA)


_META = {"main_section": "main list", "descending": False, "family": "track_time"}


def test_top_holder_is_earliest_among_ties() -> None:
    """A time event with two rows tied on the best mark: earliest date wins."""
    df = _frame(
        [
            # Tied best mark (9.86); the later one is listed first to prove the
            # tiebreaker, not row order, decides the holder.
            _row(mark_value=9.86, mark_raw="9.86", name="Late Runner", when=date(2015, 6, 1)),
            _row(mark_value=9.86, mark_raw="9.86", name="Early Runner", when=date(2009, 8, 16)),
            _row(mark_value=9.90, mark_raw="9.90", name="Third Runner", when=date(2011, 1, 1)),
        ]
    )

    summary = _compute_event_analytics(df, "evt", _META)["summary"]

    assert summary["top_name"] == "Early Runner"
    assert summary["top_date"] == "2009-08-16"


def test_results_are_deterministic_across_calls() -> None:
    """Two identical calls must produce byte-for-byte identical output."""
    df = _frame(
        [
            _row(mark_value=9.86, mark_raw="9.86", name="Z Runner", when=date(2015, 6, 1)),
            _row(mark_value=9.86, mark_raw="9.86", name="A Runner", when=date(2009, 8, 16)),
            _row(mark_value=9.90, mark_raw="9.90", name="B Runner", when=date(2011, 1, 1)),
        ]
    )

    first = _compute_event_analytics(df, "evt", _META)
    second = _compute_event_analytics(df, "evt", _META)

    assert first == second


def test_descending_field_event_top_holder_is_earliest_among_ties() -> None:
    """For a field event (higher is better) the same earliest-wins rule holds."""
    meta = {"main_section": "main list", "descending": True, "family": "throws_distance"}
    df = _frame(
        [
            _row(mark_value=23.12, mark_raw="23.12", name="Later Thrower", when=date(2022, 5, 1)),
            _row(mark_value=23.12, mark_raw="23.12", name="Older Thrower", when=date(1990, 5, 1)),
            _row(mark_value=22.00, mark_raw="22.00", name="Lesser Thrower", when=date(2000, 1, 1)),
        ]
    )

    summary = _compute_event_analytics(df, "evt", meta)["summary"]

    assert summary["top_name"] == "Older Thrower"
    assert summary["top_date"] == "1990-05-01"
