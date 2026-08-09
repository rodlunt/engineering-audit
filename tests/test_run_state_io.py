"""Tests for the shared run-state.json loader
(src/engineering_audit/run_state_io.py), used by both engineering-audit-render
and engineering-audit-eval."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engineering_audit.run_state_io import RunStateLoadError, load_run_state_file
from engineering_audit.schema import RUN_STATE_SCHEMA_VERSION, AuditConfig, RunMeta, RunState


def _run_state() -> RunState:
    return RunState(
        meta=RunMeta(
            tool_version="0.1.0",
            rules_pack_name="fixture-pack",
            assistant="claude-code",
            model="claude-sonnet-5",
            repo_name="widgets-app",
            repo_commit="abc1234",
            started="2026-08-09T09:00:00+00:00",
            finished="2026-08-09T09:10:00+00:00",
        ),
        config=AuditConfig(selected_domain_ids=["d01"], issue_mode="report"),
    )


def test_loads_a_valid_run_state_file(tmp_path: Path) -> None:
    path = tmp_path / "run-state.json"
    path.write_text(_run_state().to_json(), encoding="utf-8")

    loaded = load_run_state_file(path)

    assert loaded.meta.repo_name == "widgets-app"


def test_missing_file_raises_run_state_load_error(tmp_path: Path) -> None:
    with pytest.raises(RunStateLoadError, match="does not exist"):
        load_run_state_file(tmp_path / "no-such-file.json")


def test_invalid_json_raises_run_state_load_error(tmp_path: Path) -> None:
    path = tmp_path / "run-state.json"
    path.write_text("{ not valid json", encoding="utf-8")

    with pytest.raises(RunStateLoadError, match="not a valid run-state file"):
        load_run_state_file(path)


@pytest.mark.parametrize("document", ["[1, 2, 3]", "null", '"just a string"', "42"])
def test_non_dict_top_level_raises_run_state_load_error_not_attribute_error(
    tmp_path: Path, document: str
) -> None:
    path = tmp_path / "run-state.json"
    path.write_text(document, encoding="utf-8")

    with pytest.raises(RunStateLoadError, match="not a valid run-state file"):
        load_run_state_file(path)


def test_higher_schema_version_raises_run_state_load_error_naming_both_versions(
    tmp_path: Path,
) -> None:
    data = json.loads(_run_state().to_json())
    data["schema_version"] = RUN_STATE_SCHEMA_VERSION + 1
    path = tmp_path / "run-state.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(RunStateLoadError) as excinfo:
        load_run_state_file(path)
    message = str(excinfo.value)
    assert str(RUN_STATE_SCHEMA_VERSION + 1) in message
    assert str(RUN_STATE_SCHEMA_VERSION) in message


def test_schema_validation_failure_raises_run_state_load_error(tmp_path: Path) -> None:
    # A syntactically valid JSON object that fails RunState's own schema
    # (missing the required 'meta' field) must surface the same typed
    # error as any other malformed run-state, not a raw pydantic traceback.
    path = tmp_path / "run-state.json"
    path.write_text(json.dumps({"config": {"selected_domain_ids": ["d01"], "issue_mode": "report"}}), encoding="utf-8")

    with pytest.raises(RunStateLoadError, match="not a valid run-state file"):
        load_run_state_file(path)
