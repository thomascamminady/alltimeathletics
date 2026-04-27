"""Parse a single Larsson event page into a list of canonical row dicts.

Strategy
--------
1. HTML-unescape the entire page (handles ``&plusmn;``, ``&ouml;``, ``&aring;`` …).
2. Discover ``<A name="N"><H1|H3>section</H1></A>`` markers and ``<PRE>…</PRE>``
   blocks; pair each block with the most recent preceding section header.
3. Inside each block, treat each non-blank line as a candidate row.
4. Split the line on runs of 2+ whitespace (Larsson's columns are fixed-width).
5. Run the line through a fixed sequence of small extractors — one per logical
   field — and record a ``ParseDiagnostic`` for the first step that fails.

For relay events, rows alternate between a *team* line (has a leading rank) and
1–N *member* lines (indented; no leading rank). Members are accumulated and
attached to the preceding team row in a ``members`` list of dicts. We still
emit one row per team performance so the schema stays flat.

Failure surfaces
----------------
- ``ParseResult.rows``        successfully parsed rows
- ``ParseResult.diagnostics`` per-line, per-step reason for every failure
- ``ParseResult.unparsed``    backwards-compatible view of the failed line text

Each step extractor either returns its value (possibly ``None`` for legitimate
absences like a missing wind reading) or raises ``_StepError`` with a step name
and a human-readable reason. ``_parse_block`` catches the error and turns it
into a structured diagnostic so callers can aggregate failures by step name and
spot drift before it pollutes the parquet.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from alltimeathletics.events import Event

# --- regex toolkit ---------------------------------------------------------------------

_SECTION_RE = re.compile(
    r'<a\s+name="(\d+)"[^>]*>\s*<h[13][^>]*>(.*?)</h[13]>',
    re.IGNORECASE | re.DOTALL,
)
_PRE_RE = re.compile(
    # Larsson's HTML occasionally drops the '<' on the closing tag (see mmaraok.htm:6348).
    # Stop at any close-pre variant, the next section anchor, or end of document.
    r"<pre>(.*?)(?=</?pre>|<a\s+name=\"\d+\"|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_TAGS_RE = re.compile(r"<[^>]+>")
_MULTISPACE_RE = re.compile(r"\s{2,}")
_DATE_RE = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}$")
_DOB_RE = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{2}$")
# Position labels seen in the wild: '1', '1=', '2=', '1h2' (heat 2), '1s1'
# (semi 1), '1q1' (quarter 1), '1r1' (race 1), '1cA' (race walk class A),
# '1rA', '2e1' (event 1), '1-19' (place 1, race 19). Position can also be
# entirely missing or a single letter ('q', 'h', '-'). The regex is strict
# about digit-leading forms (so it doesn't match team names like 'Jamaica');
# the bare-letter forms are checked separately via _BARE_POSITIONS.
_POSITION_RE = re.compile(r"^\d[\dA-Za-z=\-]*$")
_BARE_POSITIONS = frozenset({"q", "h", "-", "Q"})
# Year-only dob: "97" -> 1997, "00" -> 2000.
_DOB_YEAR_ONLY_RE = re.compile(r"^\d{2}$")
# Country: 2-3 letters + optional trailing digit (e.g. CIS-era "URS"); accept
# mixed case to tolerate Larsson typos like 'BEl' for 'BEL'.
_COUNTRY_RE = re.compile(r"^[A-Za-z]{2,3}\d?$")
_WIND_RE = re.compile(r"^[+\-±]\d+\.\d+$")
_TOTAL_LINE_RE = re.compile(r"^\s*\d[\d\s,]*\s*total\s*$", re.IGNORECASE)


def _is_position(s: str) -> bool:
    return bool(_POSITION_RE.match(s)) or s in _BARE_POSITIONS


# --- diagnostics + result --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParseDiagnostic:
    """Why one line failed to parse, structured for aggregation by step name."""

    section: str
    line: str
    step: str
    reason: str


@dataclass(frozen=True, slots=True)
class ParseResult:
    rows: list[dict[str, Any]]
    diagnostics: list[ParseDiagnostic]

    @property
    def unparsed(self) -> list[str]:
        """Raw text of every line we couldn't read (back-compat with v0.1 callers)."""
        return [d.line for d in self.diagnostics]


