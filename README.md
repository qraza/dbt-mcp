# dbt-mcp

**Ask your dbt project what's wrong, in plain English.** An MCP server that exposes a
dbt project's run state as tools, so an AI assistant can compose its own answers to
questions like "is the warehouse healthy?" or "what broke and why?" — no orchestration
written by hand.

Built on [dbt-sentinel](https://pypi.org/project/dbt-sentinel/), which does the artifact
parsing and row sampling.

![dbt-mcp tools in MCP Inspector](docs/tools.png)

## Tools

| Tool | Answers |
| --- | --- |
| `run_summary` | What failed in the last dbt run, at a glance |
| `list_failing_tests` | Each failure: what it guards, how many rows, which test type |
| `sample_failing_rows` | The actual offending rows, capped |
| `health` | Is the server configured correctly and can it reach its inputs |

## Quickstart

```bash
uv sync
export DBT_TARGET_DIR=/path/to/dbt/target
export DBT_DUCKDB_PATH=/path/to/warehouse.duckdb   # or BQ_PROJECT=my-project
uv run dbt-mcp
```

Inspect it interactively:

```bash
npx @modelcontextprotocol/inspector \
  -e DBT_TARGET_DIR=$DBT_TARGET_DIR \
  -e DBT_DUCKDB_PATH=$DBT_DUCKDB_PATH \
  uv run dbt-mcp
```

## Configuration

| Variable | Purpose |
| --- | --- |
| `DBT_TARGET_DIR` | dbt `target/` directory (required) |
| `DBT_DUCKDB_PATH` | DuckDB warehouse file |
| `BQ_PROJECT` / `BQ_LOCATION` | BigQuery alternative |

## Design decisions

**Why MCP rather than a CLI.** A CLI answers the question you anticipated. MCP tools let
an agent compose answers to questions you didn't — it decides which tools to call and in
what order.

**Thin tools, not one god-tool.** Each tool does one legible thing so the model can reason
about when to use it. The docstrings *are* the interface: they become the tool descriptions
the model reads.

**Read-only by contract.** The warehouse is opened read-only; this inspects, never mutates.

**Errors are messages, not stack traces.** A missing config returns "DBT_TARGET_DIR is not
set; point it at a dbt target/ directory" — something an agent can act on.

## Status

M1 complete: server, four tools, verified against a real dbt project via MCP Inspector.
Next: `explain_failure` (grounded root-cause analysis), `model_lineage`, `test_history`.

## Development

```bash
uv sync --group dev
uv run ruff check .
uv run pytest -v
```
