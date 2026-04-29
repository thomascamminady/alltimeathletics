# Parser audit

Auto-generated cross-check of every scraped page against its source HTML.
See `scripts/audit_pages.py` for the methodology and `KNOWN_SOURCE_ISSUES` for catalogued upstream typos.

**182 clean**, **8 known-source-issue**, **0 unexplained**, **0 no_html** (190 pages total)

## ⚠️ Known source issues

These pages have row/section counts that can never match Larsson exactly because the upstream HTML itself has data-quality issues. Each is catalogued below; the audit fails loudly if a *new* mismatch appears that isn't in the catalogue.

| slug | html rows | parquet rows | upstream issue |
|---|---:|---:|---|
| `m60mok` | 3971 | 3969 | 2 rows have malformed dates `. .1996` (only year preserved upstream) |
| `m_60mhok` | 3986 | 3982 | 4 rows have malformed dates `. .1994`, `. .1990`, `.03.1978` upstream |
| `m60mno` | 335 | 298 | 37 rows have truncated 4-digit years like `07.03.198` (last digit missing upstream); one extra section with anchor reuse + bad title |
| `mhmaraok` | 4182 | 4181 | 1 row has country typo `KEË` (should be `KEN`); we reject non-IOC codes |
| `w_60mhok` | 2455 | 2454 | 1 row has truncated date `.03.1978` upstream |
| `w2milesok` | 463 | 462 | 1 row (Kelly McMillen) has blank dob+pos columns |
| `wjaveoldok` | 1209 | 1201 | 8 rows wrap venue+date onto a second line — multi-line layout we don't reassemble |
| `w4x400ok` | 2495 | 2493 | 2 rows (rank 450, 1973) have empty team name upstream |

## All pages — verification checklist

Each row is one page Larsson maintains. ✅ = parser exactly matches the HTML row count and the rank-1 mark of every section. ⚠️ = mismatch is catalogued in `KNOWN_SOURCE_ISSUES`. ❌ = parser bug that needs investigation.

