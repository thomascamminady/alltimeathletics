"""Invariants on the canonical parquet that don't fit the other test files.

The existing suites cover schema (``test_pipeline_schema``), semantic drift on
well-known marks (``test_qoi``), parser fixtures (``test_parse``) and consumer
ergonomics (``test_data_access``). This file is the catch-all safety net for
"silent" parser regressions — bugs that still produce a valid parquet of the
right shape, but with mangled cell values, leaked HTML, smeared families, or
duplicated rows.

Every test here aims to pin down ONE thing the parser must always preserve.
When one fails the right move is to look at the failing rows first; the
catalogues elsewhere capture what we deliberately accept.
"""

from __future__ import annotations

import re
from pathlib import Path

import polars as pl
import pytest

from alltimeathletics.events import EVENTS

REPO_ROOT = Path(__file__).resolve().parents[1]
PARQUET = REPO_ROOT / "data" / "alltime_athletics.parquet"


@pytest.fixture(scope="module")
def df() -> pl.DataFrame:
    if not PARQUET.exists():
        pytest.skip(f"{PARQUET} not present; run `make scrape`")
    return pl.read_parquet(PARQUET)


# ---------------------------------------------------------------- source_line


def test_source_line_is_present_on_every_row(df: pl.DataFrame) -> None:
    """The provenance column we added must be populated on every row."""
    nulls = df.filter(pl.col("source_line").is_null())
    empty = df.filter(pl.col("source_line").str.strip_chars() == "")
    assert nulls.height == 0, f"{nulls.height} rows have null source_line"
    assert empty.height == 0, f"{empty.height} rows have empty source_line"


def test_source_line_contains_mark_raw(df: pl.DataFrame) -> None:
    """``mark_raw`` is extracted verbatim from the source line, so it must
    appear unchanged inside ``source_line`` for every row.

    A regression that re-formats the mark token (decimal swap, annotation
    stripping, leading-zero fix) before recording would break this.
    """
    mismatch = df.filter(~pl.col("source_line").str.contains(pl.col("mark_raw"), literal=True))
    assert mismatch.height == 0, (
        f"{mismatch.height} rows have mark_raw not in source_line; e.g. "
        f"{mismatch.select('mark_raw', 'source_line').head(3).to_dicts()}"
    )


def test_source_line_length_is_reasonable(df: pl.DataFrame) -> None:
    """A Larsson row line is roughly 90–160 chars after tag-strip. Lines
    outside [20, 400] suggest the parser captured a header, blank, or fused
    block instead of a single performance line."""
    lengths = df.select(pl.col("source_line").str.len_chars().alias("n"))
    too_short = lengths.filter(pl.col("n") < 20).height
    too_long = lengths.filter(pl.col("n") > 400).height
    assert too_short == 0, f"{too_short} source_line values < 20 chars"
    assert too_long == 0, f"{too_long} source_line values > 400 chars"


# ---------------------------------------------------------------- no leakage


_HTML_TAG_RE = r"<[^>]*>|&[a-z]+;"


def test_no_html_tags_in_name(df: pl.DataFrame) -> None:
    """Names must not carry stray HTML tags or named entities."""
    bad = df.filter(pl.col("name").str.contains(_HTML_TAG_RE))
    assert bad.height == 0, (
        f"{bad.height} names contain HTML/entities: {bad['name'].head(5).to_list()}"
    )


def test_no_html_tags_in_venue(df: pl.DataFrame) -> None:
    bad = df.filter(pl.col("venue").is_not_null() & pl.col("venue").str.contains(_HTML_TAG_RE))
    assert bad.height == 0, (
        f"{bad.height} venues contain HTML/entities: {bad['venue'].head(5).to_list()}"
    )


def test_no_mojibake_in_name(df: pl.DataFrame) -> None:
    """Latin-1 → UTF-8 mishandling produces telltale sequences. None of these
    should appear in any name once the parser has decoded the page."""
    mojibake_re = r"Ã[\x80-\xbf]|Â[\x80-\xbf]|â\x80|�"
    bad = df.filter(pl.col("name").str.contains(mojibake_re))
    assert bad.height == 0, (
        f"{bad.height} names show encoding mojibake: {bad['name'].head(5).to_list()}"
    )


# Names that look like Larsson section headers ("All-time", "Indoor list",
# "Outdoor list", etc.) sometimes leak into the row table when the parser
# misclassifies a malformed header. Block them outright.
_HEADER_LEAK_RE = re.compile(
    r"^(all-?time|indoor|outdoor|yearly|world|annual|national)\s+(list|best|record)s?",
    re.IGNORECASE,
)


def test_no_section_headers_leaked_into_names(df: pl.DataFrame) -> None:
    leaked = [n for n in df["name"].unique().to_list() if _HEADER_LEAK_RE.match(n or "")]
    assert not leaked, f"section-header text leaked into name column: {leaked[:5]}"


