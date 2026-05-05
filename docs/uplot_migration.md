# uPlot migration notes — 2026-05-05

Plan for moving all charts from hand-rolled SVG to
[uPlot](https://github.com/leeoniya/uplot) (~45 KB canvas-based time-series
library).

## Inventory of what needs to move

| # | Chart | Current location | Type | Renders | Interactions |
|---|---|---|---|---|---|
| 1 | WR progression (abs / delta / pct) | `site.py:_render_wr_chart_svg` → analytics.html | step line + dots | server SVG | hover (`<title>`) |
| 2 | Best mark per year | `site.py:_render_year_line_svg` → analytics.html | line + dots | server SVG | hover (`<title>`) |
| 3 | Entries per year | `site.py:_render_year_bars_svg` → analytics.html | vertical bars | server SVG | hover (`<title>`) |
| 4 | Mark vs age (event analytics) | `site.py:_render_age_scatter_svg` → analytics.html | scatter | server SVG | hover (`<title>`) |
| 5 | Athlete performance chart | `athlete.html:buildSVG` | scatter | client SVG | click→row select, event picker, year/age toggle, mark / %WR toggle, all-events legend |
| 6 | Top countries (analytics) | `analytics.html:189` | horizontal bars | HTML/CSS | none |
| 7 | Top athletes (analytics) | `analytics.html:212` | horizontal bars | HTML/CSS | links |

## Pre-work

1. **Vendor uPlot.** Drop `uPlot.iife.min.js` and `uPlot.min.css` into
   `static/`. Bump the cache-bust hash so existing visitors refetch.
2. **Decide the data envelope.** Each chart currently has its inputs
   computed in Python and inlined as SVG. uPlot needs `[xs, ys1, …]`
   columnar arrays. Two options:
   - Embed per-chart JSON in `<script type="application/json">` tags on
     the page (matches the existing `ENTRIES`, `WR_VALUES` pattern in
     `athlete.html`). Simpler, no extra fetch.
   - Emit a sibling `data/analytics/<slug>.json` file and `fetch()` it.
     Cleaner if charts grow; adds a network round-trip.
   Recommend **inline JSON** — analytics payload is small and
   matches what athlete pages already do.
3. **Theme bridge.** Read `--fg`, `--accent`, `--border`, `--fg-muted`
   from `getComputedStyle(document.documentElement)` at chart init so
   uPlot stroke/grid/text colors honor light/dark. Re-init on theme
   change (or just on next page load — the theme toggle reloads).
4. **Tooltip plugin.** uPlot has no built-in tooltip; either:
   - Use the official `tooltips-plugin` example (bind cursor → DOM div).
   - Or write 30 lines: on `cursor.move`, look up the closest series
     point and position an absolutely-placed `<div>`.
   Want one shared plugin file in `static/uplot-tooltip.js`.

## Per-chart migration

### 1. WR progression (3 modes)
- uPlot supports stepped paths via `paths: uPlot.paths.stepped({ align: 1 })`.
  Maps cleanly.
- Need `xs` as Unix epoch seconds. The current SSR uses dates →
  pre-convert in Python.
- Three modes (abs / delta / pct) become three uPlot series toggled by
  the existing buttons; just swap `data` array on click.
- Y-axis label position (current "WR mark" / "Δ vs current" / "% of current")
  → uPlot `axes[1].label`.

### 2. Best-mark-per-year line
- Trivial. Single line, `xs = years`, `ys = mark_values`. Use
  `pxAlign: false` so dots align cleanly with year ticks.
- Tooltip needs to show `name`, `country`, `venue`, `date` — pass these
  as a parallel `meta` array indexed by point.

### 3. Entries-per-year bars
- **uPlot's weak spot.** Use `uPlot.paths.bars({ size: [0.6, 100] })` —
  works but feels less natural than the SVG version.
- Alternative: **keep this chart as SVG**. It has zero interactions, the
  Python code already exists, and bar plots are uPlot's least-good
  fit. Migrating doesn't save complexity.
- Recommend **don't migrate #3** unless we want a single rendering
  path for consistency.

### 4. Mark vs age scatter
- uPlot scatter via `paths: () => null` + `points: { show: true }`.
- 800-point cap stays; uPlot will handle that fine.
- Hover crosshair is a small UX win over the current static `<title>`.

### 5. Athlete performance chart (the big one)
- Migration value is highest here — currently a 200-line custom
  renderer with manual click-picking on `<circle>` elements.
- Each event becomes one uPlot series in "all events" mode (uPlot
  natively legends/colors series). In single-event mode, one series.
- Year/Age x-axis toggle → swap the `xs` array and re-init (uPlot
  doesn't support changing the x-axis type live without a rebuild;
  rebuild is cheap).
- Mark / %WR y-axis toggle → swap `ys`.
- **Click → Tabulator row select.** Use `cursor.bind.click(self, _, handler)`;
  on click, look up `cursor.idx` and `cursor.idxs[seriesIdx]` to find
  which point. Map back to the `entries[idx].idx` for `table.selectRow`.
