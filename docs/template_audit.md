# Template + CSS audit — 2026-05-05

Cross-check of `templates/*.html` and `static/style.css` after the recent
header → GitHub-corner refactor and footer-attribution cleanup. Findings
ordered by severity.

## Issues

1. `templates/event.html:51` — `<p class="note">` references a `.note`
   class that doesn't exist in `static/style.css`. Renders unstyled.

2. `static/style.css:147` — `.download-bar, .recent-panel, .summary-card`
   block is a verbatim copy of the `.card` rule (lines 138-143). Pure
   duplication.

3. `templates/sql.html:20` — `<details class="card">` (Schema reference)
   uses the browser's default disclosure marker, while every other
   `<details>` uses `.chart-card` with a custom `▾`/`▸` toggle.

4. `static/style.css:309` + `templates/athlete.html:54` — `.chart-panel`
   is defined alongside `.chart-body` only to serve athlete.html's one
   non-collapsible chart panel. The single class is enough.

## Minor polish

5. `static/style.css:182` — `.btn:hover { text-decoration: none; }` is
   redundant; baseline `.btn` already sets it.

6. `static/style.css:219-223` — `.legality-tabs .tab[aria-pressed="true"]`
   re-implements `.btn-on`. Could be unified.

7. `templates/athlete.html:57` — `<select class="btn">` doesn't visually
   match adjacent buttons (browser dropdown caret + native padding).

8. `static/style.css:321-372` — `.country-bars` and `.athlete-bars` are
   near-identical; only `grid-template-columns` differs.

9. `static/style.css:298-304` vs `:509` — `.chart-card` summary toggles
   `▾`/`▸`; `.dropdown` summary uses `▾` only.

10. `templates/analytics.html:20` vs `templates/athlete.html:24` —
    analytics hides the summary-card heading; athlete shows it.

11. `static/style.css:237` — `.recent-panel h2` is `--fs-md`; every other
    h2 on the index is `--fs-lg`.

12. `templates/athlete.html:80` — hardcoded chart `PALETTE` doesn't
    shift between light and dark themes.

13. `.btn-on` is JS-toggled rather than driven by `aria-pressed`. The
    state lives in two places.

## Nitpicks

14. No skip-to-content link; GitHub corner is first focusable element.
15. `templates/base.html` has a theme-toggle script bound to `#theme-toggle`,
    but the button doesn't exist anywhere — dead code.
16. `.dropdown-panel` and `.github-corner` share `z-index: 20`.
17. `templates/index.html` opens with `{% %}` blocks on one line;
    every other template uses multi-line.
18. Footer paragraphs are tight (`var(--space-1)` between them).
19. SQL textarea is briefly visible before CodeMirror takes over.
20. Multiple "card-like" surface rules (`.card`, `.dropdown-panel`,
    `.sql-table-wrap`) duplicate border + bg patterns.
