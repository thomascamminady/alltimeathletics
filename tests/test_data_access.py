"""Tests around the *consumer* experience of the parquet.

These exist so that any change to ``pipeline.py`` or ``site.py`` that would
break a downstream data-science workflow (loading with polars, loading with
pandas, finding an event by slug, joining with event metadata) fails loudly
in CI rather than silently corrupting analysis a week later.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from alltimeathletics.events import EVENTS, by_slug

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
PARQUET = DATA_DIR / "alltime_athletics.parquet"
MANIFEST = DATA_DIR / "manifest.json"


# ----------------------------------------------------------------------------
# Known data-quality issues — catalogued explicitly so they can't drift.
# Add to these sets when Larsson surfaces a new typo; remove when he fixes one
# and a test will fail loudly so we know to update the catalogue.
# ----------------------------------------------------------------------------

# (event_slug, name, venue, date) — entries with a date in the future, almost
# certainly Larsson typos of the previous year.
KNOWN_FUTURE_DATE_TYPOS: set[tuple[str, str, str, date]] = {
    ("m_800ok", "Colin Sahlman", "New York City", date(2026, 6, 1)),
    ("m_800ok", "Mohamed Attaoui", "New York City", date(2026, 6, 1)),
    ("m_800ok", "Ben Pattison", "New York City", date(2026, 6, 1)),
    ("m_800ok", "Donavan Brazier", "New York City", date(2026, 6, 1)),
    ("m100mno", "Benjamin Azamati", "Walnut", date(2026, 5, 18)),
    ("m100mno", "Edward Osei-Nketia", "Walnut", date(2026, 5, 18)),
    ("m100mno", "Garrett Kaalund", "Walnut", date(2026, 5, 18)),
    ("m100mno", "Jaleel Croal", "Walnut", date(2026, 5, 18)),
    ("m100mno", "Jelani Watkins", "Walnut", date(2026, 5, 18)),
}


@pytest.fixture(scope="module")
def df() -> pl.DataFrame:
    if not PARQUET.exists():
        pytest.skip(f"{PARQUET} not present; run `make scrape`")
    return pl.read_parquet(PARQUET)


def _per_event_json_dir() -> Path | None:
    """Per-event JSON is written by ``site.py`` into the build dir.

    Returns the first location that exists, or None if nothing has been built.
    """
    for candidate in (REPO_ROOT / "site" / "data" / "events", DATA_DIR / "events"):
        if candidate.exists():
            return candidate
    return None


# ---------------------------------------------------------------- loading ---


def test_parquet_loads_with_polars(df: pl.DataFrame) -> None:
    assert len(df) > 0
    assert len(df.columns) > 0


def test_parquet_loads_with_pandas() -> None:
    """A lot of analysts default to pandas — make sure the file is portable.

    Pandas's default parquet backend stores Date32 as object-dtype Python
    ``date`` instances; analysts who want pandas-native datetimes use
    ``dtype_backend="pyarrow"``. We test both work.
    """
    if not PARQUET.exists():
        pytest.skip("no parquet")
    pd = pytest.importorskip("pandas")
    pdf = pd.read_parquet(PARQUET)
    assert len(pdf) > 0
    assert isinstance(pdf["date"].dropna().iloc[0], date)
    # pyarrow backend should give a real arrow date type
    pdf2 = pd.read_parquet(PARQUET, dtype_backend="pyarrow")
    assert "date32" in str(pdf2["date"].dtype)


def test_parquet_loads_with_pyarrow() -> None:
    if not PARQUET.exists():
        pytest.skip("no parquet")
    pa = pytest.importorskip("pyarrow.parquet")
    table = pa.read_table(PARQUET)
    assert table.num_rows > 0


def test_parquet_size_is_reasonable() -> None:
    """A 100 MB parquet means we've stopped compressing or accidentally
    serialized the relay members blob — both are bugs to know about."""
    if not PARQUET.exists():
        pytest.skip("no parquet")
    size_mb = PARQUET.stat().st_size / (1024 * 1024)
    assert size_mb < 50, f"parquet is {size_mb:.1f} MB — compression broke?"


# ------------------------------------------------------------- discovery ---


def test_event_slug_round_trips_through_catalogue(df: pl.DataFrame) -> None:
    """Every slug in the parquet is also in the canonical catalogue."""
    parquet_slugs = set(df.select("event_slug").unique().to_series().to_list())
    catalogue_slugs = {ev.slug for ev in EVENTS}
    orphans = parquet_slugs - catalogue_slugs
    assert not orphans, f"slugs in parquet but not in events.py: {orphans}"


def test_canonical_events_with_data_match_manifest(df: pl.DataFrame) -> None:
    if not MANIFEST.exists():
        pytest.skip("no manifest")
    manifest = json.loads(MANIFEST.read_text())
    manifest_slugs = {e["slug"] for e in manifest["events"] if e["n_rows"] > 0}
    parquet_slugs = set(df.select("event_slug").unique().to_series().to_list())
    assert manifest_slugs == parquet_slugs


def test_by_slug_works_for_every_event_in_the_parquet(df: pl.DataFrame) -> None:
    for slug in df.select("event_slug").unique().to_series().to_list():
        ev = by_slug(slug)
        assert ev.slug == slug
        assert ev.label
        assert ev.url.startswith("https://")


# --------------------------------------------------------------- queries ---


def test_filter_by_event_returns_data(df: pl.DataFrame) -> None:
    """The headline use case: 'give me the 100m all-time list'."""
    sub = df.filter(
        (pl.col("event") == "100 metres")
        & (pl.col("sex") == "men")
        & (pl.col("legality") == "legal")
    )
    assert len(sub) > 1000
    top = sub.sort("mark_value").head(1).row(0, named=True)
    assert top["name"] == "Usain Bolt"


def test_sort_by_mark_value_produces_real_ranking(df: pl.DataFrame) -> None:
    sub = (
        df.filter(pl.col("event_slug") == "m_100ok")
        .sort("mark_value")
        .head(10)
        .select("rank", "mark_value")
    )
    # mark_value monotonically increases (faster = smaller seconds, ties are fine)
    values = sub["mark_value"].to_list()
    assert values == sorted(values)


def test_group_by_country_finds_top_nations(df: pl.DataFrame) -> None:
    """Common analyst question: which countries dominate the all-time lists?"""
    sub = df.filter(pl.col("event_slug") == "m_100ok")
    by_country = sub.group_by("country").agg(pl.len().alias("n")).sort("n", descending=True).head(5)
    countries = by_country["country"].to_list()
    assert "USA" in countries
    assert "JAM" in countries


def test_join_with_event_metadata(df: pl.DataFrame) -> None:
    """Round-trip: build a small join table from EVENTS, join, every row gets
    a family value."""
    meta = pl.DataFrame(
        {
            "event_slug": [ev.slug for ev in EVENTS],
            "fam_check": [ev.family for ev in EVENTS],
        }
    )
    joined = df.join(meta, on="event_slug", how="left")
    assert joined["fam_check"].null_count() == 0
    assert (joined["family"] == joined["fam_check"]).all()


# ---------------------------------------------------------- value sanity ---


def test_each_event_section_has_a_rank_1(df: pl.DataFrame) -> None:
    """Every (event_slug, section) group should start at rank=1.

    Multiple rank=1 rows in one section are legitimate (tied marks); but
    a section with no rank-1 row at all means the rank parser dropped the
    leader, which is a bug.
    """
    by_section = (
        df.group_by("event_slug", "section")
        .agg(pl.col("rank").min().alias("min_rank"))
        .filter(pl.col("min_rank") > 1)
    )
    # A handful of "ancillary"/"en route" sub-lists legitimately start mid-rank
    # because they're filtered subsets — tolerate up to 5 % of sections.
    n_total = df.select(pl.struct("event_slug", "section").n_unique()).item()
    assert len(by_section) < 0.05 * n_total, (
        f"{len(by_section)} of {n_total} sections never reach rank=1"
    )


def test_dates_make_sense(df: pl.DataFrame) -> None:
    """All dates fall in [1900, today], modulo specifically catalogued typos.

    Anything outside that window that is *not* in ``KNOWN_FUTURE_DATE_TYPOS``
    is a new bug — either parser drift or new bad data Larsson hasn't fixed.
    """
    nonnull = df.filter(pl.col("date").is_not_null())
    earliest = nonnull.select(pl.col("date").min()).item()
    assert date(1900, 1, 1) <= earliest, f"date too old: {earliest}"

    today = date.today()
    future = nonnull.filter(pl.col("date") > today)
    future_keys = {
        (r["event_slug"], r["name"], r["venue"], r["date"])
        for r in future.select("event_slug", "name", "venue", "date").to_dicts()
    }
    unexpected = future_keys - KNOWN_FUTURE_DATE_TYPOS
    assert not unexpected, (
        f"{len(unexpected)} new future-dated rows (parser bug or new Larsson "
        f"typo to catalogue): {sorted(unexpected)[:5]}"
    )


def test_name_is_never_empty(df: pl.DataFrame) -> None:
    assert df.filter(pl.col("name").str.strip_chars() == "").is_empty()


def test_country_codes_are_2_or_3_chars(df: pl.DataFrame) -> None:
    bad = df.filter(
        pl.col("country").is_not_null() & ~pl.col("country").str.contains(r"^[A-Z]{2,3}\d?$")
    )
    assert bad.is_empty(), (
        f"{len(bad)} rows have weird country codes; sample: "
        f"{bad.select('country', 'name').head(5).to_dicts()}"
    )


def test_track_marks_are_positive_seconds(df: pl.DataFrame) -> None:
    track = df.filter(
        pl.col("family").is_in(["track_time", "track_time_wind", "relay"])
        & pl.col("mark_value").is_not_null()
    )
    assert track.select(pl.col("mark_value").min()).item() > 0


def test_field_marks_are_positive_metres(df: pl.DataFrame) -> None:
    field = df.filter(
        pl.col("family").is_in(["field_distance", "field_distance_wind"])
        & pl.col("mark_value").is_not_null()
    )
    assert field.select(pl.col("mark_value").min()).item() > 0
    # No human jumps farther than 100m or throws further than ~110m.
    assert field.select(pl.col("mark_value").max()).item() < 150


# ------------------------------------------------------ generated assets ---


def test_catalogued_typos_still_present(df: pl.DataFrame) -> None:
    """Each entry in ``KNOWN_FUTURE_DATE_TYPOS`` must still be in the parquet.

    If Larsson fixes a typo upstream, this test fails — that's the signal to
    delete the entry from the catalogue. Drift in either direction is loud.
    """
    today = date.today()
    actual = {
        (r["event_slug"], r["name"], r["venue"], r["date"])
        for r in (
            df.filter(pl.col("date") > today)
            .select("event_slug", "name", "venue", "date")
            .to_dicts()
        )
    }
    fixed_upstream = KNOWN_FUTURE_DATE_TYPOS - actual
    assert not fixed_upstream, (
        "Larsson appears to have fixed these — remove from "
        f"KNOWN_FUTURE_DATE_TYPOS: {sorted(fixed_upstream)}"
    )


def test_per_event_json_files_are_valid_when_present() -> None:
    """If site.py has been run, every JSON file should parse and be a list."""
    events_dir = _per_event_json_dir()
    if events_dir is None:
        pytest.skip("per-event JSON not generated; run `make site`")
    files = sorted(events_dir.glob("*.json"))
    assert len(files) >= 150, f"only {len(files)} JSON files"
    for f in files:
        records = json.loads(f.read_text())
        assert isinstance(records, list), f"{f.name} is not a JSON array"
        if records:
            assert "rank" in records[0]
            assert "name" in records[0]


def test_per_event_json_row_counts_match_parquet(df: pl.DataFrame) -> None:
    events_dir = _per_event_json_dir()
    if events_dir is None:
        pytest.skip("per-event JSON not generated; run `make site`")
    counts_parquet = df.group_by("event_slug").agg(pl.len().alias("n")).to_dicts()
    by_slug_count = {r["event_slug"]: r["n"] for r in counts_parquet}
    for slug, expected in by_slug_count.items():
        f = events_dir / f"{slug}.json"
        if not f.exists():
            continue
        actual = len(json.loads(f.read_text()))
        assert actual == expected, f"{slug}: parquet={expected} json={actual}"
