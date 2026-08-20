# Example: briefing when a tool is unavailable

The `explain_failure` tool requires an Anthropic API key. When the server was
registered without one, the skill reported the gap plainly rather than guessing a
cause — and set confidence to *none*.

---

**Data quality brief — 2026-08-20**

**Input health:** STALE — manifest is 577h old (generated 2026-07-27). Diagnoses below
rest on this manifest; re-run `dbt build` before trusting them fully. Manifest/warehouse
model lists are otherwise consistent.

**Status:** 1 test failing.

**1. assert_int_trips_enriched_speed_within_bounds — recurring** (unchanged; failing
consistently across all 3 recorded runs since 2026-08-07 13:06, last seen 2026-08-07 16:38)

- **Impact:** Guards `int_trips_enriched`, an ephemeral model feeding 4 marts:
  `mart_hourly_patterns`, `mart_trip_summary`, `mart_payment_mix`, `mart_daily_kpis`.
  It's also one of ~20 tests on this model, several of which reconcile revenue/trip
  counts downstream — this is a high-blast-radius node.
- **Failure count:** 1,826,500 rows
- **Cause:** Unknown — `explain_failure` errored: *No Anthropic API key.* No root-cause
  diagnosis was run; not working around this silently.
- **Fix:** Set `ANTHROPIC_API_KEY` in the dbt-mcp server environment and re-run
  `explain_failure` to get a grounded cause before deciding a fix. Given the test name,
  worth manually eyeballing implausible speed values as a starting hypothesis, but don't
  act on that until confirmed.
- **Confidence:** none (root cause not obtained)

**Suggested order:** Only one failure, so no ranking needed — but treat it as urgent
given it fans out to all four marts. First priority: refresh the manifest (~24 days
stale) and set the API key so root cause can actually be diagnosed, rather than fixing
blind.

---

**Why this is in the README.** The deterministic tools still worked, so the brief kept
its impact and history sections; only the AI-dependent section degraded, and it degraded
loudly. A tool that quietly substitutes a plausible guess when a dependency is missing is
worse than one that says "I could not determine this."
