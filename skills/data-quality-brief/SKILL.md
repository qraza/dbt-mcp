---
name: data-quality-brief
description: Produce a prioritised data-quality briefing for a dbt project — what is failing, why, what it affects, and what to fix first. Use when the user asks for a data quality brief, asks "what's broken in dbt", or wants a morning check on warehouse health.
---

# Data quality brief

Produce a short, prioritised briefing on the state of a dbt project using the
`dbt-mcp` tools. Aim for something a data team could read in under a minute.

## Procedure

1. **Check the inputs first.** Call `health`. If `consistency.verdict` starts with
   `MISMATCH` or `STALE`, say so at the top of the brief and stop treating the
   diagnoses as authoritative — a stale manifest means the compiled SQL may not match
   what is deployed. Do not skip this step; a confident diagnosis of the wrong code is
   worse than no diagnosis.

2. **Get the shape of the problem.** Call `run_summary`. If nothing is failing, say so
   in one line and stop — do not pad a clean result.

3. **For each failing test** (call `list_failing_tests`):
   - `test_history` — is this new today, or long-standing? New breakage usually
     outranks something that has been failing for weeks.
   - `model_lineage` — what depends on the guarded model? A failure feeding four marts
     matters more than one feeding nothing.
   - `explain_failure` — the root cause. Call this last; it is the only tool that costs
     an API call.

4. **Prioritise** by blast radius first, then recency, then row count. State the
   ordering rationale in one clause so the reader can disagree with it.

## Output format
Data quality brief — <date>

Status: <one line: healthy, or N tests failing>
Input health: <only if health reported STALE/MISMATCH>

1. <test name> — <new | recurring | regressed>
Impact: <what depends on it>
Cause: <one sentence, plain English>
Fix: <the concrete change>
Confidence: <high | medium | low>
2. ...

Suggested order: <why this order>


## Rules

- Report low-confidence diagnoses as uncertain. Never restate a low-confidence root
  cause as fact.
- Quote row counts and model names exactly as the tools return them; do not round or
  paraphrase figures.
- If a tool returns an error, report it plainly rather than working around it silently.
- Keep the whole brief under 300 words unless there are more than three failures.
