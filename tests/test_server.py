"""Tests for the MCP server (src/engineering_audit/server.py).

Tool calls are exercised through MCPServer's own in-process call_tool(), which
runs the tool exactly as the MCP protocol would (argument validation,
structured-content wrapping, error wrapping) without needing a real stdio
transport or a separate client process.
"""

from __future__ import annotations

import asyncio
import json
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from mcp.server._otel import OpenTelemetryMiddleware

import engineering_audit.server as server_module
from engineering_audit.issues import CreatedIssue, IssueFilingError
from engineering_audit.rules import RulesPackError
from engineering_audit.schema import RunState
from engineering_audit.server import AppState, _resolve_rules_dir, build_server

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


def _record_d01_with_finding(mcp, replace: bool = False) -> dict:
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


def _preset_config_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **overrides) -> Path:
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
    assert not any(type(m).__name__ == "OpenTelemetryMiddleware" for m in mcp.middleware)

    result = _call(mcp, "list_domains", {})
    assert [d["id"] for d in result["domains"]] == ["d01", "d02"]


def test_resolve_rules_dir_from_argv_flag(tmp_path: Path) -> None:
    resolved = _resolve_rules_dir(["--rules-dir", str(tmp_path)])
    assert resolved == tmp_path


def test_resolve_rules_dir_from_argv_equals_form(tmp_path: Path) -> None:
    resolved = _resolve_rules_dir([f"--rules-dir={tmp_path}"])
    assert resolved == tmp_path


def test_resolve_rules_dir_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


# ---------------------------------------------------------------------------
# begin_run
# ---------------------------------------------------------------------------


def test_begin_run_creates_the_output_directory_and_returns_meta(tmp_path: Path) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    out_dir = tmp_path / "audit-output"
    result = _begin_run(mcp, out_dir)

    assert result["meta"]["repo_name"] == "widgets-app"
    assert result["meta"]["rules_pack_name"] == FIXTURE_PACK.name
    assert result["meta"]["finished"] is None
    assert out_dir.is_dir()


def test_begin_run_twice_without_finishing_is_rejected(tmp_path: Path) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    with pytest.raises(ToolError) as excinfo:
        _begin_run(mcp, tmp_path / "audit-output-2")
    assert "already in progress" in str(excinfo.value)


def test_begin_run_replace_discards_the_in_progress_run(tmp_path: Path) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output", repo_name="first-repo")
    result = _begin_run(mcp, tmp_path / "audit-output-2", repo_name="second-repo", replace=True)
    assert result["meta"]["repo_name"] == "second-repo"


def test_begin_run_defaults_tool_version_when_omitted(tmp_path: Path) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    result = _begin_run(mcp, tmp_path / "audit-output")
    assert result["meta"]["tool_version"]  # non-empty, package version or dev placeholder


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
    monkeypatch.setenv("ENGINEERING_AUDIT_CONFIG", str(tmp_path / "does-not-exist.json"))

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


def test_start_config_interactive_path_returns_a_url(tmp_path: Path) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")

    result = _call(mcp, "start_config", {})
    assert result["mode"] == "interactive"
    assert result["url"].startswith("http://127.0.0.1:")


def test_get_config_interactive_path_blocks_then_returns_after_form_post(tmp_path: Path) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    started = _call(mcp, "start_config", {})
    url = started["url"]

    payload = urlencode({"domain": ["d01", "d02"], "issue_mode": "report"}, doseq=True).encode(
        "utf-8"
    )
    request = urllib.request.Request(url + "submit", data=payload, method="POST")
    with urllib.request.urlopen(request, timeout=5) as resp:
        assert resp.status == 200

    result = _call(mcp, "get_config", {"timeout_s": 5})
    assert result["mode"] == "interactive"
    assert sorted(result["selected_domain_ids"]) == ["d01", "d02"]


def test_get_config_interactive_timeout_surfaces_as_a_clear_tool_error(tmp_path: Path) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _begin_run(mcp, tmp_path / "audit-output")
    started = _call(mcp, "start_config", {})

    with pytest.raises(ToolError) as excinfo:
        _call(mcp, "get_config", {"timeout_s": 0.2})
    message = str(excinfo.value)
    assert "No configuration submitted" in message
    assert started["url"] in message


# ---------------------------------------------------------------------------
# record_domain_result
# ---------------------------------------------------------------------------


def _configured_run(mcp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **config_overrides) -> None:
    _begin_run(mcp, tmp_path / "audit-output")
    _preset_config_env(monkeypatch, tmp_path, **config_overrides)
    _call(mcp, "start_config", {})
    _call(mcp, "get_config", {"timeout_s": 1})


def test_record_domain_result_rejects_a_domain_not_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _state = build_server(FIXTURE_PACK)
    _configured_run(mcp, tmp_path, monkeypatch, selected_domain_ids=["d01"])

    result = {"domain_id": "d02", "status": "completed", "rule_verdicts": _all_pass_verdicts(_domain(mcp, "d02"))}
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
    result = {"domain_id": "d01", "status": "completed", "rule_verdicts": incomplete_verdicts}
    with pytest.raises(ToolError) as excinfo:
        _call(mcp, "record_domain_result", {"result": result})
    assert "D01-R04" in str(excinfo.value)


