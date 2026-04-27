"""Parse a single Larsson event page into a list of canonical row dicts.

Strategy
--------
1. HTML-unescape the entire page (handles ``&plusmn;``, ``&ouml;``, ``&aring;`` …).
2. Discover ``<A name="N"><H1|H3>section</H1></A>`` markers and ``<PRE>…</PRE>``
   blocks; pair each block with the most recent preceding section header.
3. Inside each block, treat each non-blank line as a candidate row.
4. Split the line on runs of 2+ whitespace (Larsson's columns are fixed-width).
5. Dispatch on event *family* to map tokens → canonical schema.

For relay events, rows alternate between a *team* line (has a leading rank) and
1–N *member* lines (indented; no leading rank). Members are accumulated and
attached to the preceding team row in a ``members`` list of dicts. We still
emit one row per team performance so the schema stays flat.

Unparseable lines are logged via the returned ``unparsed`` list — the caller
(usually a test) decides whether to fail loudly.
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


def _is_position(s: str) -> bool:
    return bool(_POSITION_RE.match(s)) or s in _BARE_POSITIONS
# Year-only dob: "97" -> 1997, "00" -> 2000.
_DOB_YEAR_ONLY_RE = re.compile(r"^\d{2}$")
# Country: 2-3 letters + optional trailing digit (e.g. CIS-era "URS"); accept
# mixed case to tolerate Larsson typos like 'BEl' for 'BEL'.
_COUNTRY_RE = re.compile(r"^[A-Za-z]{2,3}\d?$")
_WIND_RE = re.compile(r"^[+\-±]\d+\.\d+$")
_TOTAL_LINE_RE = re.compile(r"^\s*\d[\d\s,]*\s*total\s*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ParseResult:
    rows: list[dict[str, Any]]
    unparsed: list[str]


# --- public entry point ----------------------------------------------------------------


def parse_page(html_text: str, event: Event) -> ParseResult:
    """Parse one event page into canonical rows + a list of lines we couldn't read."""
    # Larsson uses '±' (HTML &plusmn;) for a zero-wind reading. Map it to '+'
    # so '±0.0' becomes '+0.0' and matches our wind regex. The sign distinction
    # at exactly zero has no physical meaning.
    text = html.unescape(html_text).replace("±", "+")

    sections = _extract_sections(text)
    rows: list[dict[str, Any]] = []
    unparsed: list[str] = []

    for section_name, block in sections:
        block_rows, block_unparsed = _parse_block(block, event, section_name)
        rows.extend(block_rows)
        unparsed.extend(block_unparsed)

    return ParseResult(rows=rows, unparsed=unparsed)


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
        # find latest section marker that ends before this pre block
        section = "main"
        for marker_end, title in section_markers:
            if marker_end <= pre_start:
                section = title
            else:
                break
        body = m.group(1)
        out.append((section, body))
    return out


# --- row parsing -----------------------------------------------------------------------


