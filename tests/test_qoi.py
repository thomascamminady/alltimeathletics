"""Quantities-of-Interest sanity checks.

These tests compute well-known things from the parquet (top marks, athlete
ages, country distributions, annotation catalogue) and assert the answers
match reality. They exist because schema and parser tests catch *structural*
drift; QOI tests catch *semantic* drift — a parser change that still produces
a valid parquet but has Bolt running 19.58 instead of 9.58.

When one of these fails, the right move is usually:
1. Confirm the underlying data/parser actually changed.
2. If the data changed (Larsson revision), update the catalogue.
3. If the parser broke, fix the parser.

Categories
----------
- **frozen records**: exact-match table of historical WRs that should never
  change (Sotomayor, Powell, Edwards, Sedykh, Železný, FloJo).
- **active records**: range bounds on currently-chased WRs (Bolt 100m,
  Cheptegei 5/10k, Kiptum marathon, Crouser shot, Duplantis vault).
- **athlete ages**: dob+date sanity. Catalogues the small set of Larsson
  typos where dob > date or age is wildly off, and asserts everything else
  falls in [5, 80] years.
- **country dominance**: USA+JAM dominate sprints, KEN+ETH dominate
  distance — a parser bug that mangles the country column would jump out.
- **mark annotation catalogue**: the set of valid annotations is closed; new
  letters from upstream surface as test failures.
- **dob inconsistency rate**: (name, date) pairs with conflicting dobs is
  small — bounded by name collisions plus a handful of Larsson typos.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PARQUET = REPO_ROOT / "data" / "alltime_athletics.parquet"

# ---------------------------------------------------------------------------
# Catalogues — explicit lists of known-bad rows so any drift is loud.
# ---------------------------------------------------------------------------

# (event_slug, name, date) — rows where dob and performance date can't be
# reconciled with any reasonable century pivot. Almost all are Larsson typos
# (e.g. Karl Mann's dob '76' vs a 1948 marathon — neither 1876 nor 1976 makes
# sense). The duplicate name+date with dob == date in some entries suggests
# upstream data entry where the dob field was filled with the perf date.
KNOWN_BAD_AGE_ROWS: set[tuple[str, str, date]] = {
    ("m30kok", "Karl Mann", date(1948, 12, 28)),
    ("w_1500ok", "Lyubov Ivanova", date(1978, 8, 13)),
    ("m100km", "János Bogár", date(1998, 5, 3)),
    ("m100km", "János Bogár", date(1999, 4, 11)),
    ("mhmaraok", "Daniel Mutai", date(1996, 9, 29)),
    ("m1hourok", "Owen MacHelm", date(1997, 1, 19)),
    ("m_10kok", "Daniel Mutai", date(1998, 8, 29)),
    ("mmaraok", "Hailemariyam Kiros", date(2001, 1, 11)),
    ("wmaraok", "Hawi Alemu", date(2002, 1, 26)),
    ("mmaraok", "Abdelom Kesete", date(2001, 1, 11)),
    ("mhmaraok", "Patrick Kinyanjui", date(2008, 9, 6)),
    ("mmaraok", "Addisu Gobena", date(2001, 1, 11)),
    ("w2000hok", "Evaline Chebichi", date(2004, 6, 11)),
    ("w3000hok", "Evaline Chebichi", date(2005, 6, 18)),
    ("w_400ok", "Henriette Jæger", date(2005, 3, 8)),
}

# Annotations Larsson uses on marks. New entries here = test failure prompting
# us to either accept a new annotation or fix the parser if the letter is bleeding
# in from elsewhere.
KNOWN_MARK_ANNOTATIONS: set[str] = {
    "+",
    "A",
    "a",
    "*",
    "y",
    "i",
    "h",
    "m",
    "p",
    "e",
    "x",
    "d",
    "*A",
    "yA",
    "A*",
    "m+",
    "a+",
    "Ay",
    "B",
    "Y",
    # Symbols Larsson uses on a handful of marks. Origin varies (en route,
    # short-track, hand-timed variant, indoor mat, etc.); we keep them as raw
    # annotations so consumers can filter without re-parsing mark_raw.
    "#",
    "'",
    "´",
    "@",
    "-",
    "A@",
}


@pytest.fixture(scope="module")
def df() -> pl.DataFrame:
    if not PARQUET.exists():
        pytest.skip(f"{PARQUET} not present; run `make scrape`")
    return pl.read_parquet(PARQUET)


def _best_mark(
    df: pl.DataFrame, slug: str, *, exclude_annotations: set[str] | None = None
) -> dict[str, object]:
    """Return the row with the all-time best mark in an event."""
    sub = df.filter((pl.col("event_slug") == slug) & pl.col("mark_value").is_not_null())
    if exclude_annotations:
        sub = sub.filter(
            pl.col("mark_annotation").is_null()
            | ~pl.col("mark_annotation").is_in(list(exclude_annotations))
        )
    family = sub["family"][0]
    descending = family in ("field_distance", "field_distance_wind", "combined_points")
    return sub.sort("mark_value", descending=descending).head(1).row(0, named=True)


# ---------------------------------------------------- frozen historical WRs ---


# (slug, expected_name, expected_mark_value, year_set). These records have
# stood for 25+ years and would only "change" if the parser broke or Larsson
# rewrote the page. Exact-match by name + numeric value catches arithmetic
# drift in mark normalization (minutes-as-seconds, decimal-place errors).
FROZEN_RECORDS: list[tuple[str, str, float, int]] = [
    ("mhighok", "Javier Sotomayor", 2.45, 1993),
    ("mlongok", "Mike Powell", 8.95, 1991),
    ("mtripok", "Jonathan Edwards", 18.29, 1995),
    ("mhammok", "Yuriy Syedikh", 86.74, 1986),
    ("mjaveok", "Jan Zelezný", 98.48, 1996),
    ("w_100ok", "Florence Griffith-Joyner", 10.49, 1988),
]


@pytest.mark.parametrize(("slug", "name", "value", "year"), FROZEN_RECORDS)
def test_frozen_historical_records(
    df: pl.DataFrame, slug: str, name: str, value: float, year: int
) -> None:
    best = _best_mark(df, slug, exclude_annotations={"*"})
    assert best["name"] == name, f"{slug}: top mark is {best['name']!r}, expected {name!r}"
    assert best["mark_value"] == pytest.approx(value, abs=1e-6), (
        f"{slug}: top value {best['mark_value']} != {value}"
    )
    perf_date = best["date"]
    assert perf_date is not None and perf_date.year == year, (
        f"{slug}: top mark year is {perf_date}, expected {year}"
    )


# -------------------------------------------------------- active-era bounds ---


# Active records: bounds rather than exact match because Duplantis et al keep
# pushing them. Each entry: (slug, lo, hi, descending). For time events the
# best mark is the smallest, so lo/hi bracket the WR from below/above; for
# field events it's the largest, same convention.
ACTIVE_RECORD_BOUNDS: list[tuple[str, float, float]] = [
    ("m_100ok", 9.55, 9.62),
    ("m_200ok", 19.15, 19.25),
    ("m_400ok", 43.00, 43.10),
    ("m_800ok", 100.85, 101.00),
    ("m_1500ok", 205.5, 207.0),
    ("m_mileok", 222.5, 224.0),
    ("m_5000ok", 754.0, 757.0),
    ("m_10kok", 1568.0, 1574.0),
    ("mmaraok", 7100.0, 7245.0),
    ("wmaraok", 7790.0, 7820.0),
    ("m_400hok", 45.90, 46.00),
    ("mpoleok", 6.20, 6.50),
    ("mshotok", 23.50, 23.70),
    ("mdiscok", 75.00, 76.00),
    ("mdecaok", 9100.0, 9150.0),
]


@pytest.mark.parametrize(("slug", "lo", "hi"), ACTIVE_RECORD_BOUNDS)
def test_active_era_records_in_bounds(df: pl.DataFrame, slug: str, lo: float, hi: float) -> None:
    best = _best_mark(df, slug, exclude_annotations={"*", "h"})
    v = best["mark_value"]
    assert lo <= v <= hi, f"{slug}: top mark {v} not in [{lo}, {hi}]; row={best}"


# ------------------------------------------------------------- athlete ages ---


def _ages(df: pl.DataFrame) -> pl.DataFrame:
    """Years between dob and performance date, using only day-precision dobs."""
    return df.filter(
        pl.col("dob").is_not_null()
        & pl.col("date").is_not_null()
        & (pl.col("dob_precision") == "day")
    ).with_columns(((pl.col("date") - pl.col("dob")).dt.total_days() / 365.25).alias("age_years"))


def test_athlete_ages_at_performance_are_plausible(df: pl.DataFrame) -> None:
    """No 4-year-olds running marathons, no 90-year-olds breaking sprint records.

    Catalogued exceptions live in ``KNOWN_BAD_AGE_ROWS``. Anything outside
    [5, 80] that isn't in the catalogue is either a new Larsson typo or a
    parser regression in dob extraction / century pivot.
    """
    ages = _ages(df)
    bad = ages.filter((pl.col("age_years") < 5) | (pl.col("age_years") > 80))
    keys = {
        (r["event_slug"], r["name"], r["date"])
        for r in bad.select("event_slug", "name", "date").to_dicts()
    }
    unexpected = keys - KNOWN_BAD_AGE_ROWS
    assert not unexpected, (
        f"{len(unexpected)} new implausible-age rows not in catalogue: {sorted(unexpected)[:5]}"
    )


def test_catalogued_bad_age_rows_still_present(df: pl.DataFrame) -> None:
    """If Larsson fixes a dob typo the catalogue drifts — surface it loudly."""
    ages = _ages(df)
    bad = ages.filter((pl.col("age_years") < 5) | (pl.col("age_years") > 80))
    actual = {
        (r["event_slug"], r["name"], r["date"])
        for r in bad.select("event_slug", "name", "date").to_dicts()
    }
    fixed_upstream = KNOWN_BAD_AGE_ROWS - actual
    assert not fixed_upstream, (
        f"Larsson appears to have fixed these — remove from KNOWN_BAD_AGE_ROWS: "
        f"{sorted(fixed_upstream)}"
    )


# ----------------------------------------------------- country distribution ---


# Per-event minimum count of expected countries in the top-10 athletes (by best
# mark). Loose bounds — a real parser bug that mangled the country column would
# crash these to ~0; normal year-to-year drift is much smaller than the slack.
COUNTRY_DOMINANCE: list[tuple[str, set[str], int]] = [
    ("m_100ok", {"USA", "JAM"}, 6),  # actually 8 of 10 currently
    ("w_100ok", {"USA", "JAM"}, 7),
    ("m_200ok", {"USA", "JAM"}, 7),
    ("m_5000ok", {"KEN", "ETH", "UGA"}, 8),
    ("m_10kok", {"KEN", "ETH", "UGA"}, 7),
    ("mmaraok", {"KEN", "ETH", "UGA"}, 8),
    ("wmaraok", {"KEN", "ETH"}, 6),
    ("mlongok", {"USA"}, 5),
]


@pytest.mark.parametrize(("slug", "countries", "min_count"), COUNTRY_DOMINANCE)
def test_country_dominance_in_top_10(
    df: pl.DataFrame, slug: str, countries: set[str], min_count: int
) -> None:
    sub = df.filter((pl.col("event_slug") == slug) & pl.col("mark_value").is_not_null())
    family = sub["family"][0]
    desc = family in ("field_distance", "field_distance_wind", "combined_points")
    best_per_athlete = (
        sub.group_by("name", "country")
        .agg((pl.col("mark_value").max() if desc else pl.col("mark_value").min()).alias("best"))
        .sort("best", descending=desc)
        .head(10)
    )
    found = best_per_athlete["country"].to_list()
    hits = sum(1 for c in found if c in countries)
    assert hits >= min_count, (
        f"{slug}: only {hits} of top-10 from {countries}, expected ≥ {min_count}; got {found}"
    )


# ----------------------------------------------------- annotation catalogue ---


def test_mark_annotation_values_in_known_set(df: pl.DataFrame) -> None:
    """All non-null mark_annotation values must be in ``KNOWN_MARK_ANNOTATIONS``.

    A new value here is either a new Larsson convention to document, or a
    parser bug eating a name suffix into the annotation column.
    """
    actual = set(
        df.filter(pl.col("mark_annotation").is_not_null())
        .select("mark_annotation")
        .unique()
        .to_series()
        .to_list()
    )
    unknown = actual - KNOWN_MARK_ANNOTATIONS
    assert not unknown, (
        f"new mark_annotation values not in catalogue: {sorted(unknown)} — "
        f"document them or fix the parser"
    )


# ---------------------------------------------------- dob internal coherence ---


def test_dob_inconsistency_rate_is_small(df: pl.DataFrame) -> None:
    """Same (athlete name, performance date) → at most one dob.

    Some inconsistency is legitimate: two different athletes with the same
    name competing on the same day. We don't try to dedupe by birth date —
    we just bound the count at a small ceiling so a parser bug that
    scrambled dobs en masse would be visible.
    """
    sub = df.filter(pl.col("dob").is_not_null())
    inconsistent = (
        sub.group_by("name", "date")
        .agg(pl.col("dob").n_unique().alias("n_dobs"))
        .filter(pl.col("n_dobs") > 1)
    )
    n = inconsistent.height
    # Empirically ~19; ceiling generous enough to absorb real new name collisions
    # but tight enough that a wholesale parser regression (~thousands) would fail.
    assert n < 100, f"{n} (name, date) pairs disagree on dob — parser regression?"


# -------------------------------------------------------------- physics --


# Per-event-family physical bounds on every parsed mark. These wouldn't catch
# fine-grained errors, but they crash through the floor if family dispatch
# breaks and a track time gets parsed as metres.
def test_track_marks_within_physical_bounds(df: pl.DataFrame) -> None:
    track = df.filter(
        pl.col("family").is_in(["track_time", "track_time_wind"])
        & pl.col("mark_value").is_not_null()
    )
    # Min: 60m sub-6.4 (Coleman 6.34) — 6.0 is a safe floor.
    # Max: 24-hour run, ~300 km in 24*3600 = 86400 s.
    assert track["mark_value"].min() >= 6.0
    assert track["mark_value"].max() <= 90_000


def test_field_marks_within_physical_bounds(df: pl.DataFrame) -> None:
    field = df.filter(
        pl.col("family").is_in(["field_distance", "field_distance_wind"])
        & pl.col("mark_value").is_not_null()
    )
    assert field["mark_value"].min() > 1.0  # a 1m HJ would be weirdly low
    assert field["mark_value"].max() < 110.0  # Železný-era javelin pre-1986 spec


# ---------------------------------------------------- column-type histograms ---
#
# These tests assert the *shape* of each column: numeric columns are actually
# numeric and have plausible distributions, date columns are real dates with
# the right span, string columns have low cardinality where they should and
# high cardinality where they shouldn't. A parser change that silently turns
# a numeric column into "all NaN" or smears years across centuries trips one
# of these.


def test_dtypes_are_canonical(df: pl.DataFrame) -> None:
    """Schema check: each column has the type the consumer expects."""
    expected: dict[str, type[pl.DataType]] = {
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
    for col, dt in expected.items():
        assert df.schema[col] == dt, f"{col}: dtype is {df.schema[col]}, expected {dt}"


def test_categorical_columns_have_closed_vocabulary(df: pl.DataFrame) -> None:
    """``sex``, ``legality``, ``family``, ``dob_precision`` are closed sets."""
    assert set(df["sex"].unique().to_list()) <= {"men", "women", "mixed"}
    assert set(df["legality"].unique().to_list()) <= {"legal", "non-legal"}
    assert set(df["family"].unique().to_list()) <= {
        "track_time",
        "track_time_wind",
        "field_distance",
        "field_distance_wind",
        "combined_points",
        "relay",
    }
    assert set(df["dob_precision"].drop_nulls().unique().to_list()) <= {"day", "year"}


def test_country_column_is_iso_shaped(df: pl.DataFrame) -> None:
    """Every non-null country is 2-3 uppercase letters + optional digit."""
    bad = df.filter(
        pl.col("country").is_not_null() & ~pl.col("country").str.contains(r"^[A-Z]{2,3}\d?$")
    )
    assert bad.height == 0, (
        f"{bad.height} rows have non-IOC-shaped country: {bad['country'].unique().to_list()[:10]}"
    )


def test_date_year_distribution_is_modern(df: pl.DataFrame) -> None:
    """≥ 95 % of performance dates fall within Larsson's coverage window."""
    years = df.filter(pl.col("date").is_not_null()).select(pl.col("date").dt.year().alias("y"))
    in_window = years.filter((pl.col("y") >= 1960) & (pl.col("y") <= date.today().year + 1))
    ratio = in_window.height / max(1, years.height)
    assert ratio >= 0.95, f"only {ratio:.3%} of dates fall in [1960, this year+1]"


