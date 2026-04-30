# Audit & remaining work — 2026-04-30

A careful read of the codebase, the rendered site, and the data, ranked by:
**(1) data accuracy → (2) simplicity & accessibility → (3) functionality.**
"Done" items appear elsewhere (`docs/roadmap.md`, PRs); this file is what's
*still* worth doing.

---

## Tier 1 — data accuracy (the only thing that really matters)

### 1.1 Six rows have wind values bleeding into the `name` field 🩹
Confirmed in the parquet:

| event_slug | name string                       |
|-----------|------------------------------------|
| `m_200ok` | `0.1 Jorge Henrique da Costa Vides` |
| `m_200ok` | `0.5 Clement Campbell`              |
| `m_200ok` | `1.4 Leon Reid`                     |
| `m_200ok` | `1.6 Andre Ewers`                   |
| `mtripok` | `0.6 Pedro Pablo Pichardo`          |
| `w_100no` | `5.6 Kelliann Baptiste`             |

These create six garbage athlete pages (e.g. `0-1-jorge-...html`) and pollute
the event tables. Root cause is in `parse.py` — when the wind column merges
with the name column upstream, our extractor doesn't separate them. **Fix in
the parser**, then re-scrape. Add a regression test: assert no name matches
`^\d+\.\d+\s+`.

### 1.2 The 8 catalogued upstream typos cost ~58 rows
See `docs/parser_audit.md`. Mostly truncated dates (`. .1996`, `.03.1978`)
and one country-typo (`KEË`). For each, decide:
- **Fix in parser** with a fallback (e.g. accept year-only dates as
  `YYYY-01-01` with a `date_precision='year'` column) — preserves the row
  at the cost of a new column to communicate uncertainty.
- **Or stay strict** (current behaviour) and lose the rows but keep the
  schema clean.
The user's call. Either way, document the tradeoff in the README.

### 1.3 Future-dated typo catalogue is manual and reactive
`KNOWN_FUTURE_DATE_TYPOS` in `tests/test_data_access.py` is a list of
hand-added entries. Every time Larsson types `2026` instead of `2025`,
the daily cron breaks until someone catalogues the new entries.
**Idea:** auto-cap any future date >7 days ahead to `today` with a
`date_precision='inferred'` flag, log a warning, and let the audit step
print a summary instead of hard-failing. Reduces toil while keeping
visibility.

### 1.4 51 sections have multiple rank=1 rows (genuine ties)
Not a bug — when two athletes literally tied, both are rank 1. But the
analytics summary's "top mark" picks the first row by polars iteration
order, which is non-deterministic with respect to the user's mental
model ("alphabetical by name"). **Add a tiebreaker** in
`_compute_event_analytics` (e.g. earliest `date`, then alphabetical
`name`). Stable, intuitive.

### 1.5 No automated check that the daily cron actually fires
We changed `cron: "0 4 * * *"` blind. Verify after the next 04:00 UTC
run that the workflow ran, the diff gate behaved correctly on a
no-change day, and a release was *not* cut.

### 1.6 No tests for the analytics computation
`_compute_event_analytics`, `_render_year_line_svg`, and friends are
~400 LoC of new logic with zero coverage. They can silently break the
WR-progression chart for an entire family without anyone noticing.
**Add property tests:** for a known event, assert the WR list size,
the `best_per_year` length, and that consecutive bests are monotone.

### 1.7 The "10th-place gap" in the analytics summary uses a stale
sign convention
The gap is rendered as `+0.16` in seconds for the men's 100m, which
matches "10th-place is 0.16s slower than the WR" — correct. But the
field events use the same `+%.2f` format which means a 0.50m gap
shows as `+0.50` for a *higher* mark being worse. Visually consistent
because the user reads the unit by context, but worth confirming on
a high-jump page that the sign feels right. Add a unit suffix
(`s` / `m` / `pts`) to remove ambiguity.

---

## Tier 2 — simplicity & accessibility

### 2.1 29,092 athlete pages, 229 MB of HTML, p50 ≈ 4 KB
Most athletes have ≤4 entries. We're paying ~3 KB of repeated chrome
per file. Options:
- **(a) Cap by min entries:** only generate pages for athletes with
  ≥3 entries (cuts ~60% of files).
- **(b) Group small athletes:** "All-time list snippet" pages keyed by
  surname-prefix (`/athlete/a.html` etc.) for athletes with <3 entries.
- **(c) Move data to one big JSON, lazy-render in JS.** Largest impact
  but adds runtime complexity. **Don't do this** — kills the
  no-JS-required principle.

I'd pick (a) — single-line code change in `_render_athlete_pages`.

### 2.2 Two independent flag-emoji systems
`flags.py` (Python, server-render) and the inline `FLAGS` JS object on
event pages. The JS one is generated from the Python one at render
time, so they're consistent — but it's worth confirming on a CI test
that `flags_json` always covers every IOC code in `flag_emoji_map()`.

