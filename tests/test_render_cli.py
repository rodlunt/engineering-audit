"""Tests for the engineering-audit-render console entry point
(src/engineering_audit/render_cli.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engineering_audit import render_cli
from engineering_audit.rules import load_pack
from engineering_audit.schema import (
    RUN_STATE_SCHEMA_VERSION,
    AuditConfig,
    DomainResult,
    RuleVerdict,
    RunMeta,
    RunState,
    Verdict,
)

FIXTURE_PACK = Path(__file__).parent / "fixture_pack"


def _meta() -> RunMeta:
    return RunMeta(
        tool_version="0.1.0",
        rules_pack_name="fixture-pack",
        assistant="claude-code",
        model="claude-sonnet-5",
        repo_name="widgets-app",
        repo_commit="abc1234",
        started="2026-08-09T09:00:00+00:00",
        finished="2026-08-09T09:10:00+00:00",
    )


def _all_pass_verdicts(domain) -> list[RuleVerdict]:
    return [RuleVerdict(rule_id=r.id, verdict=Verdict.pass_) for r in domain.rules]


def _run_state() -> RunState:
    pack = load_pack(FIXTURE_PACK)
    d01 = pack.get_domain("d01")
    d02 = pack.get_domain("d02")
    assert d01 is not None and d02 is not None
    return RunState(
        meta=_meta(),
        config=AuditConfig(selected_domain_ids=["d01", "d02"], issue_mode="report"),
        domain_results={
            "d01": DomainResult(domain_id="d01", status="completed", rule_verdicts=_all_pass_verdicts(d01)),
            "d02": DomainResult(domain_id="d02", status="completed", rule_verdicts=_all_pass_verdicts(d02)),
        },
    )


def _write_state(path: Path, state: RunState | None = None) -> Path:
    path.write_text((state or _run_state()).to_json(), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_main_happy_path_writes_report_beside_the_state_file(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    state_path = _write_state(tmp_path / "run-state.json")

    render_cli.main([str(state_path), "--rules-dir", str(FIXTURE_PACK)])

    out_path = tmp_path / "report.html"
    assert out_path.is_file()
    assert "Engineering practice audit report" in out_path.read_text(encoding="utf-8")
    assert str(out_path) in capsys.readouterr().out


def test_main_respects_explicit_out_path(tmp_path: Path) -> None:
    state_path = _write_state(tmp_path / "run-state.json")
    out_path = tmp_path / "somewhere" / "audit.html"

    render_cli.main([str(state_path), "--rules-dir", str(FIXTURE_PACK), "--out", str(out_path)])

    assert out_path.is_file()


def test_main_resolves_rules_dir_from_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_path = _write_state(tmp_path / "run-state.json")
    monkeypatch.setenv("ENGINEERING_AUDIT_RULES_DIR", str(FIXTURE_PACK))

    render_cli.main([str(state_path)])

    assert (tmp_path / "report.html").is_file()


# ---------------------------------------------------------------------------
# Loud failures
# ---------------------------------------------------------------------------


def test_main_missing_rules_dir_exits_non_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENGINEERING_AUDIT_RULES_DIR", raising=False)
    state_path = _write_state(tmp_path / "run-state.json")

    with pytest.raises(SystemExit) as excinfo:
        render_cli.main([str(state_path)])
    assert "no rules pack directory" in str(excinfo.value)


def test_main_nonexistent_rules_dir_exits_non_zero(tmp_path: Path) -> None:
    state_path = _write_state(tmp_path / "run-state.json")

    with pytest.raises(SystemExit) as excinfo:
        render_cli.main([str(state_path), "--rules-dir", str(tmp_path / "does-not-exist")])
    assert "does not exist" in str(excinfo.value)


def test_main_missing_state_file_exits_non_zero(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        render_cli.main([str(tmp_path / "no-such-file.json"), "--rules-dir", str(FIXTURE_PACK)])
    assert "does not exist" in str(excinfo.value)


def test_main_invalid_json_exits_non_zero(tmp_path: Path) -> None:
    state_path = tmp_path / "run-state.json"
    state_path.write_text("{ not valid json", encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        render_cli.main([str(state_path), "--rules-dir", str(FIXTURE_PACK)])
    assert "not a valid run-state file" in str(excinfo.value)


@pytest.mark.parametrize("document", ["[1, 2, 3]", "null", '"just a string"', "42"])
def test_main_non_dict_top_level_json_exits_non_zero(tmp_path: Path, document: str) -> None:
    # RunState.from_json used to raise a raw AttributeError for a JSON top
    # level that parses but is not an object (calling .get on a list or
    # None); this must surface as the CLI's usual clean, non-zero SystemExit,
    # never an unhandled traceback.
    state_path = tmp_path / "run-state.json"
    state_path.write_text(document, encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        render_cli.main([str(state_path), "--rules-dir", str(FIXTURE_PACK)])
    assert "not a valid run-state file" in str(excinfo.value)


def test_main_higher_schema_version_exits_non_zero_naming_both_versions(tmp_path: Path) -> None:
    data = json.loads(_run_state().to_json())
    data["schema_version"] = RUN_STATE_SCHEMA_VERSION + 1
    state_path = tmp_path / "run-state.json"
    state_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        render_cli.main([str(state_path), "--rules-dir", str(FIXTURE_PACK)])
    message = str(excinfo.value)
    assert str(RUN_STATE_SCHEMA_VERSION + 1) in message
    assert str(RUN_STATE_SCHEMA_VERSION) in message


def test_main_bad_rules_pack_exits_non_zero(tmp_path: Path) -> None:
    state_path = _write_state(tmp_path / "run-state.json")
    empty_rules_dir = tmp_path / "empty-rules"
    empty_rules_dir.mkdir()

    with pytest.raises(SystemExit) as excinfo:
        render_cli.main([str(state_path), "--rules-dir", str(empty_rules_dir)])
    assert "could not load rules pack" in str(excinfo.value)


def test_main_incomplete_run_state_raises_report_error_as_clean_exit(tmp_path: Path) -> None:
    # render_report itself refuses a run whose selected domain has no
    # recorded result; the CLI must turn that into a clean, non-zero exit
    # with the message, not an uncaught traceback.
    pack = load_pack(FIXTURE_PACK)
    d01 = pack.get_domain("d01")
    assert d01 is not None
    state = RunState(
        meta=_meta(),
        config=AuditConfig(selected_domain_ids=["d01", "d02"], issue_mode="report"),
        domain_results={
            "d01": DomainResult(domain_id="d01", status="completed", rule_verdicts=_all_pass_verdicts(d01)),
        },
    )
    state_path = _write_state(tmp_path / "run-state.json", state)

    with pytest.raises(SystemExit) as excinfo:
        render_cli.main([str(state_path), "--rules-dir", str(FIXTURE_PACK)])
    assert "could not render report" in str(excinfo.value)
