# alltimeathletics

A data-analysis-friendly mirror of [Peter Larsson's all-time athletics
lists](http://www.alltime-athletics.com), maintained as a private repo
for now.

> **All performance data is © Peter Larsson and is sourced from
> [alltime-athletics.com](http://www.alltime-athletics.com).** This project
> re-formats those lists into a parquet file for personal data-analysis use.
> It is not yet authorised to publish the redistributed data; the repo is
> private and there is no public site.

## The site

`site.py` renders a static site from the parquet:

- **Per-event pages** with a sortable, filterable table (custom
  `lite-table.js`, no heavy dependency).
- **Per-event analytics pages** — world-record progression, best mark per
  year, age-at-performance scatter, decade leaders, and top countries
  (charts drawn with vendored uPlot).
- **Per-athlete career pages** plus an athlete index.
- An in-browser **SQL playground** (DuckDB-WASM) for ad-hoc queries
  against the parquet.
- A **download** page and an **about** page.

## Status

- ✅ Scraper, parser, parquet pipeline, static site renderer, local tests
- ✅ ~375k rows across 190 events; 0.034 % unparsed
- ✅ Weekly auto-refresh via `update-data.yml` cron + dated GitHub Releases
- ✅ Public hosting (GitHub Pages)

## Use the data

The whole dataset is one parquet file (~4 MB) and a CSV mirror (~70 MB,
~10 MB gzipped). Both are refreshed weekly via GitHub Actions and attached
to a dated [release](https://github.com/thomascamminady/alltimeathletics/releases).
Stable URLs always point at the latest snapshot:

```bash
# Parquet (recommended; preserves dtypes)
curl -L -o alltime_athletics.parquet \
  https://github.com/thomascamminady/alltimeathletics/releases/latest/download/alltime_athletics.parquet

# CSV (gzipped — ~10 MB)
curl -L -o alltime_athletics.csv.gz \
  https://github.com/thomascamminady/alltimeathletics/releases/latest/download/alltime_athletics.csv.gz

# Manifest (per-event row counts + parser diagnostics)
curl -L -o manifest.json \
  https://github.com/thomascamminady/alltimeathletics/releases/latest/download/manifest.json
```

Then in Python:

```python
import polars as pl

df = pl.read_parquet("alltime_athletics.parquet")
df.filter(pl.col("event") == "marathon").sort("mark_value").head(10)
```

To pin to a specific weekly snapshot (e.g. for reproducibility), browse
the [releases page](https://github.com/thomascamminady/alltimeathletics/releases)
and substitute the dated tag in the URL: `releases/download/data-YYYY-MM-DD/...`.

Schema (one row per performance):

| column        | type   | notes                                         |
|---------------|--------|-----------------------------------------------|
| `event`       | str    | e.g. "marathon", "100 metres"                 |
| `event_slug`  | str    | Larsson's URL slug, also the join key         |
| `sex`         | str    | "men" / "women" / "mixed"                     |
| `legality`    | str    | "legal" / "non-legal"                         |
| `family`      | str    | parser family (track_time, field_distance, …) |
| `section`         | str    | sub-list heading from the source page         |
| `rank`            | u32?   | as printed by Larsson; null when he omitted it |
| `mark_raw`        | str    | exact text of the mark                        |
| `mark_value`      | f64    | seconds / metres / points                     |
| `mark_annotation` | str?   | trailing mark flag (`A`, `*`, `+`, …) if any  |
| `wind`            | f64?   | nullable (sprints/jumps only)                 |
| `name`            | str    | athlete or team                               |
| `country`         | str    | IOC 3-letter, uppercased                      |
| `dob`             | date?  | nullable                                      |
| `dob_precision`   | str?   | `"day"`, `"year"`, or null (see note below)   |
| `position`        | str    | finishing position in race                    |
| `venue`           | str    |                                               |
| `date`            | date   | event date                                    |
| `source_url`      | str    | link back to the Larsson page                 |
| `source_line`     | str    | raw source line the row was parsed from       |

Two columns need care:

- **`dob_precision`** records how exact a birth date is. `"day"` means a
  full day-month-year DOB; `"year"` means the source only gave a year, so
  `dob` is imputed as `YYYY-01-01` — do not treat a January-1 `"year"` row
  as a real January-1 birthday. Null means no DOB at all.
- **`rank`** is nullable: a handful of ancillary rows carry no rank number
  because Larsson omitted it on the source page.

## Run it locally

```bash
make sync       # install deps via uv
make scrape     # fetch + parse → data/alltime_athletics.parquet (~2 min cold)
make site       # render the static site into ./site/
make serve      # http://localhost:8766
make test       # parser + schema + data-accessibility tests
make ci-local   # lint + typecheck + test (mirrors CI)
make all        # everything
```

`make help` lists every target. The HTML cache lives under `.cache/` so
re-runs only re-fetch when something changed.

## Layout

```
src/alltimeathletics/
  events.py      # canonical catalogue of every event page (190 entries)
  scrape.py      # polite httpx fetcher with on-disk cache
  parse.py       # <PRE>-block parser, family-aware
  pipeline.py    # scrape + parse → parquet + manifest
  site.py        # render the static site + per-event JSON from the parquet
  flags.py       # IOC code → flag-emoji map used by the templates
templates/       # base, index, event, analytics, athlete, athlete_index,
                 #   sql, download, about
static/          # style.css, lite-table.js (custom sortable/filterable
                 #   table), vendored uPlot (uplot.min.js/.css) +
                 #   uplot-helpers.js for the charts
data/            # parquet, manifest.json (per-event JSON is generated, not committed)
tests/           # parser + schema + data-accessibility tests
.github/workflows/
  ci.yml           # tests on every push
  update-data.yml  # weekly cron: scrape, commit parquet, cut dated release
```

## Credit

All raw data: **Peter Larsson, [alltime-athletics.com](http://www.alltime-athletics.com)**.
This project would not exist without the decades of work he has put into
maintaining those lists.
