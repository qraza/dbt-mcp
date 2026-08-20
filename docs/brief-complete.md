# Example: a complete briefing

Produced by the `data-quality-brief` skill against a dbt project, chaining six
`dbt-mcp` tool calls. None of the tools were named in the request.

---

**Data quality brief — 2026-08-20**

**Input health:** ⚠️ STALE — manifest is 577h (~24 days) old, last generated 2026-07-27.
The compiled SQL below may no longer match what's actually deployed in the warehouse —
treat the diagnosis as provisional until `dbt build` is rerun.

**Status:** 1 test failing

**1. assert_int_trips_enriched_speed_within_bounds — recurring** (first seen 2026-08-07,
unchanged across 3 recorded runs, still failing today)

- **Impact:** guards `int_trips_enriched` (ephemeral), which feeds 4 marts:
  `mart_hourly_patterns`, `mart_trip_summary`, `mart_payment_mix`, `mart_daily_kpis`.
  Any downstream speed/duration metric is affected.
- **Cause:** `avg_speed_mph` is computed as `600 * trip_distance / trip_duration_minutes`
  instead of `60 * ...` — a 10x unit-conversion error inflating every speed value.
- **Fix:** change the multiplier from 600 to 60 in the CTE (converts minutes→hours
  correctly: miles/(minutes/60)).
- **Confidence:** high — evidence: all 20 sampled rows are exactly 10x the plausible
  speed; all 1,826,500 failing rows exceed the 80mph threshold.

**Suggested order:** only one failure, so no prioritization needed — but fix this before
trusting anything downstream, since it silently corrupts 4 marts. Rerun `dbt build` /
refresh the manifest first, since the current diagnosis is based on a 24-day-old compile
and should be reconfirmed against live SQL.

---

**What to notice.** The staleness caveat survives a *high-confidence* diagnosis: the tool
found the bug, proved it arithmetically, and still told the reader to reconfirm against
live SQL before acting. Confidence in the answer and confidence in the inputs are tracked
separately.

Compare with [`brief-degraded.md`](brief-degraded.md), where the same skill ran without an
API key and reported confidence *none* rather than guessing.
