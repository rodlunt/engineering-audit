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
    duration_text,
    feedback_subject,
)
from engineering_audit.rules import Rule
from engineering_audit.schema import (
    ConsultedSource,
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
            self_assessment=SelfAssessment(
                confidence="high", limits="did not check archives"
            ),
            coverage=Coverage(files_inspected=12, files_skipped=1),
            consulted_sources=[
                ConsultedSource(
                    rule_id="D01-R01",
                    url="https://example.invalid/standard",
                    title="an external standard, which must never appear in feedback",
                    why="checked the standard's definition of a shared bed",
                    accessed="2026-08-09T09:02:00Z",
                )
            ],
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
    body = build_feedback_body(
        "The gnome export was slow.",
        _meta(),
        TelemetryConsent(
            coverage=False, rollup=False, self_assessment=False, environment=False
        ),
        _domain_results(),
    )

    assert "The gnome export was slow." in body
    assert "Run metadata" in body
    assert "widgets-app" in body
    assert "abc1234" in body
    assert "claude-code" in body
    assert "claude-sonnet-5" in body
    assert "0.1.0" in body


def test_run_metadata_reports_unknown_tool_and_rules_commit_when_not_determined() -> (
    None
):
    body = build_feedback_body(
        "hi",
        _meta(),
        TelemetryConsent(
            coverage=False, rollup=False, self_assessment=False, environment=False
        ),
        _domain_results(),
    )

    assert "Tool commit: unknown" in body
    assert "Rules commit: unknown" in body


def test_run_metadata_reports_full_tool_and_rules_commit_when_known() -> None:
    meta = _meta(tool_commit="a" * 40, rules_pack_commit=f"{'b' * 40}-dirty")
    body = build_feedback_body(
        "hi",
        meta,
        TelemetryConsent(
            coverage=False, rollup=False, self_assessment=False, environment=False
        ),
        _domain_results(),
    )

    assert f"Tool commit: {'a' * 40}" in body
    assert f"Rules commit: {'b' * 40}-dirty" in body


def test_unconsented_sections_are_omitted_entirely() -> None:
    body = build_feedback_body(
        "hi",
        _meta(),
        TelemetryConsent(
            coverage=False,
            rollup=False,
            self_assessment=False,
            environment=False,
            consulted_sources=False,
        ),
        _domain_results(),
    )

    assert "Coverage" not in body
    assert "Findings rollup" not in body
    assert "Self-assessment" not in body
    assert "Environment" not in body
    assert "Sources consulted" not in body
    assert "example.invalid/standard" not in body


def test_consented_sections_are_included_with_correct_totals() -> None:
    body = build_feedback_body(
        "hi",
        _meta(),
        TelemetryConsent(
            coverage=True,
            rollup=True,
            self_assessment=True,
            environment=True,
            consulted_sources=True,
        ),
        _domain_results(),
    )

    assert "Coverage" in body
    # Issue #134: no summed total across domains, per-domain figures only,
    # the same shape as report.py's own per-domain coverage list (issue #87).
    assert "Files inspected: 17" not in body
    assert "Files skipped: 1" not in body
    assert "- d01: 12 file(s) inspected, 1 skipped" in body
    assert "- d02: 5 file(s) inspected, 0 skipped" in body

    assert "Findings rollup" in body
    assert "Total: 1" in body
    assert "- high: 1" in body
    assert "- critical: 0" in body
    assert "- d01: 1" in body

    assert "Self-assessment by domain" in body
    assert "d01: confidence high. Limits: did not check archives." in body
    assert "d02: confidence medium." in body

    assert "Environment" in body

    assert "Sources consulted" in body
    assert (
        "D01-R01: https://example.invalid/standard (why: checked the standard's "
        in body
    )


def test_environment_section_reports_absence_when_none_recorded() -> None:
    body = build_feedback_body(
        "hi",
        _meta(environment=None),
        TelemetryConsent(environment=True),
        _domain_results(),
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
        "hi",
        _meta(),
        TelemetryConsent(
            coverage=True, rollup=True, self_assessment=True, environment=True
        ),
        _domain_results(),
    )
    assert "a finding title that must never appear in feedback" not in body
    assert "the finding body, which must also never appear in feedback" not in body


def test_consulted_source_carries_only_rule_id_url_and_why_never_title_or_accessed() -> (
    None
):
    # Design decision: consulted_sources also carries title and accessed,
    # for the local report to display, but the feedback body sent off the
    # machine is deliberately thinner, the same way findings carry only
    # counts and never their body text.
    body = build_feedback_body(
        "hi", _meta(), TelemetryConsent(consulted_sources=True), _domain_results()
    )
    assert "D01-R01: https://example.invalid/standard" in body
    assert "an external standard, which must never appear in feedback" not in body
    assert "2026-08-09T09:02:00Z" not in body


def test_build_mailto_url_encodes_subject_and_body() -> None:
    url = build_mailto_url(
        FEEDBACK_EMAIL,
        "Feedback: audit run 2026-08-09 (claude-code)",
        "line one\nline two & more",
    )
    parsed = urlparse(url)
    assert parsed.scheme == "mailto"
    assert parsed.path == FEEDBACK_EMAIL
    query = parse_qs(parsed.query)
    assert query["subject"] == ["Feedback: audit run 2026-08-09 (claude-code)"]
    assert query["body"] == ["line one\nline two & more"]


def test_feedback_repo_constant_is_the_tool_authors_repo() -> None:
    assert FEEDBACK_REPO == "rodlunt/engineering-audit"


def test_build_feedback_sections_returns_the_nine_fixed_sections_regardless_of_consent() -> (
    None
):
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
        "consulted_sources",
        "verdict_distribution",
        "duration",
        "rules_fetched",
    }
    assert "Run metadata" in sections["run_metadata"]
    assert "widgets-app" in sections["run_metadata"]
    assert "- d01: 12 file(s) inspected, 1 skipped" in sections["coverage"]
    assert "Total: 1" in sections["rollup"]
    assert "d01: confidence high." in sections["self_assessment"]
    assert "D01-R01: https://example.invalid/standard" in sections["consulted_sources"]


