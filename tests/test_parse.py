"""Smoke + correctness tests for the parser, run against frozen HTML fixtures.

The fixtures cover one event per family so a layout drift in any family
(track time, field distance, combined points, relay, hurdles with wind)
will fail loudly in CI before it can pollute the parquet.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from alltimeathletics.events import by_slug
from alltimeathletics.parse import ParseDiagnostic, parse_page

# Step names produced by the individual + relay extractors. New steps must be
# added here so a typo in one of them is caught instead of silently flying.
KNOWN_STEPS = frozenset(
    {"rank", "mark", "wind", "tail", "date", "country", "name", "relay_line"}
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load(slug: str) -> tuple[str, Any]:
    html_text = (FIXTURE_DIR / f"{slug}.htm").read_text(encoding="latin-1")
    return html_text, by_slug(slug)


# (slug, min_rows, max_unparsed)
FIXTURE_EXPECTATIONS: list[tuple[str, int, int]] = [
    ("m_100ok", 4500, 10),
    ("m_110hok", 1500, 10),
    ("mmaraok", 5000, 30),
    ("mhighok", 1500, 10),
    ("mdecaok", 1000, 10),
    ("m4x100ok", 800, 10),
    ("m20kwok", 1000, 10),
    ("whepaok", 1000, 10),
]


@pytest.mark.parametrize("slug,min_rows,max_unparsed", FIXTURE_EXPECTATIONS)
def test_fixture_parses_cleanly(slug: str, min_rows: int, max_unparsed: int) -> None:
    html_text, event = _load(slug)
    result = parse_page(html_text, event)
    assert len(result.rows) >= min_rows, (
        f"{slug}: only {len(result.rows)} rows (expected ≥ {min_rows})"
    )
    assert len(result.unparsed) <= max_unparsed, (
        f"{slug}: {len(result.unparsed)} unparsed lines (max {max_unparsed}); "
        f"first: {result.unparsed[:3]}"
    )


def test_men_100m_top_mark_is_bolt() -> None:
    html_text, event = _load("m_100ok")
    result = parse_page(html_text, event)
    top = result.rows[0]
    assert top["rank"] == 1
    assert top["mark_raw"] == "9.58"
    assert top["mark_value"] == pytest.approx(9.58)
    assert top["mark_annotation"] is None
    assert top["name"] == "Usain Bolt"
    assert top["country"] == "JAM"
    assert top["wind"] == pytest.approx(0.9)
    assert top["venue"] == "Berlin"
    assert top["date"] == date(2009, 8, 16)
    assert top["dob"] == date(1986, 8, 21)
    assert top["dob_precision"] == "day"


def test_men_high_jump_top_mark_is_sotomayor() -> None:
    html_text, event = _load("mhighok")
    result = parse_page(html_text, event)
    top = result.rows[0]
    assert top["rank"] == 1
    assert top["mark_raw"] == "2.45"
    assert top["mark_value"] == pytest.approx(2.45)
    assert top["name"] == "Javier Sotomayor"
    assert top["country"] == "CUB"


def test_marathon_top_mark_is_kiptum() -> None:
    html_text, event = _load("mmaraok")
    result = parse_page(html_text, event)
    top = result.rows[0]
    assert top["rank"] == 1
    assert "Kiptum" in top["name"]
    assert top["country"] == "KEN"
    # 2:00:35 -> 7235 seconds
    assert top["mark_value"] == pytest.approx(2 * 3600 + 0 * 60 + 35)


def test_decathlon_marks_are_points() -> None:
    html_text, event = _load("mdecaok")
    result = parse_page(html_text, event)
    top = result.rows[0]
    assert top["mark_value"] is not None
    # Decathlon WR is 9126 (Ayden Owens-Delerme post-2024 cut, or Mayer 9126 etc.).
    # Just sanity-check that all marks are in a plausible decathlon-points range.
    for r in result.rows[:50]:
        assert r["mark_value"] is not None
        assert 6000 < r["mark_value"] < 10000


def test_relay_rows_have_team_name_and_optional_members() -> None:
    html_text, event = _load("m4x100ok")
    result = parse_page(html_text, event)
    assert len(result.rows) > 0
    top = result.rows[0]
    assert top["family"] == "relay"
    assert top["mark_value"] is not None
    # mark for top 4x100 should be under 40 seconds
    assert top["mark_value"] < 40
    # members may or may not appear depending on Larsson's formatting; if
    # populated, each entry should at least have a name.
    if top.get("members"):
        for m in top["members"]:
            assert "name" in m


def test_wind_values_in_plausible_range() -> None:
    """Wind on a 100m page should sit in a physically plausible band.

    Larsson's "legal" pages can still surface annotated marks with wind above
    +2.0 (e.g. '+2.8a' altitude marks), so we only assert outer sanity bounds.
    """
    html_text, event = _load("m_100ok")
    result = parse_page(html_text, event)
    winds = [r["wind"] for r in result.rows if r["wind"] is not None]
    assert winds, "expected at least some wind readings on a 100m page"
    assert min(winds) > -10.0 and max(winds) < 10.0


def test_dates_are_parsed_to_date_objects() -> None:
    html_text, event = _load("m_100ok")
    result = parse_page(html_text, event)
    for r in result.rows[:100]:
        assert isinstance(r["date"], date), f"bad date: {r['date']!r}"


@pytest.mark.parametrize("slug", [s for s, _, _ in FIXTURE_EXPECTATIONS])
def test_diagnostics_are_well_formed(slug: str) -> None:
    """Every parse failure carries a step name + reason we can aggregate by."""
    html_text, event = _load(slug)
    result = parse_page(html_text, event)
    for d in result.diagnostics:
        assert isinstance(d, ParseDiagnostic)
        assert d.line, f"diagnostic with empty line: {d!r}"
        assert d.reason, f"diagnostic with empty reason: {d!r}"
        assert d.step in KNOWN_STEPS, f"unknown step name {d.step!r} in {slug}"
    # Backward-compat view: unparsed lines == diagnostic lines, in order.
    assert result.unparsed == [d.line for d in result.diagnostics]