class _StepError(Exception):
    """Raised by an extractor; converted to a ``ParseDiagnostic`` by the orchestrator."""

    def __init__(self, step: str, reason: str) -> None:
        super().__init__(f"{step}: {reason}")
        self.step = step
        self.reason = reason


# --- public entry point ----------------------------------------------------------------


def parse_page(html_text: str, event: Event) -> ParseResult:
    """Parse one event page into canonical rows + per-line diagnostics."""
    # Larsson uses '±' (HTML &plusmn;) for a zero-wind reading. Map it to '+'
    # so '±0.0' becomes '+0.0' and matches our wind regex. The sign distinction
    # at exactly zero has no physical meaning.
    text = html.unescape(html_text).replace("±", "+")

    sections = _extract_sections(text)
    rows: list[dict[str, Any]] = []
    diagnostics: list[ParseDiagnostic] = []

    for section_name, block in sections:
        block_rows, block_diags = _parse_block(block, event, section_name)
        rows.extend(block_rows)
        diagnostics.extend(block_diags)

    return ParseResult(rows=rows, diagnostics=diagnostics)


# --- section discovery -----------------------------------------------------------------


def _extract_sections(text: str) -> list[tuple[str, str]]:
    """Return [(section_name, pre_block_text), …] in document order.

    A PRE block is attributed to the most recent ``<A name="N">`` marker that
    appears before it. If no such marker exists (rare), it gets the section
    name "main".
    """
    section_markers: list[tuple[int, str]] = []
    for m in _SECTION_RE.finditer(text):
        title = _TAGS_RE.sub("", m.group(2)).strip()
        section_markers.append((m.end(), title or "main"))

    out: list[tuple[str, str]] = []
    for m in _PRE_RE.finditer(text):
        pre_start = m.start()
        section = "main"
        for marker_end, title in section_markers:
            if marker_end <= pre_start:
                section = title
            else:
                break
        body = m.group(1)
        out.append((section, body))
    return out


# --- block-level orchestrator ----------------------------------------------------------


def _parse_block(
    block: str, event: Event, section: str
) -> tuple[list[dict[str, Any]], list[ParseDiagnostic]]:
    rows: list[dict[str, Any]] = []
    diagnostics: list[ParseDiagnostic] = []
    pending_relay_row: dict[str, Any] | None = None

    for raw in block.splitlines():
        # Strip stray inline tags (e.g. '</Font>' that leaks into a row body
        # when Larsson forgets to close a font tag earlier on the page).
        line = _TAGS_RE.sub("", raw).rstrip()
        if not line.strip():
            continue
        if _TOTAL_LINE_RE.match(line):
            continue
        # Skip the 'Jump to:' nav block that occasionally leaks into a PRE
        # block on pages with multiple sub-lists.
        if "Jump to:" in line:
            continue
        # Skip stray HTML fragments left over from malformed pages
        # (e.g. mmaraok.htm has a literal '/pre>' line near the end).
        if line.lstrip().startswith(("<", "/")):
            continue

        tokens = _MULTISPACE_RE.split(line.strip())

        if event.family == "relay":
            row = _try_parse_relay_line(tokens, event, section)
            if row is None:
                # try as a member of the previous team line
                if pending_relay_row is not None and _looks_like_relay_member(tokens):
                    pending_relay_row.setdefault("members", []).append(
                        _parse_relay_member(tokens)
                    )
                elif _is_orphan_country_line(tokens):
                    # Larsson sometimes has a stray country-code line with no
                    # athlete name. Silently drop it.
                    pass
                else:
                    diagnostics.append(
                        ParseDiagnostic(
                            section=section,
                            line=line,
                            step="relay_line",
                            reason="line did not match team or member shape",
                        )
                    )
                continue
            if pending_relay_row is not None:
                rows.append(pending_relay_row)
            pending_relay_row = row
            continue

        try:
            row = _parse_individual_line(tokens, event, section)
        except _StepError as exc:
            diagnostics.append(
                ParseDiagnostic(
                    section=section, line=line, step=exc.step, reason=exc.reason,
                )
            )
            continue
        rows.append(row)

    if pending_relay_row is not None:
        rows.append(pending_relay_row)

    return rows, diagnostics


# --- step extractors (individual events) -----------------------------------------------
#
# Each extractor is a small, pure function that returns its value or raises a
# _StepError with a (step, reason) pair. _parse_individual_line composes them
# in a fixed order; that order documents the line layout from the leading rank
# down to the trailing date.


