"""Validate the canonical parquet's shape and a handful of well-known marks.

Runs *after* the pipeline has produced ``data/alltime_athletics.parquet``;
in CI this gates the auto-merge of the weekly data update PR.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import polars as pl
import pytest

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
PARQUET = DATA_DIR / "alltime_athletics.parquet"
MANIFEST = DATA_DIR / "manifest.json"

EXPECTED_SCHEMA: dict[str, pl.DataType] = {
    "event": pl.Utf8,
    "event_slug": pl.Utf8,
    "sex": pl.Utf8,
    "legality": pl.Utf8,
    "family": pl.Utf8,
    "section": pl.Utf8,
    "rank": pl.UInt32,
    "mark_raw": pl.Utf8,
    "mark_value": pl.Float64,
    "mark_annotation": pl.Utf8,
    "wind": pl.Float64,
    "name": pl.Utf8,
    "country": pl.Utf8,
    "dob": pl.Date,
    "dob_precision": pl.Utf8,
    "position": pl.Utf8,
    "venue": pl.Utf8,
    "date": pl.Date,
    "source_url": pl.Utf8,
}


@pytest.fixture(scope="module")
def df() -> pl.DataFrame:
    if not PARQUET.exists():
        pytest.skip(f"{PARQUET} not present; run `uv run python -m alltimeathletics.pipeline` first")
    return pl.read_parquet(PARQUET)


def test_schema_matches(df: pl.DataFrame) -> None:
    actual = dict(df.schema)
    assert actual == EXPECTED_SCHEMA, (
        f"schema drift\n  expected: {EXPECTED_SCHEMA}\n  got:      {actual}"
    )


def test_row_count_is_plausible(df: pl.DataFrame) -> None:
    # 2026-04 baseline is ~371k. Either a massive drop or a 10× balloon = bug.
    assert 200_000 <= len(df) <= 1_000_000, f"unexpected row count: {len(df)}"


def test_event_coverage(df: pl.DataFrame) -> None:
    n_slugs = df.select(pl.col("event_slug").n_unique()).item()
    assert n_slugs >= 150, f"only {n_slugs} event slugs present"


def test_country_codes_are_uppercase(df: pl.DataFrame) -> None:
    bad = df.filter(pl.col("country") != pl.col("country").str.to_uppercase())
    assert len(bad) == 0, f"{len(bad)} rows have non-uppercase country codes"


def test_wind_values_in_range(df: pl.DataFrame) -> None:
    winds = df.filter(pl.col("wind").is_not_null()).select("wind")
    if len(winds) == 0:
        pytest.skip("no wind readings")
    lo = winds.min().item()
    hi = winds.max().item()
    # Non-legal pages include marks with strong tailwinds; +20 m/s is the
    # highest ever recorded in the database. Outside [-15, +25] is a parser bug.
    assert -15.0 <= lo and hi <= 25.0, f"wind out of range: [{lo}, {hi}]"


def test_men_100m_top_mark(df: pl.DataFrame) -> None:
    top = (
        df.filter((pl.col("event_slug") == "m_100ok") & (pl.col("rank") == 1))
        .select("name", "mark_value", "country", "date")
        .row(0, named=True)
    )
    assert top["name"] == "Usain Bolt"
    assert top["country"] == "JAM"
    assert top["mark_value"] == pytest.approx(9.58)
    assert top["date"] == date(2009, 8, 16)


def test_marathon_top_mark_under_2_hours_or_close(df: pl.DataFrame) -> None:
    rows = df.filter(pl.col("event_slug") == "mmaraok").sort("mark_value").head(5)
    assert len(rows) >= 1
    # Top marathon mark under 2:01 (i.e. ≤ 7260 seconds).
    assert rows["mark_value"][0] <= 7260


def test_dob_within_human_range(df: pl.DataFrame) -> None:
    dobs = df.filter(pl.col("dob").is_not_null()).select("dob")
    if len(dobs) == 0:
        pytest.skip("no dob values")
    earliest = dobs.min().item()
    latest = dobs.max().item()
    assert date(1900, 1, 1) <= earliest, f"earliest dob too old: {earliest}"
    assert latest <= date.today(), f"future dob: {latest}"


def test_dob_precision_values_are_known(df: pl.DataFrame) -> None:
    """Every non-null dob_precision is either "day" or "year"."""
    distinct = (
        df.filter(pl.col("dob_precision").is_not_null())
        .select("dob_precision")
        .unique()
        .to_series()
        .to_list()
    )
    assert set(distinct) <= {"day", "year"}, f"unexpected dob_precision: {distinct}"


def test_dob_precision_is_consistent_with_dob(df: pl.DataFrame) -> None:
    """dob_precision is set iff dob is set; year-only entries land on Jan 1."""
    has_dob = df.filter(pl.col("dob").is_not_null())
    has_prec = df.filter(pl.col("dob_precision").is_not_null())
    assert len(has_dob) == len(has_prec), (
        f"{len(has_dob)} dob values vs {len(has_prec)} dob_precision values"
    )
    # Every "year"-precision row must be Jan 1 of its year (the synthetic
    # day/month we fabricate when only `yy` is given).
    year_only = df.filter(pl.col("dob_precision") == "year").select("dob")
    if len(year_only) > 0:
        bad = year_only.filter(
            (pl.col("dob").dt.month() != 1) | (pl.col("dob").dt.day() != 1)
        )
        assert len(bad) == 0, f"{len(bad)} year-precision rows are not Jan 1"


def test_mark_annotation_carries_real_signal(df: pl.DataFrame) -> None:
    """At least some marks should carry annotations like A (altitude), h, or *."""
    annotated = df.filter(pl.col("mark_annotation").is_not_null())
    # Larsson's pages routinely have hundreds of altitude/hand-timed/DQ marks
    # across all events. A floor of 100 is well below what we see in practice
    # but high enough to fail loudly if we ever stop populating the column.
    assert len(annotated) >= 100, (
        f"only {len(annotated)} rows have a mark_annotation — "
        "did the extractor break?"
    )


def test_manifest_matches_parquet(df: pl.DataFrame) -> None:
    if not MANIFEST.exists():
        pytest.skip("manifest missing")
    manifest = json.loads(MANIFEST.read_text())
    assert manifest["n_rows"] == len(df)
    assert manifest["n_events"] >= 150
