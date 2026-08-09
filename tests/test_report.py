"""Tests for the deterministic HTML report renderer (src/engineering_audit/report.py)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from engineering_audit.feedback import build_feedback_body, build_feedback_sections
from engineering_audit.report import _INLINE_SCRIPT, ReportError, render_report, write_report
from engineering_audit.rules import load_pack
from engineering_audit.schema import (
    AuditConfig,
    ConsultedSource,
    Coverage,
    DomainResult,
    Finding,
    RuleVerdict,
    RunMeta,
    RunState,
    SelfAssessment,
    Severity,
    TelemetryConsent,
    Verdict,
)

FIXTURE_PACK = Path(__file__).parent / "fixture_pack"


def _extract_json_script(rendered: str, element_id: str) -> dict:
    """Pull the JSON payload out of an inline
    <script type="application/json" id="..."> block and parse it, mirroring
    what the report's own JS does at runtime with JSON.parse."""
    match = re.search(
        rf'<script type="application/json" id="{re.escape(element_id)}">(.*?)</script>',
        rendered,
        re.DOTALL,
    )
    assert match is not None, f"no <script type=application/json id={element_id!r}> block found"
    return json.loads(match.group(1))


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


def test_rollup_by_domain_includes_a_domain_audited_and_found_clean() -> None:
    # d02 in _base_run_state has zero findings but did complete; it must
    # show up in "By domain" at zero, distinguishable from a domain that was
    # never selected or never run at all.
    pack = _pack()
    run_state = _base_run_state(pack)
    rendered = render_report(run_state, pack)
    assert "d02: Teacup Logistics Handling: 0" in rendered


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


def test_could_not_evaluate_verdict_for_unknown_rule_id_raises() -> None:
    # A finding referencing an unknown rule id already raises ReportError;
    # a could-not-evaluate verdict for an unknown rule id must be equally
    # loud, not rendered as "(rule not found in pack)".
    pack = _pack()
    d01 = pack.get_domain("d01")
    verdicts = _all_pass_verdicts(d01)
    verdicts[0] = RuleVerdict(
        rule_id="D01-R99",
        verdict=Verdict.COULD_NOT_EVALUATE,
        note="this rule id does not exist in the pack",
    )
    run_state = RunState(
        meta=_meta(),
        config=AuditConfig(selected_domain_ids=["d01"], issue_mode="report"),
        domain_results={"d01": DomainResult(domain_id="d01", status="completed", rule_verdicts=verdicts)},
    )
    with pytest.raises(ReportError):
        render_report(run_state, pack)


def _consulted_source(**overrides) -> ConsultedSource:
    defaults = dict(
        rule_id="D01-R01",
        url="https://example.invalid/standard",
        title="An external standard",
        why="checked the standard's definition before verdicting this rule",
        accessed="2026-08-09T09:02:00Z",
    )
    defaults.update(overrides)
    return ConsultedSource(**defaults)


def test_consulted_sources_section_renders_none_recorded_when_empty() -> None:
    # _base_run_state's domain results carry no consulted_sources: the
    # section must say "none recorded" rather than vanish, the same way an
    # empty could-not-evaluate list still renders its own heading.
    pack = _pack()
    run_state = _base_run_state(pack)
    rendered = render_report(run_state, pack)
    assert "Sources consulted this run" in rendered
    assert "none recorded" in rendered


def test_consulted_sources_section_shows_title_link_why_and_accessed() -> None:
    pack = _pack()
    d01 = pack.get_domain("d01")
    run_state = _base_run_state(
        pack,
        extra_domain_results={
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=_all_pass_verdicts(d01),
                consulted_sources=[_consulted_source(rule_id="D01-R01")],
            )
        },
    )
    rendered = render_report(run_state, pack)
    assert "Sources consulted this run" in rendered
    assert "D01-R01" in rendered
    assert '<a href="https://example.invalid/standard">An external standard</a>' in rendered
    assert "checked the standard&#x27;s definition before verdicting this rule" in rendered
    assert "accessed 2026-08-09T09:02:00Z" in rendered