def test_verdict_distribution_section_reports_per_domain_and_run_total_counts() -> None:
    # d01 has one pass and one finding verdict; d02 has one pass verdict.
    # This is the table meant to make a thin run (lots of not-applicable,
    # few findings) visibly different from a thorough one, so it must count
    # every one of the four verdict kinds, not just findings.
    sections = build_feedback_sections(_meta(), _domain_results())
    section = sections["verdict_distribution"]
    assert "Rule verdict distribution" in section
    assert "Total verdicts: 3" in section
    assert "- pass: 2" in section
    assert "- finding: 1" in section
    assert "- not-applicable: 0" in section
    assert "- could-not-evaluate: 0" in section
    assert "- d01: pass 1, finding 1, not-applicable 0, could-not-evaluate 0" in section
    assert "- d02: pass 1, finding 0, not-applicable 0, could-not-evaluate 0" in section
    # No repository content, paths, URLs or finding text: only counts.
    assert "ledger/beds.py" not in section
    assert "a finding title that must never appear in feedback" not in section


def test_verdict_distribution_section_names_a_could_not_run_domain_rather_than_zero_counts() -> (
    None
):
    # A could-not-run domain has no rule_verdicts at all (DomainResult
    # enforces this): reporting it as "pass 0, finding 0, ..." would look
    # exactly like a domain that ran and found nothing wrong, which is the
    # same confusion this section exists to end. It must be named as not
    # having run instead.
    domain_results = {
        **_domain_results(),
        "d03": DomainResult(
            domain_id="d03", status="could-not-run", reason="no git repository found"
        ),
    }
    section = build_feedback_sections(_meta(), domain_results)["verdict_distribution"]
    assert "- d03: could not run" in section
    assert "Total verdicts: 3" in section  # unaffected by the domain that never ran