def _extract_rank(tokens: list[str]) -> int:
    if not tokens:
        raise _StepError("rank", "empty token list")
    if not tokens[0].isdigit():
        raise _StepError("rank", f"leading token {tokens[0]!r} is not a rank")
    return int(tokens[0])


def _extract_mark(tokens: list[str]) -> str:
    if len(tokens) < 2:
        raise _StepError("mark", "no mark token after rank")
    return tokens[1]


def _maybe_extract_wind(tokens: list[str], idx: int) -> tuple[float | None, int]:
    """Optional wind reading at ``tokens[idx]``; returns (wind, new_cursor)."""
    if idx < len(tokens) and _WIND_RE.match(tokens[idx]):
        return _parse_wind(tokens[idx]), idx + 1
    return None, idx


def _extract_tail(
    tokens: list[str], idx: int
) -> tuple[list[str], date | None, str]:
    """Pull the trailing date+venue off the line.

    Returns (middle_tokens, parsed_date, venue). ``parsed_date`` may be None
    if the date string matches the regex but ``strptime`` rejects it (e.g. a
    typo'd day-of-month) — that mirrors v0.1 behaviour and lets the rest of
    the row through.
    """
    if len(tokens) - idx < 3:
        raise _StepError(
            "tail",
            f"only {len(tokens) - idx} tokens left, need ≥3 for name+venue+date",
        )
    date_str = tokens[-1]
    if not _DATE_RE.match(date_str):
        raise _StepError("date", f"last token {date_str!r} does not match dd.mm.yyyy")
    venue = tokens[-2]
    middle = tokens[idx:-2]
    if not middle:
        raise _StepError("name", "no tokens between mark/wind and venue")
    return middle, _parse_date(date_str), venue


def _extract_country(middle: list[str]) -> tuple[str, list[str], list[str]]:
    """Locate the country code; return (country, name_tokens, after_country_tokens).

    The country code is the rightmost IOC-shaped token in ``middle`` that is
    not at index 0 (must come after at least one name token). Falls back to
    splitting a name+country fused token by single-space gap.
    """
    for i in range(len(middle) - 1, 0, -1):
        if _COUNTRY_RE.match(middle[i]):
            return middle[i], middle[:i], middle[i + 1:]

    country, name_tokens, after_country = _split_embedded_country(middle)
    if country is None:
        raise _StepError("country", "no IOC-shaped country code in middle tokens")
    return country, name_tokens, after_country


def _classify_after_country(after_country: list[str]) -> tuple[str, str]:
    """Map the 0-N tokens after the country code to (dob_str, position).

    Layout: ``[dob?] [position?]`` — either, both, or neither, plus a
    fall-through for the rare 3+ token case where dob is at index 0 and the
    rest is a multi-word position.
    """
    if len(after_country) == 2:
        dob_str, position = after_country
        return dob_str, position
    if len(after_country) == 1:
        only = after_country[0]
        if _DOB_RE.match(only) or _DOB_YEAR_ONLY_RE.match(only):
            return only, ""
        return "", only
    if len(after_country) > 2:
        if _DOB_RE.match(after_country[0]) or _DOB_YEAR_ONLY_RE.match(after_country[0]):
            return after_country[0], " ".join(after_country[1:])
        return "", " ".join(after_country)
    return "", ""


def _assemble_name(name_tokens: list[str]) -> str:
    if not name_tokens:
        raise _StepError("name", "no tokens left for athlete name")
    return " ".join(name_tokens).strip()


def _parse_individual_line(
    tokens: list[str], event: Event, section: str
) -> dict[str, Any]:
    rank = _extract_rank(tokens)
    mark_raw = _extract_mark(tokens)
    cursor = 2

    has_wind = event.family in ("track_time_wind", "field_distance_wind")
    wind: float | None = None
    if has_wind:
        wind, cursor = _maybe_extract_wind(tokens, cursor)

    middle, parsed_date, venue = _extract_tail(tokens, cursor)
    country, name_tokens, after_country = _extract_country(middle)
    dob_str, position = _classify_after_country(after_country)
    name = _assemble_name(name_tokens)
    dob_value, dob_precision = _parse_dob_with_precision(dob_str, parsed_date)
    mark_value, mark_annotation = _normalize_mark_with_annotation(mark_raw, event.family)

    return {
        "event": event.label,
        "event_slug": event.slug,
        "sex": event.sex,
        "legality": event.legality,
        "family": event.family,
        "section": section,
        "rank": rank,
        "mark_raw": mark_raw,
        "mark_value": mark_value,
        "mark_annotation": mark_annotation,
        "wind": wind,
        "name": name,
        "country": country,
        "dob": dob_value,
        "dob_precision": dob_precision,
        "position": position,
        "venue": venue,
        "date": parsed_date,
        "members": None,
        "source_url": event.url,
    }


