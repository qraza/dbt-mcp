"""Structured logging and usage measurement for dbt-mcp.

Every tool call is recorded as one JSON line: which tool, how long it took, whether
it succeeded. That gives three things a demo doesn't have — an audit trail, latency
you can quote, and evidence of how much manual work the server displaced.

**Never log to stdout.** Under stdio transport stdout carries the MCP protocol itself;
writing anything else there corrupts the stream. Records go to a file, and errors to
stderr.
"""

from __future__ import annotations

import functools
import json
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_LOG_PATH = Path(".dbt-mcp/calls.jsonl")

# Minutes a human would spend answering the same question by hand: opening
# run_results.json, cross-referencing manifest.json, then querying the warehouse.
# Override with MANUAL_BASELINE_MINUTES.
DEFAULT_BASELINE_MINUTES = 15.0


def _log_path() -> Path:
    return Path(os.environ.get("DBT_MCP_LOG", str(DEFAULT_LOG_PATH))).expanduser()


def _write(record: dict[str, Any]) -> None:
    """Append one record. Logging must never break a tool call."""
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception as exc:  # noqa: BLE001
        print(f"dbt-mcp: could not write telemetry: {exc}", file=sys.stderr)


def logged(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Time a tool call and record the outcome.

    functools.wraps matters here: the MCP server derives the tool's name, schema and
    description from the wrapped function's signature and docstring.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        status = "ok"
        error: str | None = None
        try:
            result = fn(*args, **kwargs)
            if isinstance(result, dict) and "error" in result:
                status = "tool_error"
                error = str(result["error"])[:200]
            return result
        except Exception as exc:
            status = "exception"
            error = f"{type(exc).__name__}: {exc}"[:200]
            raise
        finally:
            _write(
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "tool": fn.__name__,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    "status": status,
                    "error": error,
                    "args": {k: str(v)[:80] for k, v in kwargs.items()},
                }
            )

    return wrapper