def test_consulted_sources_section_groups_multiple_sources_under_one_rule_id() -> None:
    pack = _pack()
    d01 = pack.get_domain("d01")
    run_state = _base_run_state(
        pack,
        extra_domain_results={
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=_all_pass_verdicts(d01),
                consulted_sources=[
                    _consulted_source(rule_id="D01-R01", url="https://example.invalid/a", title="Source A"),
                    _consulted_source(rule_id="D01-R01", url="https://example.invalid/b", title="Source B"),
                ],
            )
        },
    )
    rendered = render_report(run_state, pack)
    # Both sources appear once each, under a single D01-R01 heading rather
    # than two separate ones.
    assert rendered.count("D01-R01 (") == 1
    assert "Source A" in rendered
    assert "Source B" in rendered


def test_consulted_source_with_a_non_http_url_degrades_to_text_instead_of_raising() -> None:
    # consulted_sources is self-reported by the driving agent, not produced
    # by this tool's own gh integration like a filed-issue or feedback-issue
    # url; a scheme this page will not link must not take the whole report
    # down.
    pack = _pack()
    d01 = pack.get_domain("d01")
    run_state = _base_run_state(
        pack,
        extra_domain_results={
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=_all_pass_verdicts(d01),
                consulted_sources=[
                    _consulted_source(url="file:///etc/hosts", title="a local file reference")
                ],
            )
        },
    )
    rendered = render_report(run_state, pack)
    assert "a local file reference (file:///etc/hosts)" in rendered
    assert '<a href="file:///etc/hosts">' not in rendered


def test_report_error_when_consulted_source_references_a_rule_id_outside_its_domain() -> None:
    pack = _pack()
    d01 = pack.get_domain("d01")
    run_state = _base_run_state(
        pack,
        extra_domain_results={
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=_all_pass_verdicts(d01),
                # D02-R01 belongs to d02's own rules, not d01's.
                consulted_sources=[_consulted_source(rule_id="D02-R01")],
            )
        },
    )
    with pytest.raises(ReportError, match="D02-R01"):
        render_report(run_state, pack)


def test_findings_rollup_rows_keyed_by_domain_id_not_title(tmp_path: Path) -> None:
    # Build a two-domain pack where both domains share an identical title,
    # each with one finding, and confirm two distinct rollup rows appear.
    scratch = tmp_path / "pack"
    scratch.mkdir()
    domain_md = (
        "# Domain {num}: Same Title Domain\n\n"
        "**Trigger:** you are about to trigger domain {num}.\n\n"
        "### 1. A single rule.\n\n"
        "Body text.\n\n"
        "*Source: fixture. Rule id: D{num:02d}-R01. Volatility: durable.*\n"
    )
    (scratch / "01-a.md").write_text(domain_md.format(num=1), encoding="utf-8")
    (scratch / "02-b.md").write_text(domain_md.format(num=2), encoding="utf-8")
    pack = load_pack(scratch)
    d01 = pack.get_domain("d01")
    d02 = pack.get_domain("d02")
    assert d01.title == d02.title == "Same Title Domain"

    def _finding(rule_id: str) -> Finding:
        return Finding(
            rule_id=rule_id,
            severity=Severity.LOW,
            title="a finding",
            location="x.py",
            body_md="x",
            issue_title="x",
            issue_body="x",
        )

    run_state = RunState(
        meta=_meta(),
        config=AuditConfig(selected_domain_ids=["d01", "d02"], issue_mode="report"),
        domain_results={
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=[RuleVerdict(rule_id="D01-R01", verdict=Verdict.FINDING)],
                findings=[_finding("D01-R01")],
            ),
            "d02": DomainResult(
                domain_id="d02",
                status="completed",
                rule_verdicts=[RuleVerdict(rule_id="D02-R01", verdict=Verdict.FINDING)],
                findings=[_finding("D02-R01")],
            ),
        },
    )
    rendered = render_report(run_state, pack)
    assert "d01: Same Title Domain: 1" in rendered
    assert "d02: Same Title Domain: 1" in rendered