### 2.3 Card-style boilerplate is duplicated
`.card`, `.chart-card`, `.summary-card`, `.recent-panel`, `.download-bar`,
`.source-link` all share the "1px border, faint background" idea but each
re-declares it. Collapse into one base class + variants.

### 2.4 Header has no ARIA landmark, no skip-link
`<header>` is implicitly `role="banner"` and `<main>` is implicitly
`role="main"`, so technically OK — but adding a "skip to main content"
link as the very first focusable element would help screen-reader and
keyboard users escape the header on every page.

### 2.5 Theme toggle is icon-only
`aria-label` covers it for screen readers, but a tooltip
(`title="..."`) is already present. Consider replacing the unicode
glyphs with inline SVG so they look identical across OS/font stacks
(`☀` and `☾` render very differently on macOS vs Android).

### 2.6 Tabulator filter inputs are 1px borders
On touch devices the inputs disappear visually. Bump to 2px in
`@media (pointer: coarse)`.

### 2.7 The "Analytics →" link on the event page is hidden in a `<p class="lede">`
Easy to miss. Promote to a real button next to the H1.

### 2.8 No social cards / Open Graph tags
Sharing a link in Slack or X gives no preview. Add a single
`<meta property="og:title">` + `<meta property="og:description">` per
template. Could even auto-generate a static PNG per event from
matplotlib at build time — but plain meta tags are 95% of the value
for 5% of the work.

### 2.9 SQL playground has no syntax highlighting
For "clean and simple," that's fine. But basic line numbers + a
fixed-width tab handler would help. CodeMirror 6 weighs ~70 KB
gzipped — acceptable since this is the SQL page only and it
already lazy-loads DuckDB-WASM.

### 2.10 SQL playground doesn't persist the user's last query
One `localStorage.setItem('sql:last', q)` on every keystroke
(throttled) would make the page feel instantly more usable.

---

## Tier 3 — functionality

### 3.1 Event grid on the index is alphabetical within (sex, legality)
A user looking for "marathon" has to scroll past every track event.
Group into **Sprints / Hurdles / Middle distance / Distance / Road /
Field / Combined / Walks / Relays** with subheadings. Add to
`events.py` as a `category` field.

### 3.2 No URL deep-linking on the analytics page
Pasting a link to "the women's marathon analytics with the % toggle
on" should be one URL. Add `?mode=delta` query param read by the JS.

### 3.3 No per-event CSV download
Every event page should have a "download this event as CSV" button
(client-side blob from the existing per-event JSON).

### 3.4 No "compare athletes" view
`/?compare=Bolt+Gay` was on the original roadmap — would be a small
JS view since we already have per-athlete JSON inlined into pages.
Lazy idea: from any athlete page, a "compare with..." typeahead.

### 3.5 The WR delta toggle doesn't persist across navigation
If you set "Δ vs current WR" on the 100m, then click into the 200m
analytics, you're back to "Absolute". `localStorage` again.

### 3.6 Index page lacks a quick-link to the SQL playground beyond the header
A short blurb on the index ("Run your own queries → SQL playground")
near the download bar would help discovery.

### 3.7 No user-facing changelog
Releases are tagged `data-YYYY-MM-DD`, but a release-notes page
listing weekly highlights ("3 new sub-10s 100m's", "Cheptegei 5000m
WR moved to ...") would make repeat visits feel rewarding. Could
auto-generate from the parquet diff.

### 3.8 The age-vs-mark scatter on the analytics page caps at 800 dots
For depth-of-list events (m_100ok has 5000+ canonical perfs) we
sample by best-mark. Consider a "show all" toggle (more dots, slower
page).

### 3.9 No event-family-aware Y-tick density on the year-line chart
For very flat curves (e.g. men's marathon since 2014), the three
quartile gridlines collapse to nearly the same time. Detect and
either drop intermediate ticks or add a fourth at the median if
range is tight.

### 3.10 The header is order-dependent on screen width
With the new "SQL playground" link, on mobile (<400 px) the header
wraps awkwardly: site name → "a sortable view of..." → SQL → toggle.
A `<nav>` with a hamburger on small screens would be tidier. Or
just hide the descriptive text on mobile.

---

## Suggested next pass (90 minutes)

1. **1.1** Fix the wind-into-name parser bug. Add regression test. Re-scrape.
   *(Highest data-accuracy ROI)*
2. **1.4** Tiebreaker in `_compute_event_analytics`. Three lines.
3. **1.6** Three property tests for analytics computation.
4. **2.1** Skip athlete pages for n_entries < 3. Cuts site by ~60%.
5. **3.5 + 3.2** localStorage for WR-mode + URL `?mode=` reader.
