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
from alltimeathletics.parse import (
    ParseDiagnostic,
    _extract_sections,
    _is_legend_title,
    parse_page,
)

# Step names produced by the individual + relay extractors. New steps must be
# added here so a typo in one of them is caught instead of silently flying.
KNOWN_STEPS = frozenset({"rank", "mark", "wind", "tail", "date", "country", "name", "relay_line"})

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


# --- legend-title vs. section-title resolution (issue 1.1) ------------------------------

# Minimal reproduction of mhmaraok's header: the Jump-to nav lists #1 = "main
# list", but anchor #1 is immediately followed by an <H3> that is actually a
# footnote *legend* ("a=slightly downhill"), not a section title.
_LEGEND_HTML = """\
<TD>Jump to:<BR>
<A HREF="#1">main list</a><br>
</TD>
<H1>All-time men's best half-marathon</H1>
<H5>@=uncertified course</H5>
<A name="1"><H3>a=slightly downhill</h3></A>
<H3>+ = en route in race at longer distance</H3>
<PRE>
        1      56:42      Jacob Kiplimo        UGA     14.11.00    1      Barcelona     16.02.2025
        2      57:30      Yomif Kejelcha       ETH     01.08.97    1      Valencia      27.10.2024
</PRE>
"""

# A genuinely stale nav: the inline <H3> names a real section ("indoors") that
# differs from the nav title for the same anchor. The inline title must win.
_STALE_NAV_HTML = """\
<TD>Jump to:<BR>
<A HREF="#3">manual timing</a><br>
</TD>
<A name="3"><H3>indoors</h3></A>
<PRE>
        1      56:42      Jacob Kiplimo        UGA     14.11.00    1      Barcelona     16.02.2025
</PRE>
"""


def test_is_legend_title() -> None:
    # Annotation-definition legends.
    assert _is_legend_title("a=slightly downhill")
    assert _is_legend_title("A = altitude")
    assert _is_legend_title("@=uncertified course")
    assert _is_legend_title("+ = en route in race at longer distance")
    assert _is_legend_title("* = something")
    assert _is_legend_title("± = wind-aided")
    # Genuine section titles must NOT be flagged.
    assert not _is_legend_title("main list")
    assert not _is_legend_title("indoors")
    assert not _is_legend_title("manual timing")
    assert not _is_legend_title("mixed competition")


def test_legend_inline_title_falls_back_to_nav() -> None:
    """A legend <H3> at anchor #1 must not override the nav's 'main list'."""
    sections = _extract_sections(_LEGEND_HTML)
    assert len(sections) == 1
    section_name, anchor, _body = sections[0]
    assert section_name == "main list"
    assert anchor == "1"


def test_legend_fallback_in_full_parse() -> None:
    event = by_slug("mhmaraok")
    result = parse_page(_LEGEND_HTML, event)
    assert result.rows, "expected the two data rows to parse"
    assert all(r["section"] == "main list" for r in result.rows)
    assert not any(r["section"] == "a=slightly downhill" for r in result.rows)


def test_stale_nav_still_prefers_inline_title() -> None:
    """The inline-title preference must survive: 'indoors' beats stale nav."""
    sections = _extract_sections(_STALE_NAV_HTML)
    assert len(sections) == 1
    section_name, anchor, _body = sections[0]
    assert section_name == "indoors"
    assert anchor == "3"


# A wind-legal 200m block where Larsson omitted the '+' on the wind reading
# of the first row ("1.4") but kept it on the second ("+0.9"). The unsigned
# value must still be read as wind, not absorbed into the athlete name.
_UNSIGNED_WIND_HTML = """\
<TD>Jump to:<BR>
<A HREF="#1">main list</a><br>
</TD>
<H1>All-time men's best 200 metres</H1>
<A name="1"><H3>main list</H3></A>
<PRE>
   1   20.27   1.4    Leon Reid     GBR   26.07.94   1h2   Birmingham   01.07.2018
   2   20.31   +0.9   Andre Ewers   JAM   07.06.95   1     Charlottesville   11.05.2019
</PRE>
"""


def test_unsigned_wind_is_not_absorbed_into_name() -> None:
    event = by_slug("m_200ok")
    rows = parse_page(_UNSIGNED_WIND_HTML, event).rows
    assert len(rows) == 2
    first, second = rows
    # Unsigned wind read correctly; name is clean (no leading "1.4").
    assert first["wind"] == 1.4
    assert first["name"] == "Leon Reid"
    # Signed wind still works.
    assert second["wind"] == 0.9
    assert second["name"] == "Andre Ewers"


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
