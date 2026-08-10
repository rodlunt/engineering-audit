"""Tests for the tool update check (src/engineering_audit/update_check.py)."""

from __future__ import annotations

import subprocess

import pytest

from engineering_audit.report import _render_meta_block
from engineering_audit.schema import RunMeta
from engineering_audit.update_check import (
    TOOL_REPO_URL,
    _resolve_update_status,
    check_for_update,
    check_pack_for_update,
)
import engineering_audit.update_check as update_check_module


# ---------------------------------------------------------------------------
# _resolve_update_status: parsing and decision logic
# ---------------------------------------------------------------------------


def test_lightweight_tag_is_picked_up_as_the_latest_release() -> None:
    output = "aaaa1111\tHEAD\naaaa1111\trefs/tags/v1.2.3\n"
    result = _resolve_update_status(output, "bbbb2222", "1.0.0")
    assert result.startswith("stale: latest release is v1.2.3 (aaaa1111)")


def test_annotated_tag_prefers_the_peeled_commit_sha() -> None:
    # The tag object's own sha (tagsha1111) must never win over the peeled
    # commit sha (commitsha22): a comparison against installed_commit (a
    # commit sha) must use commit shas throughout, or a real annotated-tag
    # release would never register as "current".
    output = (
        "aaaa0000\tHEAD\n"
        "tagsha1111\trefs/tags/v2.0.0\n"
        "commitsha22\trefs/tags/v2.0.0^{}\n"
    )
    result = _resolve_update_status(output, "commitsha22", "2.0.0")
    assert result == "current (v2.0.0)"


def test_annotated_tag_peeled_line_wins_regardless_of_order() -> None:
    output = (
        "aaaa0000\tHEAD\n"
        "commitsha22\trefs/tags/v2.0.0^{}\n"
        "tagsha1111\trefs/tags/v2.0.0\n"
    )
    result = _resolve_update_status(output, "commitsha22", "2.0.0")
    assert result == "current (v2.0.0)"


def test_highest_version_picked_by_numeric_not_lexicographic_order() -> None:
    # A string sort would rank "v0.9.0" above "v0.10.0"; this proves the
    # comparison is a tuple of ints.
    output = (
        "aaaa0000\tHEAD\n"
        "sha-0-9-0\trefs/tags/v0.9.0\n"
        "sha-0-10-0\trefs/tags/v0.10.0\n"
        "sha-0-2-0\trefs/tags/v0.2.0\n"
    )
    result = _resolve_update_status(output, "sha-0-10-0", "0.10.0")
    assert result == "current (v0.10.0)"


def test_installed_matches_latest_tag_is_current() -> None:
    output = "aaaa0000\tHEAD\naaaa0000\trefs/tags/v1.0.0\n"
    result = _resolve_update_status(output, "aaaa0000", "1.0.0")
    assert result == "current (v1.0.0)"


def test_installed_matches_head_but_not_tag_is_current_ahead() -> None:
    output = "headsha11\tHEAD\ntagsha2222\trefs/tags/v1.0.0\n"
    result = _resolve_update_status(output, "headsha11", "0.4.0-dev")
    assert result == "current (ahead of v1.0.0, matches main)"


def test_installed_matches_neither_is_stale_with_versions_and_short_shas() -> None:
    output = "headsha11\tHEAD\nlatestsha22\trefs/tags/v1.5.0\n"
    result = _resolve_update_status(output, "oldsha3333", "1.4.0")
    assert result == (
        "stale: latest release is v1.5.0 (latestsha22), installed build is 1.4.0 "
        "@ oldsha3333"
    )


def test_no_version_tags_is_could_not_check() -> None:
    output = "headsha11\tHEAD\n"
    result = _resolve_update_status(output, "headsha11", "1.0.0")
    assert result == "could-not-check: no version tags found on the remote"


def test_malformed_output_is_could_not_check() -> None:
    result = _resolve_update_status("not a valid ls-remote line at all", "headsha11", "1.0.0")
    assert result.startswith("could-not-check:")


def test_empty_output_is_could_not_check() -> None:
    result = _resolve_update_status("", "headsha11", "1.0.0")
    assert result.startswith("could-not-check:")


def test_prerelease_and_suffixed_tags_are_not_matched() -> None:
    # Only exactly three numeric parts count as a release tag; a suffix
    # like -rc1 or a fourth component must not be picked up as a release.
    output = (
        "headsha11\tHEAD\n"
        "shaprerel1\trefs/tags/v2.0.0-rc1\n"
        "shafourpt1\trefs/tags/v1.0.0.1\n"
    )
    result = _resolve_update_status(output, "headsha11", "1.0.0")
    assert result == "could-not-check: no version tags found on the remote"


def test_installed_commit_none_is_could_not_check_without_parsing() -> None:
    result = _resolve_update_status("anything at all", None, "1.0.0")
    assert result == (
        "could-not-check: installed build's commit is unknown (not a git install)"
    )


# ---------------------------------------------------------------------------
# check_for_update: network boundary
# ---------------------------------------------------------------------------


