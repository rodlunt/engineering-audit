"""Tests for the deterministic HTML report renderer (src/engineering_audit/report.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from engineering_audit.report import ReportError, render_report, write_report
from engineering_audit.rules import load_pack
from engineering_audit.schema import (
    AuditConfig,
    Coverage,
    DomainResult,
    Finding,
    RuleVerdict,
    RunMeta,
    RunState,
    SelfAssessment,
    Severity,
    Verdict,
)

FIXTURE_PACK = Path(__file__).parent / "fixture_pack"


def _meta(**overrides) -> RunMeta:
    defaults = dict(
        tool_version="0.1.0",
        rules_pack_name="fixture-pack",
        assistant="claude-code",
        model="claude-sonnet-5",
        repo_name="widgets-app",
        repo_commit="abc1234",
        started="2026-08-09T09:00:00+00:00",
        finished="2026-08-09T09:10:00+00:00",
    )
    defaults.update(overrides)
    return RunMeta(**defaults)


def _pack():
    return load_pack(FIXTURE_PACK)


def _all_pass_verdicts(domain) -> list[RuleVerdict]:
    return [RuleVerdict(rule_id=r.id, verdict=Verdict.pass_) for r in domain.rules]


def _base_run_state(pack, extra_domain_results: dict | None = None) -> RunState:
    d01 = pack.get_domain("d01")
    d02 = pack.get_domain("d02")

    d01_verdicts = _all_pass_verdicts(d01)
    # Turn D01-R02 into a finding, and D01-R03 into a could-not-evaluate.
    d01_verdicts[1] = RuleVerdict(rule_id="D01-R02", verdict=Verdict.FINDING)
    d01_verdicts[2] = RuleVerdict(
        rule_id="D01-R03",
        verdict=Verdict.COULD_NOT_EVALUATE,
        note="the garden bed ledger file could not be located in this repository",
    )

    domain_results = {
        "d01": DomainResult(
            domain_id="d01",
            status="completed",
            rule_verdicts=d01_verdicts,
            findings=[
                Finding(
                    rule_id="D01-R02",
                    severity=Severity.HIGH,
                    title="Two gnomes share bed-14 without the shared-bed flag",
                    location="ledger/beds.py:42",
                    body_md="bed-14 holds two gnomes.\n\nNeither has the shared-bed flag set.",
                    issue_title="Set shared-bed flag for bed-14",
                    issue_body="bed-14 has two occupants and no shared-bed flag. See ledger/beds.py:42.",
                )
            ],
            self_assessment=SelfAssessment(confidence="high", limits=""),
            coverage=Coverage(files_inspected=12, files_skipped=1, note="one binary asset skipped"),
        ),
        "d02": DomainResult(
            domain_id="d02",
            status="completed",
            rule_verdicts=_all_pass_verdicts(d02),
            findings=[],
            self_assessment=SelfAssessment(confidence="medium", limits="did not check archived routes"),
            coverage=Coverage(files_inspected=5, files_skipped=0),
        ),
    }
    if extra_domain_results:
        domain_results.update(extra_domain_results)

    return RunState(
        meta=_meta(),
        config=AuditConfig(selected_domain_ids=["d01", "d02"], issue_mode="report"),
        domain_results=domain_results,
    )


def test_render_report_contains_finding_titles_and_could_not_evaluate_entries() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    rendered = render_report(run_state, pack)

    assert "Two gnomes share bed-14 without the shared-bed flag" in rendered
    assert "D01-R03" in rendered
    assert "the garden bed ledger file could not be located in this repository" in rendered


def test_rollup_counts_match_computed_sums() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    rendered = render_report(run_state, pack)

    # One finding total, severity high, all in domain "Gnome Husbandry Record Keeping".
    assert "Total findings: <strong>1</strong>" in rendered
    assert "high: 1" in rendered
    assert "critical: 0" in rendered
    assert "Gnome Husbandry Record Keeping: 1" in rendered


def test_coverage_totals_are_summed_from_domain_results() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    rendered = render_report(run_state, pack)

    # 12 + 5 files inspected, 1 + 0 skipped, computed, not passed in.
    assert "Total files inspected across selected domains: <strong>17</strong>" in rendered
    assert "Total files skipped: <strong>1</strong>" in rendered


def test_report_error_when_selected_domain_has_no_result() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.domain_results.pop("d02")
    with pytest.raises(ReportError):
        render_report(run_state, pack)


def test_report_error_when_completeness_fails() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    d01 = pack.get_domain("d01")
    incomplete_verdicts = _all_pass_verdicts(d01)[:-1]  # drop the last rule's verdict
    run_state.domain_results["d01"] = DomainResult(
        domain_id="d01", status="completed", rule_verdicts=incomplete_verdicts
    )
    with pytest.raises(ReportError):
        render_report(run_state, pack)


def test_report_error_when_finding_references_unknown_rule_id() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    d01 = pack.get_domain("d01")
    verdicts = _all_pass_verdicts(d01)
    verdicts[0] = RuleVerdict(rule_id="D01-R99", verdict=Verdict.FINDING)
    run_state.domain_results["d01"] = DomainResult(
        domain_id="d01",
        status="completed",
        rule_verdicts=verdicts,
        findings=[
            Finding(
                rule_id="D01-R99",
                severity=Severity.LOW,
                title="references a rule that does not exist",
                location="x.py",
                body_md="x",
                issue_title="x",
                issue_body="x",
            )
        ],
    )
    with pytest.raises(ReportError):
        render_report(run_state, pack)


def test_full_rule_body_text_never_leaks_into_report() -> None:
    # The rules pack is private IP; the report may show a rule id and its
    # short heading title, never the body prose that explains the rule.
    pack = _pack()
    run_state = _base_run_state(pack)
    rendered = render_report(run_state, pack)

    leaked_phrases = [
        "night inspection",  # from D01-R01's body
        "nightly census",  # from D01-R02's body
        "future audit needs to",  # from D01-R03's body
        "beard-length average is a derived figure",  # from D01-R04's body
    ]
    for phrase in leaked_phrases:
        assert phrase not in rendered, f"leaked rule body text: {phrase!r}"


def test_html_escape_on_a_finding_title_containing_a_script_tag() -> None:
    pack = _pack()
    d01 = pack.get_domain("d01")
    verdicts = _all_pass_verdicts(d01)
    verdicts[0] = RuleVerdict(rule_id="D01-R01", verdict=Verdict.FINDING)
    run_state = RunState(
        meta=_meta(),
        config=AuditConfig(selected_domain_ids=["d01"], issue_mode="report"),
        domain_results={
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=verdicts,
                findings=[
                    Finding(
                        rule_id="D01-R01",
                        severity=Severity.LOW,
                        title="<script>alert(1)</script>",
                        location="x.py",
                        body_md="x",
                        issue_title="x",
                        issue_body="x",
                    )
                ],
            )
        },
    )
    rendered = render_report(run_state, pack)
    assert "<script>alert(1)</script>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered


def test_could_not_run_domain_renders_reason_without_verdicts() -> None:
    pack = _pack()
    run_state = RunState(
        meta=_meta(),
        config=AuditConfig(selected_domain_ids=["d02"], issue_mode="report"),
        domain_results={
            "d02": DomainResult(domain_id="d02", status="could-not-run", reason="repository was empty")
        },
    )
    rendered = render_report(run_state, pack)
    assert "repository was empty" in rendered


def test_issue_urls_render_as_links_when_given() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    rendered = render_report(run_state, pack, issue_urls={"D01-R02": "https://example.invalid/issues/1"})
    assert 'href="https://example.invalid/issues/1"' in rendered


def test_in_report_mode_renders_copy_to_clipboard_block_when_no_issue_urls() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    rendered = render_report(run_state, pack, issue_urls=None)
    assert "Set shared-bed flag for bed-14" in rendered
    assert "copyIssueText(" in rendered
    assert "<textarea" in rendered


def test_feedback_text_rendered_when_present() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.config.feedback_text = "The gnome roster export was slow on large repos."
    rendered = render_report(run_state, pack)
    assert "The gnome roster export was slow on large repos." in rendered


def test_write_report_writes_the_file(tmp_path: Path) -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    out_path = tmp_path / "reports" / "audit.html"
    written = write_report(run_state, pack, out_path)
    assert written == out_path
    assert out_path.exists()
    assert "Engineering practice audit report" in out_path.read_text(encoding="utf-8")
