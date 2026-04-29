# Site improvements roadmap

Captured from a brainstorm of what users actually want from a sortable
view of Larsson's data, prioritized by impact-vs-cost. Items are checked
off as they ship.

## Tier 1 — small, high-impact, fits the static-site model

- [x] **#1** WR-progression toggle ("Show world records only") — pre-compute
  the set of historically-WR-equaling marks per event and add a button
  that filters the table to just those rows.
- [x] **#2** Section filter chips ("Main list" / "Indoor" / "Wind-aided" /
  "Hand-timed" / "Doping-annulled") — hidden by default = main list only.
- [x] **#3** Top-of-page summary card — current WR mark + holder + date,
  10th-place gap, median age of top-100.
- [ ] **#4** Mark-distribution histogram above the table (Vega-Lite or
  pure SVG) — bins of marks across the event.
- [x] **#5** WR-progression mini chart (inline SVG line plot, date → mark,
  only WR-equaling marks).
- [ ] **#6** Year-range filter slider (e.g. "since 2010").
- [x] **#7** Country flag emoji next to country code (🇰🇪 KEN).

## Tier 2 — moderate effort, real value

- [ ] **#8** Athlete pages — `/athlete/<slug>.html` for the top ~500
  athletes, listing every mark across events.
- [ ] **#9** Country leaderboard view — `/country/<code>.html` showing
  best mark per event for each country.
- [ ] **#10** Index page upgrade — show top 3 marks per event inline as
  a preview, instead of just a name + count.
- [x] **#11** "Recent WRs" callout on index — small panel with the 5 most
  recent WRs across all events.

## Tier 3 — bigger / more opinionated

- [ ] **#12** Compare 2 athletes side-by-side — `?compare=Bolt,Gay` URL
  param.
- [ ] **#13** Age-curve plot (mark vs age at performance, scatter +
  LOESS) per event.
- [ ] **#14** Cross-event "decathlon-style" age analysis — at age 22,
  what % of top-100 marks across events are by athletes that age?
- [ ] **#15** Dark-mode polish — `color-scheme: light dark` is declared
  but borders/gray panels aren't theme-aware.
- [ ] **#16** Per-event CSV download button (header), not just the
  global CSV.