# ---------------------------------------------------------------- columns


def test_mark_raw_never_null_or_empty(df: pl.DataFrame) -> None:
    """Every row must carry the original mark token; downstream filters and
    the table view show ``mark_raw`` directly."""
    nulls = df.filter(pl.col("mark_raw").is_null())
    empty = df.filter(pl.col("mark_raw").str.strip_chars() == "")
    assert nulls.height == 0, f"{nulls.height} rows have null mark_raw"
    assert empty.height == 0, f"{empty.height} rows have empty mark_raw"


# Position values vary widely (Larsson uses heat/semi/quarter/round suffixes
# inconsistently — '1', '1=', '1h2', 'h5', 's3', 'r3', 'D', 'P', etc.). Rather
# than enumerate the open vocabulary, we just bound length and reject anything
# that smells like a leaked name or HTML fragment.
def test_position_values_are_short_and_clean(df: pl.DataFrame) -> None:
    sub = df.filter(pl.col("position").is_not_null() & (pl.col("position") != ""))
    too_long = sub.filter(pl.col("position").str.len_chars() > 16)
    has_html = sub.filter(pl.col("position").str.contains(r"[<>&]"))
    assert too_long.height == 0, (
        f"{too_long.height} positions > 16 chars (likely name/venue leak): "
        f"{too_long['position'].head(5).to_list()}"
    )
    assert has_html.height == 0, (
        f"{has_html.height} positions contain HTML chars: {has_html['position'].head(5).to_list()}"
    )


def test_wind_is_null_for_non_wind_families(df: pl.DataFrame) -> None:
    """Wind only applies to ``track_time_wind`` / ``field_distance_wind``.
    Any wind reading on a relay or distance event means a regex misfire."""
    bad = df.filter(
        ~pl.col("family").is_in(["track_time_wind", "field_distance_wind"])
        & pl.col("wind").is_not_null()
    )
    assert bad.height == 0, (
        f"{bad.height} rows have wind set on a non-wind family; e.g. "
        f"{bad.select('family', 'wind', 'event_slug').head(3).to_dicts()}"
    )


# ---------------------------------------------------------------- structural


def test_each_event_slug_has_single_family(df: pl.DataFrame) -> None:
    """Every event slug maps to exactly one family. A regression that
    rebuilt the slug table or scrambled family dispatch would surface here."""
    by_slug = df.group_by("event_slug").agg(pl.col("family").n_unique().alias("n"))
    bad = by_slug.filter(pl.col("n") > 1)
    assert bad.height == 0, f"slugs with multiple families: {bad.to_dicts()}"


def test_one_source_url_per_event_section(df: pl.DataFrame) -> None:
    """Every (event_slug, section) pair points at exactly one source URL.
    ``source_url`` is built from the section anchor at parse time, so any
    section that suddenly has two URLs means the anchor extractor
    misclassified or the section deduper fell apart."""
    by_section = df.group_by("event_slug", "section").agg(
        pl.col("source_url").n_unique().alias("n")
    )
    bad = by_section.filter(pl.col("n") > 1)
    assert bad.height == 0, (
        f"{bad.height} sections map to multiple source URLs: {bad.head(3).to_dicts()}"
    )


def test_section_names_are_never_empty(df: pl.DataFrame) -> None:
    bad = df.filter(pl.col("section").is_null() | (pl.col("section").str.strip_chars() == ""))
    assert bad.height == 0, f"{bad.height} rows have empty section"


def test_full_row_duplicate_count_is_bounded(df: pl.DataFrame) -> None:
    """Bound the number of fully-identical parquet rows.

    Larsson legitimately repeats some en-route ancillary marks across his
    sub-sections, so the floor is not zero — but a parser regression that
    re-parses the same PRE block twice (e.g. from a malformed ``</PRE>`` that
    the section walker fails to consume) would explode this number 100×.
    """
    n_dupes = int(df.is_duplicated().sum())
    assert n_dupes < 5_000, (
        f"{n_dupes} fully-duplicate rows in the parquet — parser doubling regression?"
    )


# ---------------------------------------------------------------- temporal


def test_performance_date_is_after_dob(df: pl.DataFrame) -> None:
    """For every row with a day-precision dob and a known date, the perf
    must happen after the athlete is born. The handful of catalogued
    exceptions are upstream Larsson typos (see ``KNOWN_BAD_AGE_ROWS`` in
    ``test_qoi``); a parser regression that mangled century pivoting on the
    dob would explode this number."""
    sub = df.filter(
        pl.col("dob").is_not_null()
        & pl.col("date").is_not_null()
        & (pl.col("dob_precision") == "day")
    )
    bad = sub.filter(pl.col("date") < pl.col("dob"))
    assert bad.height < 25, (
        f"{bad.height} rows have performance date < dob — likely century pivot "
        f"regression. Sample: "
        f"{bad.select('event_slug', 'name', 'dob', 'date').head(3).to_dicts()}"
    )


