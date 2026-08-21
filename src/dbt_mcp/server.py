"""dbt-mcp — an MCP server that lets an AI assistant inspect a dbt project.

Exposes dbt run state as MCP tools so an agent can compose its own answers to
questions like "is the warehouse healthy?" or "what broke and why?" without any
orchestration written by hand.

The heavy lifting (parsing dbt artifacts, sampling offending rows) is delegated to
the published `dbt-sentinel` package, so this module stays a thin, well-behaved
protocol layer.

Configuration (environment variables):
    DBT_TARGET_DIR   path to a dbt target/ directory (required)
    DBT_DUCKDB_PATH  path to a DuckDB warehouse file
    BQ_PROJECT       BigQuery project id (alternative to DBT_DUCKDB_PATH)
    BQ_LOCATION      BigQuery dataset location, e.g. EU

Run:
    uv run dbt-mcp            # stdio transport, for Claude Desktop / Cowork / Claude Code
"""

from __future__ import annotations

import json
import os
from datetime import UTC
from pathlib import Path
from typing import Any

import duckdb
from dbt_sentinel import store
from dbt_sentinel.analyze import analyze
from dbt_sentinel.context import gather_context
from dbt_sentinel.parse import parse
from dbt_sentinel.warehouse import Warehouse, open_warehouse
from mcp.server import MCPServer

from . import telemetry
from .telemetry import logged

mcp = MCPServer(
    name="dbt-mcp",
    instructions=(
        "Inspect a dbt project's most recent run: which tests failed, why, and "
        "how models relate. Prefer list_failing_tests first to see what is wrong, "
        "then sample_failing_rows or explain_failure for a specific test."
    ),
)


# --- configuration -------------------------------------------------------


def _target_dir() -> Path:
    raw = os.environ.get("DBT_TARGET_DIR")
    if not raw:
        raise RuntimeError("DBT_TARGET_DIR is not set; point it at a dbt target/ directory.")
    p = Path(raw).expanduser()
    if not p.is_dir():
        raise RuntimeError(f"DBT_TARGET_DIR does not exist: {p}")
    return p


def _warehouse() -> Warehouse:
    duck = os.environ.get("DBT_DUCKDB_PATH")
    project = os.environ.get("BQ_PROJECT")
    return open_warehouse(
        duckdb_path=Path(duck).expanduser() if duck else None,
        bq_project=project,
        bq_location=os.environ.get("BQ_LOCATION"),
    )


# --- tools ---------------------------------------------------------------


@mcp.tool()
@logged
def run_summary() -> dict[str, Any]:
    """Summarise the most recent dbt run: how many tests failed, and their names.

    Use this first to judge overall health before drilling into any one failure.
    """
    failures = parse(_target_dir())
    return {
        "failing_test_count": len(failures),
        "healthy": not failures,
        "failing_tests": [f.test_name for f in failures],
    }


@mcp.tool()
@logged
def list_failing_tests() -> list[dict[str, Any]]:
    """List every failing test in the last dbt run, with what it guards.

    Returns the test name, the model and column it protects, how many rows failed,
    and whether it is a generic (not_null, unique...) or hand-written test.
    """
    return [
        {
            "unique_id": f.unique_id,
            "test_name": f.test_name,
            "status": f.status,
            "failure_count": f.failure_count,
            "model": f.model_name,
            "column": f.column_name,
            "relation": f.relation,
            "test_type": f.test_type or "singular",
        }
        for f in parse(_target_dir())
    ]


@mcp.tool()
@logged
def sample_failing_rows(test_name: str, limit: int = 10) -> dict[str, Any]:
    """Fetch the actual rows that caused a test to fail.

    Args:
        test_name: the test's name or unique_id, as returned by list_failing_tests.
        limit: maximum rows to return (kept small on purpose).
    """
    matches = [
        f
        for f in parse(_target_dir())
        if test_name in (f.test_name, f.unique_id)
    ]
    if not matches:
        return {"error": f"No failing test matching '{test_name}'. Try list_failing_tests."}

    warehouse = _warehouse()
    try:
        ctx = gather_context(matches[0], warehouse, sample_limit=limit)
    finally:
        warehouse.close()

    return {
        "test_name": matches[0].test_name,
        "failure_count": matches[0].failure_count,
        "columns": [name for name, _ in ctx.columns],
        "rows": ctx.sample_rows,
        "note": ctx.note,
    }


