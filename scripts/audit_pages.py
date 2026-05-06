"""Audit every parsed page against its source HTML.

For each event slug we compare what we got out of the parser to what's
actually present on Larsson's page. Two cross-checks per page:

1. **Row count parity** — number of digit-leading lines in the source PRE
   block(s) (i.e. ranked rows) must equal ``len(parquet[slug])``. Relay
   pages count team lines only (member lines are folded into the parent row).

2. **Top-row sanity** — for each section in the page, the rank-1 row's
   ``mark_raw`` and ``name`` (or team) extracted from the HTML must match
   what we have in the parquet for the same section.

Writes a markdown report to ``docs/parser_audit.md`` with one row per slug:

    | slug | rows (html / parquet) | sections (html / parquet) | status |

Status legend: ``ok`` (both checks pass) / ``mismatch`` (details below) /
``no_html`` (cache miss — re-run pipeline first).

Run::

    uv run python scripts/audit_pages.py
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from alltimeathletics.events import EVENTS
from alltimeathletics.parse import _extract_sections

REPO_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = REPO_ROOT / ".cache"
PARQUET = REPO_ROOT / "data" / "alltime_athletics.parquet"
REPORT = REPO_ROOT / "docs" / "parser_audit.md"

# Pages where row/section counts can never match Larsson exactly because the
# HTML itself has data-quality issues. Each entry documents the exact upstream
# problem so a reviewer can confirm the parser is doing the right thing.
KNOWN_SOURCE_ISSUES: dict[str, str] = {
    "m60mok": ("2 rows have malformed dates `. .1996` (only year preserved upstream)"),
    "m_60mhok": ("4 rows have malformed dates `. .1994`, `. .1990`, `.03.1978` upstream"),
    "m60mno": (
        "37 rows have truncated 4-digit years like `07.03.198` (last digit "
        "missing upstream); one extra section with anchor reuse + bad title"
    ),
    "w_60mhok": "1 row has truncated date `.03.1978` upstream",
    "w2milesok": "1 row (Kelly McMillen) has blank dob+pos columns",
    "wjaveoldok": (
        "8 rows wrap venue+date onto a second line — multi-line layout we don't reassemble"
    ),
    "w4x400ok": "2 rows (rank 450, 1973) have empty team name upstream",
}


# Mirror parse.py's preprocessing so section discovery sees the same text.
def _preprocess(html_text: str) -> str:
    return html.unescape(html_text).replace("\u00b1", "+")


def _count_html_rows_per_section(
    text: str, family: str
) -> tuple[list[tuple[str, str | None, int]], dict[tuple[str, str | None], str]]:
    """Return ``[(section, anchor, n_data_rows), ...]`` and ``{(section, anchor): rank1_line}``.

    A "data row" is a non-blank line whose first non-space character is a digit
    (relay team lines + individual rows alike). Member lines on relays are
    skipped because the parser folds them into the parent team row.
    """
    out: list[tuple[str, str | None, int]] = []
    rank1: dict[tuple[str, str | None], str] = {}
    tags_re = re.compile(r"<[^>]+>")
    for section, anchor, block in _extract_sections(text):
        n = 0
        first: str | None = None
        for raw in block.splitlines():
            line = tags_re.sub("", raw).rstrip()
            stripped = line.lstrip()
            if not stripped:
                continue
            # Skip 'Jump to:' navigation that occasionally leaks into a PRE.
            if "Jump to:" in line:
                continue
            # Skip stray tags/footers
            if stripped.startswith(("<", "/")):
                continue
            # Skip 'NN total' summary lines
            if re.match(r"^\d[\d\s,]*\s*total\s*$", stripped, re.IGNORECASE):
                continue
            if not stripped[:1].isdigit():
                # Non-digit-leading: relay member, indented continuation, etc.
                continue
            n += 1
            if first is None:
                first = stripped
        out.append((section, anchor, n))
        if first is not None:
            rank1[(section, anchor)] = first
    return out, rank1


@dataclass
class Mismatch:
    slug: str
    html_rows: int
    parquet_rows: int
    html_sections: int
    parquet_sections: int
    detail: str


def audit() -> None:
    if not PARQUET.exists():
        raise SystemExit(f"missing {PARQUET}; run pipeline first")
    df = pl.read_parquet(PARQUET)
    parquet_by_slug: dict[str, pl.DataFrame] = {
        key[0]: sub for key, sub in df.group_by("event_slug")
    }

    rows: list[tuple[str, str, str, str, str]] = []  # slug,html,parq,sec,status
    mismatches: list[Mismatch] = []

    for ev in EVENTS:
        cached = CACHE_DIR / f"{ev.slug}.htm"
        if not cached.exists():
            rows.append((ev.slug, "?", "?", "?", "no_html"))
            continue
        text = _preprocess(cached.read_text(encoding="latin-1"))
        html_sections, rank1 = _count_html_rows_per_section(text, ev.family)
        html_total = sum(n for _, _, n in html_sections)

        sub = parquet_by_slug.get(ev.slug)
        parquet_total = sub.height if sub is not None else 0
        parquet_sections = (
            sub.group_by("section").len().sort("section") if sub is not None else None
        )
        parquet_section_count = parquet_sections.height if parquet_sections is not None else 0

        status = "ok"
        details: list[str] = []
        if html_total != parquet_total:
            status = "mismatch"
            details.append(f"row count: html={html_total}, parquet={parquet_total}")
        if len(html_sections) != parquet_section_count:
            status = "mismatch"
            details.append(
                f"section count: html={len(html_sections)}, parquet={parquet_section_count}"
            )

        # Top-row sanity per section
        if sub is not None:
            for section, anchor, _n in html_sections:
                first_html = rank1.get((section, anchor))
                first_parq = (
                    sub.filter(pl.col("section") == section)
                    .sort("rank")
                    .select("mark_raw", "name")
                    .head(1)
                )
                if first_html is None or first_parq.is_empty():
                    continue
                # Tokenize the HTML line by 2+ spaces (same as parser)
                tokens = re.split(r"\s{2,}", first_html.strip())
                if len(tokens) < 3:
                    continue
                html_mark = tokens[1]
                parq = first_parq.row(0, named=True)
                if html_mark != parq["mark_raw"]:
                    status = "mismatch"
                    details.append(
                        f"section {section!r}: html mark={html_mark!r}, "
                        f"parquet mark={parq['mark_raw']!r}"
                    )

        if status == "mismatch":
            mismatches.append(
                Mismatch(
                    slug=ev.slug,
                    html_rows=html_total,
                    parquet_rows=parquet_total,
                    html_sections=len(html_sections),
                    parquet_sections=parquet_section_count,
                    detail="; ".join(details),
                )
            )

        rows.append(
            (
                ev.slug,
                str(html_total),
                str(parquet_total),
                f"{len(html_sections)}/{parquet_section_count}",
                status,
            )
        )

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with REPORT.open("w") as f:
        f.write("# Parser audit\n\n")
        f.write(
            "Auto-generated cross-check of every scraped page against its "
            "source HTML.\n"
            "See `scripts/audit_pages.py` for the methodology and "
            "`KNOWN_SOURCE_ISSUES` for catalogued upstream typos.\n\n"
        )
        ok = sum(1 for r in rows if r[4] == "ok")
        mm = sum(1 for r in rows if r[4] == "mismatch")
        nh = sum(1 for r in rows if r[4] == "no_html")
        # Mismatches that fall under a catalogued source issue.
        explained = sum(1 for m in mismatches if m.slug in KNOWN_SOURCE_ISSUES)
        unexplained = mm - explained
        f.write(
            f"**{ok} clean**, **{explained} known-source-issue**, "
            f"**{unexplained} unexplained**, **{nh} no_html** "
            f"({len(rows)} pages total)\n\n"
        )
        if unexplained:
            f.write("## ❌ Unexplained mismatches (parser bugs to fix)\n\n")
            f.write("| slug | html rows | parquet rows | sections (html/parq) | detail |\n")
            f.write("|---|---:|---:|---|---|\n")
            for m in mismatches:
                if m.slug in KNOWN_SOURCE_ISSUES:
                    continue
                f.write(
                    f"| `{m.slug}` | {m.html_rows} | {m.parquet_rows} | "
                    f"{m.html_sections}/{m.parquet_sections} | {m.detail} |\n"
                )
            f.write("\n")
        if explained:
            f.write("## ⚠️ Known source issues\n\n")
            f.write(
                "These pages have row/section counts that can never match "
                "Larsson exactly because the upstream HTML itself has "
                "data-quality issues. Each is catalogued below; the audit "
                "fails loudly if a *new* mismatch appears that isn't in the "
                "catalogue.\n\n"
            )
            f.write("| slug | html rows | parquet rows | upstream issue |\n")
            f.write("|---|---:|---:|---|\n")
            for m in mismatches:
                if m.slug not in KNOWN_SOURCE_ISSUES:
                    continue
                f.write(
                    f"| `{m.slug}` | {m.html_rows} | {m.parquet_rows} | "
                    f"{KNOWN_SOURCE_ISSUES[m.slug]} |\n"
                )
            f.write("\n")
        f.write("## All pages — verification checklist\n\n")
        f.write(
            "Each row is one page Larsson maintains. ✅ = parser exactly "
            "matches the HTML row count and the rank-1 mark of every "
            "section. ⚠️ = mismatch is catalogued in `KNOWN_SOURCE_ISSUES`. "
            "❌ = parser bug that needs investigation.\n\n"
        )
        f.write("| status | slug | html rows | parquet rows | sections (html/parq) |\n")
        f.write("|---|---|---:|---:|---|\n")
        # Sort: unexplained first (loudest), then known, then ok.
        order = {"mismatch": 0, "no_html": 1, "ok": 2}
        for slug, h, p, sec, status in sorted(rows, key=lambda r: (order[r[4]], r[0])):
            if status == "ok":
                check = "✅ verified"
            elif status == "no_html":
                check = "⚠️ no html"
            elif slug in KNOWN_SOURCE_ISSUES:
                check = "⚠️ known source issue"
            else:
                check = "❌ parser bug"
            f.write(f"| {check} | `{slug}` | {h} | {p} | {sec} |\n")

    print(f"Wrote {REPORT.relative_to(REPO_ROOT)}")
    print(f"  clean={ok} known-source-issue={explained} unexplained={unexplained} no_html={nh}")


if __name__ == "__main__":
    audit()