def test_markdownish_splits_paragraphs_on_crlf() -> None:
    # Feedback text is now rendered as a plain, escaped, editable textarea
    # value (not markdownish), so this exercises _markdownish through the
    # one place it still runs: a finding's body_md.
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.domain_results["d01"].findings[0].body_md = (
        "First paragraph.\r\n\r\nSecond paragraph."
    )
    rendered = render_report(run_state, pack)
    assert "<p>First paragraph.</p><p>Second paragraph.</p>" in rendered


def test_issue_url_with_javascript_scheme_raises() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.filed_issue_urls = {"D01-R02": "javascript:alert(1)"}
    with pytest.raises(ReportError):
        render_report(run_state, pack)


def test_issue_url_with_https_scheme_still_renders_as_link() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.filed_issue_urls = {"D01-R02": "https://example.invalid/issues/42"}
    rendered = render_report(run_state, pack)
    assert 'href="https://example.invalid/issues/42"' in rendered


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


def test_could_not_run_domain_stops_the_completeness_banner_claiming_full_coverage() -> None:
    # A could-not-run domain has no rule_verdicts by design, so it satisfies
    # "no rule left could-not-evaluate" by construction even though zero
    # rules were actually evaluated for it. The banner must never claim full
    # coverage in that case.
    pack = _pack()
    run_state = RunState(
        meta=_meta(),
        config=AuditConfig(selected_domain_ids=["d02"], issue_mode="report"),
        domain_results={
            "d02": DomainResult(domain_id="d02", status="could-not-run", reason="repository was empty")
        },
    )
    rendered = render_report(run_state, pack)
    assert "Nothing was left could-not-evaluate." not in rendered
    assert "did not run at all" in rendered
    assert "Teacup Logistics Handling" in rendered
    assert "not the same as a clean result" in rendered


def test_could_not_run_domain_alongside_a_completed_domain_still_reports_both() -> None:
    # A mix of one completed domain (with a real could-not-evaluate rule)
    # and one that never ran at all: both facts must survive into the
    # banner, not just whichever one the code checks first.
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.domain_results["d02"] = DomainResult(
        domain_id="d02", status="could-not-run", reason="no rules apply here"
    )
    rendered = render_report(run_state, pack)
    assert "D01-R03" in rendered  # the real could-not-evaluate rule from d01
    assert "did not run at all" in rendered
    assert "Teacup Logistics Handling" in rendered  # d02's title, named in the banner


def test_issue_urls_render_as_links_when_given() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.filed_issue_urls = {"D01-R02": "https://example.invalid/issues/1"}
    rendered = render_report(run_state, pack)
    assert 'href="https://example.invalid/issues/1"' in rendered


def test_in_report_mode_renders_copy_to_clipboard_block_when_no_issue_urls() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    rendered = render_report(run_state, pack)
    assert "Set shared-bed flag for bed-14" in rendered
    assert "copyIssueText(" in rendered
    assert "<textarea" in rendered


def test_feedback_text_rendered_when_present() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.config.feedback_text = "The gnome roster export was slow on large repos."
    rendered = render_report(run_state, pack)
    assert "The gnome roster export was slow on large repos." in rendered


def test_feedback_section_renders_interactive_form_when_no_issue_filed() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.config.feedback_text = "The gnome roster export was slow on large repos."
    rendered = render_report(run_state, pack)

    assert 'id="feedback-textarea"' in rendered
    assert "The gnome roster export was slow on large repos." in rendered
    assert 'onclick="emailFeedback()"' in rendered
    assert 'onclick="copyFeedback(this)"' in rendered
    assert 'id="feedback-sections-data"' in rendered
    # The embedded JSON carries the run metadata section too, built by the
    # same helper submit_feedback uses.
    assert "Run metadata" in rendered
    assert "rodneylunt79+audit-feedback@gmail.com" in rendered