def _consistency_check(warehouse: Warehouse) -> dict[str, Any]:
    """Verify the manifest and the warehouse describe the same project.

    Pointing DBT_TARGET_DIR at one project and the warehouse at another is a
    silent, dangerous misconfiguration: the test SQL still runs (ephemeral models
    are inlined as CTEs), so you get a confident diagnosis of a model that isn't
    in your warehouse. This makes that fail loudly instead.
    """
    manifest_path = _target_dir() / "manifest.json"
    with manifest_path.open(encoding="utf-8") as fh:
        manifest = json.load(fh)

    project = manifest.get("metadata", {}).get("project_name")
    generated_at = manifest.get("metadata", {}).get("generated_at")

    relations: list[str] = []
    for node in manifest.get("nodes", {}).values():
        if node.get("resource_type") != "model":
            continue
        if node.get("config", {}).get("materialized") == "ephemeral":
            continue
        schema, identifier = node.get("schema"), node.get("alias") or node.get("name")
        if schema and identifier:
            relations.append(f"{schema}.{identifier}")
        if len(relations) >= 5:
            break

    if not relations:
        return {
            "manifest_project": project,
            "manifest_generated_at": generated_at,
            "verdict": "inconclusive: no materialized models to check",
        }

    found, missing = [], []
    for relation in relations:
        try:
            warehouse.query(f"select * from {relation} limit 0")
            found.append(relation)
        except Exception:  # noqa: BLE001
            missing.append(relation)

    if not found:
        verdict = (
            "MISMATCH: none of the manifest's models exist in this warehouse. "
            "DBT_TARGET_DIR and the warehouse point at different projects; any "
            "diagnosis would describe SQL that is not in your warehouse."
        )
    elif missing:
        verdict = f"partial: {len(found)} of {len(relations)} sampled models found"
    else:
        verdict = "consistent: manifest models are present in the warehouse"

    age_note = None
    if generated_at:
        from datetime import datetime

        try:
            gen = datetime.fromisoformat(generated_at)
            hours = (datetime.now(UTC) - gen).total_seconds() / 3600
            age_note = f"manifest is {hours:.0f}h old"
            if hours > 24:
                verdict = (
                    f"STALE: {age_note}. The compiled SQL in this manifest may no "
                    "longer match the models in the warehouse; re-run dbt build "
                    "before trusting a diagnosis. (" + verdict + ")"
                )
        except ValueError:
            pass

    return {
        "manifest_project": project,
        "manifest_generated_at": generated_at,
        "manifest_age": age_note,
        "models_found": found,
        "models_missing": missing,
        "verdict": verdict,
    }


@mcp.tool()
@logged
def health() -> dict[str, Any]:
    """Check that the server is configured correctly and can reach its inputs.

    Verifies the target directory, the required artifacts, and warehouse connectivity.
    """
    report: dict[str, Any] = {"ok": True, "checks": {}}

    try:
        target = _target_dir()
        report["checks"]["target_dir"] = str(target)
        for artifact in ("run_results.json", "manifest.json"):
            exists = (target / artifact).is_file()
            report["checks"][artifact] = "found" if exists else "MISSING"
            if not exists:
                report["ok"] = False
    except RuntimeError as exc:
        report["ok"] = False
        report["checks"]["target_dir"] = f"error: {exc}"

    try:
        warehouse = _warehouse()
        try:
            warehouse.query("select 1")
            report["checks"]["warehouse"] = f"{warehouse.name}: reachable"
            consistency = _consistency_check(warehouse)
            report["checks"]["consistency"] = consistency
            if consistency["verdict"].startswith("MISMATCH"):
                report["ok"] = False
        finally:
            warehouse.close()
    except Exception as exc:  # noqa: BLE001 - report any engine's failure
        report["ok"] = False
        report["checks"]["warehouse"] = f"error: {exc}"

    return report


