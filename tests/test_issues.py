"""Tests for the gh CLI wrapper (src/engineering_audit/issues.py).

Every test injects a fake runner: nothing here ever shells out to a real gh
process.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from engineering_audit.issues import (
    CreatedIssue,
    IssueFilingError,
    create_issue,
    detect_repo,
    ensure_label,
    gh_available,
)


def _proc(returncode: int, stdout: str = "", stderr: str = "") -> "subprocess.CompletedProcess[str]":
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class _FakeRunner:
    """Records every call it receives and returns canned CompletedProcess
    results from a queue, one per call."""

    def __init__(self, results: list["subprocess.CompletedProcess[str]"]):
        self._results = list(results)
        self.calls: list[dict] = []

    def __call__(self, args: list[str], cwd: Path | None = None) -> "subprocess.CompletedProcess[str]":
        self.calls.append({"args": args, "cwd": cwd})
        return self._results.pop(0)


# ---------------------------------------------------------------------------
# gh_available
# ---------------------------------------------------------------------------


def test_gh_available_true_on_zero_exit() -> None:
    runner = _FakeRunner([_proc(0)])
    assert gh_available(runner) is True
    assert runner.calls[0]["args"] == ["gh", "auth", "status"]


def test_gh_available_false_on_nonzero_exit() -> None:
    runner = _FakeRunner([_proc(1, stderr="not logged in")])
    assert gh_available(runner) is False


# ---------------------------------------------------------------------------
# detect_repo
# ---------------------------------------------------------------------------


def test_detect_repo_returns_slug_on_success(tmp_path: Path) -> None:
    runner = _FakeRunner([_proc(0, stdout="rodlunt/widgets-app\n")])
    result = detect_repo(tmp_path, runner)
    assert result == "rodlunt/widgets-app"
    assert runner.calls[0]["cwd"] == tmp_path


def test_detect_repo_returns_none_when_no_github_remote(tmp_path: Path) -> None:
    runner = _FakeRunner([_proc(1, stderr="no git remotes found")])
    assert detect_repo(tmp_path, runner) is None


def test_detect_repo_returns_none_on_empty_stdout_with_zero_exit(tmp_path: Path) -> None:
    # Defensive: an unexpected empty-but-successful result must not be
    # mistaken for a real repo slug.
    runner = _FakeRunner([_proc(0, stdout="  \n")])
    assert detect_repo(tmp_path, runner) is None


# ---------------------------------------------------------------------------
# ensure_label
# ---------------------------------------------------------------------------


def test_ensure_label_reports_present_without_creating_anything() -> None:
    runner = _FakeRunner([_proc(0, stdout="engineering-audit\n")])
    status = ensure_label("rodlunt/widgets-app", runner=runner)
    assert (status.state, status.warning, status.usable) == ("present", None, True)
    assert len(runner.calls) == 1
    assert runner.calls[0]["args"][:3] == ["gh", "label", "list"]


def test_ensure_label_creates_the_label_when_it_is_missing() -> None:
    runner = _FakeRunner([_proc(0, stdout="some-other-label\n"), _proc(0, stdout="")])
    status = ensure_label("rodlunt/widgets-app", runner=runner)
    assert (status.state, status.warning, status.usable) == ("created", None, True)

    create_args = runner.calls[1]["args"]
    assert create_args[:4] == ["gh", "label", "create", "engineering-audit"]
    assert "--color" in create_args and "--description" in create_args


def test_ensure_label_reports_unavailable_with_one_warning_when_creation_fails() -> None:
    runner = _FakeRunner(
        [
            _proc(0, stdout=""),
            _proc(1, stderr="HTTP 403: Resource not accessible by integration"),
        ]
    )
    status = ensure_label("rodlunt/widgets-app", runner=runner)
    assert status.state == "unavailable"
    assert status.usable is False
    assert status.warning is not None
    assert "HTTP 403" in status.warning
    assert "rodlunt/widgets-app" in status.warning


def test_ensure_label_treats_an_already_exists_creation_error_as_present() -> None:
    # The list call failing must not be read as "the label is absent": the
    # create attempt is what settles it, and 'already exists' means present.
    runner = _FakeRunner(
        [
            _proc(1, stderr="could not list labels"),
            _proc(1, stderr="HTTP 422: Validation Failed (label already exists)"),
        ]
    )
    status = ensure_label("rodlunt/widgets-app", runner=runner)
    assert (status.state, status.warning) == ("present", None)


def test_ensure_label_warning_names_the_exit_code_when_gh_says_nothing() -> None:
    runner = _FakeRunner([_proc(0, stdout=""), _proc(3, stderr="   ")])
    status = ensure_label("rodlunt/widgets-app", runner=runner)
    assert status.state == "unavailable"
    assert status.warning is not None and "exited 3" in status.warning


# ---------------------------------------------------------------------------
# create_issue
# ---------------------------------------------------------------------------


def test_create_issue_returns_url_on_success() -> None:
    runner = _FakeRunner([_proc(0, stdout="https://github.com/rodlunt/widgets-app/issues/1\n")])
    result = create_issue("rodlunt/widgets-app", "Title", "Body", ["engineering-audit"], runner)
    assert result == CreatedIssue(url="https://github.com/rodlunt/widgets-app/issues/1", warnings=[])
    args = runner.calls[0]["args"]
    assert args[:3] == ["gh", "issue", "create"]
    assert "--label" in args and "engineering-audit" in args
    assert "--title" in args and "Title" in args
    assert "--body" in args and "Body" in args


def test_create_issue_raises_issue_filing_error_on_nonzero_exit() -> None:
    runner = _FakeRunner([_proc(1, stderr="HTTP 404: Not Found")])
    with pytest.raises(IssueFilingError) as excinfo:
        create_issue("rodlunt/widgets-app", "Title", "Body", [], runner)
    assert "HTTP 404" in str(excinfo.value)


def test_create_issue_raises_when_success_exit_prints_no_url() -> None:
    runner = _FakeRunner([_proc(0, stdout="")])
    with pytest.raises(IssueFilingError):
        create_issue("rodlunt/widgets-app", "Title", "Body", [], runner)


def test_create_issue_retries_once_without_labels_on_unknown_label_error() -> None:
    runner = _FakeRunner(
        [
            _proc(1, stderr="could not add label: 'engineering-audit' not found"),
            _proc(0, stdout="https://github.com/rodlunt/widgets-app/issues/2\n"),
        ]
    )
    result = create_issue("rodlunt/widgets-app", "Title", "Body", ["engineering-audit"], runner)
    assert result.url == "https://github.com/rodlunt/widgets-app/issues/2"
    assert result.warnings and "engineering-audit" in result.warnings[0]

    # First call carried the label, retry did not.
    first_args, second_args = runner.calls[0]["args"], runner.calls[1]["args"]
    assert "--label" in first_args
    assert "--label" not in second_args


def test_create_issue_raises_if_retry_without_labels_also_fails() -> None:
    runner = _FakeRunner(
        [
            _proc(1, stderr="label 'engineering-audit' not found"),
            _proc(1, stderr="HTTP 500: Internal Server Error"),
        ]
    )
    with pytest.raises(IssueFilingError) as excinfo:
        create_issue("rodlunt/widgets-app", "Title", "Body", ["engineering-audit"], runner)
    assert "HTTP 500" in str(excinfo.value)


def test_create_issue_does_not_retry_on_an_unrelated_error() -> None:
    runner = _FakeRunner([_proc(1, stderr="HTTP 403: Forbidden")])
    with pytest.raises(IssueFilingError):
        create_issue("rodlunt/widgets-app", "Title", "Body", ["engineering-audit"], runner)
    assert len(runner.calls) == 1