def test_feedback_section_shows_filed_issue_link_and_still_offers_the_form() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.config.feedback_text = "The gnome roster export was slow on large repos."
    run_state.feedback_issue_url = "https://github.com/rodlunt/engineering-audit/issues/9"
    rendered = render_report(run_state, pack)

    assert 'href="https://github.com/rodlunt/engineering-audit/issues/9"' in rendered
    assert "filed as" in rendered
    assert "Further feedback can still be sent" in rendered
    # The interactive form is still rendered, not replaced by the link.
    assert 'id="feedback-textarea"' in rendered
    assert 'onclick="emailFeedback()"' in rendered


def test_feedback_issue_url_with_non_http_scheme_raises() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.feedback_issue_url = "javascript:alert(1)"
    with pytest.raises(ReportError):
        render_report(run_state, pack)


def test_finding_reference_line_cites_the_rule_pack_source() -> None:
    # D01-R02 (the finding in _base_run_state) carries a Source: fragment in
    # the fixture pack, so the reference line must quote it, not the
    # finding payload (the finding itself never carries a source field).
    pack = _pack()
    run_state = _base_run_state(pack)
    rendered = render_report(run_state, pack)
    assert (
        "Reference: D01-R02: invented for test fixtures only, no external source" in rendered
    )


def test_finding_on_a_sourceless_rule_refuses_to_render() -> None:
    # D01-R04's footer deliberately has no Source: fragment. A finding is a
    # published claim; the report must refuse to publish a claim without
    # evidence, and must never print an "unsourced" admission instead.
    pack = _pack()
    d01 = pack.get_domain("d01")
    assert d01 is not None
    verdicts = _all_pass_verdicts(d01)
    verdicts[3] = RuleVerdict(rule_id="D01-R04", verdict=Verdict.FINDING)
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
                        rule_id="D01-R04",
                        severity=Severity.LOW,
                        title="beard-length average not recalculated on retirement",
                        location="ledger/beards.py:10",
                        body_md="x",
                        issue_title="x",
                        issue_body="x",
                    )
                ],
            )
        },
    )
    with pytest.raises(ReportError) as excinfo:
        render_report(run_state, pack)
    message = str(excinfo.value)
    assert "D01-R04" in message
    assert "no cited source" in message
    assert "unsourced" not in message.lower()


def test_write_report_writes_the_file(tmp_path: Path) -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    out_path = tmp_path / "reports" / "audit.html"
    written = write_report(run_state, pack, out_path)
    assert written == out_path
    assert out_path.exists()
    assert "Engineering practice audit report" in out_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Meta block: Rules commit / Tool commit rows
# ---------------------------------------------------------------------------


def test_meta_block_shows_full_rules_and_tool_commit_when_present() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    full_tool_sha = "d" * 40
    full_pack_sha = f"{'e' * 40}-dirty"
    run_state.meta.tool_commit = full_tool_sha
    run_state.meta.rules_pack_commit = full_pack_sha
    rendered = render_report(run_state, pack)

    assert (
        '<div class="meta-label">Tool commit</div>'
        f'<div class="meta-value">{full_tool_sha}</div>' in rendered
    )
    assert (
        '<div class="meta-label">Rules commit</div>'
        f'<div class="meta-value">{full_pack_sha}</div>' in rendered
    )


def test_meta_block_shows_unknown_for_rules_and_tool_commit_when_none() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.meta.tool_commit = None
    run_state.meta.rules_pack_commit = None
    rendered = render_report(run_state, pack)

    assert (
        '<div class="meta-label">Tool commit</div><div class="meta-value">unknown</div>' in rendered
    )
    assert (
        '<div class="meta-label">Rules commit</div><div class="meta-value">unknown</div>' in rendered
    )


# ---------------------------------------------------------------------------
# Feedback section: tick boxes, embedded JSON, script-injection escaping
# ---------------------------------------------------------------------------


