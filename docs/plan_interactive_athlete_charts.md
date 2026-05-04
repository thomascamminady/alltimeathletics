# Plan: Interactive athlete charts + quality gates

Steps are meant to be worked through in order.  Mark each **Done** when complete.

---

## Step 1 — Site-build smoke test  [x]

Add `tests/test_site_build.py` that calls `uv run python -m alltimeathletics.site`
on a temp directory and asserts exit-code 0, `athlete/index.html` is present, and
at least one event page exists.  Skip (not fail) when the parquet is absent.

---

## Step 2 — Pre-commit configuration  [x]

Create `.pre-commit-config.yaml` with local hooks:

| Hook | Command |
|------|---------|
| ruff format | `uv run ruff format --check src tests` |
| ruff lint   | `uv run ruff check src tests` |
| ty typecheck | `uv run ty check src` |
| pytest | `uv run pytest` (skips data-dependent tests automatically) |

Site build is too slow (~15 s) and data-dependent for a pre-commit hook;
it belongs in CI (`make site && make test`) and in the smoke-test added in Step 1.

---

## Step 3 — SVG text-overlap audit  [x]

Scan all server-side SVG renderers in `site.py` for cases where axis labels or
endpoint labels can collide:

- Y-axis endpoint labels drawn on top of quartile labels when the range is
  small.
- X-axis decade labels drawn too close to the first/last year labels.
- Athlete career-scatter: age-tick labels can crowd near the edges.

Fix: skip a quartile label when it is within N pixels of an endpoint label.
Add a minimum-spacing guard before emitting any tick label.

---

## Step 4 — Athlete page: replace server-side SVG with interactive JS charts  [ ]

### 4a  Data plumbed from site.py  [ ]

In `render()`, derive:
```python
wr_values = {slug: meta["wr_progression"][-1]["mark_value"]
             for slug, meta in event_meta.items() if meta.get("wr_progression")}
```
Pass `wr_values`, `event_family`, `event_descending` to `_athlete_worker_init`
via new `_W_WR_VALUES`, `_W_EVENT_FAMILY`, `_W_EVENT_DESCENDING` worker globals.
In `_athlete_worker_task`, build a per-athlete `wr_json`, `event_family_json`,
`event_descending_json` limited to the events the athlete competed in, and pass
them to the template.

Also add `idx` field (array position) to each entry so Tabulator rows can be
found by index.

### 4b  Template: replace SVG blocks with chart container + controls  [ ]

Remove `career_svg` / `age_svg` blocks from `athlete.html`.
Add a single `<div id="perf-chart">` preceded by a control bar:

- **Event** `<select id="chart-event">` — one `<option>` per event; hidden when
  "All events" is selected.
- **Show all events** `<button id="chart-all">` — toggles combined mode.
- **X axis** `<button id="chart-xaxis">` — toggles Year / Age.
- **Y axis** `<button id="chart-ymode">` — toggles Mark / % of WR.

### 4c  Chart JS: single-event scatter  [ ]

Write `renderChart()` in JS that:
1. Filters `ENTRIES` to the selected event.
2. Computes x (year from date, or decimal age from DOB).
3. Computes y (raw `mark_value`, or `(mark_value / wr) * 100` for field /
   `(wr / mark_value) * 100` for track — both → 100 % = WR pace).
4. Draws an SVG scatter (same visual style as the existing server-side charts:
   `wr-chart`, `scatter-dot`, `grid`, `ax`, `ax-grid` CSS classes).
5. Formats Y-axis labels correctly: `mm:ss.ss` for track, metres for field,
   `xx.x %` for pct mode.

### 4d  Chart JS: x-axis toggle (year ↔ age)  [ ]

Toggle button updates `state.xAxis` and calls `renderChart()`.
Disable Age mode when `DOB` is null (grey out the button).

### 4e  Chart JS: % of WR mode  [ ]

Toggle button updates `state.yMode` and calls `renderChart()`.
Disable when the event has no WR value (e.g. relay).

### 4f  Chart JS: all-events combined view  [ ]

When `state.mode === "all"`:
- Filter `ENTRIES` to events present in `WR_VALUES`.
- Force Y to `pct_wr` mode (raw marks are incomparable across events).
- Assign one colour per `event_slug` from a small categorical palette.
- Draw all dots in one SVG; add a colour legend (event label → colour swatch).

### 4g  Dot click → Tabulator row highlight  [ ]

Each SVG `<circle>` gets `data-idx` = entry's `idx` field.
On click:
```js
const row = table.getRow(idx);
row.scrollTo();
table.selectRow(idx);
```
Add `selectable: 1` (single selection) to the Tabulator config.
Add a "deselect on chart click elsewhere" handler.

---

## Step 5 — Text-overlap guard for new JS charts  [ ]

After implementing Step 4, ensure the JS chart renderer applies the same
minimum-spacing guard as Step 3: skip labels that would overlap the previous
one, with a configurable `MIN_LABEL_GAP` constant.

---

## Notes

- Relay entries should be excluded from all chart modes (no individual WR exists).
- The existing `career_svg` / `age_svg` template vars and site.py rendering code
  can be removed once Step 4 is complete.
- The stats summary panel (career span, best rank, etc.) should remain; only
  the SVG chart blocks are replaced.
