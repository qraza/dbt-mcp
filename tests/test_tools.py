"""Tests for the dbt-mcp tools.

Fixtures are built in tmp_path so the suite is self-contained: no sibling repo,
no warehouse, no API key. The Anthropic call is mocked.
"""

from __future__ import annotations

import json

import pytest

from sentinel_mcp import server


@pytest.fixture
def target(tmp_path, monkeypatch):
    """A minimal dbt target/ directory: one model, one failing test."""
    manifest = {
        "metadata": {"project_name": "demo", "generated_at": "2026-08-01T00:00:00Z"},
        "nodes": {
            "model.demo.stg_trips": {
                "resource_type": "model",
                "name": "stg_trips",
                "schema": "main",
                "config": {"materialized": "view"},
                "depends_on": {"nodes": []},
            },
            "model.demo.int_trips": {
                "resource_type": "model",
                "name": "int_trips",
                "schema": "main",
                "description": "enriched trips",
                "config": {"materialized": "ephemeral"},
                "depends_on": {"nodes": ["model.demo.stg_trips"]},
            },
            "model.demo.mart_kpis": {
                "resource_type": "model",
                "name": "mart_kpis",
                "schema": "main",
                "config": {"materialized": "table"},
                "depends_on": {"nodes": ["model.demo.int_trips"]},
            },
            "test.demo.assert_speed": {
                "resource_type": "test",
                "name": "assert_speed",
                "compiled_code": "select * from trips where speed > 80",
                "depends_on": {"nodes": ["model.demo.int_trips"]},
            },
        },
    }
    run_results = {
        "results": [
            {"unique_id": "test.demo.assert_speed", "status": "fail", "failures": 3}
        ]
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "run_results.json").write_text(json.dumps(run_results))
    monkeypatch.setenv("DBT_TARGET_DIR", str(tmp_path))
    return tmp_path


def test_run_summary(target) -> None:
    r = server.run_summary()
    assert r["failing_test_count"] == 1
    assert r["healthy"] is False
    assert "assert_speed" in r["failing_tests"]


def test_list_failing_tests(target) -> None:
    rows = server.list_failing_tests()
    assert len(rows) == 1
    assert rows[0]["model"] == "int_trips"
    assert rows[0]["failure_count"] == 3


def test_model_lineage(target) -> None:
    r = server.model_lineage("int_trips")
    assert r["materialized"] == "ephemeral"
    assert r["depends_on"] == ["stg_trips"]
    assert r["used_by"] == ["mart_kpis"]
    assert "assert_speed" in r["guarded_by_tests"]


def test_model_lineage_unknown_model_lists_options(target) -> None:
    r = server.model_lineage("nope")
    assert "error" in r
    assert "int_trips" in r["available_models"]


def test_missing_target_dir_is_a_clear_error(monkeypatch) -> None:
    monkeypatch.delenv("DBT_TARGET_DIR", raising=False)
    with pytest.raises(RuntimeError, match="DBT_TARGET_DIR"):
        server.run_summary()


def test_test_history_without_database(target, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SENTINEL_HISTORY_PATH", str(tmp_path / "none.duckdb"))
    r = server.test_history("test.demo.assert_speed")
    assert "error" in r and "history" in r["error"].lower()