def test_feedback_consent_checkboxes_prefilled_true_from_config() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.config.telemetry_consent = TelemetryConsent(
        coverage=True, rollup=True, self_assessment=True, environment=True, consulted_sources=True
    )
    rendered = render_report(run_state, pack)

    for input_id in (
        "consent-coverage",
        "consent-rollup",
        "consent-self-assessment",
        "consent-environment",
        "consent-consulted-sources",
    ):
        match = re.search(rf'<input type="checkbox" id="{input_id}"([^>]*)>', rendered)
        assert match is not None, f"checkbox {input_id!r} not found"
        assert "checked" in match.group(1), f"checkbox {input_id!r} expected checked"


def test_feedback_consent_checkboxes_prefilled_false_from_config() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.config.telemetry_consent = TelemetryConsent(
        coverage=False, rollup=False, self_assessment=False, environment=False, consulted_sources=False
    )
    rendered = render_report(run_state, pack)

    for input_id in (
        "consent-coverage",
        "consent-rollup",
        "consent-self-assessment",
        "consent-environment",
        "consent-consulted-sources",
    ):
        match = re.search(rf'<input type="checkbox" id="{input_id}"([^>]*)>', rendered)
        assert match is not None, f"checkbox {input_id!r} not found"
        assert "checked" not in match.group(1), f"checkbox {input_id!r} expected unchecked"


def test_feedback_consulted_sources_consent_label_states_the_privacy_note() -> None:
    # Issue #57: URLs fetched while auditing a private repository can hint
    # at what that repository is about, so the label controlling whether
    # they are sent must say this plainly, not just default the box off.
    pack = _pack()
    run_state = _base_run_state(pack)
    rendered = render_report(run_state, pack)
    assert "can hint at what that repository is about" in rendered


def test_feedback_run_metadata_row_is_locked_checked_and_disabled() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    rendered = render_report(run_state, pack)

    assert (
        '<label class="consent-row locked"><input type="checkbox" checked disabled> '
        "Run metadata (always included when sending feedback)</label>" in rendered
    )


def test_feedback_embedded_json_parses_and_matches_build_feedback_sections() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    rendered = render_report(run_state, pack)

    data = _extract_json_script(rendered, "feedback-sections-data")
    expected_sections = build_feedback_sections(run_state.meta, run_state.domain_results)

    assert data["run_metadata"] == expected_sections["run_metadata"]
    assert data["coverage"] == expected_sections["coverage"]
    assert data["rollup"] == expected_sections["rollup"]
    assert data["self_assessment"] == expected_sections["self_assessment"]
    assert data["environment"] == expected_sections["environment"]
    assert data["consulted_sources"] == expected_sections["consulted_sources"]
    assert data["email"] == "rodneylunt79+audit-feedback@gmail.com"

    # Cross-check against the MCP path's own builder: with only one section
    # consented, build_feedback_body's output must be exactly the always-on
    # run-metadata chunk plus that one section, joined the same way the
    # report's own JS joins ticked sections.
    base_consent = {
        "coverage": False, "rollup": False, "self_assessment": False, "environment": False,
        "consulted_sources": False,
    }
    for key in ("coverage", "rollup", "self_assessment", "environment", "consulted_sources"):
        consent_kwargs = {**base_consent, key: True}
        body = build_feedback_body(
            None, run_state.meta, TelemetryConsent(**consent_kwargs), run_state.domain_results
        )
        assert body == data["run_metadata"] + "\n\n" + data[key]


