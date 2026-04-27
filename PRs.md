# PR roadmap

A staged plan for hardening the data accuracy, robustness, and publication
story. Each PR is independently reviewable and mergeable; later PRs assume
earlier ones have landed but aren't blocked on them.

The first three are already in flight; the rest are scoped but not yet open.

## Status legend

- **Open** — branch pushed, PR open on GitHub.
- **Drafted** — scoped here, not yet implemented.
- **Optional** — only worth doing if the listed motivation actually shows up.

---

## PR #1 — Reliability and cleanup pass

**Status:** Open (`improve/reliability-and-cleanup`).

Small, focused fixes uncovered by reading the codebase end-to-end. Not a
refactor — each item is a real concrete bug or wart.

- **Scraper retries.** A single transient 5xx or connection error on any of
  ~190 weekly pages currently fails the whole pipeline. Add bounded
  exponential backoff (4 attempts, 2s/4s/8s, fail-fast on 4xx).
- **Null `mark_value` sort.** The Tabulator mark sorter used `?? 0`, which
  made null marks sort *better* than every real time/distance in ascending
  tables. Push nulls to the end regardless of direction.
- **Dead `alltimeathletics` console script.** The pyproject entry point only
  printed `"Hello from alltimeathletics!"`. Removed it; documented commands
  are `python -m alltimeathletics.pipeline` and `… .site`.
- **`pyproject.toml` polish.** Real description, plus minimal `[tool.ruff]`
  and `[tool.pytest.ini_options]` config so editors and CI agree.
- **CI ty step.** Drop `continue-on-error: true` now that `ty check src`
  passes — the safety net was hiding any future regression.
- **Lints surfaced by the wider ruff ruleset** (UP/SIM/B/E): `datetime.UTC`
  alias, yoda comparisons in tests, two over-long lines.

---

## PR #2 — Parser: per-step extractors + structured diagnostics

**Status:** Open (`refactor/parse-step-diagnostics`). Behaviour-preserving.

The parser had one ~95-line `_parse_individual_line` that funnelled every
failure through an opaque `_UnparseableRow` exception. After a weekly run
we couldn't tell whether a `wind=None` meant "no reading in source" or
"regex didn't match this row's quirk", and we couldn't aggregate failures
by the step that rejected them.

- **Step extractors.** Replace the monolith with `_extract_rank`,
  `_extract_mark`, `_maybe_extract_wind`, `_extract_tail`, `_extract_country`,
  `_classify_after_country`, `_assemble_name`. Each either returns its value
  (possibly `None` for legitimate absences) or raises
  `_StepError(step, reason)`.
- **Structured diagnostics.** `ParseResult` gains
  `diagnostics: list[ParseDiagnostic]` carrying section, raw line, step
  name, and reason. The legacy `unparsed: list[str]` is preserved as a
  property derived from it so existing callers keep working.
- **Manifest-level visibility.** `pipeline.py` emits per-event and global
  `unparsed_by_step` counters in `manifest.json` and prints a
  `failure steps: country=12, tail=3, …` line on every run.
- **Parametrized well-formedness test.** Every diagnostic must have a
  non-empty line, non-empty reason, and a step name from a closed
  `KNOWN_STEPS` set — typo-guard for new step names.

---

## PR #3 — Schema bump: `dob_precision` + `mark_annotation`

**Status:** Open (`feat/dob-precision-mark-annotation`). Schema change
(two new `pl.Utf8` columns).

Two real silent-data-loss bugs the previous shape couldn't expose:

- **DOB precision was fabricated.** `_parse_dob` returned
  `date(year, 1, 1)` for year-only entries like `"97"`. Downstream
  consumers couldn't distinguish that from someone genuinely born Jan 1.
  → `dob_precision` column with values `"day"`, `"year"`, or `null`.
