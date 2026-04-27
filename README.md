# alltimeathletics

A data-analysis-friendly mirror of [Peter Larsson's all-time athletics lists](http://www.alltime-athletics.com).

> **All performance data is © Peter Larsson and is sourced from
> [alltime-athletics.com](http://www.alltime-athletics.com).** This project is an unofficial
> derivative view that re-formats the same lists into a parquet file and a sortable
> table per event. Every event page links straight back to Larsson's canonical
> source — please visit it for the authoritative lists.

## What this is

The Larsson site is the canonical track & field all-time database, but the
HTML is fixed-width text inside `<pre>` tags — fine for reading, painful for
filtering, sorting, or any data-science work. This repo:

1. Scrapes every event page once a week (polite, sequential, with cache)
2. Parses each `<PRE>` block into a canonical row schema
3. Writes one parquet file (`data/alltime_athletics.parquet`, ~5 MB, ~370k rows)
4. Generates a static [GitHub Pages site](https://thomascamminady.github.io/alltimeathletics/) with one sortable, filterable table per event

The weekly refresh runs as a GitHub Action that opens a PR against `main`; if
schema + sanity checks pass, the PR auto-merges and the Pages site
re-deploys. No manual intervention.

## Use the data

```python
import polars as pl

df = pl.read_parquet(
    "https://github.com/thomascamminady/alltimeathletics/raw/main/data/alltime_athletics.parquet"
)
df.filter(pl.col("event") == "marathon").sort("mark_value").head(10)
```

Schema: see `src/alltimeathletics/pipeline.py` — one row per performance with
`event`, `sex`, `legality`, `family`, `rank`, `mark_raw`, `mark_value` (numeric),
`wind`, `name`, `country`, `dob`, `position`, `venue`, `date`, `source_url`.

## Develop locally

```bash
uv sync
uv run python -m alltimeathletics.pipeline           # scrape + parse → data/
uv run python -m alltimeathletics.site --out site/   # render the static site
uv run pytest                                        # parser + schema tests
python -m http.server -d site                        # preview on :8000
```

The pipeline caches HTML under `.cache/` so re-runs only re-fetch when
something changed.

## Layout

```
src/alltimeathletics/
  events.py      # canonical catalogue of every event page (190 entries)
  scrape.py      # polite httpx fetcher with on-disk cache
  parse.py       # <PRE>-block parser, family-aware
  pipeline.py    # scrape + parse → parquet + per-event JSON
  site.py        # render the static site from the parquet
templates/       # base.html, index.html, event.html
static/          # style.css + vendored Tabulator
data/            # parquet, manifest.json, events/<slug>.json
tests/           # parser smoke tests + parquet schema validation
.github/workflows/
  update-data.yml  # weekly cron → PR → auto-merge
  deploy.yml       # main → Pages
  ci.yml           # tests on every PR
```

## Credit

All raw data: **Peter Larsson, [alltime-athletics.com](http://www.alltime-athletics.com)**.
This project would not exist without the decades of work he has put into
maintaining those lists. If you find this mirror useful, please also consider
visiting the source.

## License

The code in this repository is MIT-licensed. The performance data is © Peter
Larsson; this project redistributes it as fair use for non-commercial
data-analysis purposes and links back to the source from every page.