def _parse_block(
    block: str, event: Event, section: str
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    unparsed: list[str] = []
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
            row = _parse_relay_line(tokens, event, section)
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
                    unparsed.append(line)
                continue
            if pending_relay_row is not None:
                rows.append(pending_relay_row)
            pending_relay_row = row
            continue

        try:
            row = _parse_individual_line(tokens, event, section)
        except _UnparseableRow:
            unparsed.append(line)
            continue
        rows.append(row)

    if pending_relay_row is not None:
        rows.append(pending_relay_row)

    return rows, unparsed


class _UnparseableRow(Exception):
    """Sentinel for lines we can't make sense of."""


def _parse_individual_line(
    tokens: list[str], event: Event, section: str
) -> dict[str, Any]:
    # First token must be the rank.
    if not tokens or not tokens[0].isdigit():
        raise _UnparseableRow

    has_wind = event.family in ("track_time_wind", "field_distance_wind")
    rank = int(tokens[0])
    mark_raw = tokens[1]
    idx = 2

    wind: float | None = None
    if has_wind and idx < len(tokens) and _WIND_RE.match(tokens[idx]):
        wind = _parse_wind(tokens[idx])
        idx += 1

    # Walk from the end. Date is always last and venue is always second-to-last.
    if len(tokens) - idx < 3:
        raise _UnparseableRow
    date_str = tokens[-1]
    if not _DATE_RE.match(date_str):
        raise _UnparseableRow
    venue = tokens[-2]

    # `middle` = everything between mark/wind and venue/date.
    # Layout: name(s) ... country [dob?] [position?]
    middle = tokens[idx:-2]
    if not middle:
        raise _UnparseableRow

    # Country: rightmost token matching the IOC pattern, but not at the very
    # start (must come after at least one name token). Walk right→left.
    country_idx: int | None = None
    for i in range(len(middle) - 1, 0, -1):
        if _COUNTRY_RE.match(middle[i]):
            country_idx = i
            break

    if country_idx is None:
        # Fallback for unusually long names where the column padding got eaten:
        # the country code may be jammed onto the end of a name token (one
        # space separation). Look for "<word> XYZ" inside any middle token.
        country, name_tokens, after_country = _split_embedded_country(middle)
        if country is None:
            raise _UnparseableRow
    else:
        name_tokens = middle[:country_idx]
        after_country = middle[country_idx + 1:]
        country = middle[country_idx]

    # After country we have 0–2 tokens: optional dob, optional position.
    dob_str: str = ""
    position: str = ""
    if len(after_country) == 2:
        dob_str, position = after_country
    elif len(after_country) == 1:
        only = after_country[0]
        if _DOB_RE.match(only) or _DOB_YEAR_ONLY_RE.match(only):
            dob_str = only
        else:
            position = only
    elif len(after_country) > 2:
        # Unexpected: try to interpret first as dob, rest as joined position
        if _DOB_RE.match(after_country[0]) or _DOB_YEAR_ONLY_RE.match(after_country[0]):
            dob_str = after_country[0]
            position = " ".join(after_country[1:])
        else:
            position = " ".join(after_country)

    if not name_tokens:
        raise _UnparseableRow

    name = " ".join(name_tokens).strip()

    return {
        "event": event.label,
        "event_slug": event.slug,
        "sex": event.sex,
        "legality": event.legality,
        "family": event.family,
        "section": section,
        "rank": rank,
        "mark_raw": mark_raw,
        "mark_value": _normalize_mark(mark_raw, event.family),
        "wind": wind,
        "name": name,
        "country": country,
        "dob": _parse_dob(dob_str) if dob_str else None,
        "position": position,
        "venue": venue,
        "date": _parse_date(date_str),
        "members": None,
        "source_url": event.url,
    }


def _parse_relay_line(
    tokens: list[str], event: Event, section: str
) -> dict[str, Any] | None:
    """Relay team rows: rank, time, team_name, [position?], venue, date.

    Returns ``None`` if this looks like a member follow-up line.
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

    return {
        "event": event.label,
        "event_slug": event.slug,
        "sex": event.sex,
        "legality": event.legality,
        "family": event.family,
        "section": section,
        "rank": rank,
        "mark_raw": mark_raw,
        "mark_value": _normalize_mark(mark_raw, event.family),
        "wind": None,
        "name": team,
        "country": team,  # team name doubles as country for relays
        "dob": None,
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
        return candidate, name_tokens, middle[i + 1 :]
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
    member: dict[str, Any] = {"name": "", "split": None, "dob": None, "country": None}
    name_parts: list[str] = []
    for t in tokens:
        if t.startswith("(") and t.endswith(")"):
            member["split"] = t.strip("()")
        elif _DOB_RE.match(t):
            member["dob"] = _parse_dob(t)
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
        return datetime.strptime(s, "%d.%m.%Y").date()
    except ValueError:
        return None


def _parse_dob(s: str) -> date | None:
    """Parse 'dd.mm.yy' with a sensible century pivot.

    Year-only entries ('97') become Jan 1 of that year. Larsson uses 2-digit
    years; we assume 00-09 → 2000s, otherwise 19xx. Athletes born ≥ 2010 are
    currently miscoded as 19xx — we'll fix when (if) it matters.
    """
    if not s or s == "??":
        return None
    try:
        if _DOB_YEAR_ONLY_RE.match(s):
            year = int(s)
            century = 2000 if year < 10 else 1900
            return date(century + year, 1, 1)
        d, m, y = s.split(".")
        year = int(y)
        century = 2000 if year < 10 else 1900
        return date(century + year, int(m), int(d))
    except (ValueError, IndexError):
        return None


def _normalize_mark(raw: str, family: str) -> float | None:
    """Convert a printed mark to a numeric sort key.

    - track_time / track_time_wind: seconds (floats; supports h:mm:ss, m:ss.cc, ss.cc)
    - field_distance / field_distance_wind: metres (float)
    - combined_points: integer points → float
    - relay: seconds
    """
    s = raw.strip()
    # strip trailing annotations like 'a', 'd', '+', 'h', 'A', 'm'
    core = re.sub(r"[A-Za-z+*]+$", "", s)
    core = core.rstrip(".")
    if not core:
        return None

    if family in ("field_distance", "field_distance_wind"):
        try:
            return float(core)
        except ValueError:
            return None

    if family == "combined_points":
        try:
            return float(core)
        except ValueError:
            return None

    # track-style time
    parts = core.split(":")
    try:
        if len(parts) == 1:
            return float(parts[0])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        return None
    return None
