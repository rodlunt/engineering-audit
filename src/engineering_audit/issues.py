"""GitHub interaction via the user's own `gh` CLI.

This module never handles a GitHub token or credential of any kind: every
call shells out to `gh`, which relies on whatever auth the user already has
configured on this machine. That is a deliberate scope boundary, not an
oversight.

Every function takes a `runner` callable, defaulting to a thin wrapper around
`subprocess.run`, so tests can inject a fake and assert on exactly which `gh`
command was built without touching a real process or a real repository.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

__all__ = [
    "CommandRunner",
    "CreatedIssue",
    "IssueFilingError",
    "gh_available",
    "detect_repo",
    "create_issue",
]

CommandRunner = Callable[..., "subprocess.CompletedProcess[str]"]


class IssueFilingError(Exception):
    """Raised when `gh issue create` exits non-zero and no recoverable retry
    applies. Carries the command's stderr verbatim: an issue-filing failure
    is exactly the kind of thing that must never be swallowed into a quiet
    "0 issues filed", because a finding that was never actually reported
    looks identical, downstream, to a clean run."""


@dataclass
class CreatedIssue:
    """The result of successfully creating one issue.

    `warnings` is non-empty only for the missing-label retry path: a label
    that does not exist on the target repository is cosmetic (the issue
    still gets filed), but it must not vanish silently either, since a
    caller relying on the "engineering-audit" label to find these issues
    later needs to know some of them do not carry it.
    """

    url: str
    warnings: list[str] = field(default_factory=list)


def _default_runner(args: list[str], cwd: Path | None = None) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)


def gh_available(runner: CommandRunner = _default_runner) -> bool:
    """True if `gh` is installed and authenticated.

    Checked via `gh auth status`'s exit code rather than just "is gh on
    PATH": an unauthenticated gh binary can still be invoked, and would fail
    every subsequent call with a much less clear error than catching it
    here first.
    """
    result = runner(["gh", "auth", "status"])
    return result.returncode == 0


def detect_repo(cwd: Path, runner: CommandRunner = _default_runner) -> str | None:
    """Return the `owner/name` slug of the GitHub repository at `cwd`, or
    None if `cwd` has no GitHub remote.

    A non-zero exit from `gh repo view` here always means "no GitHub remote
    could be resolved from this directory", never a distinct error class:
    `gh` itself does not give us a machine-readable way to separate "not a
    git repository" from "a git repository with no GitHub remote" from
    other lookup failures, and all three collapse to the same actionable
    outcome for a caller: this directory is not a GitHub repo, ask the user
    for `owner/name` explicitly instead. Call `gh_available` first if you
    need to distinguish "not installed/authenticated" from "no remote".
    """
    result = runner(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        cwd=cwd,
    )
    if result.returncode != 0:
        return None
    name = (result.stdout or "").strip()
    return name or None


def _looks_like_unknown_label_error(stderr: str) -> bool:
    lowered = stderr.lower()
    if "label" not in lowered:
        return False
    return any(
        phrase in lowered
        for phrase in ("not found", "does not exist", "could not resolve", "could not add")
    )


def _extract_issue_url(result: "subprocess.CompletedProcess[str]", repo: str) -> str:
    stdout = (result.stdout or "").strip()
    if not stdout:
        raise IssueFilingError(
            f"gh issue create for repo {repo} exited 0 but printed no output "
            "(expected the created issue's URL on the last line)"
        )
    url = stdout.splitlines()[-1].strip()
    if not url:
        raise IssueFilingError(
            f"gh issue create for repo {repo} exited 0 but the last output line was empty"
        )
    return url


def create_issue(
    repo: str,
    title: str,
    body: str,
    labels: list[str],
    runner: CommandRunner = _default_runner,
) -> CreatedIssue:
    """File one issue on `repo` via `gh issue create`.

    Raises IssueFilingError, carrying stderr, on a non-zero exit that is not
    the missing-label case. A missing label is retried once with no labels
    at all and reported back as a warning rather than as a failure: losing
    the label is cosmetic, losing the issue (and the finding it records) is
    not, and this function must never do the latter to avoid the former.
    """

    def _run(label_list: list[str]) -> "subprocess.CompletedProcess[str]":
        args = ["gh", "issue", "create", "--repo", repo, "--title", title, "--body", body]
        for label in label_list:
            args += ["--label", label]
        return runner(args)

    result = _run(labels)
    if result.returncode == 0:
        return CreatedIssue(url=_extract_issue_url(result, repo))

    stderr = result.stderr or ""
    if labels and _looks_like_unknown_label_error(stderr):
        retry = _run([])
        if retry.returncode != 0:
            raise IssueFilingError(
                f"gh issue create failed for repo {repo} even after retrying without "
                f"label(s) {labels} (the label error was: {stderr.strip()!r}); retry "
                f"failed too: {retry.stderr.strip()!r}"
            )
        warning = (
            f"label(s) {labels} not found on repo {repo}; issue filed without them"
        )
        return CreatedIssue(url=_extract_issue_url(retry, repo), warnings=[warning])

    raise IssueFilingError(
        f"gh issue create failed for repo {repo} (exit {result.returncode}): {stderr.strip()}"
    )
