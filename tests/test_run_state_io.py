"""Tests for the shared run-state file I/O (src/engineering_audit/run_state_io.py):
the loader used by engineering-audit-render, engineering-audit-eval and the
server's resume path, and the atomic writer every saved file goes through."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import engineering_audit.run_state_io as io_module
from engineering_audit.run_state_io import (
    RunStateLoadError,
    atomic_write_text,
    load_run_progress_file,
    load_run_state_file,
    save_run_progress,
)
from engineering_audit.schema import (
    RUN_STATE_SCHEMA_VERSION,
    AuditConfig,
    RunMeta,
    RunProgress,
    RunState,
)


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
    path.write_text(
        json.dumps(
            {"config": {"selected_domain_ids": ["d01"], "issue_mode": "report"}}
        ),
        encoding="utf-8",
    )

    with pytest.raises(RunStateLoadError, match="not a valid run-state file"):
        load_run_state_file(path)


def test_non_integer_schema_version_is_a_named_error_not_a_type_error(
    tmp_path: Path,
) -> None:
    # A string version compares fine against an int in some languages and
    # raises TypeError in this one; either way it is a corrupt file, and the
    # caller must get the same typed error it gets for every other corruption.
    data = json.loads(_run_state().to_json())
    data["schema_version"] = "2"
    path = tmp_path / "run-state.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(RunStateLoadError, match="not an integer version"):
        load_run_state_file(path)


# ---------------------------------------------------------------------------
# RunProgress: the crash-recovery record
# ---------------------------------------------------------------------------


def _progress(**overrides) -> RunProgress:
    defaults = dict(
        meta=_run_state().meta,
        config=AuditConfig(selected_domain_ids=["d01"], issue_mode="report"),
    )
    defaults.update(overrides)
    return RunProgress(**defaults)


def test_save_and_load_run_progress_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "run-state.progress.json"
    progress = _progress(filed_issues={"D01-R02#1": "https://example.invalid/issues/1"})

    save_run_progress(path, progress)

    assert load_run_progress_file(path) == progress


def test_missing_progress_file_is_a_load_error_not_a_silent_empty_state(
    tmp_path: Path,
) -> None:
    with pytest.raises(RunStateLoadError, match="does not exist"):
        load_run_progress_file(tmp_path / "run-state.progress.json")


def test_corrupt_progress_file_raises_rather_than_parsing_partially(
    tmp_path: Path,
) -> None:
    # The half-written-file case the atomic writer exists to prevent, checked
    # from the reading side: it must be refused, never read as a run that
    # simply had fewer domains.
    path = tmp_path / "run-state.progress.json"
    path.write_text(_progress().to_json()[:200], encoding="utf-8")

    with pytest.raises(RunStateLoadError, match="not a valid run-progress file"):
        load_run_progress_file(path)


def test_higher_schema_version_progress_file_is_refused_naming_both_versions(
    tmp_path: Path,
) -> None:
    data = json.loads(_progress().to_json())
    data["schema_version"] = RUN_STATE_SCHEMA_VERSION + 1
    path = tmp_path / "run-state.progress.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(RunStateLoadError) as excinfo:
        load_run_progress_file(path)
    message = str(excinfo.value)
    assert str(RUN_STATE_SCHEMA_VERSION + 1) in message
    assert str(RUN_STATE_SCHEMA_VERSION) in message


def test_undecodable_progress_file_is_a_load_error_not_a_unicode_crash(
    tmp_path: Path,
) -> None:
    path = tmp_path / "run-state.progress.json"
    path.write_bytes(b"\xff\xfe\x00binary rubbish")

    with pytest.raises(RunStateLoadError, match="could not read"):
        load_run_progress_file(path)


# ---------------------------------------------------------------------------
# atomic_write_text
# ---------------------------------------------------------------------------


def test_atomic_write_replaces_the_file_contents(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("old", encoding="utf-8")

    atomic_write_text(path, "new")

    assert path.read_text(encoding="utf-8") == "new"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]


def test_atomic_write_stages_the_temp_file_in_the_target_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # os.replace is only atomic within one filesystem, so the temp file has to
    # be a sibling of the target rather than in the system temp directory. If
    # this ever regresses, the rename silently degrades into a copy that can
    # be interrupted halfway, which is exactly the failure this module exists
    # to rule out.
    renames: list[tuple[str, str]] = []
    real_replace = os.replace

    def _record(src, dst):
        renames.append((str(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(io_module.os, "replace", _record)
    path = tmp_path / "nested" / "state.json"
    path.parent.mkdir()

    atomic_write_text(path, "new")

    assert len(renames) == 1
    assert Path(renames[0][0]).parent == path.parent


def test_an_interrupted_rename_leaves_the_previous_state_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.json"
    previous = _progress().to_json()
    path.write_text(previous, encoding="utf-8")

    def _explode(_src, _dst):
        raise OSError("interrupted")

    monkeypatch.setattr(io_module.os, "replace", _explode)

    with pytest.raises(OSError, match="interrupted"):
        atomic_write_text(path, "half a new file")

    # The point of the exercise: the reader still finds the last good state,
    # not a truncated one that would parse as a valid but wrong run.
    assert path.read_text(encoding="utf-8") == previous
    assert load_run_progress_file(path) == _progress()
    # And the failed write left no temp file litter behind.
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_a_write_that_fails_midway_leaves_the_previous_state_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The other half of the crash: the process dies while the bytes are going
    # down, before any rename. Simulated by failing the flush-to-disk step.
    path = tmp_path / "state.json"
    previous = _progress().to_json()
    path.write_text(previous, encoding="utf-8")

    def _explode(_fd):
        raise OSError("no space left on device")

    monkeypatch.setattr(io_module.os, "fsync", _explode)

    with pytest.raises(OSError, match="no space left"):
        atomic_write_text(path, "half a new file")

    assert path.read_text(encoding="utf-8") == previous
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]
