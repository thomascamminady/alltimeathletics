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

### 2.1 CI runs only 2 of 9 test files 🐛 (NEW, high value)
`.github/workflows/ci.yml` runs `test_parse.py` + `test_pipeline_schema.py`
only. The weekly `update-data.yml` adds `test_qoi`, `test_data_access`,
`test_audit`. That leaves **`test_invariants.py`, `test_pct_of_wr.py`,
`test_site_build.py` never gating a PR**, and the data-quality suites only run
on the weekly scrape. All 151 tests pass against the committed parquet today, so
there's no reason not to gate them.

**Fix:** in `ci.yml`, replace the two narrow steps with `uv run pytest` (the
audit test self-skips without `.cache`, the rest run against the committed
parquet). Also lint `scripts/` — `ci.yml` lints `src tests` only, which is how
the `plot.py` `zip(strict=)` errors reached `main` (pre-commit caught them
locally, CI wouldn't have).

### 2.2 README is stale 📄 (NEW)
`README.md` still says `static/ # style.css + vendored Tabulator` (Tabulator was
replaced by `lite-table.js` + uPlot; no Tabulator file remains). It also omits
the SQL playground, the analytics pages, athlete pages, and the download page.
**Fix:** refresh the "structure" and "features" sections; document the parquet
schema including `dob_precision` and the `rank=null` convention for
rank-omitted rows.

### 2.3 Documentation sprawl 📄
`docs/` holds `audit.md`, `parser_audit.md` (auto-generated — keep),
`roadmap.md`, `plan_interactive_athlete_charts.md`, `template_audit.md`,
`uplot_migration.md`. The last three are one-off planning notes that are now
mostly implemented. **Fix:** fold anything still-relevant into `roadmap.md` or
this file, delete the rest, so the next reader has one "what's done / what's
left" source instead of six.

### 2.4 Sortable table headers are mouse-only ♿ (NEW, real a11y gap)
`lite-table.js` wires sorting via `th.addEventListener("click", …)` (line ~91)
with no `tabindex`, `keydown` (Enter/Space), `role="button"`, or `aria-sort`.
The core UI of the whole site can't be sorted by keyboard and screen readers
don't announce sort direction. **Fix:** make sort headers focusable
(`tabindex="0"`), activate on Enter/Space, and set `aria-sort` on the active
column.

### 2.5 No skip-link to main content ♿
`<header>`/`<main>` give implicit landmarks, but a "skip to main content" link
as the first focusable element would let keyboard/SR users bypass the nav on
every page. One `<a class="visually-hidden-focusable" href="#main">` + an
`id="main"` on `<main>` in `base.html`.

### 2.6 No Open Graph / meta description
Sharing any page in Slack/X yields no preview. Add `<meta name="description">`
and `og:title`/`og:description` to `base.html` (block-overridable per page).
5 minutes, 95% of the value of static share-card PNGs.

### 2.7 Touch-device input affordance
Header filter inputs and the table filters are 1px borders — they visually
vanish on coarse-pointer devices. Bump to 2px under
`@media (pointer: coarse)`.

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
