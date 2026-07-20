"""Where the line sits between "Larsson typed something odd" and "our parser broke".

This project mirrors a hand-maintained site that gains new rows every week.
Treating every new upstream quirk as a CI failure meant the weekly refresh went
red on routine noise, and a human had to hand-catalogue each new typo before
fresh data could land. That is the wrong trade: an upstream typo is *data to
mirror faithfully*, not a build break.

Policy
------
Data-quality anomalies are **reported, not fatal**, until there are enough of
them to mean *our code* broke rather than *the source page* changed. Each gate
gets a budget. The asymmetry that makes this safe: a real parser regression — a
bad century pivot, a layout change that guts a family, a tokenizer bug — produces
anomalies by the hundred and blows through any budget immediately. A Larsson typo
produces one or two.

Everything under budget is emitted as a warning, so it stays visible in test
output (and in the workflow's job summary) without blocking the mirror.

The structural gates are the real safety net and need no maintenance: the
pipeline's own unparsed-ratio ceiling, the schema tests, the per-event record
bounds, and the country-dominance checks all fail loudly on genuine breakage
regardless of what Larsson types this week.

Consequence: the ``KNOWN_*`` catalogues are now **documentation, not gates**.
They record issues someone already investigated, which keeps the warnings
meaningful. Nothing breaks when they drift out of date, so they never need to be
updated just to make CI pass.
"""

from __future__ import annotations

import warnings
from collections.abc import Collection
from typing import Any

# --- budgets ---------------------------------------------------------------------------
#
# Sized so routine upstream noise (a handful of rows) warns, while a systemic
# parser fault (which lands in the hundreds or thousands) still fails.

# Rows whose (dob, date) pair implies an impossible age. The century-pivot
# regression we actually shipped once produced ~40 of these; a truly broken
# pivot produces thousands. Set well above normal upstream noise (~30).
IMPLAUSIBLE_AGE_BUDGET = 250

# Rows dated absurdly far ahead. A garbled year affects a few rows; a broken
# date parser affects the whole page.
FAR_FUTURE_DATE_BUDGET = 50

# Distinct mark-annotation characters we have never seen before. Larsson adds
# one every few months; a tokenizer fault would spray dozens.
NEW_ANNOTATION_BUDGET = 25

# Parser-vs-source row mismatches, as a fraction of *that page's* rows. A page
# quietly losing more than this is a layout regression, not a typo.
SEVERE_PAGE_MISMATCH_FRACTION = 0.10

# Total mismatching rows across all pages, as a fraction of the whole dataset.
GLOBAL_MISMATCH_FRACTION = 0.005


def report_anomalies(
    items: Collection[Any],
    *,
    budget: int,
    label: str,
    hint: str = "",
    sample: int = 5,
) -> None:
    """Warn while ``items`` stays under ``budget``; fail once it reaches it.

    ``label`` should read as a plural noun phrase ("implausible-age rows"), so
    the assembled message is a sentence. ``hint`` is appended to tell a reader
    what to do about it.
    """
    n = len(items)
    if n == 0:
        return
    try:
        preview = sorted(items)[:sample]  # type: ignore[type-var]
    except TypeError:  # unorderable elements — take them as they come
        preview = list(items)[:sample]
    message = f"{n} {label} (budget {budget}); e.g. {preview}"
    if hint:
        message = f"{message} — {hint}"
    if n >= budget:
        raise AssertionError(
            f"{message}. This many at once means a parser regression, not upstream noise."
        )
    warnings.warn(message, stacklevel=2)


def report_catalogue_drift(missing: Collection[Any], *, catalogue: str) -> None:
    """Note catalogue entries that no longer match the data.

    Always a warning. An entry disappearing means Larsson *fixed* something
    upstream — good news that must never break the build. The catalogue is
    documentation; drift is a cue to prune it at leisure.
    """
    if not missing:
        return
    warnings.warn(
        f"{len(missing)} entries in {catalogue} no longer match the data "
        f"(likely fixed upstream) — prune when convenient: {sorted(missing)[:5]}",
        stacklevel=2,
    )
