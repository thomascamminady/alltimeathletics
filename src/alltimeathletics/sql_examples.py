"""Pre-canned SQL examples for the playground dropdown."""

from __future__ import annotations


def _build_example_queries() -> list[dict[str, str]]:
    """Pre-canned SQL examples for the playground dropdown.

    First entry is the default that loads on page open. Queries assume
    the ``perf`` view (the parquet aliased) and use the legal / All-time
    section idiom defined above.
    """
    # Larsson's section labels are inconsistent: most canonical lists are
    # named "All-time men's best 100m" but a handful (road events, men's
    # 1500m, women's 800m, …) are simply "main list" or "Main list". We
    # widen the canonical filter to cover both styles so the example
    # queries don't silently drop ~25 events.
    canonical_filter = (
        "  AND (section LIKE 'All-time%'\n       OR section IN ('main list', 'Main list'))\n"
    )
    return [
        {
            "group": "Records",
            "title": "Current world records (latest first)",
            "sql": (
                "-- One row per event: the current world record, latest broken on top.\n"
                "SELECT event, sex, mark_raw, name, country, venue, date\n"
                "FROM perf\n"
                "WHERE rank = 1\n"
                "  AND legality = 'legal'\n"
                "  AND family <> 'relay'\n"
                f"{canonical_filter}"
                "ORDER BY date DESC;"
            ),
        },
        {
            "group": "Records",
            "title": "Longest-standing world records",
            "sql": (
                "-- WRs that have stood the longest, oldest first.\n"
                "SELECT event, sex, mark_raw, name, country, venue, date,\n"
                "       DATE_DIFF('year', date, CURRENT_DATE) AS years_old\n"
                "FROM perf\n"
                "WHERE rank = 1\n"
                "  AND legality = 'legal'\n"
                "  AND family <> 'relay'\n"
                f"{canonical_filter}"
                "ORDER BY date ASC\n"
                "LIMIT 25;"
            ),
        },
        {
            "group": "Records",
            "title": "World records set in 2024 or later",
            "sql": (
                "-- The most recent crop of WRs.\n"
                "SELECT event, sex, mark_raw, name, country, venue, date\n"
                "FROM perf\n"
                "WHERE rank = 1\n"
                "  AND legality = 'legal'\n"
                f"{canonical_filter}"
                "  AND date >= DATE '2024-01-01'\n"
                "ORDER BY date DESC;"
            ),
        },
        {
            "group": "Records",
            "title": "World records by decade set",
            "sql": (
                "-- How many of today's WRs were set in each decade?\n"
                "SELECT (EXTRACT(year FROM date) / 10)::INT * 10 AS decade,\n"
                "       COUNT(*) AS wrs_still_standing\n"
                "FROM perf\n"
                "WHERE rank = 1\n"
                "  AND legality = 'legal'\n"
                "  AND family <> 'relay'\n"
                f"{canonical_filter}"
                "GROUP BY decade\n"
                "ORDER BY decade;"
            ),
        },
        {
            "group": "Records",
            "title": "Men's marathon: every WR ever set",
            "sql": (
                "-- Every world record progression for the men's marathon: each row\n"
                "-- is the moment the all-time best dropped to a new mark. Compares\n"
                "-- each performance against the running min of all marks up to and\n"
                "-- including its own date; ties keep only the earliest occurrence.\n"
                "WITH ordered AS (\n"
                "  SELECT date, mark_raw, mark_value, name, country, venue\n"
                "  FROM perf\n"
                "  WHERE event_slug = 'mmaraok'\n"
                "    AND legality = 'legal'\n"
                "    AND section LIKE 'All-time%'\n"
                "    AND mark_value IS NOT NULL\n"
                "    AND (mark_annotation IS NULL OR mark_annotation <> '*')\n"
                ")\n"
                "SELECT date, mark_raw AS time, name, country, venue\n"
                "FROM ordered\n"
                "QUALIFY mark_value = MIN(mark_value) OVER (ORDER BY date)\n"
                "    AND ROW_NUMBER() OVER (PARTITION BY mark_value ORDER BY date) = 1\n"
                "ORDER BY date;"
            ),
        },
        {
            "group": "Records",
            "title": "Athletes holding multiple current WRs",
            "sql": (
                "-- Anyone whose name shows up at the top of more than one event.\n"
                "SELECT name, sex, country,\n"
                "       COUNT(*) AS records,\n"
                "       STRING_AGG(event, ', ' ORDER BY event) AS events\n"
                "FROM perf\n"
                "WHERE rank = 1\n"
                "  AND legality = 'legal'\n"
                "  AND family <> 'relay'\n"
                "  AND section LIKE 'All-time%'\n"
                "GROUP BY name, sex, country\n"
                "HAVING COUNT(*) > 1\n"
                "ORDER BY records DESC, name;"
            ),
        },
        {
            "group": "Records",
            "title": "Countries with the most current WRs",
            "sql": (
                "-- Which countries hold the most world records right now?\n"
                "SELECT country, COUNT(*) AS world_records\n"
                "FROM perf\n"
                "WHERE rank = 1\n"
                "  AND legality = 'legal'\n"
                "  AND family <> 'relay'\n"
                "  AND section LIKE 'All-time%'\n"
                "GROUP BY country\n"
                "ORDER BY world_records DESC, country;"
            ),
        },
        {
            "group": "Men vs women",
            "title": "Men vs women WR gap, per event",
            "sql": (
                "-- Relative percentage gap between the men's and women's WR.\n"
                "WITH wr AS (\n"
                "  SELECT event, sex, mark_value, mark_raw\n"
                "  FROM perf\n"
                "  WHERE rank = 1 AND legality = 'legal'\n"
                "    AND family <> 'relay'\n"
                "    AND section LIKE 'All-time%'\n"
                ")\n"
                "SELECT m.event,\n"
                "       m.mark_raw AS men,\n"
                "       w.mark_raw AS women,\n"
                "       ROUND(100.0 * ABS(m.mark_value - w.mark_value) /\n"
                "             GREATEST(m.mark_value, w.mark_value), 2) AS gap_pct\n"
                "FROM wr m\n"
                "JOIN wr w USING (event)\n"
                "WHERE m.sex = 'men' AND w.sex = 'women'\n"
                "ORDER BY gap_pct DESC;"
            ),
        },
        {
            "group": "Men vs women",
            "title": "Closest #1 vs #2 in each event",
            "sql": (
                "-- Most-contested events: tightest margin between the two best ever.\n"
                "WITH t AS (\n"
                "  SELECT event, sex, rank, mark_value, mark_raw, name\n"
                "  FROM perf\n"
                "  WHERE rank IN (1, 2)\n"
                "    AND legality = 'legal'\n"
                "    AND family <> 'relay'\n"
                "    AND section LIKE 'All-time%'\n"
                ")\n"
                "SELECT a.event, a.sex,\n"
                "       a.mark_raw AS top1, a.name AS top1_name,\n"
                "       b.mark_raw AS top2, b.name AS top2_name,\n"
                "       ROUND(100.0 * ABS(a.mark_value - b.mark_value) /\n"
                "             GREATEST(a.mark_value, b.mark_value), 3) AS gap_pct\n"
                "FROM t a\n"
                "JOIN t b USING (event, sex)\n"
                "WHERE a.rank = 1 AND b.rank = 2\n"
                "ORDER BY gap_pct ASC\n"
                "LIMIT 25;"
            ),
        },
        {
            "group": "Athletes",
            "title": "Most versatile athletes (top-50 across events)",
            "sql": (
                "-- Athletes ranked top-50 in the most distinct events.\n"
                "SELECT name, country, sex,\n"
                "       COUNT(DISTINCT event) AS events_in_top50,\n"
                "       STRING_AGG(DISTINCT event, ', ' ORDER BY event) AS events\n"
                "FROM perf\n"
                "WHERE rank <= 50\n"
                "  AND legality = 'legal'\n"
                "  AND family <> 'relay'\n"
                "  AND section LIKE 'All-time%'\n"
                "GROUP BY name, country, sex\n"
                "ORDER BY events_in_top50 DESC, name\n"
                "LIMIT 30;"
            ),
        },
        {
            "group": "Athletes",
            "title": "Most all-time top-10 marks (any event)",
            "sql": (
                "-- Who shows up most in all-time top-10s? Big number = era of dominance.\n"
                "SELECT name, country,\n"
                "       COUNT(*) AS top10_marks,\n"
                "       COUNT(DISTINCT event) AS in_events\n"
                "FROM perf\n"
                "WHERE rank <= 10\n"
                "  AND legality = 'legal'\n"
                "  AND family <> 'relay'\n"
                "  AND section LIKE 'All-time%'\n"
                "GROUP BY name, country\n"
                "ORDER BY top10_marks DESC, name\n"
                "LIMIT 25;"
            ),
        },
        {
            "group": "Athletes",
            "title": "Youngest athletes to set a current WR",
            "sql": (
                "-- Age at the moment they set the still-standing WR.\n"
                "SELECT name, sex, event, mark_raw, dob, date,\n"
                "       ROUND((date - dob) / 365.25, 2) AS age_at_record\n"
                "FROM perf\n"
                "WHERE rank = 1\n"
                "  AND legality = 'legal'\n"
                "  AND family <> 'relay'\n"
                "  AND section LIKE 'All-time%'\n"
                "  AND dob IS NOT NULL\n"
                "ORDER BY age_at_record ASC\n"
                "LIMIT 15;"
            ),
        },
        {
            "group": "Athletes",
            "title": "Oldest athletes to set a current WR",
            "sql": (
                "-- The other end of the curve.\n"
                "SELECT name, sex, event, mark_raw, dob, date,\n"
                "       ROUND((date - dob) / 365.25, 2) AS age_at_record\n"
                "FROM perf\n"
                "WHERE rank = 1\n"
                "  AND legality = 'legal'\n"
                "  AND family <> 'relay'\n"
                "  AND section LIKE 'All-time%'\n"
                "  AND dob IS NOT NULL\n"
                "ORDER BY age_at_record DESC\n"
                "LIMIT 15;"
            ),
        },
        {
            "group": "Events",
            "title": "Biggest #1 vs #2 gap, men only",
            "sql": (
                "-- Most untouchable men's records: how far ahead is #1?\n"
                "WITH t AS (\n"
                "  SELECT event, rank, mark_value, mark_raw, name\n"
                "  FROM perf\n"
                "  WHERE sex = 'men'\n"
                "    AND rank IN (1, 2)\n"
                "    AND legality = 'legal'\n"
                "    AND family <> 'relay'\n"
                "    AND section LIKE 'All-time%'\n"
                ")\n"
                "SELECT a.event,\n"
                "       a.mark_raw AS top1, a.name AS top1_name,\n"
                "       b.mark_raw AS top2, b.name AS top2_name,\n"
                "       ROUND(100.0 * ABS(a.mark_value - b.mark_value) /\n"
                "             GREATEST(a.mark_value, b.mark_value), 2) AS gap_pct\n"
                "FROM t a JOIN t b USING (event)\n"
                "WHERE a.rank = 1 AND b.rank = 2\n"
                "ORDER BY gap_pct DESC;"
            ),
        },
        {
            "group": "Events",
            "title": "Sub-10s 100m runs by year (men)",
            "sql": (
                "-- The pace of 100m progress: how many sub-10 runs per calendar year?\n"
                "SELECT EXTRACT(year FROM date)::INT AS year,\n"
                "       COUNT(*) AS sub10_runs\n"
                "FROM perf\n"
                "WHERE event_slug = 'm_100ok'\n"
                "  AND mark_value < 10.00\n"
                "  AND legality = 'legal'\n"
                "  AND section LIKE 'All-time%'\n"
                "GROUP BY year\n"
                "ORDER BY year;"
            ),
        },
        {
            "group": "Events",
            "title": "Sub-2:05 marathons (men)",
            "sql": (
                "-- All sub-2:05 men's marathons in the all-time list.\n"
                "SELECT name, country, mark_raw, venue, date\n"
                "FROM perf\n"
                "WHERE event_slug = 'mmaraok'\n"
                "  AND mark_value < 7500   -- 2:05:00 in seconds\n"
                "  AND legality = 'legal'\n"
                "  AND section LIKE 'All-time%'\n"
                "ORDER BY mark_value ASC;"
            ),
        },
        {
            "group": "Events",
            "title": "Country dominance: men's marathon top 100",
            "sql": (
                "-- Which nations own the men's marathon all-time top 100?\n"
                "SELECT country, COUNT(*) AS top100_marks\n"
                "FROM perf\n"
                "WHERE event_slug = 'mmaraok'\n"
                "  AND rank <= 100\n"
                "  AND legality = 'legal'\n"
                "  AND section LIKE 'All-time%'\n"
                "GROUP BY country\n"
                "ORDER BY top100_marks DESC;"
            ),
        },
        {
            "group": "Geography",
            "title": "Top countries across all-time top-100 lists",
            "sql": (
                "-- Sum of all-time top-100 entries across every event.\n"
                "SELECT country, COUNT(*) AS top100_entries\n"
                "FROM perf\n"
                "WHERE rank <= 100\n"
                "  AND legality = 'legal'\n"
                "  AND family <> 'relay'\n"
                "  AND section LIKE 'All-time%'\n"
                "GROUP BY country\n"
                "ORDER BY top100_entries DESC\n"
                "LIMIT 30;"
            ),
        },
        {
            "group": "Geography",
            "title": "Venues where the most current WRs were set",
            "sql": (
                "-- Where do records get broken? Cities with the most WRs still on the books.\n"
                "SELECT venue, COUNT(*) AS records_set\n"
                "FROM perf\n"
                "WHERE rank = 1\n"
                "  AND legality = 'legal'\n"
                "  AND family <> 'relay'\n"
                "  AND section LIKE 'All-time%'\n"
                "  AND venue IS NOT NULL\n"
                "GROUP BY venue\n"
                "ORDER BY records_set DESC, venue\n"
                "LIMIT 25;"
            ),
        },
        {
            "group": "Combined",
            "title": "Decathlon all-time top 25 (men)",
            "sql": (
                "-- All-time best decathlon scores.\n"
                "SELECT rank, mark_raw AS points, name, country, venue, date\n"
                "FROM perf\n"
                "WHERE event_slug = 'mdecaok'\n"
                "  AND legality = 'legal'\n"
                "  AND section LIKE 'All-time%'\n"
                "ORDER BY rank ASC\n"
                "LIMIT 25;"
            ),
        },
        {
            "group": "Combined",
            "title": "Heptathlon all-time top 25 (women)",
            "sql": (
                "-- All-time best heptathlon scores.\n"
                "SELECT rank, mark_raw AS points, name, country, venue, date\n"
                "FROM perf\n"
                "WHERE event_slug = 'whepaok'\n"
                "  AND legality = 'legal'\n"
                "  AND section LIKE 'All-time%'\n"
                "ORDER BY rank ASC\n"
                "LIMIT 25;"
            ),
        },
    ]