def test_record_domain_result_replace_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
        {"result": {"domain_id": "d01", "status": "could-not-run", "reason": "no ledger file present"}},
    )
    assert result["status"] == "could-not-run"
    assert result["finding_count"] == 0


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
        _call(mcp, "get_domain", {"domain_id": domain_id})  # the agent reads the rule text
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


def _fake_create_issue(fail_on: set[str] | None = None, warn_on: set[str] | None = None):
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
        warnings = [f"label(s) {labels} not found on repo {repo}; issue filed without them"] if (
            warn_on and title in warn_on
        ) else []
        return CreatedIssue(url=f"https://github.com/{repo}/issues/{counter['n']}", warnings=warnings)

    return _fake, calls


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


def test_file_issues_preview_never_invokes_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert result["filed"] == {"D01-R02": "https://github.com/rodlunt/widgets-app/issues/1"}
    assert calls == [
        {
            "repo": "rodlunt/widgets-app",
            "title": "Set shared-bed flag for bed-14",
            "body": (
                "bed-14 has two occupants and no shared-bed flag.\n\n"
                "Found by an engineering-practice audit (rule D01-R02, severity high, "
                "at ledger/beds.py:42). Reference: invented for test fixtures only, "
                "no external source"
            ),
            "labels": ["engineering-audit"],
        }
    ]


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


def test_file_issues_issue_mode_report_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    retry_result = _call(mcp, "file_issues", {"confirm": True, "repo": "rodlunt/widgets-app"})
    assert retry_result["filed"] == {"D02-R01": "https://github.com/rodlunt/widgets-app/issues/1"}
    assert [c["title"] for c in calls2] == ["A d02 finding"]


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


def test_file_issues_confirm_detects_repo_from_repo_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audited_repo = tmp_path / "audited-repo"
    audited_repo.mkdir()
    _fake, calls = _fake_create_issue()
    monkeypatch.setattr(server_module, "create_issue", _fake)
    monkeypatch.setattr(server_module, "gh_available", lambda: True)
    monkeypatch.setattr(server_module, "detect_repo", lambda cwd: "rodlunt/detected-repo")

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

    result = _call(mcp, "submit_feedback", {"extra_text": "This came from the agent, not the form."})
    assert result["mode"] == "issue"
    assert "This came from the agent, not the form." in calls[0]["body"]


def test_submit_feedback_files_to_feedback_repo_with_feedback_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake, calls = _fake_create_issue()
    monkeypatch.setattr(server_module, "create_issue", _fake)
    monkeypatch.setattr(server_module, "gh_available", lambda: True)

    mcp, _state = build_server(FIXTURE_PACK)
    _configured_run(mcp, tmp_path, monkeypatch, feedback_text="The gnome export was slow.")

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


def test_submit_feedback_gh_unavailable_returns_mailto_with_encoded_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server_module, "gh_available", lambda: False)

    mcp, _state = build_server(FIXTURE_PACK)
    _configured_run(mcp, tmp_path, monkeypatch, feedback_text="The gnome export was slow.")

    result = _call(mcp, "submit_feedback", {})
    assert result["mode"] == "mailto"
    assert result["mailto_url"].startswith("mailto:rodneylunt79@gmail.com?subject=")
    assert "The%20gnome%20export%20was%20slow." in result["mailto_url"] or "The+gnome" in result["mailto_url"]
    assert "The gnome export was slow." in result["body"]


def test_submit_feedback_filing_failure_falls_back_to_mailto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server_module, "gh_available", lambda: True)

    def _always_fail(repo, title, body, labels):
        raise IssueFilingError("HTTP 500")

    monkeypatch.setattr(server_module, "create_issue", _always_fail)

    mcp, _state = build_server(FIXTURE_PACK)
    _configured_run(mcp, tmp_path, monkeypatch, feedback_text="The gnome export was slow.")

    result = _call(mcp, "submit_feedback", {})
    assert result["mode"] == "mailto"
    assert "The gnome export was slow." in result["body"]


def test_submit_feedback_then_render_report_links_the_filed_issue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake, calls = _fake_create_issue()
    monkeypatch.setattr(server_module, "create_issue", _fake)
    monkeypatch.setattr(server_module, "gh_available", lambda: True)

    mcp, _state = build_server(FIXTURE_PACK)
    _configured_run(mcp, tmp_path, monkeypatch, feedback_text="The gnome export was slow.")
    _record_d01_with_finding(mcp)
    _record_d02_all_pass(mcp)

    feedback_result = _call(mcp, "submit_feedback", {})
    assert feedback_result["mode"] == "issue"

    report_result = _call(mcp, "render_report", {"finished": "2026-08-09T10:00:00Z"})
    report_text = Path(report_result["report_path"]).read_text(encoding="utf-8")
    assert feedback_result["url"] in report_text
    assert 'href="mailto:' not in report_text
