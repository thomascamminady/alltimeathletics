"""Audit-as-test: assert every parser mismatch is catalogued.

Adapter around ``scripts/audit_pages.py``. The script generates a markdown
report; this test re-runs the same logic in-process and fails if any new
mismatch appears that isn't in ``KNOWN_SOURCE_ISSUES``.

What this catches:

- **New parser regression** — a refactor breaks a page that used to parse
  cleanly. Surfaces as a hard failure (unexplained mismatch).
- **Catalogued issue silently fixed** — Larsson cleans up an upstream typo
  we documented; surfaces as a warning, not a failure, so a positive
  upstream change never breaks the weekly cron.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import audit_pages  # noqa: E402


@pytest.fixture(scope="module")
def audit_run() -> tuple[set[str], set[str]]:
    """Run the audit; return (mismatched_slugs, all_slugs_with_html)."""
    if not audit_pages.PARQUET.exists():
        pytest.skip(f"{audit_pages.PARQUET} not present; run `make scrape`")
    if not audit_pages.CACHE_DIR.exists():
        pytest.skip(f"{audit_pages.CACHE_DIR} not present; run pipeline with --cache-dir")
    import polars as pl

    df = pl.read_parquet(audit_pages.PARQUET)
    parquet_by_slug = {key[0]: sub for key, sub in df.group_by("event_slug")}
    mismatched: set[str] = set()
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
        if html_total != parquet_total or len(html_sections) != parquet_section_count:
            mismatched.add(ev.slug)
            continue
        if sub is not None:
            for section, anchor, _n in html_sections:
                first_html = rank1.get((section, anchor))
                first_parq = (
                    sub.filter(pl.col("section") == section).sort("rank").select("mark_raw").head(1)
                )
                if first_html is None or first_parq.is_empty():
                    continue
                import re

                tokens_2 = re.split(r"\s{2,}", first_html.strip())
                if len(tokens_2) < 3:
                    continue
                # Use the script's mark extractor so the test agrees with the
                # parser on rows where Larsson omitted the leading rank.
                html_mark = audit_pages._mark_from_html_line(first_html)
                if html_mark is not None and html_mark != first_parq.row(0)[0]:
                    mismatched.add(ev.slug)
                    break
    return mismatched, all_with_html


def test_no_unexplained_parser_mismatches(audit_run: tuple[set[str], set[str]]) -> None:
    """Every mismatching page must be catalogued in ``KNOWN_SOURCE_ISSUES``."""
    mismatched, _ = audit_run
    unexplained = mismatched - audit_pages.KNOWN_SOURCE_ISSUES.keys()
    assert not unexplained, (
        f"{len(unexplained)} pages mismatch without a catalogue entry: "
        f"{sorted(unexplained)} — fix the parser, or add an entry "
        f"explaining the upstream issue to KNOWN_SOURCE_ISSUES"
    )


def test_catalogued_issues_still_present(audit_run: tuple[set[str], set[str]]) -> None:
    """Warn (don't fail) when Larsson fixes a catalogued upstream typo.

    An upstream fix is good news — it should not break the weekly cron. We
    surface the stale entry as a warning so a human notices and prunes the
    catalogue on the next pass.
    """
    mismatched, all_with_html = audit_run
    fixed_upstream = (set(audit_pages.KNOWN_SOURCE_ISSUES.keys()) & all_with_html) - mismatched
    if fixed_upstream:
        warnings.warn(
            f"Larsson appears to have fixed these upstream — prune from "
            f"KNOWN_SOURCE_ISSUES: {sorted(fixed_upstream)}",
            stacklevel=2,
        )
