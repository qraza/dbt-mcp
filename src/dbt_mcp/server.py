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

import os
from pathlib import Path
from typing import Any

from dbt_sentinel.context import gather_context
from dbt_sentinel.parse import parse
from dbt_sentinel.warehouse import Warehouse, open_warehouse
from mcp.server import MCPServer

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


@mcp.tool()
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
        finally:
            warehouse.close()
    except Exception as exc:  # noqa: BLE001 - report any engine's failure
        report["ok"] = False
        report["checks"]["warehouse"] = f"error: {exc}"

    return report


def main() -> None:
    """Entry point — runs the server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()