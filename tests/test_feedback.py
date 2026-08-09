"""Tests for the shared feedback-body builder (src/engineering_audit/feedback.py)."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from engineering_audit.feedback import (
    FEEDBACK_EMAIL,
    FEEDBACK_REPO,
    build_feedback_body,
    build_feedback_sections,
    build_issue_trailing_line,
    build_mailto_url,
    feedback_subject,
)
from engineering_audit.rules import Rule
from engineering_audit.schema import (
    Coverage,
    DomainResult,
    Finding,
    RuleVerdict,
    RunMeta,
    SelfAssessment,
    Severity,
    TelemetryConsent,
    Verdict,
)


def _meta(**overrides) -> RunMeta:
    defaults = dict(
        tool_version="0.1.0",
        rules_pack_name="fixture-pack",
        assistant="claude-code",
        model="claude-sonnet-5",
        repo_name="widgets-app",
        repo_commit="abc1234",
        started="2026-08-09T09:00:00Z",
        finished="2026-08-09T09:10:00Z",
    )
    defaults.update(overrides)
    return RunMeta(**defaults)


def _domain_results() -> dict[str, DomainResult]:
    finding = Finding(
        rule_id="D01-R02",
        severity=Severity.HIGH,
        title="a finding title that must never appear in feedback",
        location="ledger/beds.py:42",
        body_md="the finding body, which must also never appear in feedback",
        issue_title="a finding title that must never appear in feedback",
        issue_body="the finding body, which must also never appear in feedback",
    )
    return {
        "d01": DomainResult(
            domain_id="d01",
            status="completed",
            rule_verdicts=[
                RuleVerdict(rule_id="D01-R01", verdict=Verdict.pass_),
                RuleVerdict(rule_id="D01-R02", verdict=Verdict.FINDING),
            ],
            findings=[finding],
            self_assessment=SelfAssessment(confidence="high", limits="did not check archives"),
            coverage=Coverage(files_inspected=12, files_skipped=1),
        ),
        "d02": DomainResult(
            domain_id="d02",
            status="completed",
            rule_verdicts=[RuleVerdict(rule_id="D02-R01", verdict=Verdict.pass_)],
            findings=[],
            self_assessment=SelfAssessment(confidence="medium"),
            coverage=Coverage(files_inspected=5, files_skipped=0),
        ),
    }


def test_feedback_subject_uses_date_and_assistant() -> None:
    subject = feedback_subject(_meta())
    assert subject == "Feedback: audit run 2026-08-09 (claude-code)"


def test_body_always_includes_free_text_and_run_metadata() -> None:
    body = build_feedback_body("The gnome export was slow.", _meta(), TelemetryConsent(
        coverage=False, rollup=False, self_assessment=False, environment=False
    ), _domain_results())

    assert "The gnome export was slow." in body
    assert "Run metadata" in body
    assert "widgets-app" in body
    assert "abc1234" in body
    assert "claude-code" in body
    assert "claude-sonnet-5" in body
    assert "0.1.0" in body


def test_unconsented_sections_are_omitted_entirely() -> None:
    body = build_feedback_body("hi", _meta(), TelemetryConsent(
        coverage=False, rollup=False, self_assessment=False, environment=False
    ), _domain_results())

    assert "Coverage" not in body
    assert "Findings rollup" not in body
    assert "Self-assessment" not in body
    assert "Environment" not in body


def test_consented_sections_are_included_with_correct_totals() -> None:
    body = build_feedback_body("hi", _meta(), TelemetryConsent(
        coverage=True, rollup=True, self_assessment=True, environment=True
    ), _domain_results())

    assert "Coverage" in body
    assert "Files inspected: 17" in body
    assert "Files skipped: 1" in body

    assert "Findings rollup" in body
    assert "Total: 1" in body
    assert "- high: 1" in body
    assert "- critical: 0" in body
    assert "- d01: 1" in body

    assert "Self-assessment by domain" in body
    assert "d01: confidence high. Limits: did not check archives." in body
    assert "d02: confidence medium." in body

    assert "Environment" in body


def test_environment_section_reports_absence_when_none_recorded() -> None:
    body = build_feedback_body(
        "hi", _meta(environment=None), TelemetryConsent(environment=True), _domain_results()
    )
    assert "No environment information reported for this run." in body


def test_environment_section_lists_recorded_keys() -> None:
    body = build_feedback_body(
        "hi",
        _meta(environment={"os": "linux", "python": "3.12"}),
        TelemetryConsent(environment=True),
        _domain_results(),
    )
    assert "os: linux" in body
    assert "python: 3.12" in body


def test_finding_text_never_appears_in_the_body_even_when_rollup_consented() -> None:
    body = build_feedback_body(
        "hi", _meta(), TelemetryConsent(coverage=True, rollup=True, self_assessment=True, environment=True),
        _domain_results(),
    )
    assert "a finding title that must never appear in feedback" not in body
    assert "the finding body, which must also never appear in feedback" not in body


def test_build_mailto_url_encodes_subject_and_body() -> None:
    url = build_mailto_url(FEEDBACK_EMAIL, "Feedback: audit run 2026-08-09 (claude-code)", "line one\nline two & more")
    parsed = urlparse(url)
    assert parsed.scheme == "mailto"
    assert parsed.path == FEEDBACK_EMAIL
    query = parse_qs(parsed.query)
    assert query["subject"] == ["Feedback: audit run 2026-08-09 (claude-code)"]
    assert query["body"] == ["line one\nline two & more"]


def test_feedback_repo_constant_is_the_tool_authors_repo() -> None:
    assert FEEDBACK_REPO == "rodlunt/engineering-audit"


def test_build_feedback_sections_returns_the_five_fixed_sections_regardless_of_consent() -> None:
    # build_feedback_sections computes every section unconditionally; only
    # build_feedback_body (and the report's own consent gating) decides
    # which ones make it into a given message.
    sections = build_feedback_sections(_meta(), _domain_results())
    assert set(sections.keys()) == {
        "run_metadata",
        "coverage",
        "rollup",
        "self_assessment",
        "environment",
    }
    assert "Run metadata" in sections["run_metadata"]
    assert "widgets-app" in sections["run_metadata"]
    assert "Files inspected: 17" in sections["coverage"]
    assert "Total: 1" in sections["rollup"]
    assert "d01: confidence high." in sections["self_assessment"]


def test_build_feedback_body_matches_build_feedback_sections_for_every_consent_combination() -> None:
    # The MCP path (build_feedback_body) must always be reconstructible from
    # build_feedback_sections' per-section chunks, since the report's JS
    # reconstructs the same body from those same chunks by a different
    # route (ticking checkboxes rather than a TelemetryConsent object).
    meta = _meta()
    domain_results = _domain_results()
    sections = build_feedback_sections(meta, domain_results)

    for coverage, rollup, self_assessment, environment in (
        (True, False, False, False),
        (False, True, False, False),
        (False, False, True, False),
        (False, False, False, True),
        (True, True, True, True),
        (False, False, False, False),
    ):
        consent = TelemetryConsent(
            coverage=coverage, rollup=rollup, self_assessment=self_assessment, environment=environment
        )
        body = build_feedback_body("hi", meta, consent, domain_results)
        expected_parts = ["hi", sections["run_metadata"]]
        if coverage:
            expected_parts.append(sections["coverage"])
        if rollup:
            expected_parts.append(sections["rollup"])
        if self_assessment:
            expected_parts.append(sections["self_assessment"])
        if environment:
            expected_parts.append(sections["environment"])
        assert body == "\n\n".join(expected_parts)


def test_build_issue_trailing_line_matches_the_wording_file_issues_sends() -> None:
    # Byte-identical to the trailing line asserted in
    # test_server.py::test_file_issues_confirm_files_one_issue_per_finding
    # for the same finding: the two callers (file_issues and the report's
    # issues section) must never be able to describe the same finding
    # differently.
    rule = Rule(
        id="D01-R02",
        title="Never assign two gnomes to the same garden bed without a shared-bed flag.",
        number=2,
        volatility="volatile",
        source="invented for test fixtures only, no external source",
    )
    finding = Finding(
        rule_id="D01-R02",
        severity=Severity.HIGH,
        title="Set shared-bed flag for bed-14",
        location="ledger/beds.py:42",
        body_md="x",
        issue_title="Set shared-bed flag for bed-14",
        issue_body="bed-14 has two occupants and no shared-bed flag. See ledger/beds.py:42.",
    )
    line = build_issue_trailing_line(finding, rule)
    assert line == (
        "Found by an engineering-practice audit (rule D01-R02, severity high, "
        "at ledger/beds.py:42). Reference: invented for test fixtures only, "
        "no external source"
    )


def test_build_issue_trailing_line_raises_for_a_sourceless_rule() -> None:
    rule = Rule(id="D01-R04", title="Unsourced rule", number=4, volatility="volatile", source=None)
    finding = Finding(
        rule_id="D01-R04",
        severity=Severity.LOW,
        title="x",
        location="x.py",
        body_md="x",
        issue_title="x",
        issue_body="x",
    )
    with pytest.raises(ValueError, match="no cited source"):
        build_issue_trailing_line(finding, rule)