# --- M2 tools -------------------------------------------------------------


def _find_test(test_name: str):
    """Resolve a test by name or unique_id from the latest run."""
    for f in parse(_target_dir()):
        if test_name in (f.test_name, f.unique_id):
            return f
    return None


@mcp.tool()
@logged
def explain_failure(test_name: str, sample_limit: int = 20) -> dict[str, Any]:
    """Explain WHY a test failed, grounded in the rows that actually broke.

    Samples the offending rows, then asks an LLM to diagnose the root cause using
    only that evidence. Returns a confidence level -- treat 'low' as "the evidence
    did not support a firm conclusion", not as a weak answer.

    Requires ANTHROPIC_API_KEY. Use list_failing_tests first to get a test name.

    Args:
        test_name: the test's name or unique_id.
        sample_limit: how many offending rows to reason over.
    """
    test = _find_test(test_name)
    if test is None:
        return {"error": f"No failing test matching '{test_name}'. Try list_failing_tests."}

    warehouse = _warehouse()
    try:
        ctx = gather_context(test, warehouse, sample_limit=sample_limit)
    finally:
        warehouse.close()

    try:
        analysis = analyze(ctx)
    except RuntimeError as exc:
        return {"error": str(exc)}

    return {
        "test_name": test.test_name,
        "failure_count": test.failure_count,
        "root_cause": analysis.root_cause,
        "suggested_fix": analysis.suggested_fix,
        "confidence": analysis.confidence,
        "evidence": analysis.evidence,
        "rows_examined": ctx.sampled_count,
    }


@mcp.tool()
@logged
def model_lineage(model_name: str) -> dict[str, Any]:
    """Show what a model depends on and what depends on it.

    Reads the dbt manifest -- a deterministic graph lookup, no AI involved.
    Useful for judging blast radius: if this model is wrong, what else is affected?

    Args:
        model_name: the model's name, e.g. 'int_trips_enriched'.
    """
    manifest_path = _target_dir() / "manifest.json"
    with manifest_path.open(encoding="utf-8") as fh:
        manifest = json.load(fh)

    nodes: dict[str, Any] = manifest.get("nodes", {})
    sources: dict[str, Any] = manifest.get("sources", {})

    matches = [
        (uid, node)
        for uid, node in nodes.items()
        if node.get("resource_type") == "model" and node.get("name") == model_name
    ]
    if not matches:
        available = sorted(
            n["name"] for n in nodes.values() if n.get("resource_type") == "model"
        )
        return {"error": f"No model named '{model_name}'.", "available_models": available}

    unique_id, node = matches[0]

    def _label(dep_id: str) -> str:
        n = nodes.get(dep_id) or sources.get(dep_id) or {}
        return n.get("name", dep_id)

    parents = [_label(d) for d in node.get("depends_on", {}).get("nodes", [])]
    children = [
        n.get("name", uid)
        for uid, n in nodes.items()
        if unique_id in n.get("depends_on", {}).get("nodes", [])
        and n.get("resource_type") == "model"
    ]
    tests = [
        n.get("name", uid)
        for uid, n in nodes.items()
        if n.get("resource_type") == "test"
        and unique_id in n.get("depends_on", {}).get("nodes", [])
    ]

    return {
        "model": model_name,
        "materialized": node.get("config", {}).get("materialized"),
        "depends_on": parents,
        "used_by": children,
        "guarded_by_tests": tests,
        "description": node.get("description") or None,
    }