def test_dob_year_distribution_covers_century(df: pl.DataFrame) -> None:
    """Athletes' birth years span at least 70 years and stay before today."""
    today_year = date.today().year
    dobs = df.filter(pl.col("dob").is_not_null()).select(pl.col("dob").dt.year().alias("y"))["y"]
    assert dobs.max() <= today_year, f"future dob seen: {dobs.max()}"
    assert dobs.max() - dobs.min() >= 70, (
        f"dob span only {dobs.max() - dobs.min()} years; expected ≥ 70"
    )


def test_wind_distribution_is_centered_and_bounded(df: pl.DataFrame) -> None:
    """Wind readings cluster near 0 with extremes capped by laws of physics."""
    wind = df.filter(pl.col("wind").is_not_null())["wind"]
    assert wind.min() >= -10.0, f"impossible headwind: {wind.min()}"
    assert wind.max() <= 20.0, f"impossible tailwind: {wind.max()}"
    # Median wind across all measured rows sits around 0.7 — both legal-only
    # and non-legal sections in the parquet. A median > 5 would mean the
    # column has been smeared with non-wind values (regression).
    assert -1.0 <= wind.median() <= 3.0


def test_mark_value_per_family_in_band(df: pl.DataFrame) -> None:
    """Per-family mark_value range — catches a family-dispatch swap."""
    bands: dict[str, tuple[float, float]] = {
        "track_time": (5.0, 90_000.0),  # 60m up to 24-hour run
        "track_time_wind": (5.0, 30.0),  # short sprint times only
        "field_distance": (0.5, 110.0),  # HJ floor to javelin (old spec)
        "field_distance_wind": (0.5, 25.0),  # LJ/TJ wind-aided
        "combined_points": (50.0, 10_000.0),  # decathlon/heptathlon
        "relay": (30.0, 2_000.0),  # 4x100 up to 4x1500
    }
    for family, (lo, hi) in bands.items():
        sub = df.filter(pl.col("family") == family)["mark_value"]
        assert sub.min() >= lo, f"{family}: min {sub.min()} < {lo}"
        assert sub.max() <= hi, f"{family}: max {sub.max()} > {hi}"