# --- relay extractors ------------------------------------------------------------------


def _try_parse_relay_line(
    tokens: list[str], event: Event, section: str
) -> dict[str, Any] | None:
    """Return a relay team row, or ``None`` if this line is not a team line.

    Returning None is a routing signal (try the line as a member-of-previous),
    not a parse failure. True parse failures bubble up via the caller.
    """
    if not tokens or not tokens[0].isdigit():
        return None
    if len(tokens) < 4:
        return None

    rank = int(tokens[0])
    mark_raw = tokens[1]

    date_str = tokens[-1]
    if not _DATE_RE.match(date_str):
        return None
    venue = tokens[-2]

    # Position is optional on relay pages — many non-legal listings omit it.
    after_team = tokens[-3]
    if _is_position(after_team) and len(tokens) >= 5:
        position = after_team
        team_tokens = tokens[2:-3]
    else:
        position = ""
        team_tokens = tokens[2:-2]

    if not team_tokens:
        return None
    team = " ".join(team_tokens).strip()

    mark_value, mark_annotation = _normalize_mark_with_annotation(mark_raw, event.family)
    return {
        "event": event.label,
        "event_slug": event.slug,
        "sex": event.sex,
        "legality": event.legality,
        "family": event.family,
        "section": section,
        "rank": rank,
        "mark_raw": mark_raw,
        "mark_value": mark_value,
        "mark_annotation": mark_annotation,
        "wind": None,
        "name": team,
        # Country is intentionally null for relays so it stays a clean IOC
        # 2-3-char code across the whole parquet. The team name lives in `name`.
        "country": None,
        "dob": None,
        "dob_precision": None,
        "position": position,
        "venue": venue,
        "date": _parse_date(date_str),
        "members": [],
        "source_url": event.url,
    }


def _split_embedded_country(
    middle: list[str],
) -> tuple[str | None, list[str], list[str]]:
    """Recover from name+country fused into one token by single-space gaps.

    Walk the middle tokens; if any token ends with a space-separated 2-3 letter
    code (e.g. 'Hallgrímsdottír ISL'), peel that code off as the country and
    return (country, name_tokens, after_country).
    """
    for i, tok in enumerate(middle):
        # Country code is at most 3 letters + an optional digit.
        m = re.search(r"\s([A-Za-z]{2,3}\d?)$", tok)
        if not m:
            continue
        candidate = m.group(1)
        # Don't peel off short uppercase fragments unless they're really IOC-shaped
        if not _COUNTRY_RE.match(candidate):
            continue
        head = tok[: m.start()].strip()
        if not head:
            continue
        name_tokens = middle[:i] + [head]
        return candidate, name_tokens, middle[i + 1:]
    return None, [], []


def _is_orphan_country_line(tokens: list[str]) -> bool:
    return len(tokens) == 1 and bool(_COUNTRY_RE.match(tokens[0]))


def _looks_like_relay_member(tokens: list[str]) -> bool:
    # Member line: 3-4 tokens like name, (split), dob, country
    if len(tokens) < 2 or len(tokens) > 5:
        return False
    if tokens[0].isdigit():
        return False
    return any(_COUNTRY_RE.match(t) for t in tokens)


def _parse_relay_member(tokens: list[str]) -> dict[str, Any]:
    member: dict[str, Any] = {
        "name": "",
        "split": None,
        "dob": None,
        "dob_precision": None,
        "country": None,
    }
    name_parts: list[str] = []
    for t in tokens:
        if t.startswith("(") and t.endswith(")"):
            member["split"] = t.strip("()")
        elif _DOB_RE.match(t):
            member["dob"], member["dob_precision"] = _parse_dob_with_precision(t)
        elif _COUNTRY_RE.match(t):
            member["country"] = t
        else:
            name_parts.append(t)
    member["name"] = " ".join(name_parts).strip()
    return member


# --- value normalizers -----------------------------------------------------------------