def test_feedback_json_escapes_closing_script_tag_in_section_text() -> None:
    # self_assessment.limits is free text an assistant writes; if it happens
    # to contain a literal "</script>" (accidentally or via a crafted
    # payload), the embedded JSON block must still parse as one JSON value
    # and must not let that text terminate the <script> element early.
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.config.telemetry_consent = TelemetryConsent(self_assessment=True)
    run_state.domain_results["d01"].self_assessment = SelfAssessment(
        confidence="high", limits='</script><script>alert(1)</script>'
    )
    rendered = render_report(run_state, pack)

    # The raw, case-sensitive sequence "</script>" must only ever appear
    # once in the whole document from this point on: the real closing tag
    # of the feedback JSON block. If the malicious text had broken out, a
    # second literal "</script>" would appear earlier, right after the
    # injected payload.
    start = rendered.index('<script type="application/json" id="feedback-sections-data">')
    first_close = rendered.index("</script>", start)
    second_probe = rendered.find("</script>", first_close + len("</script>"))
    # There are other <script> blocks on the page (issues-data, the inline
    # JS), so other "</script>" occurrences are expected further along;
    # what must NOT happen is one appearing inside what should have been
    # pure JSON content, i.e. before the JSON is a syntactically complete
    # document. Confirm that by parsing up to (and only to) the real close.
    payload = rendered[start:first_close]
    json_text = payload.split(">", 1)[1]
    data = json.loads(json_text)
    assert "</script><script>alert(1)</script>" in data["self_assessment"]
    assert second_probe > first_close  # sanity: later script tags still exist


# ---------------------------------------------------------------------------
# Issues section: tick boxes, filed-via-MCP state, button rows, shared line
# ---------------------------------------------------------------------------


def test_issue_checkbox_rendered_and_ticked_by_default() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    rendered = render_report(run_state, pack)

    assert '<input type="checkbox" id="issue-check-0" checked' in rendered


def test_issue_already_filed_via_run_state_renders_unticked_disabled_with_link() -> None:
    # filed_issue_urls is a field on RunState itself (set by render_report's
    # caller from a previous file_issues call, or carried over from a saved
    # run-state.json), so a finding already filed renders disabled with its
    # link server-side, with no JS needed to discover it.
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.filed_issue_urls = {"D01-R02": "https://example.invalid/issues/42"}
    rendered = render_report(run_state, pack)

    assert '<input type="checkbox" id="issue-check-0" disabled>' in rendered
    assert 'href="https://example.invalid/issues/42">already filed</a>' in rendered
    # An already-filed issue must not also render as ticked and selectable.
    assert '<input type="checkbox" id="issue-check-0" checked' not in rendered


def test_issue_button_rows_present_at_top_and_bottom() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    rendered = render_report(run_state, pack)

    assert rendered.count("Add selected issues to GitHub (requires GitHub PAT)") == 2
    assert rendered.count("Copy selected issues (for pasting into an LLM or editor)") == 2
    # The GitHub-filing form itself appears exactly once, not once per row.
    assert rendered.count('id="github-file-form"') == 1
    assert rendered.count('id="gh-repo"') == 1
    assert rendered.count('id="gh-pat"') == 1


def test_issue_embedded_body_ends_with_shared_trailing_line_byte_identical_to_file_issues() -> None:
    # This is the exact body file_issues sends to gh issue create for this
    # same finding (see test_server.py's
    # test_file_issues_confirm_files_one_issue_per_finding), reused here to
    # prove the report and the MCP filing path can never diverge.
    expected_body = (
        "bed-14 has two occupants and no shared-bed flag. See ledger/beds.py:42.\n\n"
        "Found by an engineering-practice audit (rule D01-R02, severity high, "
        "at ledger/beds.py:42). Reference: invented for test fixtures only, "
        "no external source"
    )
    pack = _pack()
    run_state = _base_run_state(pack)
    rendered = render_report(run_state, pack)

    data = _extract_json_script(rendered, "issues-data")
    assert data["issues"] == [
        {
            "rule_id": "D01-R02",
            "title": "Set shared-bed flag for bed-14",
            "body": expected_body,
        }
    ]
    # The visible per-issue textarea (title, blank line, body-with-trailing)
    # must carry the same text.
    assert f"Set shared-bed flag for bed-14\n\n{expected_body}" in rendered.replace(
        "&#x27;", "'"
    ).replace("&amp;", "&")


# ---------------------------------------------------------------------------
# Print / save-as-PDF affordance (#58)
# ---------------------------------------------------------------------------


def test_print_button_calls_window_print() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    rendered = render_report(run_state, pack)
    assert '<button type="button" class="print-button" onclick="window.print()">' in rendered