- The existing `state` object stays; only `renderChart()` changes.

### 6, 7. Country / athlete horizontal bars
- **Don't migrate.** uPlot has no horizontal bar mode worth using. The
  current HTML/CSS bars are 10 lines of CSS, accessible (real `<ol>`
  with hyperlinks), and cost nothing to render. Keep as-is.

## What we lose

- **No-JS rendering.** Analytics pages currently work with JS off
  (server-rendered SVG). After migration #1–#5, charts disappear when
  JS is disabled. For a static site this is a real (small) regression
  — note in `base.html` `<noscript>` if we care.
- **Native `<title>` tooltips.** Replaced by JS hover plugin. Requires
  a hover device — touch devices need a tap fallback.
- **First-render speed for analytics.** Currently the SVG paints with
  the HTML; after migration we wait for JS parse + uPlot init. Small
  but measurable on slow devices.
- **Page-source readability / accessibility tree.** SVG axis text is
  currently in the DOM and indexable; canvas isn't.

## Suggested rollout order

1. Vendor uPlot + ship the shared tooltip plugin.
2. Migrate **chart #5** (athlete) first — biggest win, isolated to one
   template, code reduction is real.
3. Migrate **#1, #2, #4** (analytics line/scatter charts).
4. Decide on **#3** (bars) once everything else is uPlot — likely keep SSR.
5. Delete the now-unused SVG helpers in `site.py`
   (`_render_year_line_svg`, `_render_age_scatter_svg`,
   `_render_wr_chart_svg`, and possibly `_render_year_bars_svg`).
6. Drop `.wr-chart`, `.scatter-dot`, `.bar`, `.ax`, `.ax-grid`,
   `.grid`, `.grid-axis` from `style.css` — uPlot brings its own.
   Keep `.chart-controls` and `.chart-legend` if reused.

## Open questions

- Do we want one consistent rendering path (all uPlot, even bars) or
  pragmatic mixed (uPlot for interactive, SVG for the trivial bar)?
- Keep server-rendered fallback for no-JS, or accept the regression?
- One uPlot per page or share an instance? (Recommend one per chart —
  simpler.)

## Bonus: charting on the SQL playground (Metabase-style)

After uPlot is vendored, the playground gets it almost for free.
Goal: run a query → result table → optional chart panel where the user
picks X column, Y column, and chart type (line / bars / scatter).

**UI** — append below `#sql-result` (only when the last query had ≥1
row):

- chart type toggle: `Table` (default) | `Line` | `Bars` | `Scatter`
- when chart selected: `X:` `<select>` of columns, `Y:` `<select>` of
  numeric columns
- the table can stay above the chart, or hide when a chart type is
  picked — Metabase keeps both visible; copy that.

**Column-type detection** — DuckDB hands back arrow types in
`result.schema.fields[i].type`. Reuse the existing `formatValue` logic:
treat anything matching `Int|Float|Decimal|Double` as numeric for Y;
treat `Date|Timestamp` as preferred X candidates; everything else
becomes string-only (legal X for bars, legal grouping).

**Chart wiring**:
- Line / scatter: `xs = rows.map(r => r[xCol])`, `ys = rows.map(r => r[yCol])`.
  If `xs` contains dates, convert to epoch seconds for uPlot.
- Bars: `uPlot.paths.bars()` over the same arrays. If X is a string
  column (e.g. `country`), we need a categorical x-axis — uPlot expects
  numeric x, so synthesize `xs = [0, 1, 2, …]` and supply
  `axes[0].values: () => labels`. Cap at ~50 categories with a banner
  if exceeded ("showing first 50 — add LIMIT to query").

**Re-render on query** — current code already replaces `#sql-result`
on each run; the chart panel hooks the same path: store the most
recent `{rows, columns, types}` as a module-level var, repaint the
chart on column-pick or chart-type change. Disable the chart toggle
while the query is running.

**Free-form vs sensible defaults** — Metabase guesses defaults
(first non-numeric → X, first numeric → Y). Worth doing here too:
auto-pick when the user opens the chart panel.

**Risks / scope creep**:
- Multi-series (one Y per group): nice but explodes UI complexity.
  Defer; tell users to pivot in SQL (`SUM(...) GROUP BY year, sex` →
  one row per (year, sex), then they pick `sex` as group). Or skip
  grouping entirely v1.
- Time-series with gaps (DuckDB returns `null` ms): uPlot draws gaps,
  fine.
- Mark-value formatting (e.g. `9.58` vs `2:01:09`): the playground
  has no idea about athletics-time formatting. Show numeric Y as-is —
  if the user wants pretty marks they can `mark_raw` it (string,
  not chartable; that's the trade).

**Order of work**:
1. Land core uPlot migration (above) so the lib is on the page.
2. Add chart-mode toggle + column pickers below `#sql-result`.
3. Wire single-series line/scatter (easy).
4. Wire bars with categorical X (medium — requires string axis).
5. Persist last-picked chart type / columns in `localStorage` so
   reload remembers — small but nice.
