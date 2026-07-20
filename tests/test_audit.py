"""Audit-as-test: compare what the parser produced against the source HTML.

Adapter around ``scripts/audit_pages.py``. The script generates a markdown
report; this test re-runs the same logic in-process.

What this catches
-----------------
- **Parser regression** — a refactor or an upstream layout change quietly guts a
  page. Surfaces as a hard failure once the loss is big enough to matter (see
  ``quality_policy``).
- **Routine upstream drift** — Larsson adds a row in a shape we don't reassemble,
  or renames a section. Surfaces as a warning; the weekly refresh stays green.

``KNOWN_SOURCE_ISSUES`` is documentation here, not a gate: it annotates pages
whose mismatch someone already explained, so the warnings stay meaningful. It
never needs updating just to make CI pass.
"""

from __future__ import annotations

import re
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.quality_policy import (
    GLOBAL_MISMATCH_FRACTION,
    SEVERE_PAGE_MISMATCH_FRACTION,
    report_catalogue_drift,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import audit_pages  # noqa: E402


@dataclass(frozen=True)
class PageMismatch:
    """One page whose parsed rows disagree with the source HTML."""

    slug: str
    html_rows: int
    parquet_rows: int
    reason: str

    @property
    def row_delta(self) -> int:
        return abs(self.html_rows - self.parquet_rows)

    @property
    def fraction(self) -> float:
        """Row delta as a share of the page's source rows."""
        return self.row_delta / self.html_rows if self.html_rows else 0.0

    def __str__(self) -> str:
        note = audit_pages.KNOWN_SOURCE_ISSUES.get(self.slug)
        suffix = f" [catalogued: {note}]" if note else ""
        return (
            f"{self.slug} ({self.reason}: html={self.html_rows} "
            f"parquet={self.parquet_rows}, {self.fraction:.1%}){suffix}"
        )


@dataclass(frozen=True)
class AuditRun:
    mismatches: list[PageMismatch]
    all_with_html: set[str]
    total_parquet_rows: int


@pytest.fixture(scope="module")
def audit_run() -> AuditRun:
    """Re-run the page audit in-process against the current parquet + HTML cache."""
    if not audit_pages.PARQUET.exists():
        pytest.skip(f"{audit_pages.PARQUET} not present; run `make scrape`")
    if not audit_pages.CACHE_DIR.exists():
        pytest.skip(f"{audit_pages.CACHE_DIR} not present; run pipeline with --cache-dir")
    import polars as pl

    df = pl.read_parquet(audit_pages.PARQUET)
    parquet_by_slug = {key[0]: sub for key, sub in df.group_by("event_slug")}
    mismatches: list[PageMismatch] = []
    all_with_html: set[str] = set()

    for ev in audit_pages.EVENTS:
        cached = audit_pages.CACHE_DIR / f"{ev.slug}.htm"
        if not cached.exists():
            continue
        all_with_html.add(ev.slug)
        text = audit_pages._preprocess(cached.read_text(encoding="latin-1"))
        html_sections, rank1 = audit_pages._count_html_rows_per_section(text, ev.family)
        html_total = sum(n for _, _, n in html_sections)
        sub = parquet_by_slug.get(ev.slug)
        parquet_total = sub.height if sub is not None else 0
        parquet_section_count = sub.group_by("section").len().height if sub is not None else 0

        if html_total != parquet_total:
            mismatches.append(PageMismatch(ev.slug, html_total, parquet_total, "row count"))
            continue
        if len(html_sections) != parquet_section_count:
            mismatches.append(PageMismatch(ev.slug, html_total, parquet_total, "section count"))
            continue
        if sub is None:
            continue
        for section, anchor, _n in html_sections:
            first_html = rank1.get((section, anchor))
            first_parq = (
                sub.filter(pl.col("section") == section).sort("rank").select("mark_raw").head(1)
            )
            if first_html is None or first_parq.is_empty():
                continue
            if len(re.split(r"\s{2,}", first_html.strip())) < 3:
                continue
            # Use the script's mark extractor so the test agrees with the
            # parser on rows where Larsson omitted the leading rank.
            html_mark = audit_pages._mark_from_html_line(first_html)
            if html_mark is not None and html_mark != first_parq.row(0)[0]:
                mismatches.append(PageMismatch(ev.slug, html_total, parquet_total, "top mark"))
                break

    return AuditRun(mismatches, all_with_html, df.height)


def test_parser_matches_source_within_budget(audit_run: AuditRun) -> None:
    """Fail only when the disagreement is big enough to mean the parser broke.

    A page losing a couple of rows out of thousands is upstream noise — Larsson
    hand-writes these pages and regularly adds a row in a shape we don't
    reassemble. A page losing a tenth of its rows, or the dataset losing half a
    percent overall, is a regression on our side.
    """
    # Pages with a catalogue entry are accepted deviations someone already
    # investigated and wrote up; they are reported but never gate the build.
    unexplained = [m for m in audit_run.mismatches if m.slug not in audit_pages.KNOWN_SOURCE_ISSUES]

    severe = [m for m in unexplained if m.fraction > SEVERE_PAGE_MISMATCH_FRACTION]
    assert not severe, (
        f"{len(severe)} page(s) disagree with the source by more than "
        f"{SEVERE_PAGE_MISMATCH_FRACTION:.0%} of their rows — that is a parser "
        f"regression, not upstream noise: {[str(m) for m in severe]}"
    )

    total_delta = sum(m.row_delta for m in unexplained)
    budget = max(1, int(audit_run.total_parquet_rows * GLOBAL_MISMATCH_FRACTION))
    assert total_delta < budget, (
        f"{total_delta} rows disagree with the source across "
        f"{len(unexplained)} uncatalogued page(s), over the {budget}-row budget "
        f"({GLOBAL_MISMATCH_FRACTION:.1%} of {audit_run.total_parquet_rows}): "
        f"{[str(m) for m in unexplained[:10]]}"
    )

    if audit_run.mismatches:
        all_delta = sum(m.row_delta for m in audit_run.mismatches)
        warnings.warn(
            f"{len(audit_run.mismatches)} page(s) disagree with the source by "
            f"{all_delta} row(s) total ({total_delta} of them uncatalogued, "
            f"budget {budget}) — within budget, mirroring upstream as-is: "
            f"{[str(m) for m in audit_run.mismatches[:10]]}",
            stacklevel=2,
        )


def test_catalogued_issues_still_present(audit_run: AuditRun) -> None:
    """Note catalogue entries Larsson has since fixed. Never fatal."""
    still_mismatching = {m.slug for m in audit_run.mismatches}
    fixed_upstream = (
        set(audit_pages.KNOWN_SOURCE_ISSUES.keys()) & audit_run.all_with_html
    ) - still_mismatching
    report_catalogue_drift(fixed_upstream, catalogue="KNOWN_SOURCE_ISSUES")