def test_check_for_update_with_no_installed_commit_never_invokes_git(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def _fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("git ls-remote must not run when installed_commit is None")

    monkeypatch.setattr(update_check_module, "_run_ls_remote", _fail_if_called)

    result = check_for_update(None, "1.0.0")

    assert called is False
    assert result.startswith("could-not-check:")
    assert not result.startswith("current")


def test_check_for_update_when_git_runner_fails_is_could_not_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(update_check_module, "_run_ls_remote", lambda repo_url: None)

    result = check_for_update("deadbeef1234", "1.0.0")

    assert result.startswith("could-not-check:")
    assert not result.startswith("current")


def test_check_for_update_when_git_exits_non_zero_is_could_not_check_with_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_run(repo_url: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["git", "ls-remote", repo_url],
            returncode=128,
            stdout="",
            stderr="fatal: could not resolve host: github.com\n",
        )

    monkeypatch.setattr(update_check_module, "_run_ls_remote", _fake_run)

    result = check_for_update("deadbeef1234", "1.0.0")

    assert result.startswith("could-not-check:")
    assert not result.startswith("current")
    assert "could not resolve host" in result


def test_check_for_update_uses_the_given_repo_url(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_urls: list[str] = []

    def _fake_run(repo_url: str) -> subprocess.CompletedProcess[str]:
        seen_urls.append(repo_url)
        return subprocess.CompletedProcess(
            args=["git", "ls-remote", repo_url],
            returncode=0,
            stdout="aaaa0000\tHEAD\naaaa0000\trefs/tags/v1.0.0\n",
            stderr="",
        )

    monkeypatch.setattr(update_check_module, "_run_ls_remote", _fake_run)

    result = check_for_update("aaaa0000", "1.0.0", repo_url="https://example.invalid/repo")

    assert seen_urls == ["https://example.invalid/repo"]
    assert result == "current (v1.0.0)"


def test_tool_repo_url_constant_points_at_the_real_repository() -> None:
    assert TOOL_REPO_URL == "https://github.com/rodlunt/engineering-audit"


# ---------------------------------------------------------------------------
# RunMeta field + report rendering
# ---------------------------------------------------------------------------


def _meta(**overrides) -> RunMeta:
    defaults = dict(
        tool_version="0.4.0",
        rules_pack_name="fixture-pack",
        assistant="claude-code",
        model="claude-sonnet-5",
        repo_name="widgets-app",
        repo_commit="abc1234",
        started="2026-08-09T09:00:00+00:00",
    )
    defaults.update(overrides)
    return RunMeta(**defaults)


def test_run_meta_update_check_defaults_to_none() -> None:
    assert _meta().update_check is None


def test_report_meta_block_shows_not_checked_when_none() -> None:
    from engineering_audit.schema import AuditConfig, RunState

    meta = _meta(update_check=None)
    run_state = RunState(
        meta=meta,
        config=AuditConfig(selected_domain_ids=["d01"], issue_mode="report"),
    )
    rendered = _render_meta_block(run_state)
    assert (
        '<div class="meta-label">Tool update</div><div class="meta-value">not checked</div>'
        in rendered
    )


def test_report_meta_block_shows_stale_status_when_set() -> None:
    from engineering_audit.schema import AuditConfig, RunState

    meta = _meta(update_check="stale: latest release is v0.5.0 (aaaa1111), installed build is 0.4.0 @ bbbb2222")
    run_state = RunState(
        meta=meta,
        config=AuditConfig(selected_domain_ids=["d01"], issue_mode="report"),
    )
    rendered = _render_meta_block(run_state)
    assert (
        '<div class="meta-label">Tool update</div>'
        '<div class="meta-value">stale: latest release is v0.5.0 (aaaa1111), installed build '
        "is 0.4.0 @ bbbb2222</div>" in rendered
    )


class TestCheckPackForUpdate:
    """The pack staleness check must obey the same rule as the tool's: a check
    that could not run is never evidence of freshness."""

    def test_unknown_pack_commit_cannot_read_as_current(self, tmp_path):
        result = check_pack_for_update(str(tmp_path), None, None)

        assert result.startswith("could-not-check")
        assert "current" != result

    def test_a_dirty_pack_is_could_not_check_not_stale(self, tmp_path):
        # A modified working tree may be ahead of the latest release, behind
        # it, or neither. It corresponds to no released version, so no
        # comparison is meaningful and "stale" would be a guess.
        result = check_pack_for_update(str(tmp_path), "abc123-dirty", "v0.6.0")

        assert result.startswith("could-not-check")
        assert "uncommitted changes" in result

    def test_a_pack_with_no_remote_is_could_not_check(self, tmp_path):
        # A vendored or third-party pack with no origin is a legitimate way to
        # run the tool, so this must not fail the run, and must not claim the
        # pack is current either.
        result = check_pack_for_update(str(tmp_path), "a" * 40, "v0.6.0")

        assert result.startswith("could-not-check")
