"""Tests for the MCP server (src/engineering_audit/server.py).

Tool calls are exercised through MCPServer's own in-process call_tool(), which
runs the tool exactly as the MCP protocol would (argument validation,
structured-content wrapping, error wrapping) without needing a real stdio
transport or a separate client process.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import time
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import urlencode

import pytest
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from mcp.server._otel import OpenTelemetryMiddleware

import engineering_audit.run_state_io as io_module
import engineering_audit.server as server_module
from engineering_audit.issues import CreatedIssue, IssueFilingError, LabelStatus
from engineering_audit.rules import RulesPackError
from engineering_audit.run_state_io import PROGRESS_FILENAME, load_run_progress_file
from engineering_audit.schema import RunMeta, RunState
from engineering_audit.server import (
    AppState,
    TelemetryStripError,
    _git_commit,
    _output_dir_ignore_warning,
    _parse_direct_url_commit,
    _resolve_rules_dir,
    _strip_ambient_otel_middleware,
    build_server,
)

FIXTURE_PACK = Path(__file__).parent / "fixture_pack"


def _call(mcp, name: str, arguments: dict):
    result = asyncio.run(mcp.call_tool(name, arguments))
    return result.structured_content


def _begin_run(mcp, output_dir: Path, **overrides) -> dict:
    defaults = dict(
        assistant="claude-code",
        model="claude-sonnet-5",
        repo_name="widgets-app",
        repo_commit="abc1234",
        started="2026-08-09T09:00:00Z",
        output_dir=str(output_dir),
    )
    defaults.update(overrides)
    return _call(mcp, "begin_run", defaults)


def _all_pass_verdicts(domain) -> list[dict]:
    return [{"rule_id": rule.id, "verdict": "pass"} for rule in domain.rules]


def _fetch_domain(mcp, domain_id: str) -> None:
    """Fetch a domain's rule text through the tool, as an agent about to audit
    it does.

    The record helpers below call this first, because that is the honest order
    and because the server records it (issue #110): a helper that recorded
    verdicts for rules it never asked for would put every test that uses it on
    the "verdicts without rules" path. The tests that mean to exercise that
    path skip this deliberately, and say so."""
    _call(mcp, "get_domain", {"domain_id": domain_id})


def _record_d01_with_finding(mcp, replace: bool = False) -> dict:
    _fetch_domain(mcp, "d01")
    verdicts = _all_pass_verdicts(_domain(mcp, "d01"))
    verdicts[1] = {"rule_id": "D01-R02", "verdict": "finding"}
    result = {
        "domain_id": "d01",
        "status": "completed",
        "rule_verdicts": verdicts,
        "findings": [
            {
                "rule_id": "D01-R02",
                "severity": "high",
                "title": "Two gnomes share bed-14 without the shared-bed flag",
                "location": "ledger/beds.py:42",
                "body_md": "bed-14 holds two gnomes.",
                "issue_title": "Set shared-bed flag for bed-14",
                "issue_body": "bed-14 has two occupants and no shared-bed flag.",
            }
        ],
    }
    return _call(mcp, "record_domain_result", {"result": result, "replace": replace})


def _record_d02_all_pass(mcp, replace: bool = False) -> dict:
    _fetch_domain(mcp, "d02")
    result = {
        "domain_id": "d02",
        "status": "completed",
        "rule_verdicts": _all_pass_verdicts(_domain(mcp, "d02")),
    }
    return _call(mcp, "record_domain_result", {"result": result, "replace": replace})


def _domain(mcp, domain_id: str):
    # Tests need the live Domain object (for its rule ids), not the tool's
    # dict projection, so this reaches into the state via a fresh load: cheap
    # for the tiny fixture pack and keeps the tests decoupled from server
    # internals.
    from engineering_audit.rules import load_pack

    return load_pack(FIXTURE_PACK).get_domain(domain_id)


def _completed_d01(mcp) -> dict:
    return {
        "domain_id": "d01",
        "status": "completed",
        "rule_verdicts": _all_pass_verdicts(_domain(mcp, "d01")),
        "findings": [],
    }


def _submit_config_page(
    url: str, domain_ids: list[str], issue_mode: str = "report"
) -> None:
    """Fill in and post the interactive configuration page, the way a browser
    would: fetch the page first to read its per-run CSRF token, then post."""
    with urllib.request.urlopen(url, timeout=5) as resp:
        page = resp.read().decode("utf-8")
    token_match = re.search(r'name="csrf_token" value="([^"]+)"', page)
    assert token_match is not None
    payload = urlencode(
        {
            "domain": domain_ids,
            "issue_mode": issue_mode,
            "csrf_token": token_match.group(1),
        },
        doseq=True,
    ).encode("utf-8")
    request = urllib.request.Request(url + "submit", data=payload, method="POST")
    with urllib.request.urlopen(request, timeout=5) as resp:
        assert resp.status == 200


def _preset_config_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **overrides
) -> Path:
    payload = {"selected_domain_ids": ["d01", "d02"], "issue_mode": "report"}
    payload.update(overrides)
    config_path = tmp_path / "preset-config.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("ENGINEERING_AUDIT_CONFIG", str(config_path))
    return config_path


def test_build_server_loads_the_fixture_pack() -> None:
    mcp, state = build_server(FIXTURE_PACK)
    assert isinstance(state, AppState)
    assert [d.id for d in state.pack.domains] == ["d01", "d02"]


def test_build_server_raises_on_a_bad_rules_dir(tmp_path: Path) -> None:
    with pytest.raises(RulesPackError):
        build_server(tmp_path / "does-not-exist")


def test_list_domains_tool_reports_domains_and_skip() -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    result = _call(mcp, "list_domains", {})

    domain_ids = [d["id"] for d in result["domains"]]
    assert domain_ids == ["d01", "d02"]

    d01 = next(d for d in result["domains"] if d["id"] == "d01")
    assert d01["number"] == 1
    assert d01["slug"] == "gnome-husbandry"
    assert d01["title"] == "Gnome Husbandry Record Keeping"
    assert d01["rule_count"] == 4
    assert d01["trigger"].startswith("you are about to register")

    skipped_names = [Path(s["path"]).name for s in result["skipped_files"]]
    assert "03-no-trigger-draft.md" in skipped_names


def test_get_domain_tool_returns_full_document_text() -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    result = asyncio.run(mcp.call_tool("get_domain", {"domain_id": "d01"}))
    text = result.content[0].text
    assert text.startswith("# Domain 01: Gnome Husbandry Record Keeping")
    assert "D01-R04" in text


def test_get_domain_tool_raises_a_clear_error_for_an_unknown_id() -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    with pytest.raises(ToolError) as excinfo:
        asyncio.run(mcp.call_tool("get_domain", {"domain_id": "d99"}))
    message = str(excinfo.value)
    assert "d99" in message
    assert "d01" in message
    assert "d02" in message


def test_build_server_strips_opentelemetry_middleware_but_tools_still_work() -> None:
    # The SDK installs OpenTelemetryMiddleware on every server by default;
    # this tool's consent model forbids ambient telemetry, so build_server()
    # must strip it while leaving the rest of the middleware chain (and tool
    # dispatch) intact.
    mcp, _state = build_server(FIXTURE_PACK)
    assert not any(isinstance(m, OpenTelemetryMiddleware) for m in mcp.middleware)
    assert not any(
        type(m).__name__ == "OpenTelemetryMiddleware" for m in mcp.middleware
    )

    result = _call(mcp, "list_domains", {})
    assert [d["id"] for d in result["domains"]] == ["d01", "d02"]


def test_strip_otel_middleware_raises_if_nothing_matched_to_strip() -> None:
    # Simulates the SDK no longer installing anything the private isinstance
    # check recognises: build_server must treat "nothing to remove" as a
    # loud failure, not as evidence the server is already clean (issue #107).
    mcp = MCPServer("otel-strip-nothing-to-find")
    mcp.middleware[:] = [
        m for m in mcp.middleware if not isinstance(m, OpenTelemetryMiddleware)
    ]
    assert not any(isinstance(m, OpenTelemetryMiddleware) for m in mcp.middleware)

    with pytest.raises(TelemetryStripError, match="no OpenTelemetryMiddleware"):
        _strip_ambient_otel_middleware(mcp)


def test_strip_otel_middleware_raises_if_a_lookalike_survives_the_isinstance_filter() -> (
    None
):
    # Simulates a future SDK renaming or relocating OpenTelemetryMiddleware
    # while mcp.server._otel still exists: the isinstance-based strip would
    # silently match nothing, so this exercises the name-based backstop
    # instead (issue #107). The real OpenTelemetryMiddleware the SDK installs
    # by default is left in place so the "nothing found to strip" check
    # passes cleanly, isolating this test to the survivor check.
    class OtelRenamedMiddleware:
        """Stands in for a telemetry middleware class the SDK renamed to
        something isinstance() no longer recognises against the pinned
        private import, but whose name still says what it is."""

    mcp = MCPServer("otel-strip-lookalike-survives")
    mcp.middleware.append(OtelRenamedMiddleware())

    with pytest.raises(TelemetryStripError, match="OtelRenamedMiddleware"):
        _strip_ambient_otel_middleware(mcp)

    # The strip itself still ran: the real middleware it does recognise is
    # gone, only the lookalike survived to trip the postcondition.
    assert not any(isinstance(m, OpenTelemetryMiddleware) for m in mcp.middleware)


def test_tool_surface_is_the_ten_tools_with_their_documented_parameters() -> None:
    # build_server composes seven per-concern registration functions; the
    # protocol surface they produce between them is what clients and AUDIT.md
    # depend on, so it is pinned here rather than left to be noticed later by
    # a client that stopped working.
    #
    # begin_run's 'resume' was added deliberately with crash-recovery: it is
    # the only way an agent can accept or decline continuing an interrupted
    # run, so it has to be on the wire. No tool was added or removed.
    mcp, _state = build_server(FIXTURE_PACK)
    tools = asyncio.run(mcp.list_tools())

    assert [tool.name for tool in tools] == [
        "list_domains",
        "get_domain",
        "begin_run",
        "start_config",
        "get_config",
        "record_domain_result",
        "run_status",
        "file_issues",
        "submit_feedback",
        "render_report",
    ]

    surface = {
        tool.name: (
            sorted(tool.input_schema.get("properties", {})),
            sorted(tool.input_schema.get("required", [])),
        )
        for tool in tools
    }
    assert surface == {
        "list_domains": ([], []),
        "get_domain": (["domain_id"], ["domain_id"]),
        "begin_run": (
            [
                "assistant",
                "environment",
                "model",
                "output_dir",
                "replace",
                "repo_commit",
                "repo_dir",
                "repo_name",
                "resume",
                "started",
                "tool_version",
            ],
            ["assistant", "model", "output_dir", "repo_commit", "repo_name", "started"],
        ),
        "start_config": ([], []),
        "get_config": (["timeout_s"], []),
        "record_domain_result": (["replace", "result"], ["result"]),
        "run_status": ([], []),
        "file_issues": (["confirm", "repo"], []),
        "submit_feedback": (
            ["extra_text", "report_conclusion", "report_fix_first"],
            [],
        ),
        "render_report": (["finished"], ["finished"]),
    }


def test_resolve_rules_dir_from_argv_flag(tmp_path: Path) -> None:
    resolved = _resolve_rules_dir(["--rules-dir", str(tmp_path)])
    assert resolved == tmp_path


def test_resolve_rules_dir_from_argv_equals_form(tmp_path: Path) -> None:
    resolved = _resolve_rules_dir([f"--rules-dir={tmp_path}"])
    assert resolved == tmp_path


def test_resolve_rules_dir_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENGINEERING_AUDIT_RULES_DIR", str(tmp_path))
    resolved = _resolve_rules_dir([])
    assert resolved == tmp_path


def test_resolve_rules_dir_raises_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENGINEERING_AUDIT_RULES_DIR", raising=False)
    with pytest.raises(SystemExit):
        _resolve_rules_dir([])


def test_resolve_rules_dir_raises_when_not_a_directory(tmp_path: Path) -> None:
    not_a_dir = tmp_path / "nope"
    with pytest.raises(SystemExit):
        _resolve_rules_dir(["--rules-dir", str(not_a_dir)])


def test_resolve_rules_dir_raises_on_trailing_flag_with_no_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A trailing '--rules-dir' with no value must error loudly (argparse's
    # SystemExit code 2), never fall through to the environment variable,
    # which could be a stale, wrong pack.
    monkeypatch.setenv("ENGINEERING_AUDIT_RULES_DIR", "/should/not/be/used")
    with pytest.raises(SystemExit) as excinfo:
        _resolve_rules_dir(["--rules-dir"])
    assert excinfo.value.code == 2


def test_resolve_rules_dir_tolerates_no_update_check_flag(tmp_path: Path) -> None:
    # _resolve_rules_dir and _update_check_disabled_by_flag share one parser
    # (_server_arg_parser) precisely so neither chokes on a flag only the
    # other defines; this is the regression test for that.
    resolved = _resolve_rules_dir(["--rules-dir", str(tmp_path), "--no-update-check"])
    assert resolved == tmp_path


# ---------------------------------------------------------------------------
# --no-update-check / ENGINEERING_AUDIT_NO_UPDATE_CHECK
# ---------------------------------------------------------------------------


def test_update_check_disabled_by_flag_true_when_passed() -> None:
    assert server_module._update_check_disabled_by_flag(["--no-update-check"]) is True


def test_update_check_disabled_by_flag_false_by_default() -> None:
    assert server_module._update_check_disabled_by_flag([]) is False


def test_update_check_disabled_by_flag_alongside_rules_dir(tmp_path: Path) -> None:
    disabled = server_module._update_check_disabled_by_flag(
        ["--rules-dir", str(tmp_path), "--no-update-check"]
    )
    assert disabled is True


def test_update_check_enabled_from_env_true_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENGINEERING_AUDIT_NO_UPDATE_CHECK", raising=False)
    assert server_module._update_check_enabled_from_env() is True


def test_update_check_enabled_from_env_false_when_env_var_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENGINEERING_AUDIT_NO_UPDATE_CHECK", "1")
    assert server_module._update_check_enabled_from_env() is False


def test_update_check_enabled_from_env_true_when_env_var_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An empty string counts as unset, not as an explicit "off": a
    # config-management tool that leaves the variable declared but blank
    # must not silently disable the check.
    monkeypatch.setenv("ENGINEERING_AUDIT_NO_UPDATE_CHECK", "")
    assert server_module._update_check_enabled_from_env() is True


def test_main_resolves_disabled_update_check_when_flag_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # main() is the one place that reconciles --no-update-check with
    # ENGINEERING_AUDIT_NO_UPDATE_CHECK; this proves the flag reaches
    # build_server as an explicit update_check_enabled=False, without
    # needing to run the real (blocking) mcp.run() and without main()
    # ever writing to the environment (see the next test).
    monkeypatch.delenv("ENGINEERING_AUDIT_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(
        server_module.sys,
        "argv",
        ["engineering-audit-mcp", "--rules-dir", str(tmp_path), "--no-update-check"],
    )
    seen = {}

    class _FakeMCP:
        def run(self) -> None:
            pass

    def _fake_build_server(rules_dir: Path, *, update_check_enabled=None):
        seen["update_check_enabled"] = update_check_enabled
        return _FakeMCP(), None

    monkeypatch.setattr(server_module, "build_server", _fake_build_server)

    server_module.main()

    assert seen["update_check_enabled"] is False


def test_main_leaves_update_check_resolution_to_build_server_when_flag_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No --no-update-check on the command line: main() passes None, which
    # tells build_server to resolve the setting itself from the environment
    # variable (see build_server's docstring), rather than main() having to
    # read that variable a second time.
    monkeypatch.setattr(
        server_module.sys,
        "argv",
        ["engineering-audit-mcp", "--rules-dir", str(tmp_path)],
    )
    seen = {}

    class _FakeMCP:
        def run(self) -> None:
            pass

    def _fake_build_server(rules_dir: Path, *, update_check_enabled=None):
        seen["update_check_enabled"] = update_check_enabled
        return _FakeMCP(), None

    monkeypatch.setattr(server_module, "build_server", _fake_build_server)

    server_module.main()

    assert seen["update_check_enabled"] is None


def test_main_never_mutates_the_no_update_check_environment_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Issue #132: the old implementation set ENGINEERING_AUDIT_NO_UPDATE_CHECK
    # in main()'s own process environment, which every git/gh subprocess this
    # tool spawns would then inherit. The resolved setting is now carried as
    # an explicit value instead, so main() must never write this variable.
    monkeypatch.delenv("ENGINEERING_AUDIT_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(
        server_module.sys,
        "argv",
        ["engineering-audit-mcp", "--rules-dir", str(tmp_path), "--no-update-check"],
    )

    class _FakeMCP:
        def run(self) -> None:
            pass

    monkeypatch.setattr(
        server_module,
        "build_server",
        lambda rules_dir, *, update_check_enabled=None: (_FakeMCP(), None),
    )

    server_module.main()

    assert "ENGINEERING_AUDIT_NO_UPDATE_CHECK" not in server_module.os.environ


# ---------------------------------------------------------------------------
# begin_run
# ---------------------------------------------------------------------------


def test_begin_run_creates_the_output_directory_and_returns_meta(
    tmp_path: Path,
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    out_dir = tmp_path / "audit-output"
    result = _begin_run(mcp, out_dir)

    assert result["meta"]["repo_name"] == "widgets-app"
    assert result["meta"]["rules_pack_name"] == FIXTURE_PACK.name
    assert result["meta"]["finished"] is None
    assert out_dir.is_dir()


def test_begin_run_update_checks_run_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Real check_for_update/check_pack_for_update are replaced here purely to
    # avoid a real network call in the test suite; what is asserted is the
    # enabled= value begin_run passes them, which must be True unless
    # something has explicitly turned the check off.
    monkeypatch.delenv("ENGINEERING_AUDIT_NO_UPDATE_CHECK", raising=False)
    seen: dict[str, bool] = {}

    def _fake_check_for_update(tool_commit, tool_version, enabled=True):
        seen["tool"] = enabled
        return "current (v1.0.0)"

    def _fake_check_pack_for_update(pack_dir, pack_commit, pack_version, enabled=True):
        seen["pack"] = enabled
        return "current (v1.0.0)"

    monkeypatch.setattr(server_module, "check_for_update", _fake_check_for_update)
    monkeypatch.setattr(
        server_module, "check_pack_for_update", _fake_check_pack_for_update
    )

    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")

    assert seen == {"tool": True, "pack": True}


def test_begin_run_update_checks_disabled_via_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No mocking of check_for_update/check_pack_for_update here: enabled=False
    # short-circuits before either function touches git or the network, so
    # this exercises the real functions and asserts the real status strings.
    monkeypatch.setenv("ENGINEERING_AUDIT_NO_UPDATE_CHECK", "1")

    mcp, _state = build_server(FIXTURE_PACK)
    result = _begin_run(mcp, tmp_path / "audit-output")

    assert (
        result["meta"]["update_check"]
        == "not-checked: update check disabled by configuration"
    )
    assert (
        result["meta"]["pack_update_check"]
        == "not-checked: rules pack update check disabled by configuration"
    )


def test_begin_run_update_checks_disabled_via_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The third of the three states (see issue #132): the CLI flag, carried
    # as build_server's update_check_enabled=False rather than the
    # environment variable. The environment variable is deliberately left
    # unset (and would otherwise leave the check enabled), so this proves
    # the flag disables the check on its own, not via the env var.
    monkeypatch.delenv("ENGINEERING_AUDIT_NO_UPDATE_CHECK", raising=False)

    mcp, state = build_server(FIXTURE_PACK, update_check_enabled=False)
    assert state.update_check_enabled is False
    result = _begin_run(mcp, tmp_path / "audit-output")

    assert (
        result["meta"]["update_check"]
        == "not-checked: update check disabled by configuration"
    )
    assert (
        result["meta"]["pack_update_check"]
        == "not-checked: rules pack update check disabled by configuration"
    )


@pytest.mark.parametrize(
    "build_kwargs, env_var",
    [
        pytest.param({}, "1", id="disabled-by-env-var"),
        pytest.param({"update_check_enabled": False}, None, id="disabled-by-flag"),
    ],
)
def test_begin_run_update_check_disabled_status_is_distinct_from_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    build_kwargs: dict[str, bool],
    env_var: str | None,
) -> None:
    # Load bearing: a status that could read as "current" when the check
    # never ran is exactly the bug this module exists to prevent, whichever
    # of the two inputs (env var or flag) did the disabling.
    if env_var is None:
        monkeypatch.delenv("ENGINEERING_AUDIT_NO_UPDATE_CHECK", raising=False)
    else:
        monkeypatch.setenv("ENGINEERING_AUDIT_NO_UPDATE_CHECK", env_var)

    mcp, _state = build_server(FIXTURE_PACK, **build_kwargs)
    result = _begin_run(mcp, tmp_path / "audit-output")

    for field in ("update_check", "pack_update_check"):
        value = result["meta"][field]
        assert not value.startswith("current")
        assert not value.startswith("could-not-check")
        assert value.startswith("not-checked")


def test_begin_run_twice_without_finishing_is_rejected(tmp_path: Path) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    with pytest.raises(ToolError) as excinfo:
        _begin_run(mcp, tmp_path / "audit-output-2")
    assert "already in progress" in str(excinfo.value)


def test_begin_run_replace_discards_the_in_progress_run(tmp_path: Path) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output", repo_name="first-repo")
    result = _begin_run(
        mcp, tmp_path / "audit-output-2", repo_name="second-repo", replace=True
    )
    assert result["meta"]["repo_name"] == "second-repo"


def test_begin_run_defaults_tool_version_when_omitted(tmp_path: Path) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    result = _begin_run(mcp, tmp_path / "audit-output")
    assert result["meta"][
        "tool_version"
    ]  # non-empty, package version or dev placeholder


def test_begin_run_stamps_server_started_independently_of_the_assistant_supplied_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Issue #102: 'started' is the assistant's own claim, taken on trust like
    # everything else it asserts. The server must stamp its own clock too,
    # rather than only ever recording what it was told, so the report has
    # something to check the claim against.
    monkeypatch.setattr(server_module, "_now_utc_iso", lambda: "2026-08-11T00:00:00Z")
    mcp, _state = build_server(FIXTURE_PACK)
    result = _begin_run(mcp, tmp_path / "audit-output", started="2020-01-01T00:00:00Z")

    assert result["meta"]["started"] == "2020-01-01T00:00:00Z"
    assert result["meta"]["server_started"] == "2026-08-11T00:00:00Z"


def _init_git_repo(path: Path) -> None:
    """Initialise path as a git repo with one commit covering whatever is
    already on disk (--allow-empty so this also works on an empty
    directory), using -c flags for user.email/user.name so this works on a
    bare CI runner with no global git identity configured."""
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test",
            "commit",
            "--allow-empty",
            "-m",
            "init",
        ],
        cwd=path,
        check=True,
        capture_output=True,
    )


def _head_sha(path: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_git_commit_returns_the_head_sha_for_a_real_git_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    assert _git_commit(repo, subtree_only=False) == _head_sha(repo)


def test_git_commit_appends_dirty_suffix_when_working_tree_has_uncommitted_changes(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "untracked.txt").write_text("uncommitted", encoding="utf-8")

    result = _git_commit(repo, subtree_only=False)
    assert result == f"{_head_sha(repo)}-dirty"


def test_git_commit_returns_none_for_a_non_repo_directory(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "plain-dir"
    not_a_repo.mkdir()
    assert _git_commit(not_a_repo, subtree_only=False) is None


# ---------------------------------------------------------------------------
# _git_commit subtree_only scoping (issue #168): dirty must mean "changes
# within the scoped subtree", not "changes anywhere in the containing repo".
# ---------------------------------------------------------------------------


def test_git_commit_subtree_only_ignores_an_untracked_file_outside_the_scoped_dir(
    tmp_path: Path,
) -> None:
    # The real tester bug: a stray file anywhere else in the containing
    # clone (a .DS_Store at the clone root, a saved audit-output/
    # directory) must not dirty a commit scoped to a subdirectory the tool
    # actually reads, like the rules pack.
    repo = tmp_path / "repo"
    rules_dir = repo / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "d01.md").write_text("# domain\n", encoding="utf-8")
    _init_git_repo(repo)
    (repo / ".DS_Store").write_text("junk", encoding="utf-8")

    # Proves the untracked file really does dirty the old, repo-wide scope
    # (subtree_only=False): the fix must change the answer only because
    # subtree_only=True was asked for, not because the file stopped
    # mattering some other way.
    assert _git_commit(rules_dir, subtree_only=False) == f"{_head_sha(repo)}-dirty"

    assert _git_commit(rules_dir, subtree_only=True) == _head_sha(repo)


def test_git_commit_subtree_only_reports_dirty_for_an_untracked_file_inside_the_scoped_dir(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    rules_dir = repo / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "d01.md").write_text("# domain\n", encoding="utf-8")
    _init_git_repo(repo)
    # A new domain file load_pack would actually pick up: genuinely changes
    # the pack, so this must count even though it is untracked.
    (rules_dir / "d99-new-domain.md").write_text("# new domain\n", encoding="utf-8")

    assert _git_commit(rules_dir, subtree_only=True) == f"{_head_sha(repo)}-dirty"


def test_git_commit_subtree_only_reports_dirty_for_a_modified_tracked_file_inside_the_scoped_dir(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    rules_dir = repo / "rules"
    rules_dir.mkdir(parents=True)
    domain_file = rules_dir / "d01.md"
    domain_file.write_text("# domain\n", encoding="utf-8")
    _init_git_repo(repo)
    domain_file.write_text("# domain, modified\n", encoding="utf-8")

    assert _git_commit(rules_dir, subtree_only=True) == f"{_head_sha(repo)}-dirty"


# ---------------------------------------------------------------------------
# _output_dir_ignore_warning (issue #109)
# ---------------------------------------------------------------------------


def test_output_dir_ignore_warning_fires_when_output_dir_is_not_gitignored(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    output_dir = repo / "audit-output"
    output_dir.mkdir()

    warning = _output_dir_ignore_warning(repo, output_dir)
    assert warning is not None
    assert str(output_dir) in warning
    assert "gitignore" in warning.lower()


def test_output_dir_ignore_warning_is_silent_when_output_dir_is_gitignored(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text("audit-output/\n", encoding="utf-8")
    _init_git_repo(repo)
    output_dir = repo / "audit-output"
    output_dir.mkdir()

    assert _output_dir_ignore_warning(repo, output_dir) is None


def test_output_dir_ignore_warning_is_silent_with_no_repo_dir(tmp_path: Path) -> None:
    assert _output_dir_ignore_warning(None, tmp_path / "audit-output") is None


def test_output_dir_ignore_warning_is_silent_for_a_non_repo_directory(
    tmp_path: Path,
) -> None:
    not_a_repo = tmp_path / "plain-dir"
    not_a_repo.mkdir()
    assert _output_dir_ignore_warning(not_a_repo, not_a_repo / "audit-output") is None


def test_parse_direct_url_commit_returns_commit_id_for_a_git_style_payload() -> None:
    payload = json.dumps(
        {
            "url": "https://github.com/rodlunt/engineering-audit",
            "vcs_info": {
                "vcs": "git",
                "requested_revision": "main",
                "commit_id": "8b158dda99f3c5e7714840296db550f7a4978c5",
            },
        }
    )
    assert (
        _parse_direct_url_commit(payload) == "8b158dda99f3c5e7714840296db550f7a4978c5"
    )


def test_parse_direct_url_commit_returns_none_for_a_local_dir_payload() -> None:
    # A plain local/editable install's direct_url.json has no vcs_info at
    # all: this is the ordinary "installed from a source checkout" case,
    # not a failure, and must still render as "unknown" rather than error.
    payload = json.dumps(
        {"url": "file:///home/dev/engineering-audit", "dir_info": {"editable": True}}
    )
    assert _parse_direct_url_commit(payload) is None


def test_parse_direct_url_commit_returns_none_for_invalid_json() -> None:
    assert _parse_direct_url_commit("not json at all {") is None


# ---------------------------------------------------------------------------
# _parse_direct_url_source_dir / _default_tool_commit git fallback (issue #169)
# ---------------------------------------------------------------------------


def test_parse_direct_url_source_dir_extracts_the_path_from_a_directory_install(
    tmp_path: Path,
) -> None:
    payload = json.dumps({"url": tmp_path.as_uri(), "dir_info": {"editable": True}})
    assert server_module._parse_direct_url_source_dir(payload) == tmp_path


def test_parse_direct_url_source_dir_returns_none_for_a_git_style_payload() -> None:
    payload = json.dumps(
        {
            "url": "https://github.com/rodlunt/engineering-audit",
            "vcs_info": {
                "vcs": "git",
                "requested_revision": "main",
                "commit_id": "8b158dda99f3c5e7714840296db550f7a4978c5",
            },
        }
    )
    assert server_module._parse_direct_url_source_dir(payload) is None


def test_parse_direct_url_source_dir_returns_none_when_neither_block_is_present() -> (
    None
):
    # The wheel/PyPI shape: no vcs_info, no dir_info, nothing to fall back to.
    payload = json.dumps(
        {
            "url": "https://files.pythonhosted.org/packages/.../engineering_audit-0.8.0-py3-none-any.whl",
            "archive_info": {"hash": "sha256=deadbeef"},
        }
    )
    assert server_module._parse_direct_url_source_dir(payload) is None


def test_parse_direct_url_source_dir_returns_none_for_invalid_json() -> None:
    assert server_module._parse_direct_url_source_dir("not json at all {") is None


class _FakeDistribution:
    """Stand-in for importlib.metadata.Distribution: _default_tool_commit
    only ever calls .read_text on it."""

    def __init__(self, direct_url_json: str | None) -> None:
        self._direct_url_json = direct_url_json

    def read_text(self, filename: str) -> str | None:
        assert filename == "direct_url.json"
        return self._direct_url_json


def test_default_tool_commit_falls_back_to_git_for_an_editable_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "checkout"
    source.mkdir()
    _init_git_repo(source)
    direct_url_json = json.dumps(
        {"url": source.as_uri(), "dir_info": {"editable": True}}
    )
    monkeypatch.setattr(
        server_module,
        "_pkg_distribution",
        lambda name: _FakeDistribution(direct_url_json),
    )

    assert server_module._default_tool_commit() == _head_sha(source)


def test_default_tool_commit_editable_install_with_a_modified_file_is_dirty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "checkout"
    source.mkdir()
    _init_git_repo(source)
    (source / "scratch.py").write_text("x = 1\n", encoding="utf-8")
    direct_url_json = json.dumps(
        {"url": source.as_uri(), "dir_info": {"editable": True}}
    )
    monkeypatch.setattr(
        server_module,
        "_pkg_distribution",
        lambda name: _FakeDistribution(direct_url_json),
    )

    assert server_module._default_tool_commit() == f"{_head_sha(source)}-dirty"


def test_default_tool_commit_wheel_style_install_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No vcs_info (not a git install) and no dir_info (not a directory
    # install), the ordinary shape for a PyPI wheel install: there is no
    # source tree to fall back to, so this must still report unknown.
    direct_url_json = json.dumps(
        {
            "url": "https://files.pythonhosted.org/packages/.../engineering_audit-0.8.0-py3-none-any.whl",
            "archive_info": {"hash": "sha256=deadbeef"},
        }
    )
    monkeypatch.setattr(
        server_module,
        "_pkg_distribution",
        lambda name: _FakeDistribution(direct_url_json),
    )

    assert server_module._default_tool_commit() is None


# ---------------------------------------------------------------------------
# start_config / get_config: preset (headless) path
# ---------------------------------------------------------------------------


def test_start_config_preset_path_loads_a_valid_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    _preset_config_env(monkeypatch, tmp_path)

    result = _call(mcp, "start_config", {})
    assert result["mode"] == "preset"
    assert result["selected_domain_ids"] == ["d01", "d02"]
    assert result["config"]["issue_mode"] == "report"


def test_start_config_preset_path_rejects_a_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    monkeypatch.setenv(
        "ENGINEERING_AUDIT_CONFIG", str(tmp_path / "does-not-exist.json")
    )

    with pytest.raises(ToolError) as excinfo:
        _call(mcp, "start_config", {})
    assert "does-not-exist.json" in str(excinfo.value)


def test_start_config_preset_path_rejects_invalid_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    bad_file = tmp_path / "bad-config.json"
    bad_file.write_text("{ not valid json", encoding="utf-8")
    monkeypatch.setenv("ENGINEERING_AUDIT_CONFIG", str(bad_file))

    with pytest.raises(ToolError) as excinfo:
        _call(mcp, "start_config", {})
    assert "not a valid AuditConfig" in str(excinfo.value)


def test_start_config_preset_path_rejects_schema_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    # selected_domain_ids must not be empty; this is a schema-valid JSON
    # document that AuditConfig itself rejects.
    _preset_config_env(monkeypatch, tmp_path, selected_domain_ids=[])

    with pytest.raises(ToolError) as excinfo:
        _call(mcp, "start_config", {})
    assert "not a valid AuditConfig" in str(excinfo.value)


def test_start_config_preset_path_rejects_unknown_domain_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    _preset_config_env(monkeypatch, tmp_path, selected_domain_ids=["d01", "d99"])

    with pytest.raises(ToolError) as excinfo:
        _call(mcp, "start_config", {})
    assert "d99" in str(excinfo.value)


# ---------------------------------------------------------------------------
# start_config: deliverables_dir validation (issue #109)
#
# The preset path is the second caller output_location.py's rules have to
# hold, alongside the interactive page's own POST handler (see
# tests/test_config_page.py), because a preset AuditConfig never passes
# through that page's _parse_submission at all.
# ---------------------------------------------------------------------------


def test_start_config_preset_path_honours_a_custom_deliverables_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    target = tmp_path / "reports"
    target.mkdir()
    _preset_config_env(monkeypatch, tmp_path, deliverables_dir=str(target))

    result = _call(mcp, "start_config", {})
    assert result["config"]["deliverables_dir"] == str(target.resolve())


def test_start_config_preset_path_rejects_a_deliverables_dir_with_a_missing_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    missing = tmp_path / "does-not-exist" / "reports"
    _preset_config_env(monkeypatch, tmp_path, deliverables_dir=str(missing))

    with pytest.raises(ToolError) as excinfo:
        _call(mcp, "start_config", {})
    assert "does not exist" in str(excinfo.value)


def test_start_config_preset_path_rejects_a_deliverables_dir_naming_an_unknown_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Issue #152: Path.expanduser() raises a bare RuntimeError for
    # ~nosuchuser/..., which start_config used to let escape uncaught, so
    # the assistant saw an internal error instead of something it could act
    # on. This pins the fix to the same clean ValueError every other
    # deliverables_dir rejection here already raises, not merely "no
    # RuntimeError": ToolError is what an unhandled exception also becomes,
    # so the assertion on the message is what actually distinguishes the
    # intended clean error from the crash the issue reports.
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    _preset_config_env(
        monkeypatch, tmp_path, deliverables_dir="~nosuchuser/audit-reports"
    )

    with pytest.raises(ToolError) as excinfo:
        _call(mcp, "start_config", {})
    message = str(excinfo.value)
    assert "~nosuchuser/audit-reports" in message
    assert "user" in message.lower()
    assert "cannot be used" in message


def test_start_config_preset_path_never_silently_overwrites_an_existing_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    target = tmp_path / "reports"
    target.mkdir()
    (target / "report.html").write_text("an earlier run's report", encoding="utf-8")
    _preset_config_env(monkeypatch, tmp_path, deliverables_dir=str(target))

    with pytest.raises(ToolError) as excinfo:
        _call(mcp, "start_config", {})
    assert "already contains" in str(excinfo.value)


def test_get_config_preset_path_returns_the_stored_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    _preset_config_env(monkeypatch, tmp_path)
    _call(mcp, "start_config", {})

    result = _call(mcp, "get_config", {"timeout_s": 1})
    assert result["mode"] == "preset"
    assert result["selected_domain_ids"] == ["d01", "d02"]


def test_get_config_requires_start_config_first(tmp_path: Path) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    with pytest.raises(ToolError) as excinfo:
        _call(mcp, "get_config", {"timeout_s": 1})
    assert "start_config" in str(excinfo.value)


# ---------------------------------------------------------------------------
# start_config / get_config: interactive path
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_real_browser(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Keep the suite from opening real browser tabs: start_config's
    interactive path calls webbrowser.open, which on a developer machine
    would pop a tab per test run. Records the URLs it was asked to open."""
    opened: list[str] = []

    def _record(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr(server_module.webbrowser, "open", _record)
    return opened


def test_start_config_interactive_path_returns_a_url(tmp_path: Path) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")

    result = _call(mcp, "start_config", {})
    assert result["mode"] == "interactive"
    assert result["url"].startswith("http://127.0.0.1:")


def test_start_config_interactive_path_warns_when_output_dir_is_not_gitignored(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    output_dir = repo / "audit-output"

    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, output_dir, repo_dir=str(repo))
    result = _call(mcp, "start_config", {})

    with urllib.request.urlopen(result["url"], timeout=5) as resp:
        page = resp.read().decode("utf-8")
    assert str(output_dir) in page
    assert "gitignore" in page.lower()


def test_start_config_interactive_path_is_silent_when_output_dir_is_gitignored(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text("audit-output/\n", encoding="utf-8")
    _init_git_repo(repo)
    output_dir = repo / "audit-output"

    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, output_dir, repo_dir=str(repo))
    result = _call(mcp, "start_config", {})

    with urllib.request.urlopen(result["url"], timeout=5) as resp:
        page = resp.read().decode("utf-8")
    assert 'class="gitignore-warning"' not in page


def test_start_config_interactive_path_opens_the_browser_and_says_so(
    tmp_path: Path, _no_real_browser: list[str]
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")

    result = _call(mcp, "start_config", {})
    assert result["opened_in_browser"] is True
    assert _no_real_browser == [result["url"]]


def test_start_config_interactive_path_survives_a_browserless_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A display-less box (SSH session, CI) may raise webbrowser.Error; the
    # page must still start and the URL must still come back, with the
    # response honestly reporting that no tab opened.
    def _raise(url: str) -> bool:
        raise webbrowser.Error("no runnable browser")

    monkeypatch.setattr(server_module.webbrowser, "open", _raise)
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")

    result = _call(mcp, "start_config", {})
    assert result["mode"] == "interactive"
    assert result["url"].startswith("http://127.0.0.1:")
    assert result["opened_in_browser"] is False


def test_get_config_interactive_path_blocks_then_returns_after_form_post(
    tmp_path: Path,
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    started = _call(mcp, "start_config", {})
    url = started["url"]

    # The config page now embeds a per-run CSRF token (issue #39) that a
    # POST must echo back, so fetch the page first and read it out of the
    # hidden field, the same way a real browser submission would.
    with urllib.request.urlopen(url, timeout=5) as resp:
        page = resp.read().decode("utf-8")
    token_match = re.search(r'name="csrf_token" value="([^"]+)"', page)
    assert token_match is not None
    token = token_match.group(1)

    payload = urlencode(
        {"domain": ["d01", "d02"], "issue_mode": "report", "csrf_token": token},
        doseq=True,
    ).encode("utf-8")
    request = urllib.request.Request(url + "submit", data=payload, method="POST")
    with urllib.request.urlopen(request, timeout=5) as resp:
        assert resp.status == 200

    result = _call(mcp, "get_config", {"timeout_s": 5})
    assert result["mode"] == "interactive"
    assert sorted(result["selected_domain_ids"]) == ["d01", "d02"]


def test_get_config_interactive_timeout_surfaces_as_a_clear_tool_error(
    tmp_path: Path,
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    started = _call(mcp, "start_config", {})

    with pytest.raises(ToolError) as excinfo:
        _call(mcp, "get_config", {"timeout_s": 0.2})
    message = str(excinfo.value)
    assert "No configuration submitted" in message
    assert started["url"] in message


# ---------------------------------------------------------------------------
# get_config's three states (issue #85)
# ---------------------------------------------------------------------------


def test_get_config_returns_waiting_rather_than_holding_the_call_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The defect in issue #85: a get_config that blocks for the whole of
    # timeout_s is cancelled by any host whose own per-tool timeout is
    # shorter, and that cancellation took the MCP process and the config page
    # down with it. The call must come back inside the poll interval no matter
    # how long the run's overall deadline is.
    monkeypatch.setattr(server_module, "_CONFIG_POLL_INTERVAL_S", 0.2)
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    started = _call(mcp, "start_config", {})

    before = time.monotonic()
    result = _call(mcp, "get_config", {"timeout_s": 3600})
    elapsed = time.monotonic() - before

    assert result["status"] == "waiting"
    assert elapsed < 30, "get_config held the call open past its poll interval"
    assert result["url"] == started["url"]
    assert "config" not in result
    assert "selected_domain_ids" not in result
    assert result["timeout_s"] == 3600
    assert result["waited_s"] >= 0
    assert "call get_config again" in result["instruction"]


def test_get_config_waiting_is_never_mistakable_for_a_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A caller that reads "status" gets an unambiguous answer, and a caller
    # that reads nothing at all still cannot proceed: nothing downstream will
    # accept a domain result while the run has no config.
    monkeypatch.setattr(server_module, "_CONFIG_POLL_INTERVAL_S", 0.2)
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    _call(mcp, "start_config", {})

    assert _call(mcp, "get_config", {"timeout_s": 3600})["status"] == "waiting"
    with pytest.raises(ToolError) as excinfo:
        _call(mcp, "record_domain_result", {"result": _completed_d01(mcp)})
    assert "configuration" in str(excinfo.value)


def test_get_config_polled_repeatedly_reaches_configured_after_the_form_is_posted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server_module, "_CONFIG_POLL_INTERVAL_S", 0.2)
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    started = _call(mcp, "start_config", {})
    url = started["url"]

    assert _call(mcp, "get_config", {"timeout_s": 60})["status"] == "waiting"
    assert _call(mcp, "get_config", {"timeout_s": 60})["status"] == "waiting"

    _submit_config_page(url, ["d01", "d02"])

    result = _call(mcp, "get_config", {"timeout_s": 60})
    assert result["status"] == "configured"
    assert sorted(result["selected_domain_ids"]) == ["d01", "d02"]


def test_get_config_deadline_is_cumulative_across_polls_not_per_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The deadline is the run's, not the call's. Polling more often must not
    # buy the user more waiting time, or timeout_s would mean nothing.
    monkeypatch.setattr(server_module, "_CONFIG_POLL_INTERVAL_S", 0.1)
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    _call(mcp, "start_config", {})

    deadline = time.monotonic() + 10
    statuses = []
    while time.monotonic() < deadline:
        try:
            statuses.append(_call(mcp, "get_config", {"timeout_s": 0.5})["status"])
        except ToolError as exc:
            assert "No configuration submitted within 0.5 seconds" in str(exc)
            break
    else:  # pragma: no cover - only reached if the deadline is never enforced
        pytest.fail("get_config never reported a timeout despite a 0.5 second deadline")

    assert statuses, (
        "get_config timed out without ever reporting the waiting state first"
    )
    assert set(statuses) == {"waiting"}


def test_get_config_after_a_timeout_keeps_waiting_when_given_a_larger_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Timing out does not tear the page down: the user may still be halfway
    # through ticking domains, and their submission must still be picked up.
    monkeypatch.setattr(server_module, "_CONFIG_POLL_INTERVAL_S", 0.2)
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    started = _call(mcp, "start_config", {})

    with pytest.raises(ToolError):
        _call(mcp, "get_config", {"timeout_s": 0.01})

    _submit_config_page(started["url"], ["d01"])
    result = _call(mcp, "get_config", {"timeout_s": 600})
    assert result["status"] == "configured"
    assert result["selected_domain_ids"] == ["d01"]


def test_get_config_reports_a_submission_that_landed_after_the_deadline_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An expired budget must not throw away a configuration that is already
    # sitting there: the user did the work, and reporting a timeout over the
    # top of a real submission would send them round the loop for nothing.
    monkeypatch.setattr(server_module, "_CONFIG_POLL_INTERVAL_S", 0.2)
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    started = _call(mcp, "start_config", {})
    _submit_config_page(started["url"], ["d02"])

    result = _call(mcp, "get_config", {"timeout_s": 0.0})
    assert result["status"] == "configured"
    assert result["selected_domain_ids"] == ["d02"]


def test_get_config_preset_path_reports_the_configured_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    _preset_config_env(monkeypatch, tmp_path)
    _call(mcp, "start_config", {})

    assert _call(mcp, "get_config", {"timeout_s": 1})["status"] == "configured"


# ---------------------------------------------------------------------------
# environment metadata (issue #89)
# ---------------------------------------------------------------------------


def test_begin_run_accepts_the_three_documented_environment_keys(
    tmp_path: Path,
) -> None:
    environment = {
        "os": "Ubuntu 24.04",
        "host_cli": "codex",
        "host_cli_version": "0.147.0",
    }
    mcp, _state = build_server(FIXTURE_PACK)
    result = _begin_run(mcp, tmp_path / "audit-output", environment=environment)
    assert result["meta"]["environment"] == environment


def test_begin_run_accepts_a_subset_of_the_environment_keys(tmp_path: Path) -> None:
    # Omitting a key the assistant could not determine is the honest answer,
    # and must not be harder than inventing one.
    mcp, _state = build_server(FIXTURE_PACK)
    result = _begin_run(
        mcp, tmp_path / "audit-output", environment={"os": "macOS 15.2"}
    )
    assert result["meta"]["environment"] == {"os": "macOS 15.2"}


def test_begin_run_rejects_an_environment_key_outside_the_documented_set(
    tmp_path: Path,
) -> None:
    # The security-relevant half of issue #89: this metadata ships inside
    # feedback issues filed publicly, and the assistant supplying it is
    # untrusted input. Prose in AUDIT.md is not enforcement.
    mcp, _state = build_server(FIXTURE_PACK)
    with pytest.raises(ToolError) as excinfo:
        _begin_run(
            mcp,
            tmp_path / "audit-output",
            environment={"os": "linux", "repo_secret": "internal-project-codename"},
        )
    message = str(excinfo.value)
    assert "repo_secret" in message
    assert "host_cli_version" in message


def test_begin_run_rejecting_an_environment_starts_nothing(tmp_path: Path) -> None:
    output_dir = tmp_path / "audit-output"
    mcp, state = build_server(FIXTURE_PACK)
    with pytest.raises(ToolError):
        _begin_run(mcp, output_dir, environment={"python": "3.12"})
    assert state.run is None
    assert not output_dir.exists()


def test_begin_run_rejects_an_over_long_environment_value(tmp_path: Path) -> None:
    # A closed key set with a paragraph stuffed into one of the values
    # discloses exactly as much as the open dict it replaced.
    mcp, _state = build_server(FIXTURE_PACK)
    with pytest.raises(ToolError) as excinfo:
        _begin_run(mcp, tmp_path / "audit-output", environment={"os": "x" * 500})
    assert "character limit" in str(excinfo.value)


def test_begin_run_rejects_a_blank_environment_value(tmp_path: Path) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    with pytest.raises(ToolError) as excinfo:
        _begin_run(mcp, tmp_path / "audit-output", environment={"host_cli": "   "})
    assert "Omit the key entirely" in str(excinfo.value)


def test_begin_run_without_an_environment_still_works(tmp_path: Path) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    result = _begin_run(mcp, tmp_path / "audit-output")
    assert result["meta"]["environment"] is None


# ---------------------------------------------------------------------------
# record_domain_result
# ---------------------------------------------------------------------------


def _configured_run(
    mcp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **config_overrides
) -> None:
    _begin_run(mcp, tmp_path / "audit-output")
    _preset_config_env(monkeypatch, tmp_path, **config_overrides)
    _call(mcp, "start_config", {})
    _call(mcp, "get_config", {"timeout_s": 1})


def test_record_domain_result_rejects_a_domain_not_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _configured_run(mcp, tmp_path, monkeypatch, selected_domain_ids=["d01"])

    result = {
        "domain_id": "d02",
        "status": "completed",
        "rule_verdicts": _all_pass_verdicts(_domain(mcp, "d02")),
    }
    with pytest.raises(ToolError) as excinfo:
        _call(mcp, "record_domain_result", {"result": result})
    assert "d02" in str(excinfo.value)
    assert "d01" in str(excinfo.value)


def test_record_domain_result_rejects_an_incomplete_completed_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _configured_run(mcp, tmp_path, monkeypatch)

    incomplete_verdicts = _all_pass_verdicts(_domain(mcp, "d01"))[:-1]  # drop D01-R04
    result = {
        "domain_id": "d01",
        "status": "completed",
        "rule_verdicts": incomplete_verdicts,
    }
    with pytest.raises(ToolError) as excinfo:
        _call(mcp, "record_domain_result", {"result": result})
    assert "D01-R04" in str(excinfo.value)


def test_record_domain_result_replace_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _configured_run(mcp, tmp_path, monkeypatch)

    _record_d01_with_finding(mcp)
    with pytest.raises(ToolError) as excinfo:
        _record_d01_with_finding(mcp)
    assert "already has a recorded result" in str(excinfo.value)

    # replace=True is accepted and overwrites the previous result.
    result = _record_d01_with_finding(mcp, replace=True)
    assert result["finding_count"] == 1


def test_record_domain_result_accepts_could_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _configured_run(mcp, tmp_path, monkeypatch)

    result = _call(
        mcp,
        "record_domain_result",
        {
            "result": {
                "domain_id": "d01",
                "status": "could-not-run",
                "reason": "no ledger file present",
            }
        },
    )
    assert result["status"] == "could-not-run"
    assert result["finding_count"] == 0


def _consulted_source(**overrides) -> dict:
    defaults = dict(
        rule_id="D01-R01",
        url="https://example.invalid/standard",
        title="An external standard",
        why="checked the standard's definition before verdicting this rule",
        accessed="2026-08-09T09:02:00Z",
    )
    defaults.update(overrides)
    return defaults


def test_record_domain_result_accepts_consulted_sources_for_the_domains_own_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _configured_run(mcp, tmp_path, monkeypatch)

    result = {
        "domain_id": "d01",
        "status": "completed",
        "rule_verdicts": _all_pass_verdicts(_domain(mcp, "d01")),
        "consulted_sources": [_consulted_source(rule_id="D01-R01")],
    }
    response = _call(mcp, "record_domain_result", {"result": result})
    assert response["status"] == "completed"


def test_record_domain_result_rejects_a_consulted_source_rule_id_outside_the_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _configured_run(mcp, tmp_path, monkeypatch)

    result = {
        "domain_id": "d01",
        "status": "completed",
        "rule_verdicts": _all_pass_verdicts(_domain(mcp, "d01")),
        # D02-R01 belongs to d02, not the d01 result it is attached to here.
        "consulted_sources": [_consulted_source(rule_id="D02-R01")],
    }
    with pytest.raises(ToolError) as excinfo:
        _call(mcp, "record_domain_result", {"result": result})
    assert "D02-R01" in str(excinfo.value)
    assert "does not define" in str(excinfo.value)


def test_record_domain_result_accepts_consulted_sources_on_a_could_not_run_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A source consulted while deciding a domain could not run at all is
    # still checked, and still accepted when it names one of this domain's
    # own rules: consulted_sources is validated independently of status.
    mcp, _state = build_server(FIXTURE_PACK)
    _configured_run(mcp, tmp_path, monkeypatch)

    result = {
        "domain_id": "d01",
        "status": "could-not-run",
        "reason": "no ledger file present",
        "consulted_sources": [_consulted_source(rule_id="D01-R01")],
    }
    response = _call(mcp, "record_domain_result", {"result": result})
    assert response["status"] == "could-not-run"


# ---------------------------------------------------------------------------
# run_status
# ---------------------------------------------------------------------------


def test_run_status_progresses_as_domains_are_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _configured_run(mcp, tmp_path, monkeypatch)

    status = _call(mcp, "run_status", {})
    assert status["recorded_domain_ids"] == []
    assert status["missing_domain_ids"] == ["d01", "d02"]
    assert status["finding_count"] == 0

    _record_d01_with_finding(mcp)
    status = _call(mcp, "run_status", {})
    assert status["recorded_domain_ids"] == ["d01"]
    assert status["missing_domain_ids"] == ["d02"]
    assert status["finding_count"] == 1

    _record_d02_all_pass(mcp)
    status = _call(mcp, "run_status", {})
    assert status["recorded_domain_ids"] == ["d01", "d02"]
    assert status["missing_domain_ids"] == []
    assert status["finding_count"] == 1


# ---------------------------------------------------------------------------
# render_report
# ---------------------------------------------------------------------------


def test_render_report_refuses_while_a_selected_domain_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _configured_run(mcp, tmp_path, monkeypatch)
    _record_d01_with_finding(mcp)

    with pytest.raises(ToolError) as excinfo:
        _call(mcp, "render_report", {"finished": "2026-08-09T10:00:00Z"})
    assert "d02" in str(excinfo.value)


def test_render_report_succeeds_once_every_selected_domain_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    out_dir = tmp_path / "audit-output"
    _begin_run(mcp, out_dir)
    _preset_config_env(monkeypatch, tmp_path)
    _call(mcp, "start_config", {})
    _call(mcp, "get_config", {"timeout_s": 1})
    _record_d01_with_finding(mcp)
    _record_d02_all_pass(mcp)

    result = _call(mcp, "render_report", {"finished": "2026-08-09T10:00:00Z"})

    report_path = Path(result["report_path"])
    run_state_path = Path(result["run_state_path"])
    assert report_path == out_dir / "report.html"
    assert run_state_path == out_dir / "run-state.json"
    assert report_path.is_file()
    assert run_state_path.is_file()

    report_text = report_path.read_text(encoding="utf-8")
    assert "Two gnomes share bed-14 without the shared-bed flag" in report_text

    restored = RunState.from_json(run_state_path.read_text(encoding="utf-8"))
    assert restored.meta.repo_name == "widgets-app"
    assert restored.meta.finished == "2026-08-09T10:00:00Z"
    assert restored.domain_results["d01"].findings[0].rule_id == "D01-R02"

    assert result["findings_summary"]["total_findings"] == 1
    assert result["findings_summary"]["by_severity"]["high"] == 1


def test_render_report_writes_to_a_custom_deliverables_dir_and_leaves_the_progress_file_in_output_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The heart of issue #109's recommended option (a): the picker chooses
    # where the deliverables land, but output_dir stays the run's working
    # directory for the crash-recovery progress file throughout, and that
    # file is gone once the report is written, same as the default path.
    mcp, _state = build_server(FIXTURE_PACK)
    out_dir = tmp_path / "audit-output"
    deliverables_dir = tmp_path / "reports" / "this-run"
    deliverables_dir.mkdir(parents=True)
    _begin_run(mcp, out_dir)
    _preset_config_env(monkeypatch, tmp_path, deliverables_dir=str(deliverables_dir))
    _call(mcp, "start_config", {})
    _call(mcp, "get_config", {"timeout_s": 1})

    # The progress file is written into output_dir well before render_report,
    # exactly as it always was; the custom deliverables choice must not have
    # redirected it.
    assert (out_dir / PROGRESS_FILENAME).is_file()

    _record_d01_with_finding(mcp)
    _record_d02_all_pass(mcp)

    result = _call(mcp, "render_report", {"finished": "2026-08-09T10:00:00Z"})

    report_path = Path(result["report_path"])
    run_state_path = Path(result["run_state_path"])
    assert report_path == deliverables_dir / "report.html"
    assert run_state_path == deliverables_dir / "run-state.json"
    assert report_path.is_file()
    assert run_state_path.is_file()
    # Nothing landed in output_dir except the now-removed progress file.
    assert not (out_dir / "report.html").exists()
    assert not (out_dir / "run-state.json").exists()
    assert not (out_dir / PROGRESS_FILENAME).exists()


def test_render_report_stamps_server_finished_independently_of_the_assistant_supplied_finished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    out_dir = tmp_path / "audit-output"
    monkeypatch.setattr(server_module, "_now_utc_iso", lambda: "2026-08-11T00:00:03Z")
    _begin_run(mcp, out_dir)
    _preset_config_env(monkeypatch, tmp_path)
    _call(mcp, "start_config", {})
    _call(mcp, "get_config", {"timeout_s": 1})
    _record_d01_with_finding(mcp)
    _record_d02_all_pass(mcp)

    monkeypatch.setattr(server_module, "_now_utc_iso", lambda: "2026-08-11T00:10:00Z")
    result = _call(mcp, "render_report", {"finished": "2020-01-01T00:00:00Z"})
    restored = RunState.from_json(
        Path(result["run_state_path"]).read_text(encoding="utf-8")
    )

    assert restored.meta.finished == "2020-01-01T00:00:00Z"
    assert restored.meta.server_started == "2026-08-11T00:00:03Z"
    assert restored.meta.server_finished == "2026-08-11T00:10:00Z"


def test_render_report_frees_the_run_so_begin_run_does_not_need_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output", repo_name="first-repo")
    _preset_config_env(monkeypatch, tmp_path)
    _call(mcp, "start_config", {})
    _call(mcp, "get_config", {"timeout_s": 1})
    _record_d01_with_finding(mcp)
    _record_d02_all_pass(mcp)
    _call(mcp, "render_report", {"finished": "2026-08-09T10:00:00Z"})

    # No replace=True needed: the previous run finished.
    result = _begin_run(mcp, tmp_path / "audit-output-2", repo_name="second-repo")
    assert result["meta"]["repo_name"] == "second-repo"


def test_begin_run_populates_rules_pack_commit_when_the_rules_dir_is_a_git_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rules_dir = tmp_path / "rules-pack"
    shutil.copytree(FIXTURE_PACK, rules_dir)
    _init_git_repo(rules_dir)
    expected_sha = _head_sha(rules_dir)

    mcp, _state = build_server(rules_dir)
    result = _begin_run(mcp, tmp_path / "audit-output")
    assert result["meta"]["rules_pack_commit"] == expected_sha

    # And it survives through to the finished run's meta, not just the
    # in-progress tracker's: finished_meta is rebuilt from run.meta at
    # render_report time via model_dump(), so this is the check that a
    # future refactor of that rebuild can't silently drop the new field.
    _preset_config_env(monkeypatch, tmp_path)
    _call(mcp, "start_config", {})
    _call(mcp, "get_config", {"timeout_s": 1})
    _record_d01_with_finding(mcp)
    _record_d02_all_pass(mcp)
    report_result = _call(mcp, "render_report", {"finished": "2026-08-09T10:00:00Z"})

    run_state = RunState.from_json(
        Path(report_result["run_state_path"]).read_text(encoding="utf-8")
    )
    assert run_state.meta.rules_pack_commit == expected_sha


# ---------------------------------------------------------------------------
# Full happy-path integration test: every tool, in order, on the fixture pack.
# ---------------------------------------------------------------------------


def test_full_audit_flow_walks_every_tool_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    out_dir = tmp_path / "audit-output"

    domains = _call(mcp, "list_domains", {})
    domain_ids = [d["id"] for d in domains["domains"]]
    assert domain_ids == ["d01", "d02"]

    begin_result = _begin_run(mcp, out_dir)
    assert begin_result["meta"]["repo_name"] == "widgets-app"

    _preset_config_env(monkeypatch, tmp_path)
    started = _call(mcp, "start_config", {})
    assert started["mode"] == "preset"

    config = _call(mcp, "get_config", {"timeout_s": 5})
    selected_ids = config["selected_domain_ids"]
    assert selected_ids == ["d01", "d02"]

    for domain_id in selected_ids:
        _call(
            mcp, "get_domain", {"domain_id": domain_id}
        )  # the agent reads the rule text
        if domain_id == "d01":
            outcome = _record_d01_with_finding(mcp)
        else:
            outcome = _record_d02_all_pass(mcp)
        assert outcome["domain_id"] == domain_id

    status = _call(mcp, "run_status", {})
    assert status["missing_domain_ids"] == []

    finished_result = _call(mcp, "render_report", {"finished": "2026-08-09T10:15:00Z"})
    assert Path(finished_result["report_path"]).is_file()
    assert Path(finished_result["run_state_path"]).is_file()
    assert finished_result["findings_summary"]["total_findings"] == 1


# ---------------------------------------------------------------------------
# file_issues
# ---------------------------------------------------------------------------


def _fake_create_issue(
    fail_on: set[str] | None = None, warn_on: set[str] | None = None
):
    """Build a fake create_issue plus the list of calls it recorded.

    Mirrors engineering_audit.issues.create_issue's signature exactly (as
    called from server.py, with no runner argument), issuing incrementing
    fake URLs and raising IssueFilingError for any title in fail_on.
    """
    calls: list[dict] = []
    counter = {"n": 0}

    def _fake(repo: str, title: str, body: str, labels: list[str]) -> CreatedIssue:
        calls.append({"repo": repo, "title": title, "body": body, "labels": labels})
        if fail_on and title in fail_on:
            raise IssueFilingError(f"gh issue create failed for {title!r}")
        counter["n"] += 1
        warnings = (
            [f"label(s) {labels} not found on repo {repo}; issue filed without them"]
            if (warn_on and title in warn_on)
            else []
        )
        return CreatedIssue(
            url=f"https://github.com/{repo}/issues/{counter['n']}", warnings=warnings
        )

    return _fake, calls


@pytest.fixture(autouse=True)
def _stub_ensure_label(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """file_issues ensures the label exists once per call, which would shell
    out to a real gh. Every test gets the label-already-present answer by
    default; the label-path tests override it. Records the calls, so a test
    can assert it happened once per run rather than once per issue."""
    calls: list[dict] = []

    def _fake(repo: str, name: str = "engineering-audit") -> LabelStatus:
        calls.append({"repo": repo, "name": name})
        return LabelStatus(name=name, state="present")

    monkeypatch.setattr(server_module, "ensure_label", _fake)
    return calls


def _stub_label_status(
    monkeypatch: pytest.MonkeyPatch, status: LabelStatus
) -> list[dict]:
    calls: list[dict] = []

    def _fake(repo: str, name: str = "engineering-audit") -> LabelStatus:
        calls.append({"repo": repo, "name": name})
        return status

    monkeypatch.setattr(server_module, "ensure_label", _fake)
    return calls


def _configured_github_run(
    mcp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo_dir: Path | None = None
) -> None:
    _begin_run(
        mcp,
        tmp_path / "audit-output",
        repo_dir=str(repo_dir) if repo_dir else None,
    )
    _preset_config_env(monkeypatch, tmp_path, issue_mode="github")
    _call(mcp, "start_config", {})
    _call(mcp, "get_config", {"timeout_s": 1})


def test_file_issues_preview_never_invokes_gh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _must_not_be_called(*_args, **_kwargs):
        raise AssertionError("gh must not be invoked while previewing (confirm=False)")

    _fake, calls = _fake_create_issue()
    monkeypatch.setattr(server_module, "create_issue", _fake)
    monkeypatch.setattr(server_module, "gh_available", _must_not_be_called)
    monkeypatch.setattr(server_module, "detect_repo", _must_not_be_called)

    mcp, _state = build_server(FIXTURE_PACK)
    _configured_github_run(mcp, tmp_path, monkeypatch)
    _record_d01_with_finding(mcp)
    _record_d02_all_pass(mcp)

    result = _call(mcp, "file_issues", {})
    assert result["count"] == 1
    assert result["titles"] == ["Set shared-bed flag for bed-14"]
    assert calls == []


def test_file_issues_confirm_files_one_issue_per_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake, calls = _fake_create_issue()
    monkeypatch.setattr(server_module, "create_issue", _fake)

    mcp, _state = build_server(FIXTURE_PACK)
    _configured_github_run(mcp, tmp_path, monkeypatch)
    _record_d01_with_finding(mcp)
    _record_d02_all_pass(mcp)

    result = _call(mcp, "file_issues", {"confirm": True, "repo": "rodlunt/widgets-app"})

    assert result["repo"] == "rodlunt/widgets-app"
    assert result["filed"] == {
        "D01-R02#1": "https://github.com/rodlunt/widgets-app/issues/1"
    }
    # d01 was fetched (_record_d01_with_finding calls _fetch_domain first)
    # and carries no self_assessment, so the domain note (issue #130) names
    # "no self-assessed confidence reported" and "fetched ... this run".
    assert calls == [
        {
            "repo": "rodlunt/widgets-app",
            "title": "Set shared-bed flag for bed-14",
            "body": (
                "bed-14 has two occupants and no shared-bed flag.\n\n"
                "Found by an engineering-practice audit (rule D01-R02, severity high, "
                "at ledger/beds.py:42). This finding's domain: no self-assessed "
                "confidence reported; its rule text was fetched from the server this "
                "run. Reference: invented for test fixtures only, no external source"
            ),
            "labels": ["engineering-audit"],
        }
    ]


def _extract_issues_data(rendered_html: str) -> dict:
    """Pull the report's ``<script type="application/json" id="issues-data">``
    payload out of a rendered report, mirroring what the report's own JS
    does with JSON.parse at runtime (see test_report.py's
    _extract_json_script, duplicated here rather than imported since the
    two test modules otherwise share no fixtures)."""
    match = re.search(
        r'<script type="application/json" id="issues-data">(.*?)</script>',
        rendered_html,
        re.DOTALL,
    )
    assert match is not None, "no issues-data script block found in rendered report"
    return json.loads(match.group(1))


def test_file_issues_and_report_issues_section_produce_the_same_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Issue #134's shape again: a correction (markdown stripping, issue
    # #128; the domain-confidence note, issue #130) applied to report.py's
    # issues section and not to server.py's own file_issues would leave the
    # gh-CLI-filed issue, a permanent external record, disagreeing with
    # what the report itself shows for the identical finding. This exercises
    # both real code paths (not a hand-copied re-implementation of either)
    # and asserts they produce byte-identical bodies.
    _fake, calls = _fake_create_issue()
    monkeypatch.setattr(server_module, "create_issue", _fake)

    mcp, _state = build_server(FIXTURE_PACK)
    _configured_github_run(mcp, tmp_path, monkeypatch)
    _fetch_domain(mcp, "d01")
    verdicts = _all_pass_verdicts(_domain(mcp, "d01"))
    verdicts[1] = {"rule_id": "D01-R02", "verdict": "finding"}
    _call(
        mcp,
        "record_domain_result",
        {
            "result": {
                "domain_id": "d01",
                "status": "completed",
                "rule_verdicts": verdicts,
                "findings": [
                    {
                        "rule_id": "D01-R02",
                        "severity": "high",
                        "title": "Two gnomes share bed-14 without the shared-bed flag",
                        "location": "ledger/beds.py:42",
                        "body_md": "bed-14 holds two gnomes.",
                        "issue_title": "Set shared-bed flag for bed-14",
                        # Deliberately carries markdown (issue #128) so this
                        # test also proves both paths strip it the same way,
                        # not just that both attach the same domain note.
                        "issue_body": (
                            "**The issue**: bed-14 has two occupants and no "
                            "shared-bed flag."
                        ),
                    }
                ],
                # A self-assessment (issue #130) so the domain note both
                # paths attach has real, non-default content to compare.
                "self_assessment": {"confidence": "low", "limits": ""},
            },
            "replace": False,
        },
    )
    _record_d02_all_pass(mcp)

    # Path 1: server.py's file_issues, the gh CLI path.
    _call(mcp, "file_issues", {"confirm": True, "repo": "rodlunt/widgets-app"})
    assert len(calls) == 1
    filed_body = calls[0]["body"]

    # Path 2: report.py's issues section, read back out of the rendered
    # report the same run then produces.
    report_result = _call(mcp, "render_report", {"finished": "2026-08-09T10:00:00Z"})
    rendered = Path(report_result["report_path"]).read_text(encoding="utf-8")
    issues_data = _extract_issues_data(rendered)
    report_issue = next(
        issue for issue in issues_data["issues"] if issue["rule_id"] == "D01-R02"
    )

    assert filed_body == report_issue["body"]
    # Pin the actual shared text, not just that the two agree with each
    # other: both the markdown strip and the domain note must have fired.
    assert filed_body == (
        "The issue: bed-14 has two occupants and no shared-bed flag.\n\n"
        "Found by an engineering-practice audit (rule D01-R02, severity high, "
        "at ledger/beds.py:42). This finding's domain: self-assessed confidence "
        "low; its rule text was fetched from the server this run. Reference: "
        "invented for test fixtures only, no external source"
    )
    assert "*" not in filed_body


def test_file_issues_refuses_a_finding_on_a_sourceless_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # D01-R04's footer in the fixture pack deliberately carries no Source:
    # fragment. A filed issue is a published claim; filing must refuse
    # loudly before anything is created, and must never publish an
    # "unsourced" admission instead.
    _fake, calls = _fake_create_issue()
    monkeypatch.setattr(server_module, "create_issue", _fake)

    mcp, _state = build_server(FIXTURE_PACK)
    _configured_github_run(mcp, tmp_path, monkeypatch)

    verdicts = _all_pass_verdicts(_domain(mcp, "d01"))
    verdicts[3] = {"rule_id": "D01-R04", "verdict": "finding"}
    result = {
        "domain_id": "d01",
        "status": "completed",
        "rule_verdicts": verdicts,
        "findings": [
            {
                "rule_id": "D01-R04",
                "severity": "low",
                "title": "beard-length average not recalculated on retirement",
                "location": "ledger/beards.py:10",
                "body_md": "x",
                "issue_title": "x",
                "issue_body": "x",
            }
        ],
    }
    _call(mcp, "record_domain_result", {"result": result})
    _record_d02_all_pass(mcp)

    with pytest.raises(ToolError) as excinfo:
        _call(mcp, "file_issues", {"confirm": True, "repo": "rodlunt/widgets-app"})
    message = str(excinfo.value)
    assert "D01-R04" in message
    assert "no cited source" in message
    assert "unsourced" not in message.lower()
    assert calls == []


def test_file_issues_issue_mode_report_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _configured_run(mcp, tmp_path, monkeypatch)  # defaults to issue_mode="report"
    _record_d01_with_finding(mcp)
    _record_d02_all_pass(mcp)

    with pytest.raises(ToolError) as excinfo:
        _call(mcp, "file_issues", {})
    assert "issue_mode" in str(excinfo.value)
    assert "github" in str(excinfo.value)


def test_file_issues_requires_at_least_one_recorded_domain_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _configured_github_run(mcp, tmp_path, monkeypatch)

    with pytest.raises(ToolError) as excinfo:
        _call(mcp, "file_issues", {})
    assert "No domain results recorded" in str(excinfo.value)


def test_file_issues_partial_failure_reports_filed_and_unfiled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # d01 has one finding (D01-R02); make its own fixture domain result carry
    # a second finding on d02 so two issues are pending, and fail the second.
    mcp, _state = build_server(FIXTURE_PACK)
    _configured_github_run(mcp, tmp_path, monkeypatch)
    _record_d01_with_finding(mcp)
    verdicts = _all_pass_verdicts(_domain(mcp, "d02"))
    verdicts[0] = {"rule_id": "D02-R01", "verdict": "finding"}
    _call(
        mcp,
        "record_domain_result",
        {
            "result": {
                "domain_id": "d02",
                "status": "completed",
                "rule_verdicts": verdicts,
                "findings": [
                    {
                        "rule_id": "D02-R01",
                        "severity": "low",
                        "title": "A d02 finding",
                        "location": "x.py",
                        "body_md": "x",
                        "issue_title": "A d02 finding",
                        "issue_body": "x",
                    }
                ],
            }
        },
    )

    _fake, calls = _fake_create_issue(fail_on={"A d02 finding"})
    monkeypatch.setattr(server_module, "create_issue", _fake)

    with pytest.raises(ToolError) as excinfo:
        _call(mcp, "file_issues", {"confirm": True, "repo": "rodlunt/widgets-app"})
    message = str(excinfo.value)
    assert "D01-R02" in message  # filed
    assert "D02-R01" in message  # not filed
    assert len(calls) == 2

    # The one that succeeded must be recorded, so a retry does not re-file it.
    _fake2, calls2 = _fake_create_issue()
    monkeypatch.setattr(server_module, "create_issue", _fake2)
    retry_result = _call(
        mcp, "file_issues", {"confirm": True, "repo": "rodlunt/widgets-app"}
    )
    assert retry_result["filed"] == {
        "D02-R01#1": "https://github.com/rodlunt/widgets-app/issues/1"
    }
    assert [c["title"] for c in calls2] == ["A d02 finding"]


def _record_d01_with_two_findings_on_one_rule(mcp) -> dict:
    """A domain result carrying two findings for the same rule, which the
    schema allows and real runs produce."""
    verdicts = _all_pass_verdicts(_domain(mcp, "d01"))
    verdicts[1] = {"rule_id": "D01-R02", "verdict": "finding"}
    result = {
        "domain_id": "d01",
        "status": "completed",
        "rule_verdicts": verdicts,
        "findings": [
            {
                "rule_id": "D01-R02",
                "severity": "high",
                "title": "bed-14 has no shared-bed flag",
                "location": "ledger/beds.py:42",
                "body_md": "bed-14 holds two gnomes.",
                "issue_title": "Set shared-bed flag for bed-14",
                "issue_body": "bed-14 has two occupants and no shared-bed flag.",
            },
            {
                "rule_id": "D01-R02",
                "severity": "medium",
                "title": "bed-19 has no shared-bed flag",
                "location": "ledger/beds.py:57",
                "body_md": "bed-19 holds two gnomes.",
                "issue_title": "Set shared-bed flag for bed-19",
                "issue_body": "bed-19 has two occupants and no shared-bed flag.",
            },
        ],
    }
    return _call(mcp, "record_domain_result", {"result": result})


def test_file_issues_keeps_both_urls_for_two_findings_on_one_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Keyed by rule id, the second finding's url overwrote the first's and
    # the caller could never report or link it.
    _fake, calls = _fake_create_issue()
    monkeypatch.setattr(server_module, "create_issue", _fake)

    mcp, _state = build_server(FIXTURE_PACK)
    _configured_github_run(mcp, tmp_path, monkeypatch)
    _record_d01_with_two_findings_on_one_rule(mcp)
    _record_d02_all_pass(mcp)

    result = _call(mcp, "file_issues", {"confirm": True, "repo": "rodlunt/widgets-app"})

    assert result["filed"] == {
        "D01-R02#1": "https://github.com/rodlunt/widgets-app/issues/1",
        "D01-R02#2": "https://github.com/rodlunt/widgets-app/issues/2",
    }
    assert result["all_filed_issue_urls"] == result["filed"]
    assert [c["title"] for c in calls] == [
        "Set shared-bed flag for bed-14",
        "Set shared-bed flag for bed-19",
    ]


def test_file_issues_retry_after_a_failure_between_two_findings_on_one_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The bookkeeping was keyed by rule id, so the first finding's success
    # marked the whole rule filed and the second one was skipped forever on
    # retry: a finding that was never reported, looking exactly like one that
    # was.
    _fake, _calls = _fake_create_issue(fail_on={"Set shared-bed flag for bed-19"})
    monkeypatch.setattr(server_module, "create_issue", _fake)

    mcp, _state = build_server(FIXTURE_PACK)
    _configured_github_run(mcp, tmp_path, monkeypatch)
    _record_d01_with_two_findings_on_one_rule(mcp)
    _record_d02_all_pass(mcp)

    with pytest.raises(ToolError) as excinfo:
        _call(mcp, "file_issues", {"confirm": True, "repo": "rodlunt/widgets-app"})
    message = str(excinfo.value)
    assert "D01-R02#1" in message  # filed
    assert "D01-R02#2" in message  # not filed

    _fake2, calls2 = _fake_create_issue()
    monkeypatch.setattr(server_module, "create_issue", _fake2)
    retry = _call(mcp, "file_issues", {"confirm": True, "repo": "rodlunt/widgets-app"})

    assert [c["title"] for c in calls2] == ["Set shared-bed flag for bed-19"]
    assert retry["filed"] == {
        "D01-R02#2": "https://github.com/rodlunt/widgets-app/issues/1"
    }
    assert retry["all_filed_issue_urls"] == {
        "D01-R02#1": "https://github.com/rodlunt/widgets-app/issues/1",
        "D01-R02#2": "https://github.com/rodlunt/widgets-app/issues/1",
    }

    # Nothing is left pending: a third call has nothing to file.
    preview = _call(mcp, "file_issues", {})
    assert preview["count"] == 0


def test_render_report_carries_a_distinct_filed_url_per_finding_on_one_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # RunState.filed_issue_urls is keyed per finding ("<rule id>#<n>") since
    # schema_version 3, matching run.filed_issues exactly, so two findings on
    # the same rule each keep their own url in the written run state and the
    # rendered report links each of them separately rather than showing one
    # shared "already filed" link for both.
    _fake, _calls = _fake_create_issue()
    monkeypatch.setattr(server_module, "create_issue", _fake)

    mcp, _state = build_server(FIXTURE_PACK)
    _configured_github_run(mcp, tmp_path, monkeypatch)
    _record_d01_with_two_findings_on_one_rule(mcp)
    _record_d02_all_pass(mcp)
    _call(mcp, "file_issues", {"confirm": True, "repo": "rodlunt/widgets-app"})

    report_result = _call(mcp, "render_report", {"finished": "2026-08-09T10:00:00Z"})
    restored = RunState.from_json(
        Path(report_result["run_state_path"]).read_text(encoding="utf-8")
    )
    assert restored.filed_issue_urls == {
        "D01-R02#1": "https://github.com/rodlunt/widgets-app/issues/1",
        "D01-R02#2": "https://github.com/rodlunt/widgets-app/issues/2",
    }

    rendered = Path(report_result["report_path"]).read_text(encoding="utf-8")
    assert (
        'href="https://github.com/rodlunt/widgets-app/issues/1">already filed</a>'
        in rendered
    )
    assert (
        'href="https://github.com/rodlunt/widgets-app/issues/2">already filed</a>'
        in rendered
    )


def test_file_issues_missing_label_warning_is_surfaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake, _calls = _fake_create_issue(warn_on={"Set shared-bed flag for bed-14"})
    monkeypatch.setattr(server_module, "create_issue", _fake)

    mcp, _state = build_server(FIXTURE_PACK)
    _configured_github_run(mcp, tmp_path, monkeypatch)
    _record_d01_with_finding(mcp)
    _record_d02_all_pass(mcp)

    result = _call(mcp, "file_issues", {"confirm": True, "repo": "rodlunt/widgets-app"})
    assert result["warnings"] and "not found on repo" in result["warnings"][0]


def test_file_issues_checks_the_label_once_per_call_not_once_per_issue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _stub_ensure_label: list[dict]
) -> None:
    _fake, calls = _fake_create_issue()
    monkeypatch.setattr(server_module, "create_issue", _fake)

    mcp, _state = build_server(FIXTURE_PACK)
    _configured_github_run(mcp, tmp_path, monkeypatch)
    _record_d01_with_two_findings_on_one_rule(mcp)
    _record_d02_all_pass(mcp)

    result = _call(mcp, "file_issues", {"confirm": True, "repo": "rodlunt/widgets-app"})

    assert _stub_ensure_label == [
        {"repo": "rodlunt/widgets-app", "name": "engineering-audit"}
    ]
    assert result["label"] == {"name": "engineering-audit", "state": "present"}
    assert result["warnings"] == []
    assert len(calls) == 2
    assert all(c["labels"] == ["engineering-audit"] for c in calls)


def test_file_issues_files_labelled_when_the_label_had_to_be_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    label_calls = _stub_label_status(
        monkeypatch, LabelStatus(name="engineering-audit", state="created")
    )
    _fake, calls = _fake_create_issue()
    monkeypatch.setattr(server_module, "create_issue", _fake)

    mcp, _state = build_server(FIXTURE_PACK)
    _configured_github_run(mcp, tmp_path, monkeypatch)
    _record_d01_with_two_findings_on_one_rule(mcp)
    _record_d02_all_pass(mcp)

    result = _call(mcp, "file_issues", {"confirm": True, "repo": "rodlunt/widgets-app"})

    assert len(label_calls) == 1
    assert result["label"] == {"name": "engineering-audit", "state": "created"}
    assert result["warnings"] == []
    assert all(c["labels"] == ["engineering-audit"] for c in calls)


def test_file_issues_label_creation_failure_warns_once_and_files_unlabelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The old behaviour was one warning per issue for the same fact (33 of
    # them on the 2026-08-09 self-audit). One warning per run, and never a
    # silent pass: the response says the label is unavailable.
    label_calls = _stub_label_status(
        monkeypatch,
        LabelStatus(
            name="engineering-audit",
            state="unavailable",
            warning="label 'engineering-audit' is not on repo rodlunt/widgets-app and could "
            "not be created (HTTP 403: Resource not accessible by integration).",
        ),
    )
    _fake, calls = _fake_create_issue()
    monkeypatch.setattr(server_module, "create_issue", _fake)

    mcp, _state = build_server(FIXTURE_PACK)
    _configured_github_run(mcp, tmp_path, monkeypatch)
    _record_d01_with_two_findings_on_one_rule(mcp)
    _record_d02_all_pass(mcp)

    result = _call(mcp, "file_issues", {"confirm": True, "repo": "rodlunt/widgets-app"})

    assert len(label_calls) == 1
    assert result["label"]["state"] == "unavailable"
    assert len(result["warnings"]) == 1
    assert "could not be created" in result["warnings"][0]
    # Both issues still filed, just without the label.
    assert len(calls) == 2
    assert all(c["labels"] == [] for c in calls)


def test_file_issues_deduplicates_a_repeated_per_issue_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # create_issue's own missing-label retry (a label deleted mid-run) reports
    # the same fact for every issue after it; the run's output must carry it
    # once.
    _fake, calls = _fake_create_issue(
        warn_on={"Set shared-bed flag for bed-14", "Set shared-bed flag for bed-19"}
    )
    monkeypatch.setattr(server_module, "create_issue", _fake)

    mcp, _state = build_server(FIXTURE_PACK)
    _configured_github_run(mcp, tmp_path, monkeypatch)
    _record_d01_with_two_findings_on_one_rule(mcp)
    _record_d02_all_pass(mcp)

    result = _call(mcp, "file_issues", {"confirm": True, "repo": "rodlunt/widgets-app"})
    assert len(calls) == 2
    assert len(result["warnings"]) == 1


def test_file_issues_confirm_detects_repo_from_repo_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audited_repo = tmp_path / "audited-repo"
    audited_repo.mkdir()
    _fake, calls = _fake_create_issue()
    monkeypatch.setattr(server_module, "create_issue", _fake)
    monkeypatch.setattr(server_module, "gh_available", lambda: True)
    monkeypatch.setattr(
        server_module, "detect_repo", lambda cwd: "rodlunt/detected-repo"
    )

    mcp, _state = build_server(FIXTURE_PACK)
    _configured_github_run(mcp, tmp_path, monkeypatch, repo_dir=audited_repo)
    _record_d01_with_finding(mcp)
    _record_d02_all_pass(mcp)

    result = _call(mcp, "file_issues", {"confirm": True})
    assert result["repo"] == "rodlunt/detected-repo"
    assert calls[0]["repo"] == "rodlunt/detected-repo"


def test_file_issues_confirm_raises_when_gh_unavailable_and_no_repo_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audited_repo = tmp_path / "audited-repo"
    audited_repo.mkdir()
    monkeypatch.setattr(server_module, "gh_available", lambda: False)

    mcp, _state = build_server(FIXTURE_PACK)
    _configured_github_run(mcp, tmp_path, monkeypatch, repo_dir=audited_repo)
    _record_d01_with_finding(mcp)
    _record_d02_all_pass(mcp)

    with pytest.raises(ToolError) as excinfo:
        _call(mcp, "file_issues", {"confirm": True})
    assert "gh is not available" in str(excinfo.value)


def _bare_tracker(
    tmp_path: Path, repo_dir: Path | None = None
) -> server_module.RunTracker:
    meta = RunMeta(
        tool_version="0.0.0-dev",
        rules_pack_name="fixture_pack",
        assistant="claude-code",
        model="claude-sonnet-5",
        repo_name="widgets-app",
        repo_commit="abc1234",
        started="2026-08-09T09:00:00Z",
    )
    return server_module.RunTracker(meta=meta, output_dir=tmp_path, repo_dir=repo_dir)


def test_resolve_target_repo_takes_an_explicit_repo_without_touching_gh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _must_not_be_called(*_args, **_kwargs):
        raise AssertionError("an explicit repo must not trigger repo detection")

    monkeypatch.setattr(server_module, "gh_available", _must_not_be_called)
    monkeypatch.setattr(server_module, "detect_repo", _must_not_be_called)

    tracker = _bare_tracker(tmp_path, repo_dir=tmp_path)
    assert (
        server_module._resolve_target_repo(tracker, "rodlunt/widgets-app")
        == "rodlunt/widgets-app"
    )


def test_resolve_target_repo_raises_when_detection_finds_no_github_remote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server_module, "gh_available", lambda: True)
    monkeypatch.setattr(server_module, "detect_repo", lambda cwd: None)

    tracker = _bare_tracker(tmp_path, repo_dir=tmp_path)
    with pytest.raises(ValueError) as excinfo:
        server_module._resolve_target_repo(tracker, None)
    assert "no GitHub" in str(excinfo.value)


def test_file_issues_confirm_raises_when_no_repo_dir_and_no_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _configured_github_run(mcp, tmp_path, monkeypatch)  # no repo_dir
    _record_d01_with_finding(mcp)
    _record_d02_all_pass(mcp)

    with pytest.raises(ToolError) as excinfo:
        _call(mcp, "file_issues", {"confirm": True})
    assert "No repo_dir" in str(excinfo.value)


# ---------------------------------------------------------------------------
# submit_feedback
# ---------------------------------------------------------------------------


def test_submit_feedback_errors_when_nothing_to_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _configured_run(mcp, tmp_path, monkeypatch)  # feedback_text unset

    with pytest.raises(ToolError) as excinfo:
        _call(mcp, "submit_feedback", {})
    assert "Nothing to send" in str(excinfo.value)


def test_submit_feedback_extra_text_is_accepted_when_config_has_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake, calls = _fake_create_issue()
    monkeypatch.setattr(server_module, "create_issue", _fake)
    monkeypatch.setattr(server_module, "gh_available", lambda: True)

    mcp, _state = build_server(FIXTURE_PACK)
    _configured_run(mcp, tmp_path, monkeypatch)

    result = _call(
        mcp,
        "submit_feedback",
        {"extra_text": "This came from the agent, not the form."},
    )
    assert result["mode"] == "issue"
    assert "This came from the agent, not the form." in calls[0]["body"]


def test_submit_feedback_files_to_feedback_repo_with_feedback_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake, calls = _fake_create_issue()
    monkeypatch.setattr(server_module, "create_issue", _fake)
    monkeypatch.setattr(server_module, "gh_available", lambda: True)

    mcp, _state = build_server(FIXTURE_PACK)
    _configured_run(
        mcp, tmp_path, monkeypatch, feedback_text="The gnome export was slow."
    )

    result = _call(mcp, "submit_feedback", {})
    assert result["mode"] == "issue"
    assert calls[0]["repo"] == "rodlunt/engineering-audit"
    assert calls[0]["labels"] == ["feedback"]
    assert "The gnome export was slow." in calls[0]["body"]
    assert "Run metadata" in calls[0]["body"]


def test_submit_feedback_omits_unconsented_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake, calls = _fake_create_issue()
    monkeypatch.setattr(server_module, "create_issue", _fake)
    monkeypatch.setattr(server_module, "gh_available", lambda: True)

    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    _preset_config_env(
        monkeypatch,
        tmp_path,
        feedback_text="The gnome export was slow.",
        telemetry_consent={
            "coverage": False,
            "rollup": False,
            "self_assessment": False,
            "environment": False,
        },
    )
    _call(mcp, "start_config", {})
    _call(mcp, "get_config", {"timeout_s": 1})
    _record_d01_with_finding(mcp)
    _record_d02_all_pass(mcp)

    _call(mcp, "submit_feedback", {})
    body = calls[0]["body"]
    assert "Coverage" not in body
    assert "Findings rollup" not in body
    assert "Self-assessment" not in body
    assert "Environment" not in body


def test_submit_feedback_includes_consented_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake, calls = _fake_create_issue()
    monkeypatch.setattr(server_module, "create_issue", _fake)
    monkeypatch.setattr(server_module, "gh_available", lambda: True)

    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    _preset_config_env(
        monkeypatch,
        tmp_path,
        feedback_text="The gnome export was slow.",
        telemetry_consent={
            "coverage": True,
            "rollup": True,
            "self_assessment": True,
            "environment": True,
        },
    )
    _call(mcp, "start_config", {})
    _call(mcp, "get_config", {"timeout_s": 1})
    _record_d01_with_finding(mcp)
    _record_d02_all_pass(mcp)

    _call(mcp, "submit_feedback", {})
    body = calls[0]["body"]
    assert "Coverage" in body
    assert "Findings rollup" in body
    assert "Self-assessment by domain" in body
    assert "Environment" in body
    # Finding text must never leave via feedback, only counts.
    assert "Two gnomes share bed-14 without the shared-bed flag" not in body


def test_submit_feedback_omits_verdict_distribution_duration_and_rules_fetched_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake, calls = _fake_create_issue()
    monkeypatch.setattr(server_module, "create_issue", _fake)
    monkeypatch.setattr(server_module, "gh_available", lambda: True)

    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    _preset_config_env(
        monkeypatch,
        tmp_path,
        feedback_text="The gnome export was slow.",
        telemetry_consent={
            "verdict_distribution": False,
            "duration": False,
            "rules_fetched": False,
        },
    )
    _call(mcp, "start_config", {})
    _call(mcp, "get_config", {"timeout_s": 1})
    _record_d01_with_finding(mcp)
    _record_d02_all_pass(mcp)

    _call(mcp, "submit_feedback", {})
    body = calls[0]["body"]
    assert "Rule verdict distribution" not in body
    assert "Duration" not in body
    assert "Rules fetched" not in body


def test_submit_feedback_includes_verdict_distribution_duration_and_rules_fetched_when_consented(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake, calls = _fake_create_issue()
    monkeypatch.setattr(server_module, "create_issue", _fake)
    monkeypatch.setattr(server_module, "gh_available", lambda: True)

    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    _preset_config_env(
        monkeypatch,
        tmp_path,
        feedback_text="The gnome export was slow.",
        telemetry_consent={
            "verdict_distribution": True,
            "duration": True,
            "rules_fetched": True,
        },
    )
    _call(mcp, "start_config", {})
    _call(mcp, "get_config", {"timeout_s": 1})
    # Both domains are fetched via get_domain by the record helpers below
    # (see _fetch_domain), so the rules-fetched section must report both as
    # fetched, never as unrecorded: this is a live run, not one carried in
    # from a save that predates fetch tracking.
    _record_d01_with_finding(mcp)
    _record_d02_all_pass(mcp)

    _call(mcp, "submit_feedback", {})
    body = calls[0]["body"]
    assert "Rule verdict distribution" in body
    assert "Total verdicts:" in body
    assert "Duration" in body
    assert "Rules fetched" in body
    assert "- d01: fetched" in body
    assert "- d02: fetched" in body
    assert "unrecorded" not in body
    # Finding text must never leave via feedback, only counts.
    assert "Two gnomes share bed-14 without the shared-bed flag" not in body


def test_submit_feedback_reader_conclusions_omitted_unless_consented(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Issue #135: report_conclusion/report_fix_first are ignored unless the
    # reader_conclusions section was consented to, same as every other
    # telemetry section this tool sends.
    _fake, calls = _fake_create_issue()
    monkeypatch.setattr(server_module, "create_issue", _fake)
    monkeypatch.setattr(server_module, "gh_available", lambda: True)

    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    _preset_config_env(
        monkeypatch,
        tmp_path,
        feedback_text="The gnome export was slow.",
        telemetry_consent={"reader_conclusions": False},
    )
    _call(mcp, "start_config", {})
    _call(mcp, "get_config", {"timeout_s": 1})
    _record_d01_with_finding(mcp)
    _record_d02_all_pass(mcp)

    _call(
        mcp,
        "submit_feedback",
        {
            "report_conclusion": "It found a shared-bed flag bug.",
            "report_fix_first": "The missing flag on bed-14.",
        },
    )
    body = calls[0]["body"]
    assert "Reader's own conclusions" not in body
    assert "shared-bed flag bug" not in body


def test_submit_feedback_reader_conclusions_included_when_consented(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake, calls = _fake_create_issue()
    monkeypatch.setattr(server_module, "create_issue", _fake)
    monkeypatch.setattr(server_module, "gh_available", lambda: True)

    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    _preset_config_env(
        monkeypatch,
        tmp_path,
        feedback_text="The gnome export was slow.",
        telemetry_consent={"reader_conclusions": True},
    )
    _call(mcp, "start_config", {})
    _call(mcp, "get_config", {"timeout_s": 1})
    _record_d01_with_finding(mcp)
    _record_d02_all_pass(mcp)

    _call(
        mcp,
        "submit_feedback",
        {
            "report_conclusion": "It found a shared-bed flag bug.",
            "report_fix_first": "The missing flag on bed-14.",
        },
    )
    body = calls[0]["body"]
    assert "Reader's own conclusions" in body
    assert "A1: It found a shared-bed flag bug." in body
    assert "A2: The missing flag on bed-14." in body


def test_submit_feedback_gh_unavailable_returns_mailto_with_encoded_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server_module, "gh_available", lambda: False)

    mcp, _state = build_server(FIXTURE_PACK)
    _configured_run(
        mcp, tmp_path, monkeypatch, feedback_text="The gnome export was slow."
    )

    result = _call(mcp, "submit_feedback", {})
    assert result["mode"] == "mailto"
    assert result["mailto_url"].startswith(
        "mailto:rodneylunt79+audit-feedback@gmail.com?subject="
    )
    assert (
        "The%20gnome%20export%20was%20slow." in result["mailto_url"]
        or "The+gnome" in result["mailto_url"]
    )
    assert "The gnome export was slow." in result["body"]


def test_submit_feedback_filing_failure_falls_back_to_mailto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server_module, "gh_available", lambda: True)

    def _always_fail(repo, title, body, labels):
        raise IssueFilingError("HTTP 500")

    monkeypatch.setattr(server_module, "create_issue", _always_fail)

    mcp, _state = build_server(FIXTURE_PACK)
    _configured_run(
        mcp, tmp_path, monkeypatch, feedback_text="The gnome export was slow."
    )

    result = _call(mcp, "submit_feedback", {})
    assert result["mode"] == "mailto"
    assert "The gnome export was slow." in result["body"]


def test_submit_feedback_after_render_report_still_sends_and_updates_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # AUDIT.md documents feedback as the step after rendering the report, so
    # that order must work: before the fix this raised "No audit run in
    # progress" because render_report cleared the tracker, and the user's
    # feedback was silently dropped.
    _fake, calls = _fake_create_issue()
    monkeypatch.setattr(server_module, "create_issue", _fake)
    monkeypatch.setattr(server_module, "gh_available", lambda: True)

    mcp, _state = build_server(FIXTURE_PACK)
    _configured_run(
        mcp, tmp_path, monkeypatch, feedback_text="The gnome export was slow."
    )
    _record_d01_with_finding(mcp)
    _record_d02_all_pass(mcp)

    report_result = _call(mcp, "render_report", {"finished": "2026-08-09T10:00:00Z"})
    feedback_result = _call(mcp, "submit_feedback", {})

    assert feedback_result["mode"] == "issue"
    assert "The gnome export was slow." in calls[0]["body"]
    assert feedback_result["report_updated"] is True

    # The already-written report and run-state must be rewritten to carry the
    # link, or they claim no feedback was ever sent.
    report_text = Path(report_result["report_path"]).read_text(encoding="utf-8")
    assert feedback_result["url"] in report_text
    restored = RunState.from_json(
        Path(report_result["run_state_path"]).read_text(encoding="utf-8")
    )
    assert restored.feedback_issue_url == feedback_result["url"]


def test_submit_feedback_after_render_report_warns_when_the_report_cannot_be_rewritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The feedback issue is already filed by then, so a rewrite failure must
    # never raise (that would invite a double-file on a retry) and must never
    # pass silently either: one clear warning, with the URL still returned.
    _fake, _calls = _fake_create_issue()
    monkeypatch.setattr(server_module, "create_issue", _fake)
    monkeypatch.setattr(server_module, "gh_available", lambda: True)

    mcp, _state = build_server(FIXTURE_PACK)
    _configured_run(
        mcp, tmp_path, monkeypatch, feedback_text="The gnome export was slow."
    )
    _record_d01_with_finding(mcp)
    _record_d02_all_pass(mcp)
    _call(mcp, "render_report", {"finished": "2026-08-09T10:00:00Z"})

    def _explode(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(server_module, "write_report", _explode)

    result = _call(mcp, "submit_feedback", {})
    assert result["mode"] == "issue"
    assert result["url"]
    assert result["report_updated"] is False
    assert len(result["warnings"]) == 1
    assert "could not be updated" in result["warnings"][0]


def test_submit_feedback_after_begin_run_of_a_new_run_targets_the_new_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A finished run stays reachable for a late submit_feedback, but only
    # until the next run starts: feedback must never be attached to the wrong
    # run's report.
    _fake, _calls = _fake_create_issue()
    monkeypatch.setattr(server_module, "create_issue", _fake)
    monkeypatch.setattr(server_module, "gh_available", lambda: True)

    mcp, _state = build_server(FIXTURE_PACK)
    _configured_run(
        mcp, tmp_path, monkeypatch, feedback_text="The gnome export was slow."
    )
    _record_d01_with_finding(mcp)
    _record_d02_all_pass(mcp)
    first_report = _call(mcp, "render_report", {"finished": "2026-08-09T10:00:00Z"})

    _begin_run(mcp, tmp_path / "audit-output-2", repo_name="second-repo")
    with pytest.raises(ToolError) as excinfo:
        _call(mcp, "submit_feedback", {})
    # The new run has no configuration yet, so this is the new run's error,
    # not the finished run's feedback being resent.
    assert "no configuration yet" in str(excinfo.value)

    report_text = Path(first_report["report_path"]).read_text(encoding="utf-8")
    assert "https://github.com/rodlunt/engineering-audit/issues/1" not in report_text


def test_submit_feedback_without_any_run_still_errors(tmp_path: Path) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    with pytest.raises(ToolError) as excinfo:
        _call(mcp, "submit_feedback", {})
    assert "No audit run in progress" in str(excinfo.value)


def test_submit_feedback_then_render_report_links_the_filed_issue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake, calls = _fake_create_issue()
    monkeypatch.setattr(server_module, "create_issue", _fake)
    monkeypatch.setattr(server_module, "gh_available", lambda: True)

    mcp, _state = build_server(FIXTURE_PACK)
    _configured_run(
        mcp, tmp_path, monkeypatch, feedback_text="The gnome export was slow."
    )
    _record_d01_with_finding(mcp)
    _record_d02_all_pass(mcp)

    feedback_result = _call(mcp, "submit_feedback", {})
    assert feedback_result["mode"] == "issue"

    report_result = _call(mcp, "render_report", {"finished": "2026-08-09T10:00:00Z"})
    report_text = Path(report_result["report_path"]).read_text(encoding="utf-8")
    assert feedback_result["url"] in report_text
    assert 'href="mailto:' not in report_text


# ---------------------------------------------------------------------------
# Crash-safe persistence and resume
# ---------------------------------------------------------------------------


def _progress_file(out_dir: Path) -> Path:
    return out_dir / PROGRESS_FILENAME


def _saved(out_dir: Path):
    return load_run_progress_file(_progress_file(out_dir))


def _interrupted_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, out_dir: Path):
    """Drive a run as far as two recorded domains and then abandon the server,
    exactly as a killed process would: no render_report, nothing flushed at
    the end, only whatever each step wrote as it went."""
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, out_dir)
    _preset_config_env(monkeypatch, tmp_path)
    _call(mcp, "start_config", {})
    _call(mcp, "get_config", {"timeout_s": 1})
    _record_d01_with_finding(mcp)
    _record_d02_all_pass(mcp)
    return mcp


def test_progress_is_saved_at_every_step_of_a_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    out_dir = tmp_path / "audit-output"

    _begin_run(mcp, out_dir)
    # Written before a single domain is audited: an interruption here still
    # leaves a record that a run was started and never finished.
    assert _progress_file(out_dir).is_file()
    assert _saved(out_dir).config is None
    assert _saved(out_dir).completed is False

    _preset_config_env(monkeypatch, tmp_path)
    _call(mcp, "start_config", {})
    _call(mcp, "get_config", {"timeout_s": 1})
    saved_config = _saved(out_dir).config
    assert saved_config is not None
    assert saved_config.selected_domain_ids == ["d01", "d02"]

    _record_d01_with_finding(mcp)
    assert list(_saved(out_dir).domain_results) == ["d01"]
    assert _saved(out_dir).domain_results["d01"].findings[0].rule_id == "D01-R02"

    _record_d02_all_pass(mcp)
    assert list(_saved(out_dir).domain_results) == ["d01", "d02"]


def test_render_report_removes_the_recovery_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_dir = tmp_path / "audit-output"
    mcp = _interrupted_run(tmp_path, monkeypatch, out_dir)

    _call(mcp, "render_report", {"finished": "2026-08-09T10:00:00Z"})

    assert not _progress_file(out_dir).exists()
    # And a later begin_run on the same directory starts clean rather than
    # offering to resume a run that is already finished.
    fresh, _state = build_server(FIXTURE_PACK)
    assert _begin_run(fresh, out_dir)["run_started"] is True


def test_a_completed_recovery_file_that_survived_removal_is_not_offered_as_resumable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Belt and braces for the unlink failing (a read-only directory, a backup
    # restored later): the record says completed, so it is never resumable.
    out_dir = tmp_path / "audit-output"
    mcp = _interrupted_run(tmp_path, monkeypatch, out_dir)
    _call(mcp, "render_report", {"finished": "2026-08-09T10:00:00Z"})

    finished_record = json.loads(
        (out_dir / "run-state.json").read_text(encoding="utf-8")
    )
    _progress_file(out_dir).write_text(
        json.dumps({**finished_record, "completed": True}), encoding="utf-8"
    )

    fresh, _state = build_server(FIXTURE_PACK)
    assert _begin_run(fresh, out_dir)["run_started"] is True


def test_a_fresh_server_finds_the_unfinished_run_with_every_recorded_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The crash: server one dies after two domains, and a completely separate
    # server process is pointed at the same output directory.
    out_dir = tmp_path / "audit-output"
    _interrupted_run(tmp_path, monkeypatch, out_dir)

    mcp, _state = build_server(FIXTURE_PACK)
    offer = _begin_run(mcp, out_dir)

    assert offer["run_started"] is False
    assert offer["resumable"] is True
    # No "meta" key: an agent that reads this response as a started run gets a
    # KeyError rather than a plausible run that does not exist.
    assert "meta" not in offer
    prior = offer["prior_run"]
    assert prior["repo_name"] == "widgets-app"
    assert prior["recorded_domain_ids"] == ["d01", "d02"]
    assert prior["missing_domain_ids"] == []
    assert prior["finding_count"] == 1
    assert "resume=True" in offer["instruction"]

    # Nothing was started by the offer itself.
    with pytest.raises(ToolError, match="No audit run in progress"):
        _call(mcp, "run_status", {})


def test_resume_true_continues_the_run_through_to_a_rendered_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_dir = tmp_path / "audit-output"
    _interrupted_run(tmp_path, monkeypatch, out_dir)

    mcp, _state = build_server(FIXTURE_PACK)
    resumed = _begin_run(mcp, out_dir, resume=True)

    assert resumed["resumed"] is True
    assert resumed["recorded_domain_ids"] == ["d01", "d02"]
    assert resumed["missing_domain_ids"] == []
    assert resumed["selected_domain_ids"] == ["d01", "d02"]
    assert resumed["meta"]["started"] == "2026-08-09T09:00:00Z"

    # The configuration came back with it: no start_config, no second config
    # page, straight on to finishing the run.
    status = _call(mcp, "run_status", {})
    assert status["missing_domain_ids"] == []
    assert status["finding_count"] == 1

    result = _call(mcp, "render_report", {"finished": "2026-08-09T10:00:00Z"})
    report_text = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "Two gnomes share bed-14 without the shared-bed flag" in report_text
    assert result["findings_summary"]["total_findings"] == 1


def test_resume_true_after_an_interruption_before_the_first_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Interrupted between begin_run and the first result: there is nothing to
    # recover but the run's own metadata, and the response has to say so
    # rather than implying results were recovered.
    out_dir = tmp_path / "audit-output"
    first, _state = build_server(FIXTURE_PACK)
    _begin_run(first, out_dir)

    mcp, _state2 = build_server(FIXTURE_PACK)
    resumed = _begin_run(mcp, out_dir, resume=True)

    assert resumed["resumed"] is True
    assert resumed["recorded_domain_ids"] == []
    assert resumed["config"] is None
    assert "start_config" in resumed["instruction"]

    # start_config still works: the resumed run did not inherit a config mode
    # pointing at a config page served by the process that died.
    _preset_config_env(monkeypatch, tmp_path)
    assert _call(mcp, "start_config", {})["mode"] == "preset"


def test_resume_false_starts_fresh_and_reports_what_it_discarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_dir = tmp_path / "audit-output"
    _interrupted_run(tmp_path, monkeypatch, out_dir)

    mcp, _state = build_server(FIXTURE_PACK)
    result = _begin_run(mcp, out_dir, resume=False)

    assert result["run_started"] is True
    assert result["resumed"] is False
    # A discard the user chose is still a discard they should be told about.
    assert result["discarded_prior_run"]["recorded_domain_ids"] == ["d01", "d02"]
    # The saved state is now this new, empty run.
    assert _saved(out_dir).domain_results == {}
    assert _saved(out_dir).config is None


def test_replace_true_counts_as_declining_the_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # replace=True is already an explicit "throw away what is in progress";
    # asking a second time would be asking a question the caller answered.
    out_dir = tmp_path / "audit-output"
    mcp = _interrupted_run(tmp_path, monkeypatch, out_dir)

    result = _begin_run(mcp, out_dir, replace=True)
    assert result["run_started"] is True
    assert result["resumed"] is False
    assert result["discarded_prior_run"]["recorded_domain_ids"] == ["d01", "d02"]


def test_a_prior_run_for_a_different_repository_is_never_resumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_dir = tmp_path / "audit-output"
    _interrupted_run(tmp_path, monkeypatch, out_dir)

    mcp, _state = build_server(FIXTURE_PACK)
    offer = _begin_run(mcp, out_dir, repo_name="somebody-elses-repo")

    assert offer["run_started"] is False
    assert offer["resumable"] is False
    assert "widgets-app" in offer["reason"]
    assert "somebody-elses-repo" in offer["reason"]

    # And asking for it outright is refused, not quietly honoured.
    with pytest.raises(ToolError) as excinfo:
        _begin_run(mcp, out_dir, repo_name="somebody-elses-repo", resume=True)
    assert "widgets-app" in str(excinfo.value)
    assert "somebody-elses-repo" in str(excinfo.value)

    # The other repository's work is still there, untouched by either call.
    assert list(_saved(out_dir).domain_results) == ["d01", "d02"]


def test_corrupt_recovery_state_is_reported_loudly_not_treated_as_no_prior_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_dir = tmp_path / "audit-output"
    _interrupted_run(tmp_path, monkeypatch, out_dir)
    _progress_file(out_dir).write_text(
        "{ this file was truncated mid-", encoding="utf-8"
    )

    mcp, _state = build_server(FIXTURE_PACK)
    offer = _begin_run(mcp, out_dir)

    assert offer["run_started"] is False
    assert offer["resumable"] is False
    assert offer["prior_run"]["readable"] is False
    assert "run-progress file" in offer["reason"]
    # Nothing was started, so this cannot pass for a fresh run that simply
    # found nothing to resume.
    with pytest.raises(ToolError, match="No audit run in progress"):
        _call(mcp, "run_status", {})

    with pytest.raises(ToolError) as excinfo:
        _begin_run(mcp, out_dir, resume=True)
    assert "Cannot resume" in str(excinfo.value)


def test_resume_true_with_nothing_saved_errors_rather_than_starting_quietly(
    tmp_path: Path,
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    with pytest.raises(ToolError) as excinfo:
        _begin_run(mcp, tmp_path / "audit-output", resume=True)
    assert "no unfinished run saved" in str(excinfo.value)


def test_resume_warns_when_the_repository_has_moved_on_a_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Legitimate mid-audit, so it warns rather than refusing, but the results
    # already recorded were reached against the old commit and the report will
    # keep saying so.
    out_dir = tmp_path / "audit-output"
    _interrupted_run(tmp_path, monkeypatch, out_dir)

    mcp, _state = build_server(FIXTURE_PACK)
    resumed = _begin_run(mcp, out_dir, resume=True, repo_commit="9999999")

    assert resumed["resumed"] is True
    assert resumed["meta"]["repo_commit"] == "abc1234"
    assert any("abc1234" in w and "9999999" in w for w in resumed["warnings"])


def test_resume_by_a_different_model_records_both_and_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The saved pair produced the results already in the file; this call's pair
    # will produce the rest. Both are true of the finished run, so the report
    # has to be able to name both. Keeping only the saved pair was #93: the
    # caller handed in the right answer on every resume and it was discarded
    # silently, so the provenance header credited whoever started the run.
    out_dir = tmp_path / "audit-output"
    _interrupted_run(tmp_path, monkeypatch, out_dir)

    mcp, _state = build_server(FIXTURE_PACK)
    resumed = _begin_run(
        mcp, out_dir, resume=True, assistant="codex", model="gpt-5.6-sol"
    )

    assert resumed["resumed"] is True
    assert resumed["meta"]["assistant"] == "codex"
    assert resumed["meta"]["model"] == "gpt-5.6-sol"
    assert resumed["meta"]["earlier_contributors"] == ["claude-code/claude-sonnet-5"]
    assert any(
        "claude-code/claude-sonnet-5" in w and "codex/gpt-5.6-sol" in w
        for w in resumed["warnings"]
    )


def test_resume_by_the_same_model_records_no_earlier_contributor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The ordinary case. Nothing changed hands, so the header must not sprout a
    # contributors row implying it did.
    out_dir = tmp_path / "audit-output"
    _interrupted_run(tmp_path, monkeypatch, out_dir)

    mcp, _state = build_server(FIXTURE_PACK)
    resumed = _begin_run(mcp, out_dir, resume=True)

    assert resumed["meta"]["earlier_contributors"] == []
    assert not any("previous contributor" in w for w in resumed["warnings"])


def test_resume_keeps_the_original_server_started_rather_than_restamping_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # server_started names when this run's story began, the same way
    # 'started' does (and 'started' is already kept unchanged across a
    # resume; see _resume_run). Restamping it to the moment of the resume
    # would make the server-measured duration only ever cover the resumed
    # portion, silently disagreeing with an assistant that honestly reports
    # the whole run's span from the original start.
    out_dir = tmp_path / "audit-output"
    monkeypatch.setattr(server_module, "_now_utc_iso", lambda: "2026-08-09T09:00:01Z")
    _interrupted_run(tmp_path, monkeypatch, out_dir)

    mcp, _state = build_server(FIXTURE_PACK)
    monkeypatch.setattr(server_module, "_now_utc_iso", lambda: "2026-08-09T11:30:00Z")
    resumed = _begin_run(mcp, out_dir, resume=True)
    assert resumed["meta"]["server_started"] == "2026-08-09T09:00:01Z"

    monkeypatch.setattr(server_module, "_now_utc_iso", lambda: "2026-08-09T11:45:00Z")
    result = _call(mcp, "render_report", {"finished": "2026-08-09T11:45:00Z"})
    restored = RunState.from_json(
        Path(result["run_state_path"]).read_text(encoding="utf-8")
    )

    # Kept from the original begin_run, not the resume, and not the render.
    assert restored.meta.server_started == "2026-08-09T09:00:01Z"
    assert restored.meta.server_finished == "2026-08-09T11:45:00Z"


def test_resume_of_a_run_that_predates_server_timestamps_leaves_server_started_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A run-progress file saved before this fix has no server_started at
    # all. Resuming it must not backfill one from "now": that would only
    # measure the resumed portion, not the whole run, and comparing it
    # against the assistant's full-span duration would manufacture a false
    # disagreement out of nothing but the resume itself. Leaving it unset
    # forever for this run is the honest answer: it really was never measured.
    out_dir = tmp_path / "audit-output"
    _interrupted_run(tmp_path, monkeypatch, out_dir)
    progress_path = out_dir / PROGRESS_FILENAME
    raw = json.loads(progress_path.read_text(encoding="utf-8"))
    raw["meta"]["server_started"] = None
    progress_path.write_text(json.dumps(raw), encoding="utf-8")

    mcp, _state = build_server(FIXTURE_PACK)
    monkeypatch.setattr(server_module, "_now_utc_iso", lambda: "2026-08-09T11:30:00Z")
    resumed = _begin_run(mcp, out_dir, resume=True)

    assert resumed["meta"]["server_started"] is None


def test_resume_refuses_when_the_rules_pack_no_longer_has_a_selected_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A resumed run has to be completable against the pack loaded now; if it
    # is not, saying so beats recording half of it against different rules.
    out_dir = tmp_path / "audit-output"
    _interrupted_run(tmp_path, monkeypatch, out_dir)

    trimmed_pack = tmp_path / "trimmed-pack"
    shutil.copytree(FIXTURE_PACK, trimmed_pack)
    for stale in trimmed_pack.glob("02-*.md"):
        stale.unlink()

    mcp, _state = build_server(trimmed_pack)
    with pytest.raises(ToolError) as excinfo:
        _begin_run(mcp, out_dir, resume=True)
    assert "d02" in str(excinfo.value)
    assert "resume=False" in str(excinfo.value)


def test_filed_issue_urls_are_saved_as_they_are_filed_so_a_resume_cannot_double_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Duplicate issues on the user's own repository are the one failure here
    # that cannot be undone from this side, so the urls are saved per issue,
    # not once at the end of the call.
    _fake, _calls = _fake_create_issue()
    monkeypatch.setattr(server_module, "create_issue", _fake)

    mcp, _state = build_server(FIXTURE_PACK)
    out_dir = tmp_path / "audit-output"
    _configured_github_run(mcp, tmp_path, monkeypatch)
    _record_d01_with_finding(mcp)
    _call(mcp, "file_issues", {"confirm": True, "repo": "acme/widgets"})

    saved = _saved(out_dir)
    assert list(saved.filed_issues) == ["D01-R02#1"]

    resumed_mcp, _state2 = build_server(FIXTURE_PACK)
    _begin_run(resumed_mcp, out_dir, resume=True)
    preview = _call(resumed_mcp, "file_issues", {})
    assert preview["count"] == 0


def test_a_save_failure_warns_once_and_never_stops_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _configured_run(mcp, tmp_path, monkeypatch)

    def _explode(*_args, **_kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr(server_module, "save_run_progress", _explode)

    first = _record_d01_with_finding(mcp)
    assert first["finding_count"] == 1  # the result is still recorded
    assert len(first["warnings"]) == 1
    assert "crash-recovery" in first["warnings"][0]
    assert "no space left on device" in first["warnings"][0]

    # Same fact, second failure: said once, then remembered as said, because a
    # line repeated on every response is a line that stops being read.
    second = _record_d02_all_pass(mcp)
    assert "warnings" not in second

    # And the run still finishes and produces its real output.
    result = _call(mcp, "render_report", {"finished": "2026-08-09T10:00:00Z"})
    assert Path(result["report_path"]).is_file()


def test_an_interrupted_save_leaves_the_previous_saved_state_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Atomicity through the real path, not just the writer's own unit test: a
    # rename that fails mid-run must leave the last good state, never a
    # truncated file that a later resume would read as a shorter run.
    mcp, _state = build_server(FIXTURE_PACK)
    out_dir = tmp_path / "audit-output"
    _configured_run(mcp, tmp_path, monkeypatch)
    _record_d01_with_finding(mcp)

    def _explode(_src, _dst):
        raise OSError("interrupted")

    monkeypatch.setattr(io_module.os, "replace", _explode)
    result = _record_d02_all_pass(mcp)
    assert len(result["warnings"]) == 1

    saved = _saved(out_dir)
    assert list(saved.domain_results) == ["d01"]
    assert saved.domain_results["d01"].findings[0].rule_id == "D01-R02"


# ---------------------------------------------------------------------------
# Recording which domains had their rules fetched (issue #110)
# ---------------------------------------------------------------------------


def _record_d01_all_pass_without_fetching(mcp, replace: bool = False) -> dict:
    """Record every rule in d01 as a pass WITHOUT calling get_domain first.

    The rule ids come from the pack on disk, not from the tool, which is
    exactly the shape of the failure this probe exists for: an agent that
    produces a full set of verdicts for a domain whose rule text never passed
    through the server."""
    result = {
        "domain_id": "d01",
        "status": "completed",
        "rule_verdicts": _all_pass_verdicts(_domain(mcp, "d01")),
    }
    return _call(mcp, "record_domain_result", {"result": result, "replace": replace})


def test_get_domain_is_recorded_against_the_run_and_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    out_dir = tmp_path / "audit-output"
    _configured_run(mcp, tmp_path, monkeypatch)

    assert _saved(out_dir).rules_fetched_domain_ids == []

    _fetch_domain(mcp, "d01")

    # Saved at the fetch, not deferred to the next recorded result: a server
    # killed in between must not come back saying the rules were never asked
    # for.
    assert _saved(out_dir).rules_fetched_domain_ids == ["d01"]


def test_a_recorded_result_says_whether_its_rules_were_fetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _configured_run(mcp, tmp_path, monkeypatch)

    fetched = _record_d01_with_finding(mcp)
    assert fetched["rules_fetched"] is True
    assert "warnings" not in fetched


def test_verdicts_for_a_domain_that_was_never_fetched_are_recorded_and_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Recorded, not refused. Refusing is trivially satisfied by fetching the
    # text and ignoring it, which would destroy the signal; recording keeps
    # both the verdicts and the evidence that nothing supported them.
    mcp, _state = build_server(FIXTURE_PACK)
    out_dir = tmp_path / "audit-output"
    _configured_run(mcp, tmp_path, monkeypatch)

    response = _record_d01_all_pass_without_fetching(mcp)

    assert response["status"] == "completed"
    assert response["rules_fetched"] is False
    assert len(response["warnings"]) == 1
    warning = response["warnings"][0]
    assert "get_domain('d01') was never called" in warning
    assert "replace=True" in warning

    # The result is on disk, and so is the fact that nothing was fetched.
    saved = _saved(out_dir)
    assert list(saved.domain_results) == ["d01"]
    assert saved.rules_fetched_domain_ids == []


def test_a_could_not_run_domain_is_not_warned_about(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A could-not-run result carries no verdicts by construction, so there is
    # nothing it could have reached without the rules. Warning here would be
    # noise, and noise is what teaches an agent to skip the field.
    mcp, _state = build_server(FIXTURE_PACK)
    _configured_run(mcp, tmp_path, monkeypatch)

    response = _call(
        mcp,
        "record_domain_result",
        {
            "result": {
                "domain_id": "d01",
                "status": "could-not-run",
                "reason": "no ledger file",
            }
        },
    )

    assert response["rules_fetched"] is False
    assert "warnings" not in response


def test_a_fetch_before_begin_run_belongs_to_no_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Documented in get_domain: a fetch made with no run in progress is not
    # attributed to the next one. Attributing it would let one run's fetch
    # vouch for another run's verdicts.
    mcp, _state = build_server(FIXTURE_PACK)
    _fetch_domain(mcp, "d01")
    _configured_run(mcp, tmp_path, monkeypatch)

    assert _record_d01_all_pass_without_fetching(mcp)["rules_fetched"] is False


def test_the_finished_run_state_carries_the_fetched_domains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Persisted into run-state.json, not just the recovery file: a report
    # re-rendered from it months later still carries the signal.
    mcp, _state = build_server(FIXTURE_PACK)
    _configured_run(mcp, tmp_path, monkeypatch)
    _record_d01_with_finding(mcp)
    _record_d02_all_pass(mcp)

    result = _call(mcp, "render_report", {"finished": "2026-08-09T10:00:00Z"})

    saved_state = json.loads(Path(result["run_state_path"]).read_text(encoding="utf-8"))
    assert saved_state["rules_fetched_domain_ids"] == ["d01", "d02"]
    assert saved_state["rules_fetch_unknown_domain_ids"] == []
    # No schema bump came with the field: an older reader can ignore it and
    # still render every report it renders today.
    assert saved_state["schema_version"] == 4


def test_render_report_hands_back_the_domains_whose_rules_were_never_fetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The report names them, but the report is a file the agent hands over;
    # this response is what the agent reads. A signal that lives only in the
    # HTML is one the user hears about only if they open it.
    mcp, _state = build_server(FIXTURE_PACK)
    _configured_run(mcp, tmp_path, monkeypatch)
    _record_d01_all_pass_without_fetching(mcp)
    _record_d02_all_pass(mcp)

    result = _call(mcp, "render_report", {"finished": "2026-08-09T10:00:00Z"})

    assert result["rules_fetched"] == {
        "fetched_domain_ids": ["d02"],
        "verdicts_without_rules_fetched_domain_ids": ["d01"],
        "fetch_not_recorded_domain_ids": [],
    }
    assert any(
        "without their rule text ever being fetched" in w for w in result["warnings"]
    )


def test_a_resumed_run_keeps_the_fetches_made_before_the_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The hard case. Which domains were fetched is a fact about the run, not
    # about the process: an agent that fetched d01 before the crash and records
    # it after must not be accused of skipping the rules it read, and must not
    # be made to fetch them twice.
    out_dir = tmp_path / "audit-output"
    first, _state = build_server(FIXTURE_PACK)
    _begin_run(first, out_dir)
    _preset_config_env(monkeypatch, tmp_path)
    _call(first, "start_config", {})
    _call(first, "get_config", {"timeout_s": 1})
    _fetch_domain(first, "d01")
    # ...and the server dies here, before d01's result was ever recorded.

    resumed, _state2 = build_server(FIXTURE_PACK)
    _begin_run(resumed, out_dir, resume=True)

    recorded = _record_d01_all_pass_without_fetching(resumed)
    assert recorded["rules_fetched"] is True
    assert "warnings" not in recorded

    # A domain nobody ever fetched is still judged on its own evidence: the
    # resume restores what happened, it does not vouch for the whole run.
    d02 = {
        "domain_id": "d02",
        "status": "completed",
        "rule_verdicts": _all_pass_verdicts(_domain(resumed, "d02")),
    }
    unfetched = _call(resumed, "record_domain_result", {"result": d02})
    assert unfetched["rules_fetched"] is False


def _strip_fetch_fields_from_progress(out_dir: Path) -> None:
    """Rewrite the saved recovery file the way a build from before issue #110
    wrote it: with no record of fetching at all."""
    path = _progress_file(out_dir)
    saved = json.loads(path.read_text(encoding="utf-8"))
    del saved["rules_fetched_domain_ids"]
    del saved["rules_fetch_unknown_domain_ids"]
    path.write_text(json.dumps(saved), encoding="utf-8")


def test_resuming_a_record_written_before_this_existed_reports_unknown_not_a_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An older record never recorded this, so its already-recorded domains are
    # unknown. Unknown must not be laundered into "fetched" (which would clear
    # a run nobody can vouch for) and must not be reported as "not fetched"
    # (which would accuse a run that may well have done the work).
    out_dir = tmp_path / "audit-output"
    _interrupted_run(tmp_path, monkeypatch, out_dir)
    _strip_fetch_fields_from_progress(out_dir)

    mcp, _state = build_server(FIXTURE_PACK)
    resumed = _begin_run(mcp, out_dir, resume=True)

    assert resumed["resumed"] is True
    assert any("predates the record" in w for w in resumed["warnings"])

    saved = _saved(out_dir)
    assert saved.rules_fetched_domain_ids == []
    assert saved.rules_fetch_unknown_domain_ids == ["d01", "d02"]

    # Re-recording one of those carried-in domains reports unknown, not a
    # verdict in either direction.
    unknown = _record_d01_all_pass_without_fetching(mcp, replace=True)
    assert unknown["rules_fetched"] is None
    assert any("not recorded" in w for w in unknown["warnings"])


def test_a_legacy_resume_still_judges_domains_audited_after_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Unknown is confined to what was carried in. Recording starts at the
    # resume, so a domain audited afterwards cannot hide behind the old
    # record's silence, and a fetch made afterwards settles the question for
    # good.
    out_dir = tmp_path / "audit-output"
    _interrupted_run(tmp_path, monkeypatch, out_dir)
    _strip_fetch_fields_from_progress(out_dir)

    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, out_dir, resume=True)

    fetched_now = _record_d01_with_finding(mcp, replace=True)
    assert fetched_now["rules_fetched"] is True
    saved = _saved(out_dir)
    assert saved.rules_fetched_domain_ids == ["d01"]
    assert saved.rules_fetch_unknown_domain_ids == ["d02"]


# ---------------------------------------------------------------------------
# Issue #155: the legacy not-applicable relaxation must survive a resave
# ---------------------------------------------------------------------------


def _rewrite_progress_with_legacy_not_applicable(
    out_dir: Path, schema_version: int
) -> None:
    """Rewrite the saved recovery file to look like one a pre-#100 build
    wrote: a not-applicable verdict with no note at all, at a schema_version
    below the one that started requiring it (NOT_APPLICABLE_NOTE_SCHEMA_VERSION,
    currently 4). d02's one recorded verdict is repurposed for this, the same
    way _strip_fetch_fields_from_progress repurposes the saved file above for
    its own pre-#110 scenario."""
    path = _progress_file(out_dir)
    saved = json.loads(path.read_text(encoding="utf-8"))
    d02_verdict = saved["domain_results"]["d02"]["rule_verdicts"][0]
    d02_verdict["verdict"] = "not-applicable"
    d02_verdict["note"] = None
    saved["schema_version"] = schema_version
    path.write_text(json.dumps(saved), encoding="utf-8")


def test_a_legacy_run_survives_two_interruptions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #155. A run-progress file written before #100 tolerates an
    unjustified not-applicable verdict on load, because it declares a
    schema_version below NOT_APPLICABLE_NOTE_SCHEMA_VERSION.

    The defect this guards against: _resume_run immediately re-persists the
    resumed run via a freshly built RunProgress (_run_progress), not a
    re-serialisation of what was loaded. Before this fix, that fresh object
    always declared the CURRENT schema_version regardless of what was
    loaded, so the file on disk now claimed to be fully compliant while
    still holding the unjustified verdict its own declared version says
    should not exist. A second resume then loaded that file at face value
    (version 4, no relaxation) and failed with a ValidationError, making the
    run permanently unresumable after exactly one more interruption.
    """
    out_dir = tmp_path / "audit-output"
    _interrupted_run(tmp_path, monkeypatch, out_dir)
    _rewrite_progress_with_legacy_not_applicable(out_dir, schema_version=3)

    # First interruption's resume: this already works today, and must keep
    # working. The point under test is what it leaves on disk afterwards.
    first_resume_server, _s1 = build_server(FIXTURE_PACK)
    first = _begin_run(first_resume_server, out_dir, resume=True)
    assert first["resumed"] is True

    saved_after_first_resume = json.loads(
        _progress_file(out_dir).read_text(encoding="utf-8")
    )
    # The file still holds the unjustified verdict, so it must still say so.
    assert saved_after_first_resume["schema_version"] == 3

    # Second interruption: resume again from exactly what the first resume
    # wrote. Before the fix this raises ToolError wrapping a ValidationError
    # ("verdict is not-applicable but no note (reason) was given"), because
    # the file now (wrongly) declared schema_version 4.
    second_resume_server, _s2 = build_server(FIXTURE_PACK)
    second = _begin_run(second_resume_server, out_dir, resume=True)
    assert second["resumed"] is True

    # The run can still be finished and re-rendered afterwards.
    result = _call(
        second_resume_server, "render_report", {"finished": "2026-08-09T10:00:00Z"}
    )
    assert Path(result["report_path"]).is_file()


def test_record_domain_result_still_rejects_a_fresh_unjustified_not_applicable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The legacy relaxation this fix carries through resumes and resaves
    must not become a general escape hatch. A verdict recorded now, not
    carried in from an old file, still needs its reason (issue #100)."""
    out_dir = tmp_path / "audit-output"
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, out_dir)
    _preset_config_env(monkeypatch, tmp_path)
    _call(mcp, "start_config", {})
    _call(mcp, "get_config", {"timeout_s": 1})
    _fetch_domain(mcp, "d01")

    verdicts = _all_pass_verdicts(_domain(mcp, "d01"))
    verdicts[0] = {"rule_id": verdicts[0]["rule_id"], "verdict": "not-applicable"}
    result = {"domain_id": "d01", "status": "completed", "rule_verdicts": verdicts}

    with pytest.raises(ToolError, match="not-applicable"):
        _call(mcp, "record_domain_result", {"result": result})