@mcp.tool()
@logged
def test_history(test_name: str) -> dict[str, Any]:
    """Show whether a test is newly broken or has been failing for a while.

    Reads dbt-sentinel's own run history. Use this to prioritise: a test that
    started failing today is usually more urgent than one failing for weeks.

    Args:
        test_name: the test's unique_id (preferred) or name.
    """
    history_path = Path(
        os.environ.get("SENTINEL_HISTORY_PATH", str(store.DEFAULT_HISTORY_PATH))
    ).expanduser()

    if not history_path.is_file():
        return {
            "error": f"No history database at {history_path}. "
            "Run `sentinel analyze` at least once to start recording runs."
        }

    con = duckdb.connect(str(history_path), read_only=True)
    try:
        rows = con.execute(
            """
            select r.run_at, t.unique_id, t.test_name, t.status,
                   t.failure_count, t.confidence
            from test_results t join runs r using (run_id)
            where t.unique_id = ? or t.test_name = ?
            order by r.run_at
            """,
            [test_name, test_name],
        ).fetchall()
    finally:
        con.close()

    if not rows:
        return {"test_name": test_name, "runs": [], "note": "No recorded history for this test."}

    entries = [
        {
            "run_at": str(run_at),
            "status": status,
            "failure_count": failure_count,
            "confidence": confidence,
        }
        for run_at, _uid, _name, status, failure_count, confidence in rows
    ]
    first, last = entries[0], entries[-1]
    trend = "unchanged"
    if first["failure_count"] and last["failure_count"]:
        if last["failure_count"] > first["failure_count"]:
            trend = "worsening"
        elif last["failure_count"] < first["failure_count"]:
            trend = "improving"

    return {
        "test_name": rows[0][2],
        "times_recorded": len(entries),
        "first_seen": first["run_at"],
        "last_seen": last["run_at"],
        "trend": trend,
        "runs": entries,
    }


@mcp.tool()
@logged
def usage_stats() -> dict[str, Any]:
    """Summarise how this server has been used: calls, latency, errors, time saved.

    Reads the structured call log. The time-saved figure compares actual tool latency
    against MANUAL_BASELINE_MINUTES -- how long the same question takes by hand
    (opening run_results.json, cross-referencing manifest.json, querying the warehouse).
    It is an estimate based on a stated assumption, not a measurement of the human.
    """
    path = Path(
        os.environ.get("DBT_MCP_LOG", str(telemetry.DEFAULT_LOG_PATH))
    ).expanduser()
    if not path.is_file():
        return {"error": f"No call log at {path}. Make some tool calls first."}

    records = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not records:
        return {"error": "Call log is empty."}

    durations = sorted(r["duration_ms"] for r in records if "duration_ms" in r)
    by_tool: dict[str, int] = {}
    for r in records:
        by_tool[r.get("tool", "?")] = by_tool.get(r.get("tool", "?"), 0) + 1

    failures = [r for r in records if r.get("status") != "ok"]
    baseline = float(
        os.environ.get("MANUAL_BASELINE_MINUTES", telemetry.DEFAULT_BASELINE_MINUTES)
    )
    # A single question costs several tool calls, so counting calls would inflate
    # the figure. Attribute saved time only to completed diagnoses -- the work a
    # human would otherwise have done by hand.
    answered = len(
        [r for r in records if r.get("tool") == "explain_failure" and r.get("status") == "ok"]
    )
    machine_minutes = sum(durations) / 1000 / 60
    saved = max(0.0, round(answered * baseline - machine_minutes, 1))

    def pct(p: float) -> float:
        if not durations:
            return 0.0
        return round(durations[min(int(len(durations) * p), len(durations) - 1)], 1)

    return {
        "total_calls": len(records),
        "successful": len([r for r in records if r.get("status") == "ok"]),
        "diagnoses_completed": answered,
        "errors": len(failures),
        "calls_by_tool": by_tool,
        "latency_ms": {"p50": pct(0.5), "p95": pct(0.95), "max": round(durations[-1], 1)},
        "manual_baseline_minutes": baseline,
        "estimated_minutes_saved": saved,
        "assumption": (
            f"Assumes {baseline} minutes per diagnosis done by hand; only completed "
            f"explain_failure calls count, not every tool call. "
            "Adjust with MANUAL_BASELINE_MINUTES."
        ),
    }


def main() -> None:
    """Entry point — runs the server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()