def test_decade_coverage_on_top_track_events(df: pl.DataFrame) -> None:
    """Major events should span at least five decades on the canonical list.
    A parser bug that drops century-pivoted years (e.g. 1980s entries with
    a two-digit dob) would shrink the year span."""
    for slug in ("m_100ok", "m_5000ok", "mmaraok", "w_100ok"):
        years = (
            df.filter(pl.col("event_slug") == slug)
            .select(pl.col("date").dt.year().alias("y"))
            .filter(pl.col("y").is_not_null())["y"]
            .to_list()
        )
        decades = {y // 10 for y in years}
        assert len(decades) >= 5, (
            f"{slug}: only {len(decades)} decades represented ({sorted(decades)})"
        )


# ---------------------------------------------------------------- per-event volume


# Per-event row-count bands. Lower bound catches a parser regression that
# drops most of an event; upper bound catches a relay/section misclassifier
# that doubles rows. Bands are 0.5×–3× the 2026-04 baseline and intentionally
# generous, so genuine list growth doesn't make the test flap.
EVENT_VOLUME_BANDS: list[tuple[str, int, int]] = [
    ("m_100ok", 3000, 15000),
    ("m_200ok", 3000, 15000),
    ("m_400ok", 3000, 15000),
    ("m_800ok", 3000, 15000),
    ("m_1500ok", 3000, 15000),
    ("m_5000ok", 3000, 15000),
    ("m_10kok", 2000, 15000),
    ("mmaraok", 2000, 30000),
    ("wmaraok", 1000, 20000),
    ("mlongok", 2000, 12000),
    ("mhighok", 800, 6000),
    ("mdecaok", 500, 4000),
    ("m4x100ok", 400, 6000),
]


@pytest.mark.parametrize(("slug", "lo", "hi"), EVENT_VOLUME_BANDS)
def test_event_row_count_in_band(df: pl.DataFrame, slug: str, lo: int, hi: int) -> None:
    n = df.filter(pl.col("event_slug") == slug).height
    assert lo <= n <= hi, f"{slug}: {n} rows not in [{lo}, {hi}] — parser regression?"


# ---------------------------------------------------------------- catalogue parity


def test_every_catalogue_event_with_a_homepage_appears_in_parquet(
    df: pl.DataFrame,
) -> None:
    """Every event in ``EVENTS`` that the pipeline doesn't explicitly skip
    must produce at least one row. A silent regression that fails to fetch
    or parse one full event would surface here."""
    parquet_slugs = set(df["event_slug"].unique().to_list())
    catalogue_slugs = {ev.slug for ev in EVENTS}
    missing = catalogue_slugs - parquet_slugs
    # The pipeline's per-event counts are capped only by Larsson having data.
    # Missing slugs are fine when fewer than ~5 % of the catalogue, since some
    # rare events (women's hammer pre-2000) just have no entries; allow that
    # but flag wholesale shortfalls.
    assert len(missing) < 0.05 * len(catalogue_slugs), (
        f"{len(missing)} catalogue slugs missing from parquet: {sorted(missing)[:10]}"
    )


# ---------------------------------------------------------------- top-event sanity


def test_men_100m_top10_all_in_sub10_band(df: pl.DataFrame) -> None:
    """The top 10 marks on the men's 100m canonical list must all be in
    [9.5, 9.95) — anything outside means the sort or the family dispatch
    broke. A row above 9.95 in the top 10 means slower marks bubbled up."""
    top10 = (
        df.filter((pl.col("event_slug") == "m_100ok") & (pl.col("rank") <= 10))
        .filter(pl.col("section").str.starts_with("All-time"))
        .sort("mark_value")
        .head(10)
    )
    values = top10["mark_value"].to_list()
    assert len(values) == 10, f"only {len(values)} top-10 rows on m_100ok"
    assert all(9.5 <= v < 9.95 for v in values), f"top-10 m_100ok values: {values}"


def test_marathon_top10_all_under_two_hours_three_minutes(df: pl.DataFrame) -> None:
    """The top 10 men's marathon marks are all under 2:03 (7380 s)."""
    top10 = (
        df.filter(pl.col("event_slug") == "mmaraok")
        .filter(pl.col("section").str.starts_with("All-time"))
        .sort("mark_value")
        .head(10)
    )
    values = top10["mark_value"].to_list()
    assert len(values) == 10
    assert all(v <= 7380 for v in values), (
        f"slow mark in top-10 marathon: {[v for v in values if v > 7380]}"
    )