def _parse_wind(s: str) -> float | None:
    s = s.strip()
    if s in ("", "?"):
        return None
    try:
        return float(s.replace("±", ""))
    except ValueError:
        return None


def _parse_date(s: str) -> date | None:
    try:
        d = datetime.strptime(s, "%d.%m.%Y").date()
    except ValueError:
        return None
    # Larsson's pages occasionally have typos like '09.08.2925' for 2025;
    # drop anything outside the plausible window rather than poison filters.
    if d.year < 1900 or d.year > date.today().year + 1:
        return None
    return d


def _parse_dob_with_precision(
    s: str, performance_date: date | None = None
) -> tuple[date | None, str | None]:
    """Parse 'dd.mm.yy' or year-only 'yy'; return (date, precision).

    ``precision`` is one of:
    - ``"day"``  for full ``dd.mm.yy`` entries — the date is real
    - ``"year"`` for year-only entries — month/day are fabricated as Jan 1
    - ``None``   when nothing parseable was provided

    Larsson uses 2-digit years (1900s OR 2000s — ambiguous). When the
    performance date is known, we pick the century that yields a plausible
    athlete age (5–100). Without it we fall back to a sliding cutoff anchored
    on today's year so freshly-competing teens born after 2010 don't get
    miscoded as 100-year-olds.
    """
    if not s or s == "??":
        return None, None
    try:
        if _DOB_YEAR_ONLY_RE.match(s):
            year = int(s)
            century = _pick_century(year, performance_date)
            return date(century + year, 1, 1), "year"
        d, m, y = s.split(".")
        year = int(y)
        century = _pick_century(year, performance_date)
        return date(century + year, int(m), int(d)), "day"
    except (ValueError, IndexError):
        return None, None


def _pick_century(year_two_digit: int, performance_date: date | None) -> int:
    """Choose 1900 or 2000 for a 2-digit year given an optional context date."""
    if performance_date is not None:
        cand_1900 = 1900 + year_two_digit
        cand_2000 = 2000 + year_two_digit
        age_1900 = performance_date.year - cand_1900
        age_2000 = performance_date.year - cand_2000
        if 5 <= age_1900 <= 100 and not (5 <= age_2000 <= 100):
            return 1900
        if 5 <= age_2000 <= 100 and not (5 <= age_1900 <= 100):
            return 2000
    cutoff = date.today().year % 100
    return 2000 if year_two_digit <= cutoff else 1900


_MARK_ANNOTATION_RE = re.compile(r"[A-Za-z+*]+$")


def _split_mark_annotation(raw: str) -> tuple[str, str | None]:
    """Strip trailing annotation letters/symbols from a mark string.

    Examples:
    - ``"9.79A"``     → (``"9.79"``,    ``"A"``)    altitude-aided
    - ``"9.78*"``     → (``"9.78"``,    ``"*"``)    later disqualified
    - ``"10.10h"``    → (``"10.10"``,   ``"h"``)    hand-timed
    - ``"2:00:35"``   → (``"2:00:35"``, ``None``)
    """
    s = raw.strip()
    m = _MARK_ANNOTATION_RE.search(s)
    if m is None:
        return s, None
    return s[: m.start()], m.group(0)


def _normalize_mark_with_annotation(
    raw: str, family: str
) -> tuple[float | None, str | None]:
    """Convert a printed mark to ``(numeric_value, annotation)``.

    - track_time / track_time_wind: seconds (floats; supports h:mm:ss, m:ss.cc, ss.cc)
    - field_distance / field_distance_wind: metres (float)
    - combined_points: integer points → float
    - relay: seconds

    The annotation (e.g. ``"A"`` for altitude, ``"h"`` for hand-timed,
    ``"*"`` for later DQ) is preserved as a separate column so consumers
    can filter or weight on it instead of having to re-parse ``mark_raw``.
    """
    core, annotation = _split_mark_annotation(raw)
    core = core.rstrip(".")
    if not core:
        return None, annotation

    if family in ("field_distance", "field_distance_wind"):
        try:
            return float(core), annotation
        except ValueError:
            return None, annotation

    if family == "combined_points":
        try:
            return float(core), annotation
        except ValueError:
            return None, annotation

    # track-style time
    parts = core.split(":")
    try:
        if len(parts) == 1:
            return float(parts[0]), annotation
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1]), annotation
        if len(parts) == 3:
            return (
                int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2]),
                annotation,
            )
    except ValueError:
        return None, annotation
    return None, annotation
