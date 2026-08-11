"""Tests for src/engineering_audit/output_location.py (issue #109).

Shared by the interactive configuration page and the headless preset path,
so these functions are pinned down independently of either caller: see
config_page.py's own tests for the interactive-page behaviour and
test_server.py for the preset-path and render_report behaviour.
"""

from __future__ import annotations

import os

import pytest

from engineering_audit.output_location import (
    REPORT_FILENAME,
    RUN_STATE_FILENAME,
    deliverables_dir_for,
    existing_deliverables_warning,
    resolve_deliverables_dir,
    validate_deliverables_dir,
)


def test_resolve_deliverables_dir_expands_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert resolve_deliverables_dir("~/reports") == (tmp_path / "reports").resolve()


def test_resolve_deliverables_dir_makes_a_relative_path_absolute(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert resolve_deliverables_dir("reports") == (tmp_path / "reports").resolve()


def test_validate_deliverables_dir_accepts_an_empty_existing_directory(
    tmp_path,
) -> None:
    target = tmp_path / "reports"
    target.mkdir()
    assert validate_deliverables_dir(target) is None


def test_validate_deliverables_dir_accepts_a_directory_that_does_not_exist_yet_when_the_parent_does(
    tmp_path,
) -> None:
    target = tmp_path / "reports"
    assert validate_deliverables_dir(target) is None


def test_validate_deliverables_dir_rejects_a_missing_parent(tmp_path) -> None:
    target = tmp_path / "does-not-exist" / "reports"
    error = validate_deliverables_dir(target)
    assert error is not None
    assert "does not exist" in error


def test_validate_deliverables_dir_rejects_a_path_that_is_a_file(tmp_path) -> None:
    target = tmp_path / "not-a-directory"
    target.write_text("surprise", encoding="utf-8")
    error = validate_deliverables_dir(target)
    assert error is not None
    assert "not a directory" in error


def test_validate_deliverables_dir_rejects_an_unwritable_parent(tmp_path) -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip(
            "running as root: permission bits never block root, so an unwritable "
            "parent cannot be exercised this way"
        )
    parent = tmp_path / "locked"
    parent.mkdir()
    parent.chmod(0o500)
    try:
        error = validate_deliverables_dir(parent / "reports")
    finally:
        parent.chmod(0o700)
    assert error is not None
    assert "not writable" in error


@pytest.mark.parametrize("existing_name", [REPORT_FILENAME, RUN_STATE_FILENAME])
def test_validate_deliverables_dir_rejects_a_directory_already_holding_a_report(
    tmp_path, existing_name
) -> None:
    target = tmp_path / "reports"
    target.mkdir()
    (target / existing_name).write_text("an earlier run", encoding="utf-8")
    error = validate_deliverables_dir(target)
    assert error is not None
    assert existing_name in error
    assert "already contains" in error


def test_existing_deliverables_warning_none_for_a_directory_that_does_not_exist_yet(
    tmp_path,
) -> None:
    target = tmp_path / "audit-output"
    assert existing_deliverables_warning(target) is None


def test_existing_deliverables_warning_none_for_an_empty_existing_directory(
    tmp_path,
) -> None:
    target = tmp_path / "audit-output"
    target.mkdir()
    assert existing_deliverables_warning(target) is None


@pytest.mark.parametrize("existing_name", [REPORT_FILENAME, RUN_STATE_FILENAME])
def test_existing_deliverables_warning_warns_without_refusing(
    tmp_path, existing_name
) -> None:
    target = tmp_path / "audit-output"
    target.mkdir()
    (target / existing_name).write_text("an earlier run", encoding="utf-8")
    warning = existing_deliverables_warning(target)
    assert warning is not None
    assert existing_name in warning
    assert "already contains" in warning
    # Issue #133: this must warn, never refuse. validate_deliverables_dir is
    # the function that refuses; this one only ever returns a string or None.
    assert "replace" in warning.lower()


def test_existing_deliverables_warning_names_both_files_when_both_are_present(
    tmp_path,
) -> None:
    target = tmp_path / "audit-output"
    target.mkdir()
    (target / REPORT_FILENAME).write_text("an earlier run", encoding="utf-8")
    (target / RUN_STATE_FILENAME).write_text("{}", encoding="utf-8")
    warning = existing_deliverables_warning(target)
    assert warning is not None
    assert REPORT_FILENAME in warning
    assert RUN_STATE_FILENAME in warning


def test_deliverables_dir_for_defaults_to_output_dir_when_none_chosen(tmp_path) -> None:
    output_dir = tmp_path / "audit-output"
    assert deliverables_dir_for(output_dir, None) == output_dir


def test_deliverables_dir_for_honours_an_explicit_choice(tmp_path) -> None:
    output_dir = tmp_path / "audit-output"
    chosen = tmp_path / "reports" / "this-run"
    assert deliverables_dir_for(output_dir, str(chosen)) == chosen