- **Mark annotations were silently stripped.** `_normalize_mark` did
  `re.sub(r"[A-Za-z+*]+$", "", s)` so `9.79A` (altitude), `9.79h`
  (hand-timed), and `9.79*` (later DQ'd) all collapsed to `9.79`. → new
  `mark_annotation` column preserving the trailing token. Real signal
  Larsson encodes that we were throwing away.

Tests:
- `test_dob_precision_values_are_known` — closed set of literals.
- `test_dob_precision_is_consistent_with_dob` — `dob` non-null iff
  `dob_precision` non-null; every `"year"`-precision row is Jan 1.
- `test_mark_annotation_carries_real_signal` — at least 100 annotated
  rows in the parquet, fails loudly if the extractor breaks.

Frontend: both columns added as hidden filterable columns in
`event.html` so power users can `.filter(mark_annotation == "A")` from
the UI; default view is unchanged.

**Out of scope (deferred):** the 2-digit-year pivot (currently
`year < 10 → 2000s`) will start miscoding athletes born ≥ 2010. Now
visible via `dob_precision`+`date`; a follow-up can pivot relative to
performance date.

---

## PR #4 — Data publication: GitHub Releases + CSV mirror

**Status:** Drafted.

Today the only way to grab the data is `data/alltime_athletics.parquet`
on `main`. That URL always serves the *current* week's snapshot — there's
no way to pin to a historical week, and no non-parquet format for
consumers who reach for pandas/R/Excel first.

- **Snapshot every weekly cron to a GitHub Release.** One step in
  `update-data.yml`: `gh release create data-$(date -u +%Y-%m-%d)` with
  the parquet + manifest + CSV attached. Stable immutable URL per
  snapshot; reproducibility for downstream notebooks.
- **CSV mirror.** Write `data/alltime_athletics.csv.gz` alongside the
  parquet (~2 MB compressed). Makes the data trivial to consume from
  pandas, R, DuckDB, or `awk` without a parquet reader.
- **README + index page snippets.** Polars / pandas / DuckDB / R copy-paste
  examples, all reading from the same `raw/main` URL. Right now we only
  show polars; that's the smallest change that materially widens the
  audience.

Out of scope: per-event JSON releases (those are render-time only and
~80 MB total — adds noise without much benefit over the parquet).

---

## PR #5 — Scrape robustness: page-shape validation + content hashing

**Status:** Drafted.

PR #1 added retries; that catches transient errors. This PR catches the
silent-success failure modes — pages that come back 200 OK but are
truncated, redirected, or unchanged.

- **Post-fetch shape validation.** Before declaring a fetch successful,
  verify the page contains ≥1 `<PRE>` block and ≥1 `<a name=…>` marker.
  A redirected error stub would currently parse to zero rows and feed
  the pipeline silently.
- **Per-page content hash + length in the manifest.** Add
  `manifest["events"][i]["source_hash"]` (sha256) and `["source_bytes"]`
  on every fetch. Three immediate wins:
  - Detect "Larsson didn't change this page" without re-parsing.
  - Catch silent server-side truncation (page came back at 30% of
    expected size).
  - The bot's PR description can list which pages actually changed,
    not just "data refreshed".
- **Per-event row-count floor.** Today the schema test asserts
  `200k ≤ total ≤ 1M`. Add a per-event check: a single event should not
  lose >10% of rows week-over-week. Implemented as a CI step that diffs
  the new manifest against the previous one on `main`.
- **Weekly diff summary in the auto-merge PR.** When the bot opens the
  data PR, include a one-screen summary: rows added/removed/changed by
  event, top 10 new entries by event, any rows whose `mark_value`
  changed. Makes Monday review trivial.

---

## PR #6 — Stable `row_id` for week-over-week diffing

**Status:** Drafted. Schema change (one new `pl.Utf8` column).

Today rows have no stable primary key. The implicit one is
`(event_slug, rank, name, mark_raw, date, venue)` — usually unique but
not enforced. Two athletes with the same time on the same day at the
same meet would collapse silently.

- **`row_id` column** = `sha1(event_slug | name | mark_raw | date | venue)`
  truncated to 16 hex chars. Stable across weeks unless one of the
  identifying fields actually changes. This is what `JOIN`s and diffs
  key on.
- **Powers the diff in PR #5.** "Rows whose `mark_value` changed" is
  defined as "same `row_id`, different `mark_value`".
- **Cost:** ~6 MB parquet bloat (16 bytes × 371k rows). Worth it for the
  diff capability and stable joins.

Independent of PR #5 — both PRs just want this column, but neither
requires the other to ship first.

---

## PR #7 — Optional: append-only audit parquet

**Status:** Drafted, optional.

Only worth shipping if you actually want the longitudinal view. PR #5's
weekly diff in the PR body is probably enough day-to-day.

- **`data/audit.parquet`** appended every week with
  `(scraped_at, row_id, field_changed, old_value, new_value)`. Lets you
  reconstruct "when did this athlete's DOB change in Larsson's data?"
- **Cost:** ~50 KB/week, ~2.5 MB/year. Genuinely cheap.
- **Skip if:** no concrete query you'd run against it. The diff in
  PR #5's PR body covers "what changed this week" without needing
  history.