def test_print_stylesheet_hides_interactive_filing_ui_and_forces_light_palette() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    rendered = render_report(run_state, pack)

    match = re.search(r"@media print \{(.*?)\n  \}\n", rendered, re.DOTALL)
    assert match is not None, "no @media print block found"
    print_css = match.group(1)

    # Interactive filing controls are hidden: checkboxes, the PAT form, buttons.
    assert ".issue-select" in print_css
    assert ".github-file-form" in print_css
    assert "button {" in print_css
    assert "display: none !important;" in print_css
    # The light palette is forced regardless of the OS colour scheme.
    assert "--bg: #f7f7f5;" in print_css
    assert "--fg: #1a1a1a;" in print_css
    # A finding must not be split across a page break.
    assert "break-inside: avoid" in print_css


# ---------------------------------------------------------------------------
# Medium severity badge contrast (#52)
# ---------------------------------------------------------------------------


def test_light_mode_medium_severity_colour_meets_wcag_contrast_minimum() -> None:
    # .severity-medium renders color: #1a1a1a text on background: var(--medium).
    # #9a7b00 (the old value) gave a 4.31:1 contrast ratio against that text,
    # short of WCAG 2.2 SC 1.4.3's 4.5:1 minimum for this badge's 0.75rem
    # text. #a08000 gives 4.63:1.
    pack = _pack()
    run_state = _base_run_state(pack)
    rendered = render_report(run_state, pack)
    assert "--medium: #a08000;" in rendered
    assert "#9a7b00" not in rendered


# ---------------------------------------------------------------------------
# Content Security Policy (part of #40)
# ---------------------------------------------------------------------------


def test_report_page_sets_a_content_security_policy_restricting_connect_src() -> None:
    # The report's inline JS sends a user-entered GitHub PAT to
    # api.github.com over fetch; a CSP caps the blast radius of any future
    # escaping bug by restricting where that fetch (and any script) can go.
    pack = _pack()
    run_state = _base_run_state(pack)
    rendered = render_report(run_state, pack)

    match = re.search(
        r'<meta http-equiv="Content-Security-Policy" content="([^"]*)">', rendered
    )
    assert match is not None, "no Content-Security-Policy meta tag found"
    policy = match.group(1)
    assert "connect-src https://api.github.com" in policy
    assert "default-src 'none'" in policy
    # No external script or style host is permitted: only the page's own
    # inline script/style, never a CDN or third-party origin.
    assert "script-src 'unsafe-inline'" in policy
    assert "style-src 'unsafe-inline'" in policy


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------


def test_footer_contains_author_link_tool_link_version_and_locality_sentence() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    rendered = render_report(run_state, pack)

    assert 'href="https://github.com/rodlunt"' in rendered
    assert 'href="https://github.com/rodlunt/engineering-audit"' in rendered
    assert (
        "Generated by engineering-audit v0.1.0 (commit unknown) against rules pack "
        "fixture-pack (commit unknown), auditing widgets-app at commit abc1234, "
        "finished 2026-08-09T09:10:00+00:00." in rendered
    )
    assert (
        "This report was generated locally. Nothing in it leaves your machine unless you "
        "choose to send or file it." in rendered
    )


def test_footer_renders_unknown_when_tool_and_rules_pack_commit_are_none() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.meta.tool_commit = None
    run_state.meta.rules_pack_commit = None
    rendered = render_report(run_state, pack)

    assert "(commit unknown)" in rendered
    assert rendered.count("(commit unknown)") == 2


def _footer_html(rendered: str) -> str:
    """Pull just the <footer>...</footer> block out of a rendered report,
    so an assertion about the shortened footer sentence can't accidentally
    pass or fail because of the (deliberately full-length) meta block."""
    match = re.search(r"<footer>(.*?)</footer>", rendered, re.DOTALL)
    assert match is not None, "no <footer> block found"
    return match.group(1)