def test_coverage_section_lists_per_domain_counts_with_no_cross_domain_totals() -> None:
    # Issue #134: feedback.py summed files_inspected/files_skipped across
    # every domain and shipped that total, the same figure issue #87 removed
    # from the report for having no honest reading (a 344-file repository
    # rendered "5320 skipped" across 16 domains, since a file sixteen
    # domains each declined to open was counted sixteen times). The sum is
    # dropped here too; the per-domain list is what report.py's own
    # _coverage_summary already renders correctly.
    section = build_feedback_sections(_meta(), _domain_results())["coverage"]
    assert "Files inspected:" not in section
    assert "Files skipped:" not in section
    assert "- d01: 12 file(s) inspected, 1 skipped" in section
    assert "- d02: 5 file(s) inspected, 0 skipped" in section


def test_coverage_section_names_a_could_not_run_domain_rather_than_zero_coverage() -> (
    None
):
    domain_results = {
        **_domain_results(),
        "d03": DomainResult(
            domain_id="d03", status="could-not-run", reason="no git repository found"
        ),
    }
    section = build_feedback_sections(_meta(), domain_results)["coverage"]
    assert "- d03: did not run" in section


def test_coverage_section_reports_no_coverage_reported_when_domain_has_none() -> None:
    domain_results = {
        **_domain_results(),
        "d03": DomainResult(
            domain_id="d03",
            status="completed",
            rule_verdicts=[RuleVerdict(rule_id="D03-R01", verdict=Verdict.pass_)],
        ),
    }
    section = build_feedback_sections(_meta(), domain_results)["coverage"]
    assert "- d03: no coverage reported" in section


def test_coverage_section_includes_the_note_when_one_is_recorded() -> None:
    domain_results = {
        "d01": DomainResult(
            domain_id="d01",
            status="completed",
            coverage=Coverage(
                files_inspected=12, files_skipped=1, note="one binary asset skipped"
            ),
        ),
    }
    section = build_feedback_sections(_meta(), domain_results)["coverage"]
    assert (
        "- d01: 12 file(s) inspected, 1 skipped (one binary asset skipped)" in section
    )


def test_duration_section_matches_the_reports_own_duration_wording() -> None:
    meta = _meta(
        started="2026-08-09T09:00:00Z",
        finished="2026-08-09T09:10:00Z",
        server_started="2026-08-09T09:00:01Z",
        server_finished="2026-08-09T09:10:02Z",
    )
    section = build_feedback_sections(meta, _domain_results())["duration"]
    assert section == f"Duration\n{duration_text(meta)}"
    assert "10m0s" in section
    assert "server-measured" in section


def test_duration_section_reports_unmeasured_honestly_rather_than_as_agreement() -> (
    None
):
    # server_started/server_finished absent means "never measured", not
    # "agrees with the assistant". The section must say so, not silently
    # show only the assistant's figure as though it had been checked.
    meta = _meta(server_started=None, server_finished=None)
    section = build_feedback_sections(meta, _domain_results())["duration"]
    assert "not measured by the server, so this could not be checked" in section


def test_rules_fetched_section_reports_per_domain_fetched_state() -> None:
    sections = build_feedback_sections(
        _meta(),
        _domain_results(),
        rules_fetched_domain_ids=["d01"],
        rules_fetch_unknown_domain_ids=[],
    )
    section = sections["rules_fetched"]
    assert "Rules fetched" in section
    assert "never that it was read or applied" in section
    assert "- d01: fetched" in section
    assert "- d02: not fetched" in section


def test_rules_fetched_section_reports_unrecorded_rather_than_not_fetched_for_a_legacy_run() -> (
    None
):
    # rules_fetched_domain_ids=None means the whole run predates fetch
    # tracking: every domain's status is unknown, and the section must say
    # "unrecorded" for each, never collapse that into "not fetched" (which
    # would accuse a run that may well have fetched the rules) or "fetched"
    # (which would launder it clean).
    sections = build_feedback_sections(_meta(), _domain_results())
    section = sections["rules_fetched"]
    assert "- d01: unrecorded" in section
    assert "- d02: unrecorded" in section
    assert "- d01: fetched" not in section
    assert "- d01: not fetched" not in section
    assert "- d02: fetched" not in section
    assert "- d02: not fetched" not in section


