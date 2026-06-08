# Audit & remaining work — 2026-06-08

A full read of the codebase, the rendered site, and the freshly-scraped data,
ranked by the project's priority order:
**(1) data accuracy → (2) simplicity & accessibility → (3) functionality.**

This supersedes the 2026-04-30 edition. Items resolved since then are listed at
the bottom so the history is visible.

---

## Tier 1 — data accuracy

### 1.1 `mhmaraok` main list is mislabeled `a=slightly downhill` 🐛 (NEW)
The men's half-marathon page opens with
`<A name="1"><H3>a=slightly downhill</h3></A>` — but that `<H3>` is a **footnote
legend** explaining the `a=` annotation, not a section title. The data that
follows is the canonical main list (the Jump-to nav correctly says
anchor 1 = `main list`). `parse._inline_title_for_anchor` prefers the inline
`<H3>`, so **all 4,211 rows get `section = "a=slightly downhill"`**.

Impact: the analytics page shows *"Canonical list: a=slightly downhill"*, the
SQL example queries' `section LIKE 'All-time%' OR 'main list'` filter misses the
event entirely, and `main_section` detection is wrong. The marks/names/dates
themselves are correct — it's a labeling bug, but it corrupts the `section`
column and every canonical-section consumer.

**Fix:** in `_inline_title_for_anchor` (or its caller), reject titles that look
like annotation legends — e.g. match `^.{1,3}\s*=` or a leading `+`/`*`/`a=` —
and fall back to the Jump-to nav title for that anchor. Add a fixture for
`mhmaraok` asserting `main list` is present. After fixing, re-check the broader
class: `section` values containing `=` or starting with `+`/`*`.

### 1.2 Six rows have a positive wind value bleeding into `name` 🐛 (STILL OPEN)
Larsson usually writes wind with a sign (`+1.4`) but occasionally omits it
(`1.4`). `_WIND_RE = ^[+\-±]\d+[.,]\d+$` requires the sign, so the unsigned
reading is left in the token stream and absorbed into the athlete name:

| event_slug | source mark / wind | parsed name |
|---|---|---|
| `m_200ok` | 20.27  `1.4` | `1.4 Leon Reid` |
| `m_200ok` | 20.31  `1.6` | `1.6 Andre Ewers` |
| `m_200ok` | 20.37  `0.5` | `0.5 Clement Campbell` |
| `m_200ok` | 20.38  `0.1` | `0.1 Jorge Henrique da Costa Vides` |
| `mtripok` | 17.29  `0.6` | `0.6 Pedro Pablo Pichardo` |
| `w_100no` | 11.04  `5.6` | `5.6 Kelliann Baptiste` |

The wind is lost, the name is wrong, and each produces a junk athlete page
(`/athlete/1-4-leon-reid.html`). **Fix:** in `_maybe_extract_wind`, also accept
an unsigned decimal (`^\d+[.,]\d+$`) at the wind cursor position for wind-family
events — a real name token is never a bare decimal there. Add a regression test:
no `name` matches `^\d+[.,]\d+\s`.

### 1.3 "Top mark" / WR holder is non-deterministic among tied marks (STILL OPEN)
`_compute_event_analytics` does `canonical.sort("mark_value", descending=...)`
(site.py:1204) and takes row 0. When several athletes share the top mark (51
sections have tied rank-1 rows — legitimate), which holder is shown depends on
parquet row order. **Fix:** add a stable tiebreaker — `sort(["mark_value",
"date", "name"])` — so the earliest-set (and then alphabetical) mark wins
consistently. Same pattern for the 10th/100th-place rows.

### 1.4 Catalogue-driven typo detection is still manual & time-coupled (PARTIALLY ADDRESSED)
`test_catalogued_typos_still_present` is now existence-based (good — it no longer
breaks when wall-clock passes a catalogued date). But `test_dates_make_sense`
still hard-fails the weekly cron whenever Larsson types a new future date, until
a human catalogues it. Consider: warn-and-summarize for dates ≤ ~30 days ahead
(plausibly a pre-loaded start list), hard-fail only for far-future dates (the
year-typo signature). Lower urgency now that the existence test is fixed.

### 1.5 Per-row date-precision is not surfaced (DESIGN GAP)
Year-only DOBs are stored as `YYYY-01-01` with `dob_precision='year'`, which is
good — but consumers of the parquet can't tell a real Jan-1 birthday from an
imputed one without reading `dob_precision`. This is documented behaviour;
just make sure the README's schema table calls it out (see 2.2).

---

## Tier 2 — simplicity & accessibility