def test_footer_shortens_a_full_sha_to_twelve_characters() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    full_tool_sha = "a" * 40
    full_pack_sha = "b" * 40
    run_state.meta.tool_commit = full_tool_sha
    run_state.meta.rules_pack_commit = full_pack_sha
    footer = _footer_html(render_report(run_state, pack))

    assert f"(commit {full_tool_sha[:12]})" in footer
    assert f"(commit {full_pack_sha[:12]})" in footer
    assert full_tool_sha not in footer
    assert full_pack_sha not in footer


def test_footer_keeps_dirty_suffix_after_shortening() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    dirty_sha = f"{'c' * 40}-dirty"
    run_state.meta.tool_commit = dirty_sha
    footer = _footer_html(render_report(run_state, pack))

    assert f"(commit {dirty_sha[:12]}-dirty)" in footer
    assert dirty_sha not in footer


# ---------------------------------------------------------------------------
# GitHub-filing JS: cross-session double-filing guard (fetchExistingIssueTitles,
# fileSelectedIssues in _INLINE_SCRIPT)
# ---------------------------------------------------------------------------


def test_js_dedup_precheck_hits_the_labelled_all_states_search_endpoint() -> None:
    # The pre-check must search state=all (an already-filed issue may have
    # been closed since) restricted to this tool's own label, not the
    # unfiltered issue list.
    assert (
        '"https://api.github.com/repos/" + repo\n    + "/issues?state=all&labels=engineering-audit&per_page=100"'
        in _INLINE_SCRIPT
    )


def test_js_dedup_precheck_uses_the_same_auth_headers_as_filing() -> None:
    assert '"Authorization": "Bearer " + pat' in _INLINE_SCRIPT
    assert '"Accept": "application/vnd.github+json"' in _INLINE_SCRIPT


def test_js_dedup_paginates_via_link_header_capped_at_three_pages() -> None:
    assert 'response.headers.get("Link")' in _INLINE_SCRIPT
    assert "_fetchExistingIssuesPage(url, headers, 1, 3, [])" in _INLINE_SCRIPT
    assert "page < maxPages" in _INLINE_SCRIPT


def test_js_dedup_already_filed_status_text_and_disabled_checkbox() -> None:
    assert 'link.textContent = "already filed"' in _INLINE_SCRIPT
    assert "cb.disabled = true;" in _INLINE_SCRIPT
    assert "function _markAlreadyFiled(" in _INLINE_SCRIPT


def test_js_dedup_fails_closed_when_the_precheck_request_itself_fails() -> None:
    # The fail-closed rule: a dedup check that could not run must never be
    # mistaken for "nothing exists yet" and silently fall through to filing.
    assert "Fail closed" in _INLINE_SCRIPT
    assert "so nothing was filed" in _INLINE_SCRIPT
    assert "fetchExistingIssueTitles(repo, pat).then(" in _INLINE_SCRIPT
    assert ").catch(function (err) {" in _INLINE_SCRIPT


# ---------------------------------------------------------------------------
# Cancel a bulk filing run in progress (#53)
# ---------------------------------------------------------------------------


def test_stop_button_rendered_hidden_alongside_file_button() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    rendered = render_report(run_state, pack)

    assert (
        '<button type="button" id="gh-stop-button" onclick="stopFilingIssues()" '
        'style="display:none">Stop</button>' in rendered
    )


def test_js_stop_flag_checked_before_each_fetch_and_resets_on_every_run() -> None:
    # fileNext checks the stop flag before firing the *next* fetch, so an
    # in-flight request is always allowed to finish; the flag is reset to
    # false at the start of every fileSelectedIssues() call, so a stopped
    # run does not leave a later run pre-cancelled.
    assert "function stopFilingIssues() {" in _INLINE_SCRIPT
    assert "_fileStopRequested = true;" in _INLINE_SCRIPT
    assert "_fileStopRequested = false;" in _INLINE_SCRIPT
    assert "if (_fileStopRequested) {" in _INLINE_SCRIPT
    assert "Filing stopped early." in _INLINE_SCRIPT