def test_rules_fetched_section_distinguishes_a_domain_carried_in_from_an_earlier_untracked_resume() -> (
    None
):
    # A run that DOES record fetches can still carry one domain forward from
    # before tracking existed (an earlier resume): that domain lands in
    # rules_fetch_unknown_domain_ids even though the run overall has a
    # concrete (non-None) fetched list, and must still read as unrecorded,
    # not as "not fetched".
    sections = build_feedback_sections(
        _meta(),
        _domain_results(),
        rules_fetched_domain_ids=[],
        rules_fetch_unknown_domain_ids=["d01"],
    )
    section = sections["rules_fetched"]
    assert "- d01: unrecorded" in section
    assert "- d02: not fetched" in section


def test_rules_fetched_section_names_a_could_not_run_domain_as_did_not_run() -> None:
    domain_results = {
        **_domain_results(),
        "d03": DomainResult(
            domain_id="d03", status="could-not-run", reason="no git repository found"
        ),
    }
    section = build_feedback_sections(
        _meta(),
        domain_results,
        rules_fetched_domain_ids=["d01"],
        rules_fetch_unknown_domain_ids=[],
    )["rules_fetched"]
    assert "- d03: did not run" in section


def test_consulted_sources_section_reports_absence_when_none_recorded() -> None:
    # d02 in _domain_results() has no consulted_sources; a domain_results dict
    # where nothing at all was recorded must say so explicitly rather than
    # rendering an empty section indistinguishable from one that was never
    # built.
    domain_results = {"d02": _domain_results()["d02"]}
    sections = build_feedback_sections(_meta(), domain_results)
    assert (
        "No sources were consulted outside the rules pack this run."
        in sections["consulted_sources"]
    )


def test_rollup_by_domain_includes_a_domain_that_was_audited_and_came_back_clean() -> (
    None
):
    # d02 in _domain_results() is completed with zero findings: it must
    # appear in the rollup's "By domain" breakdown at zero, not be omitted
    # as if it were never audited at all.
    sections = build_feedback_sections(_meta(), _domain_results())
    assert "d01: 1" in sections["rollup"]
    assert "d02: 0" in sections["rollup"]


def test_build_feedback_body_matches_build_feedback_sections_for_every_consent_combination() -> (
    None
):
    # The MCP path (build_feedback_body) must always be reconstructible from
    # build_feedback_sections' per-section chunks, since the report's JS
    # reconstructs the same body from those same chunks by a different
    # route (ticking checkboxes rather than a TelemetryConsent object).
    meta = _meta()
    domain_results = _domain_results()
    sections = build_feedback_sections(meta, domain_results)

    flag_names = (
        "coverage",
        "rollup",
        "self_assessment",
        "environment",
        "consulted_sources",
        "verdict_distribution",
        "duration",
        "rules_fetched",
    )
    combinations = [
        {name: (name == chosen) for name in flag_names} for chosen in flag_names
    ]
    combinations.append({name: True for name in flag_names})
    combinations.append({name: False for name in flag_names})

    for consent_kwargs in combinations:
        consent = TelemetryConsent(**consent_kwargs)
        body = build_feedback_body("hi", meta, consent, domain_results)
        expected_parts = ["hi", sections["run_metadata"]]
        for name in flag_names:
            if consent_kwargs[name]:
                expected_parts.append(sections[name])
        assert body == "\n\n".join(expected_parts)


def test_verdict_distribution_duration_and_rules_fetched_are_omitted_unless_consented() -> (
    None
):
    body = build_feedback_body("hi", _meta(), TelemetryConsent(), _domain_results())
    assert "Rule verdict distribution" not in body
    assert "Duration" not in body
    assert "Rules fetched" not in body


def test_verdict_distribution_duration_and_rules_fetched_appear_when_consented() -> (
    None
):
    body = build_feedback_body(
        "hi",
        _meta(),
        TelemetryConsent(verdict_distribution=True, duration=True, rules_fetched=True),
        _domain_results(),
    )
    assert "Rule verdict distribution" in body
    assert "Duration" in body
    assert "Rules fetched" in body


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
    rule = Rule(
        id="D01-R04",
        title="Unsourced rule",
        number=4,
        volatility="volatile",
        source=None,
    )
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