All Tier 2 items (2.1 CI coverage, 2.2 README, 2.3 doc sprawl, 2.4 keyboard
table headers, 2.5 skip-link, 2.6 Open Graph tags, 2.7 touch-input borders)
were completed on 2026-06-08 — see the "Resolved" section below.

---

## Tier 3 — functionality

### 3.1 SQL playground renders every result row into the DOM 🐛 (NEW)
`renderTable` (sql.html) maps over the full result set with no cap. The default
example is fine, but `SELECT * FROM perf` returns ~375k rows → it builds one
giant HTML string and freezes/crashes the tab. **Fix:** cap the rendered rows
(e.g. first 1,000) with a "showing 1,000 of N — add LIMIT to see fewer" note;
keep the full count in `.sql-rowcount`.

### 3.2 No URL deep-linking on the analytics page
The WR-chart mode (Absolute / Δ / %) and which layers are toggled aren't
reflected in the URL, so you can't link someone to a specific view. Add a
`?mode=delta` query param (read on load, updated on toggle). Persisting the
last choice in `localStorage` would also carry it across events.

### 3.3 No per-event CSV download
The index links the global CSV/parquet, but an event page has no "download this
event" button. A client-side `Blob` from the already-loaded per-event JSON is a
small add.

### 3.4 Event grid ordering
Index events are sorted by distance within (sex, legality) now, which is good.
A grouped layout with subheadings (Sprints / Hurdles / Middle / Distance / Road
/ Field / Combined / Walks / Relays) would still help scanning; add a `category`
to `events.py` if pursued.

### 3.5 Age-vs-mark scatter caps at 800 points
Deep events (m_100ok has 5,000+ canonical marks) are sampled by best mark. A
"show all" toggle would let power users see the full cloud at the cost of page
weight.

### 3.6 `site.py` is 1,617 lines
It mixes render orchestration, per-event analytics computation, chart-data prep,
and athlete-page rendering. Not urgent, but splitting analytics computation into
its own module would make the data-accuracy-critical code easier to test in
isolation. (As of 2026-06-08 it does have direct coverage —
`tests/test_analytics.py` — added with the 1.3 fix.)

---

## Resolved 2026-06-08 (this pass)

- ✅ **1.1** mhmaraok main list no longer mislabeled from a footnote `<H3>`
  legend (4,211 rows now `main list`).
- ✅ **1.2** Unsigned wind readings (`1.4`) parse as wind instead of bleeding
  into the athlete name (6 rows fixed; parquet-level guard added).
- ✅ **1.3** Deterministic tiebreaker (mark, then date, then name) for the
  top-mark holder and every representative-picking sort in
  `_compute_event_analytics`; first direct unit tests for that function.
- ✅ **1.4** `test_dates_make_sense` warns on near-future dates (≤1y) and only
  hard-fails far-future ones, so the weekly cron no longer breaks on routine
  upcoming-meet / one-year-typo noise.
- ✅ **2.1** CI runs the full `pytest` suite and lints `scripts/` (was 2 of 9
  test files; `scripts/` unlinted).
- ✅ **2.2** README refreshed (Tabulator→LiteTable/uPlot, current pages &
  modules, schema incl. `dob_precision` / nullable `rank`).
- ✅ **2.3** Implemented planning docs removed; one remaining-work source
  (audit.md + roadmap.md).
- ✅ **2.4** Sortable table headers are keyboard-operable with `aria-sort`.
- ✅ **2.5** Skip-to-main-content link added site-wide.
- ✅ **2.6** Open Graph + meta-description tags in `base.html`.
- ✅ **2.7** Filter inputs use 2px borders on coarse-pointer devices.

## Resolved since 2026-04-30

- ✅ Athlete pages capped at ≥2 entries (was 29k pages / 638 MB → 21k / 471 MB).
- ✅ Card CSS consolidated into one base class + aliases.
- ✅ "View analytics" is now a real button next to the event H1.
- ✅ Tabulator replaced by LiteTable; charts migrated to uPlot.
- ✅ Relay dot-separated times (`3.07.41`) parse correctly (was 527 null marks).
- ✅ Bare finishing position no longer mis-parsed as a year-only DOB
  (was ~40 phantom implausible-age rows).
- ✅ Rank-omitted ancillary rows recovered with `rank=null` (mpoleok 5.93).
- ✅ `test_catalogued_typos_still_present` is existence-based (no longer
  time-fragile).
- ✅ Footer shows the build commit hash.
- ✅ WR / best-per-year chart lines extend to today.