def test_name_column_is_high_cardinality(df: pl.DataFrame) -> None:
    """Hundreds of thousands of rows but tens of thousands of distinct athletes."""
    n_unique = df["name"].n_unique()
    # If the parser collapsed names into a constant or a tiny set, this fails.
    assert n_unique >= 10_000, f"only {n_unique} distinct names — parser regression?"


def test_no_wind_strings_leaked_into_name(df: pl.DataFrame) -> None:
    """Names must not start with a leading sign-and-digit (regression for #wind-leak bug)."""
    leaked = df.filter(pl.col("name").str.contains(r"^[+\-]\d"))
    assert leaked.height == 0, (
        f"{leaked.height} names start with sign+digit (wind leaked into name): "
        f"{leaked['name'].head(5).to_list()}"
    )


def test_mark_value_never_null(df: pl.DataFrame) -> None:
    """Every parsed row must have a numeric mark — string ``mark_raw`` is always
    parseable into ``mark_value`` after the European-decimal/annotation cleanup."""
    nulls = df.filter(pl.col("mark_value").is_null())
    assert nulls.height == 0, (
        f"{nulls.height} rows have null mark_value; sample mark_raw="
        f"{nulls['mark_raw'].head(5).to_list()}"
    )


def test_source_url_includes_section_anchor(df: pl.DataFrame) -> None:
    """Per-row deep-link to Larsson's section: ``…htm#<anchor>``.

    Larsson doesn't anchor individual rows, but every section has an
    ``<A name="N">`` anchor that the page's Jump-to nav links to. Each row's
    ``source_url`` must include that anchor so users can deep-link from our
    table back to the originating section on the source page.
    """
    no_anchor = df.filter(~pl.col("source_url").str.contains("#"))
    assert no_anchor.height == 0, (
        f"{no_anchor.height} rows lack '#anchor' in source_url; e.g. "
        f"{no_anchor['source_url'].head(3).to_list()}"
    )