| status | slug | html rows | parquet rows | sections (html/parq) |
|---|---|---:|---:|---|
| ⚠️ known source issue | `m60mno` | 335 | 298 | 10/9 |
| ⚠️ known source issue | `m60mok` | 3971 | 3969 | 5/5 |
| ⚠️ known source issue | `m_60mhok` | 3986 | 3982 | 4/4 |
| ⚠️ known source issue | `mhmaraok` | 4182 | 4181 | 1/1 |
| ⚠️ known source issue | `w2milesok` | 463 | 462 | 2/2 |
| ⚠️ known source issue | `w4x400ok` | 2495 | 2493 | 3/3 |
| ⚠️ known source issue | `w_60mhok` | 2455 | 2454 | 5/5 |
| ⚠️ known source issue | `wjaveoldok` | 1209 | 1201 | 2/2 |
| ✅ verified | `m100km` | 197 | 197 | 1/1 |
| ✅ verified | `m100mno` | 2455 | 2455 | 25/25 |
| ✅ verified | `m10kroad` | 1255 | 1255 | 2/2 |
| ✅ verified | `m10kroadno` | 36 | 36 | 4/4 |
| ✅ verified | `m10kwno` | 403 | 403 | 9/9 |
| ✅ verified | `m10kwok` | 1059 | 1059 | 3/3 |
| ✅ verified | `m10milesroad` | 231 | 231 | 2/2 |
| ✅ verified | `m10milesroadno` | 7 | 7 | 2/2 |
| ✅ verified | `m15kroad` | 1545 | 1545 | 2/2 |
| ✅ verified | `m15kroadno` | 13 | 13 | 4/4 |
| ✅ verified | `m1hourno` | 1 | 1 | 1/1 |
| ✅ verified | `m1hourok` | 221 | 221 | 1/1 |
| ✅ verified | `m2000hno` | 422 | 422 | 6/6 |
| ✅ verified | `m2000hok` | 521 | 521 | 2/2 |
| ✅ verified | `m20kroad` | 2535 | 2535 | 2/2 |
| ✅ verified | `m20kroadno` | 6 | 6 | 2/2 |
| ✅ verified | `m20kwno` | 72 | 72 | 4/4 |
| ✅ verified | `m20kwok` | 1917 | 1917 | 3/3 |
| ✅ verified | `m25kok` | 78 | 78 | 1/1 |
| ✅ verified | `m3000hno` | 75 | 75 | 10/10 |
| ✅ verified | `m3000hok` | 11053 | 11053 | 2/2 |
| ✅ verified | `m30kok` | 61 | 61 | 1/1 |
| ✅ verified | `m30kroad` | 3668 | 3668 | 1/1 |
| ✅ verified | `m30kroadno` | 8 | 8 | 2/2 |
| ✅ verified | `m35kwok` | 235 | 235 | 1/1 |
| ✅ verified | `m4x100no` | 76 | 76 | 3/3 |
| ✅ verified | `m4x100ok` | 3281 | 3281 | 2/2 |
| ✅ verified | `m4x1500ok` | 92 | 92 | 1/1 |
| ✅ verified | `m4x200no` | 3 | 3 | 1/1 |
| ✅ verified | `m4x200ok` | 658 | 658 | 2/2 |
| ✅ verified | `m4x400no` | 77 | 77 | 4/4 |
| ✅ verified | `m4x400ok` | 2597 | 2597 | 3/3 |
| ✅ verified | `m4x800no` | 1 | 1 | 1/1 |
| ✅ verified | `m4x800ok` | 107 | 107 | 3/3 |
| ✅ verified | `m50kwno` | 52 | 52 | 3/3 |
| ✅ verified | `m50kwok` | 1051 | 1051 | 2/2 |
| ✅ verified | `mHalf-Marathonwok` | 126 | 126 | 1/1 |
| ✅ verified | `mMarathonwok` | 109 | 109 | 1/1 |
| ✅ verified | `m_1000no` | 1 | 1 | 1/1 |
| ✅ verified | `m_1000ok` | 313 | 313 | 2/2 |
| ✅ verified | `m_100ok` | 4962 | 4962 | 7/7 |
| ✅ verified | `m_100yno` | 167 | 167 | 11/11 |
| ✅ verified | `m_100yok` | 177 | 177 | 2/2 |
| ✅ verified | `m_10kno` | 36 | 36 | 4/4 |
| ✅ verified | `m_10kok` | 10970 | 10970 | 2/2 |
| ✅ verified | `m_110hno` | 1569 | 1569 | 13/13 |
| ✅ verified | `m_110hok` | 10120 | 10120 | 4/4 |
| ✅ verified | `m_1500no` | 37 | 37 | 4/4 |
| ✅ verified | `m_1500ok` | 12455 | 12455 | 3/3 |
| ✅ verified | `m_2000ok` | 352 | 352 | 2/2 |
| ✅ verified | `m_200hno` | 7 | 7 | 3/3 |
| ✅ verified | `m_200hok` | 86 | 86 | 4/4 |
| ✅ verified | `m_200no` | 1266 | 1266 | 21/21 |
| ✅ verified | `m_200ok` | 5288 | 5288 | 9/9 |
| ✅ verified | `m_2miok` | 599 | 599 | 2/2 |
| ✅ verified | `m_3000no` | 8 | 8 | 3/3 |
| ✅ verified | `m_3000ok` | 6604 | 6604 | 3/3 |
| ✅ verified | `m_300no` | 546 | 546 | 5/5 |
| ✅ verified | `m_300ok` | 900 | 900 | 5/5 |
| ✅ verified | `m_400hno` | 26 | 26 | 6/6 |
| ✅ verified | `m_400hok` | 4473 | 4473 | 3/3 |
| ✅ verified | `m_400no` | 109 | 109 | 11/11 |
| ✅ verified | `m_400ok` | 5327 | 5327 | 5/5 |
| ✅ verified | `m_4xmileok` | 159 | 159 | 3/3 |
| ✅ verified | `m_5000no` | 26 | 26 | 3/3 |
| ✅ verified | `m_5000ok` | 9995 | 9995 | 3/3 |
| ✅ verified | `m_600no` | 49 | 49 | 1/1 |
| ✅ verified | `m_600ok` | 289 | 289 | 3/3 |
| ✅ verified | `m_60mhno` | 51 | 51 | 6/6 |
| ✅ verified | `m_800no` | 98 | 98 | 8/8 |
| ✅ verified | `m_800ok` | 9698 | 9698 | 3/3 |
| ✅ verified | `m_mileno` | 70 | 70 | 7/7 |
| ✅ verified | `m_mileok` | 8682 | 8682 | 3/3 |
| ✅ verified | `mdecano` | 129 | 129 | 3/3 |
| ✅ verified | `mdecaok` | 2875 | 2875 | 3/3 |
| ✅ verified | `mdiscno` | 75 | 75 | 14/14 |
| ✅ verified | `mdiscok` | 2960 | 2960 | 4/4 |
| ✅ verified | `mhammno` | 48 | 48 | 13/13 |
| ✅ verified | `mhammok` | 2510 | 2510 | 2/2 |
| ✅ verified | `mhighno` | 53 | 53 | 10/10 |
| ✅ verified | `mhighok` | 3663 | 3663 | 8/8 |
| ✅ verified | `mhmarano` | 107 | 107 | 7/7 |
| ✅ verified | `mjaveno` | 83 | 83 | 6/6 |
| ✅ verified | `mjaveok` | 2666 | 2666 | 3/3 |
| ✅ verified | `mjaveoldno` | 7 | 7 | 4/4 |
| ✅ verified | `mjaveoldok` | 556 | 556 | 2/2 |
| ✅ verified | `mlongno` | 974 | 974 | 19/19 |
| ✅ verified | `mlongok` | 3368 | 3368 | 4/4 |
| ✅ verified | `mmarano` | 51 | 51 | 5/5 |
| ✅ verified | `mmaraok` | 6274 | 6274 | 1/1 |
| ✅ verified | `mpoleno` | 147 | 147 | 22/22 |
| ✅ verified | `mpoleok` | 11007 | 11007 | 10/10 |
| ✅ verified | `mshotno` | 177 | 177 | 19/19 |
| ✅ verified | `mshotok` | 7772 | 7772 | 4/4 |
| ✅ verified | `mtripno` | 390 | 390 | 5/5 |
| ✅ verified | `mtripok` | 2378 | 2378 | 4/4 |
| ✅ verified | `w10kroad` | 1621 | 1621 | 3/3 |
| ✅ verified | `w10kroadno` | 8 | 8 | 2/2 |
| ✅ verified | `w10kwno` | 91 | 91 | 10/10 |
| ✅ verified | `w10kwok` | 576 | 576 | 3/3 |
| ✅ verified | `w10milesroad` | 293 | 293 | 3/3 |
| ✅ verified | `w15kroad` | 1384 | 1384 | 1/1 |
| ✅ verified | `w15kroadno` | 3 | 3 | 1/1 |
| ✅ verified | `w2000hno` | 993 | 993 | 10/10 |
| ✅ verified | `w2000hok` | 1112 | 1112 | 2/2 |
| ✅ verified | `w20kroad` | 3042 | 3042 | 2/2 |
| ✅ verified | `w20kroadno` | 12 | 12 | 1/1 |
| ✅ verified | `w20kwno` | 69 | 69 | 3/3 |
| ✅ verified | `w20kwok` | 4346 | 4346 | 2/2 |
| ✅ verified | `w2milesno` | 3 | 3 | 3/3 |
| ✅ verified | `w3000hno` | 162 | 162 | 11/11 |
| ✅ verified | `w3000hok` | 31539 | 31539 | 5/5 |
| ✅ verified | `w30kroad` | 4031 | 4031 | 2/2 |
| ✅ verified | `w30kroadno` | 30 | 30 | 1/1 |
| ✅ verified | `w35kwok` | 308 | 308 | 1/1 |
| ✅ verified | `w4x100no` | 30 | 30 | 3/3 |
| ✅ verified | `w4x100ok` | 1243 | 1243 | 2/2 |
| ✅ verified | `w4x1500ok` | 201 | 201 | 1/1 |
| ✅ verified | `w4x400no` | 64 | 64 | 4/4 |
| ✅ verified | `w4x800no` | 2 | 2 | 2/2 |
| ✅ verified | `w4x800ok` | 194 | 194 | 2/2 |
| ✅ verified | `w50kwok` | 264 | 264 | 2/2 |
| ✅ verified | `w5kwno` | 181 | 181 | 8/8 |
| ✅ verified | `w5kwok` | 499 | 499 | 3/3 |
| ✅ verified | `w60mno` | 330 | 330 | 8/8 |
| ✅ verified | `w60mok` | 3359 | 3359 | 3/3 |
| ✅ verified | `wHalf-Marathonwok` | 57 | 57 | 1/1 |
| ✅ verified | `wMarathonwok` | 56 | 56 | 1/1 |
| ✅ verified | `w_1000no` | 6 | 6 | 3/3 |
| ✅ verified | `w_1000ok` | 266 | 266 | 2/2 |
| ✅ verified | `w_100hno` | 401 | 401 | 4/4 |
| ✅ verified | `w_100hok` | 2442 | 2442 | 4/4 |
| ✅ verified | `w_100no` | 1200 | 1200 | 9/9 |
| ✅ verified | `w_100ok` | 3472 | 3472 | 5/5 |
| ✅ verified | `w_10kno` | 49 | 49 | 5/5 |
| ✅ verified | `w_10kok` | 2671 | 2671 | 2/2 |
| ✅ verified | `w_1500no` | 108 | 108 | 6/6 |
| ✅ verified | `w_1500ok` | 4087 | 4087 | 2/2 |
| ✅ verified | `w_2000no` | 4 | 4 | 3/3 |
| ✅ verified | `w_2000ok` | 187 | 187 | 2/2 |
| ✅ verified | `w_200no` | 528 | 528 | 9/9 |
| ✅ verified | `w_200ok` | 2900 | 2900 | 5/5 |
| ✅ verified | `w_3000no` | 8 | 8 | 4/4 |
| ✅ verified | `w_3000ok` | 1949 | 1949 | 3/3 |
| ✅ verified | `w_300no` | 595 | 595 | 8/8 |
| ✅ verified | `w_300ok` | 744 | 744 | 5/5 |
| ✅ verified | `w_400hno` | 16 | 16 | 3/3 |
| ✅ verified | `w_400hok` | 3176 | 3176 | 2/2 |
| ✅ verified | `w_400no` | 70 | 70 | 3/3 |
| ✅ verified | `w_400ok` | 4348 | 4348 | 4/4 |
| ✅ verified | `w_5000no` | 74 | 74 | 8/8 |
| ✅ verified | `w_5000ok` | 5809 | 5809 | 3/3 |
| ✅ verified | `w_600ok` | 271 | 271 | 3/3 |
| ✅ verified | `w_60mhno` | 24 | 24 | 3/3 |
| ✅ verified | `w_800no` | 66 | 66 | 5/5 |
| ✅ verified | `w_800ok` | 2612 | 2612 | 3/3 |
| ✅ verified | `w_mileno` | 21 | 21 | 4/4 |
| ✅ verified | `w_mileok` | 615 | 615 | 3/3 |
| ✅ verified | `wdiscno` | 67 | 67 | 8/8 |
| ✅ verified | `wdiscok` | 4011 | 4011 | 4/4 |
| ✅ verified | `whammno` | 728 | 728 | 13/13 |
| ✅ verified | `whammok` | 17675 | 17675 | 4/4 |
| ✅ verified | `whepano` | 83 | 83 | 3/3 |
| ✅ verified | `whepaok` | 2422 | 2422 | 2/2 |
| ✅ verified | `whighno` | 42 | 42 | 6/6 |
| ✅ verified | `whighok` | 2274 | 2274 | 6/6 |
| ✅ verified | `whmarano` | 65 | 65 | 6/6 |
| ✅ verified | `whmaraok` | 4175 | 4175 | 1/1 |
| ✅ verified | `wjaveno` | 153 | 153 | 3/3 |
| ✅ verified | `wjaveok` | 3497 | 3497 | 4/4 |
| ✅ verified | `wlongno` | 424 | 424 | 12/12 |
| ✅ verified | `wlongok` | 2014 | 2014 | 4/4 |
| ✅ verified | `wmarano` | 50 | 50 | 4/4 |
| ✅ verified | `wmaraok` | 5744 | 5744 | 2/2 |
| ✅ verified | `wpoleno` | 213 | 213 | 22/22 |
| ✅ verified | `wpoleok` | 25453 | 25453 | 7/7 |
| ✅ verified | `wshotno` | 125 | 125 | 5/5 |
| ✅ verified | `wshotok` | 2827 | 2827 | 4/4 |
| ✅ verified | `wtripleno` | 470 | 470 | 10/10 |
| ✅ verified | `wtripleok` | 4138 | 4138 | 4/4 |
| ✅ verified | `x4x400no` | 5 | 5 | 3/3 |
| ✅ verified | `x4x400ok` | 491 | 491 | 2/2 |
