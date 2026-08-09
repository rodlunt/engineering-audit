"""Tests for the gh CLI wrapper (src/engineering_audit/issues.py).

Every test injects a fake runner: nothing here ever shells out to a real gh
process.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from engineering_audit.issues import CreatedIssue, IssueFilingError, create_issue, detect_repo, gh_available


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
