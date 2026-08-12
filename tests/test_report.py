"""Tests for the deterministic HTML report renderer (src/engineering_audit/report.py)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from engineering_audit import report as report_module
from engineering_audit.feedback import build_feedback_body, build_feedback_sections
from engineering_audit.report import (
    _INLINE_SCRIPT,
    ReportError,
    render_report,
    write_report,
)
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
    assert match is not None, (
        f"no <script type=application/json id={element_id!r}> block found"
    )
    return json.loads(match.group(1))


def _domain_row(rendered: str, domain_id: str) -> str:
    """The per-domain table's row for one domain id, as raw HTML.

    Keyed on the domain id in the row header, never on the domain title:
    two domains from different rules-pack files can share a title, and the
    table must keep them apart (see
    test_domain_table_rows_keyed_by_domain_id_not_title)."""
    match = re.search(
        rf'<tr><th scope="row"><span class="domain-id">{re.escape(domain_id)}</span>.*?</tr>',
        rendered,
        re.DOTALL,
    )
    assert match is not None, f"no per-domain table row found for {domain_id!r}"
    return match.group(0)


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
            coverage=Coverage(
                files_inspected=12, files_skipped=1, note="one binary asset skipped"
            ),
        ),
        "d02": DomainResult(
            domain_id="d02",
            status="completed",
            rule_verdicts=_all_pass_verdicts(d02),
            findings=[],
            self_assessment=SelfAssessment(
                confidence="medium", limits="did not check archived routes"
            ),
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
    assert (
        "the garden bed ledger file could not be located in this repository" in rendered
    )


def test_header_names_earlier_contributors_on_a_handed_over_run() -> None:
    # The header is where #93 actually bit: it named the model that started the
    # run rather than the one that produced the findings, with nothing to
    # prompt a reader to doubt it. A run worked on by two must say so.
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.meta.earlier_contributors = ["codex/gpt-5.6-luna"]
    rendered = render_report(run_state, pack)

    assert "Earlier contributors" in rendered
    assert "codex/gpt-5.6-luna" in rendered


def test_header_has_no_contributors_row_for_an_ordinary_run() -> None:
    # An empty list must render nothing at all, not an empty row: a blank
    # "Earlier contributors" cell would imply a handover that never happened.
    pack = _pack()
    rendered = render_report(_base_run_state(pack), pack)

    assert "Earlier contributors" not in rendered


def test_rollup_counts_match_computed_sums() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    rendered = render_report(run_state, pack)

    # One finding total, severity high, all in domain "Gnome Husbandry Record Keeping".
    # Every count ships with the base it came out of (issue #123): a bare
    # "Total findings: 1" left the reader to supply their own denominator.
    assert (
        "<strong>1</strong> finding across 7 rules verdicted in 2 of 2 domains."
        in rendered
    )
    assert "<li>high: 1 of 1</li>" in rendered
    assert "<li>critical: 0 of 1</li>" in rendered
    # The pass count is now visible in the report, not only in the feedback
    # payload: 5 of the 7 verdicts are passes (d01 has one finding and one
    # could-not-evaluate).
    assert "<li>pass: 5 of 7</li>" in rendered
    assert "<li>could not evaluate: 1 of 7</li>" in rendered
    assert "1 of 1" in _domain_row(rendered, "d01")


def test_rollup_by_domain_includes_a_domain_audited_and_found_clean() -> None:
    # d02 in _base_run_state has zero findings but did complete; it must
    # show up in the per-domain table at zero, distinguishable from a domain
    # that was never selected or never run at all.
    pack = _pack()
    run_state = _base_run_state(pack)
    rendered = render_report(run_state, pack)
    row = _domain_row(rendered, "d02")
    assert "Teacup Logistics Handling" in row
    assert "0 of 1" in row
    assert "pass: 3, finding: 0, not applicable: 0, could not evaluate: 0, of 3 " in row


def test_coverage_section_lists_per_domain_counts_with_no_cross_domain_totals() -> None:
    # Issue #87: summed "Total files inspected/skipped" figures across
    # selected domains double-counted every file once per domain that
    # declined to open it (a 344-file repository rendered "5320 skipped"
    # across 16 domains). The totals are dropped entirely; the per-domain
    # figures (now the table's Files column) are correct on their own, and
    # the totals row says why it is not summing them rather than going blank.
    pack = _pack()
    run_state = _base_run_state(pack)
    rendered = render_report(run_state, pack)

    assert "Total files inspected" not in rendered
    assert "Total files skipped" not in rendered
    assert (
        '12 inspected, 1 skipped <span class="muted">(one binary asset skipped)</span>'
        in _domain_row(rendered, "d01")
    )
    assert "5 inspected, 0 skipped" in _domain_row(rendered, "d02")
    assert "not summed: a file two domains both opened would count twice" in rendered


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
        domain_results={
            "d01": DomainResult(
                domain_id="d01", status="completed", rule_verdicts=verdicts
            )
        },
    )
    with pytest.raises(ReportError):
        render_report(run_state, pack)


def test_could_not_evaluate_groups_rows_by_reason_sorted_by_descending_count() -> None:
    # Issue #88: a real 16-domain run produced 122 could-not-evaluate rows
    # carrying only 18 distinct reasons, with the boilerplate reasons
    # burying the rare, rule-specific ones. Rows sharing the same reason
    # text must collapse into one group listing every rule id, with groups
    # ordered by descending rule count.
    pack = _pack()
    common_reason = "no requirements documentation found in this repository"
    rare_reason = (
        "the persistence schema is managed by a separate database-migrations repository"
    )

    d01_verdicts = [
        RuleVerdict(
            rule_id="D01-R01", verdict=Verdict.COULD_NOT_EVALUATE, note=common_reason
        ),
        RuleVerdict(
            rule_id="D01-R02", verdict=Verdict.COULD_NOT_EVALUATE, note=common_reason
        ),
        RuleVerdict(
            rule_id="D01-R03", verdict=Verdict.COULD_NOT_EVALUATE, note=rare_reason
        ),
        RuleVerdict(rule_id="D01-R04", verdict=Verdict.pass_),
    ]
    d02 = pack.get_domain("d02")
    d02_verdicts = _all_pass_verdicts(d02)
    d02_verdicts[0] = RuleVerdict(
        rule_id="D02-R01", verdict=Verdict.COULD_NOT_EVALUATE, note=common_reason
    )
    run_state = RunState(
        meta=_meta(),
        config=AuditConfig(selected_domain_ids=["d01", "d02"], issue_mode="report"),
        domain_results={
            "d01": DomainResult(
                domain_id="d01", status="completed", rule_verdicts=d01_verdicts
            ),
            "d02": DomainResult(
                domain_id="d02", status="completed", rule_verdicts=d02_verdicts
            ),
        },
    )
    rendered = render_report(run_state, pack)

    # Heading keeps the total row count (4), not the distinct-reason count
    # (2), and carries the base it is a count out of (issue #123).
    assert "Could not evaluate: 4 of 7 rules verdicted" in rendered
    assert "These are rules the audit could not reach a verdict on" in rendered
    assert "not findings" in rendered

    assert "D01-R01, D01-R02, D02-R01 (3 rules)" in rendered
    assert "D01-R03 (1 rule)" in rendered
    # The common reason (3 rule ids) sorts ahead of the rare one (1 rule id).
    assert rendered.index(common_reason) < rendered.index(rare_reason)


def _all_not_applicable_verdicts(domain, note: str) -> list[RuleVerdict]:
    return [
        RuleVerdict(rule_id=r.id, verdict=Verdict.NOT_APPLICABLE, note=note)
        for r in domain.rules
    ]


def test_not_applicable_verdicts_are_counted_and_their_reasons_listed() -> None:
    # Issue #100: the rendered report never mentioned not-applicable at all,
    # so 172 waved-away rules left no trace in the output.
    pack = _pack()
    d01 = pack.get_domain("d01")
    d02 = pack.get_domain("d02")
    reason = "this repository ships no gnome roster, only teacups"
    d01_verdicts = _all_pass_verdicts(d01)
    d01_verdicts[0] = RuleVerdict(
        rule_id="D01-R01", verdict=Verdict.NOT_APPLICABLE, note=reason
    )
    run_state = RunState(
        meta=_meta(),
        config=AuditConfig(selected_domain_ids=["d01", "d02"], issue_mode="report"),
        domain_results={
            "d01": DomainResult(
                domain_id="d01", status="completed", rule_verdicts=d01_verdicts
            ),
            "d02": DomainResult(
                domain_id="d02",
                status="completed",
                rule_verdicts=_all_pass_verdicts(d02),
            ),
        },
    )
    rendered = render_report(run_state, pack)

    assert "Not applicable: 1 of 7 rules verdicted" in rendered
    # The per-domain counts moved into the table (issue #123), denominators
    # and all: a domain that set nothing aside still shows its zero there.
    assert "not applicable: 1, could not evaluate: 0, of 4 rules verdicted" in (
        _domain_row(rendered, "d01")
    )
    assert "not applicable: 0, could not evaluate: 0, of 3 rules verdicted" in (
        _domain_row(rendered, "d02")
    )
    assert reason in rendered
    assert "D01-R01 (1 rule)" in rendered


def test_a_wholly_not_applicable_domain_is_distinguishable_from_one_swept_clean() -> (
    None
):
    # The defect in one assertion: d01 had every rule waved away and d02 was
    # actually swept, and both rendered as "0 findings" with nothing else to
    # tell them apart.
    pack = _pack()
    d01 = pack.get_domain("d01")
    d02 = pack.get_domain("d02")
    reason = "this repository houses no gnomes, so the husbandry rules have no subject"
    run_state = RunState(
        meta=_meta(),
        config=AuditConfig(selected_domain_ids=["d01", "d02"], issue_mode="report"),
        domain_results={
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=_all_not_applicable_verdicts(d01, reason),
            ),
            "d02": DomainResult(
                domain_id="d02",
                status="completed",
                rule_verdicts=_all_pass_verdicts(d02),
            ),
        },
    )
    rendered = render_report(run_state, pack)

    # The rollup row that used to read exactly like a clean sweep is a table
    # row now, and the two domains' verdict cells cannot be confused: d01
    # set every one of its four rules aside, d02 passed all three of its.
    assert (
        "pass: 0, finding: 0, not applicable: 4, could not evaluate: 0, of 4 rules"
        in _domain_row(rendered, "d01")
    )
    assert (
        "pass: 3, finding: 0, not applicable: 0, could not evaluate: 0, of 3 rules"
        in _domain_row(rendered, "d02")
    )
    # The domain is named as set aside in full, and the findings section says
    # so too rather than reusing the clean result's "No findings."
    assert "1 selected domain(s) had every rule set aside as not applicable" in rendered
    assert (
        "all 4 of 4 rule(s) in this domain were set aside as not applicable" in rendered
    )
    assert reason in rendered
    # ...and the swept-clean domain in the same list still says how many rules
    # it was swept over, so the two rows cannot be read as the same result.
    assert (
        "d02: Teacup Logistics Handling: no findings, from 3 rule(s) verdicted"
        in rendered
    )


def test_not_applicable_all_clear_message_when_nothing_was_set_aside() -> None:
    pack = _pack()
    rendered = render_report(_base_run_state(pack), pack)
    assert "No rule was set aside as not applicable." in rendered
    assert "Not applicable (" not in rendered


def test_not_applicable_groups_rows_by_reason_sorted_by_descending_count() -> None:
    # The same grouping could-not-evaluate uses (issue #88), reached through
    # the shared helper rather than a second copy of it: rows sharing a
    # reason collapse into one group, biggest group first.
    pack = _pack()
    common_reason = "this repository has no gnome ledger of any kind"
    rare_reason = "beard-length averages are computed in a separate roster service"
    d01_verdicts = [
        RuleVerdict(
            rule_id="D01-R01", verdict=Verdict.NOT_APPLICABLE, note=common_reason
        ),
        RuleVerdict(
            rule_id="D01-R02", verdict=Verdict.NOT_APPLICABLE, note=common_reason
        ),
        RuleVerdict(
            rule_id="D01-R03", verdict=Verdict.NOT_APPLICABLE, note=common_reason
        ),
        RuleVerdict(
            rule_id="D01-R04", verdict=Verdict.NOT_APPLICABLE, note=rare_reason
        ),
    ]
    run_state = RunState(
        meta=_meta(),
        config=AuditConfig(selected_domain_ids=["d01"], issue_mode="report"),
        domain_results={
            "d01": DomainResult(
                domain_id="d01", status="completed", rule_verdicts=d01_verdicts
            )
        },
    )
    rendered = render_report(run_state, pack)

    assert "Not applicable: 4 of 4 rules verdicted" in rendered
    assert "D01-R01, D01-R02, D01-R03 (3 rules)" in rendered
    assert "D01-R04 (1 rule)" in rendered
    assert rendered.index(common_reason) < rendered.index(rare_reason)


# ---------------------------------------------------------------------------
# Rules fetched (issue #110)
# ---------------------------------------------------------------------------

# The sentence the block must always carry, in every state that reports an
# answer: fetching is not reading. Pinned as a constant so a rewording of the
# block cannot quietly drop the limit and leave the claim behind.
_LIMIT_SENTENCE = (
    "This says the rule text was fetched from the server, and nothing more."
)


def test_rules_fetched_names_a_domain_that_recorded_verdicts_without_them() -> None:
    # The failure this exists for (issue #110): 260 verdicts recorded against
    # rule text that was never requested, and a report that said nothing.
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.rules_fetched_domain_ids = ["d01"]
    rendered = render_report(run_state, pack)

    assert "Rules fetched (1 of 2 domain(s) never fetched)" in rendered
    assert (
        "1 domain(s) recorded rule verdicts without their rule text ever being fetched "
        "this run</strong>: Teacup Logistics Handling (d02)" in rendered
    )
    assert "Treat them as unsupported until they are redone." in rendered
    assert _LIMIT_SENTENCE in rendered


def test_rules_fetched_clean_result_states_what_it_cannot_show() -> None:
    # The clean rendering is where a reader is most likely to upgrade
    # "fetched" into "audited" on the tool's behalf, so the limit is loudest
    # exactly here.
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.rules_fetched_domain_ids = ["d01", "d02"]
    rendered = render_report(run_state, pack)

    assert (
        "All 2 domain(s) that recorded verdicts had their rule text fetched from this "
        "server first." in rendered
    )
    assert _LIMIT_SENTENCE in rendered
    assert (
        "a run that fetched every domain and then guessed would look the same here"
        in rendered
    )
    # Never the stronger claim. The block can say the rule text was fetched;
    # it can never say the rules were read or applied, and it says so.
    assert "It is not evidence that the rules were read, or applied" in rendered
    assert "rules were applied" not in rendered


def test_rules_fetched_reports_an_older_run_state_as_not_recorded() -> None:
    # An older run-state never recorded this. Unknown renders as unknown: not
    # as a clean bill of health, and not as an accusation either.
    pack = _pack()
    run_state = _base_run_state(pack)
    assert run_state.rules_fetched_domain_ids is None
    rendered = render_report(run_state, pack)

    assert "Rules fetched: not recorded" in rendered
    assert "Unknown is not a pass and not a failure" in rendered
    assert "never being fetched this run" not in rendered
    assert "had their rule text fetched from this server first" not in rendered


def test_rules_fetched_keeps_a_resumed_legacy_domain_separate_from_a_skipped_one() -> (
    None
):
    # A resumed run that carried d01 in from a record written before any of
    # this was tracked, and then recorded d02 without fetching it. The two are
    # different facts and the report keeps them apart.
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.rules_fetched_domain_ids = []
    run_state.rules_fetch_unknown_domain_ids = ["d01"]
    rendered = render_report(run_state, pack)

    assert "Rules fetched (1 of 2 domain(s) never fetched)" in rendered
    assert "Teacup Logistics Handling (d02)" in rendered
    assert (
        "1 domain(s) were carried into this run from a saved record written before the "
        "tool recorded any of this" in rendered
    )
    assert "Gnome Husbandry Record Keeping (d01)" in rendered


def test_rules_fetched_says_a_domain_fetched_after_a_resume_is_simply_fetched() -> None:
    # Positive evidence beats an absence of it: a domain listed as both
    # carried-in and fetched was fetched, and is not reported as unknown.
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.rules_fetched_domain_ids = ["d01", "d02"]
    run_state.rules_fetch_unknown_domain_ids = ["d01"]
    rendered = render_report(run_state, pack)

    assert (
        "All 2 domain(s) that recorded verdicts had their rule text fetched" in rendered
    )
    assert "carried into this run" not in rendered


def test_rules_fetched_has_nothing_to_check_when_no_domain_reached_a_verdict() -> None:
    # A could-not-run domain carries no verdicts, so there is nothing here for
    # fetched rule text to have supported. The could-not-evaluate block is
    # where that domain is reported, and saying it twice would read as two
    # separate faults.
    pack = _pack()
    run_state = RunState(
        meta=_meta(),
        config=AuditConfig(selected_domain_ids=["d01"], issue_mode="report"),
        domain_results={
            "d01": DomainResult(
                domain_id="d01", status="could-not-run", reason="no ledger file present"
            )
        },
        rules_fetched_domain_ids=[],
    )
    rendered = render_report(run_state, pack)

    assert "No selected domain recorded a rule verdict" in rendered
    assert "never being fetched this run" not in rendered


def test_not_applicable_verdict_for_unknown_rule_id_raises() -> None:
    # Same loudness the could-not-evaluate path already has: a verdict for a
    # rule id absent from the pack is a broken run, not a cosmetic gap.
    pack = _pack()
    d01 = pack.get_domain("d01")
    verdicts = _all_pass_verdicts(d01)
    verdicts.append(
        RuleVerdict(
            rule_id="D01-R99",
            verdict=Verdict.NOT_APPLICABLE,
            note="this rule id does not exist in the pack",
        )
    )
    run_state = RunState(
        meta=_meta(),
        config=AuditConfig(selected_domain_ids=["d01"], issue_mode="report"),
        domain_results={
            "d01": DomainResult(
                domain_id="d01", status="completed", rule_verdicts=verdicts
            )
        },
    )
    with pytest.raises(ReportError):
        render_report(run_state, pack)


def test_a_legacy_run_state_renders_its_unjustified_not_applicable_as_unrecorded() -> (
    None
):
    # A run-state saved before the note requirement (schema_version 3 or
    # below) must still re-render, and its note-less verdicts must read as
    # reasons nobody recorded rather than being folded in with the real ones.
    pack = _pack()
    d01 = pack.get_domain("d01")
    run_state = RunState(
        meta=_meta(),
        config=AuditConfig(selected_domain_ids=["d01"], issue_mode="report"),
        domain_results={
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=_all_not_applicable_verdicts(
                    d01, "placeholder, stripped below"
                ),
            )
        },
    )
    raw = json.loads(run_state.to_json())
    raw["schema_version"] = 3
    for verdict in raw["domain_results"]["d01"]["rule_verdicts"]:
        verdict["note"] = None
    legacy = RunState.from_json(json.dumps(raw))

    rendered = render_report(legacy, pack)
    assert "Not applicable: 4 of 4 rules verdicted" in rendered
    assert "No reason recorded for this verdict" in rendered
    assert "1 selected domain(s) had every rule set aside as not applicable" in rendered


def test_could_not_evaluate_all_clear_message_survives_the_grouping_change() -> None:
    pack = _pack()
    d01 = pack.get_domain("d01")
    d02 = pack.get_domain("d02")
    run_state = RunState(
        meta=_meta(),
        config=AuditConfig(selected_domain_ids=["d01", "d02"], issue_mode="report"),
        domain_results={
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=_all_pass_verdicts(d01),
            ),
            "d02": DomainResult(
                domain_id="d02",
                status="completed",
                rule_verdicts=_all_pass_verdicts(d02),
            ),
        },
    )
    rendered = render_report(run_state, pack)
    assert (
        "Every selected rule reached a verdict of pass, finding or not applicable. "
        "Nothing was left could-not-evaluate." in rendered
    )


def _write_single_rule_pack(tmp_path: Path, source_footer: str):
    """Build a minimal one-domain, one-rule pack whose footer is exactly
    ``*Source: {source_footer} Rule id: D01-R01. Volatility: durable.*``, for
    tests that need precise control over a rule's parsed source (e.g.
    exercising the v1/v2 citation split from issue #86)."""
    scratch = tmp_path / "pack"
    scratch.mkdir()
    (scratch / "01-domain.md").write_text(
        "# Domain 01: Solo Rule Domain\n\n"
        "**Trigger:** you are about to exercise a single rule.\n\n"
        "### 1. The only rule in this domain.\n\n"
        "Body.\n\n"
        f"*Source: {source_footer} Rule id: D01-R01. Volatility: durable.*\n",
        encoding="utf-8",
    )
    return load_pack(scratch)


def _single_finding_run_state(rule_id: str) -> RunState:
    return RunState(
        meta=_meta(),
        config=AuditConfig(selected_domain_ids=["d01"], issue_mode="report"),
        domain_results={
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=[RuleVerdict(rule_id=rule_id, verdict=Verdict.FINDING)],
                findings=[
                    Finding(
                        rule_id=rule_id,
                        severity=Severity.LOW,
                        title="a finding",
                        location="x.py",
                        body_md="x",
                        issue_title="x",
                        issue_body="x",
                    )
                ],
            )
        },
    )


def test_v2_pack_reference_line_publishes_the_source_whole_with_no_capping(
    tmp_path: Path,
) -> None:
    # A footer carrying a Verification: marker (rule-footer-format-v2)
    # marks the whole pack as migrated; citation() must skip its v1
    # safety-net capping entirely, even for a citation that contains a
    # colon-quote pattern and a preceding sentence boundary that would
    # otherwise trigger the v1 heuristic to cut it.
    pack = _write_single_rule_pack(
        tmp_path,
        'A self-contained citation. It quotes the standard directly: "here is the '
        'quoted text". Verification: checked on 2026-08-05 and ruled out two other '
        "candidates.",
    )
    assert pack.is_v2 is True
    run_state = _single_finding_run_state("D01-R01")
    rendered = render_report(run_state, pack)

    assert (
        "Reference: D01-R01: A self-contained citation. It quotes the standard "
        "directly: &quot;here is the quoted text&quot;" in rendered
    )


def test_an_oversized_v2_source_is_truncated_rather_than_failing_the_render(
    tmp_path: Path,
) -> None:
    # A v2 pack that breaks its own authoring contract by leaving narrative
    # in Source: must still produce a report. citation() applies its ceiling
    # on both branches precisely so a partly migrated pack degrades to a
    # visibly truncated reference instead of taking the whole render down.
    pack = _write_single_rule_pack(
        tmp_path, "A" * 1200 + ". Verification: checked recently."
    )
    assert pack.is_v2 is True

    rendered = render_report(_single_finding_run_state("D01-R01"), pack)

    assert "[reference truncated]" in rendered


def test_render_report_raises_when_citation_itself_returns_an_oversized_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The render-time ceiling is a backstop for a programming error in
    # citation(), not a second policy: citation() now caps unconditionally,
    # so the only way a reference arrives here oversized is that the capping
    # did not run. Breaking citation() deliberately is therefore the only
    # honest way to exercise this path, and it must fail loudly rather than
    # ship the oversized reference.
    monkeypatch.setattr(report_module, "citation", lambda source, **_: "B" * 1000)
    pack = _write_single_rule_pack(tmp_path, "A short, self-contained citation.")

    with pytest.raises(ReportError, match="reference ceiling"):
        render_report(_single_finding_run_state("D01-R01"), pack)


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
    assert (
        '<a href="https://example.invalid/standard">An external standard</a>'
        in rendered
    )
    assert (
        "checked the standard&#x27;s definition before verdicting this rule" in rendered
    )
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
                    _consulted_source(
                        rule_id="D01-R01",
                        url="https://example.invalid/a",
                        title="Source A",
                    ),
                    _consulted_source(
                        rule_id="D01-R01",
                        url="https://example.invalid/b",
                        title="Source B",
                    ),
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


def test_consulted_source_with_a_non_http_url_degrades_to_text_instead_of_raising() -> (
    None
):
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
                    _consulted_source(
                        url="file:///etc/hosts", title="a local file reference"
                    )
                ],
            )
        },
    )
    rendered = render_report(run_state, pack)
    assert "a local file reference (file:///etc/hosts)" in rendered
    assert '<a href="file:///etc/hosts">' not in rendered


def test_report_error_when_consulted_source_references_a_rule_id_outside_its_domain() -> (
    None
):
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


def test_domain_table_rows_keyed_by_domain_id_not_title(tmp_path: Path) -> None:
    # Build a two-domain pack where both domains share an identical title,
    # each with one finding, and confirm two distinct table rows appear.
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
    for domain_id in ("d01", "d02"):
        row = _domain_row(rendered, domain_id)
        assert "Same Title Domain" in row
        assert "1 of 2" in row
        assert "finding: 1" in row


def test_markdownish_splits_paragraphs_on_crlf() -> None:
    # Feedback text is now rendered as a plain, escaped, editable textarea
    # value (not markdownish), so this exercises _markdownish through the
    # one place it still runs: a finding's body_md.
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.domain_results["d01"].findings[
        0
    ].body_md = "First paragraph.\r\n\r\nSecond paragraph."
    rendered = render_report(run_state, pack)
    assert "<p>First paragraph.</p><p>Second paragraph.</p>" in rendered


def test_issue_url_with_javascript_scheme_raises() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.filed_issue_urls = {"D01-R02#1": "javascript:alert(1)"}
    with pytest.raises(ReportError):
        render_report(run_state, pack)


def test_issue_url_with_https_scheme_still_renders_as_link() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.filed_issue_urls = {"D01-R02#1": "https://example.invalid/issues/42"}
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
            "d02": DomainResult(
                domain_id="d02", status="could-not-run", reason="repository was empty"
            )
        },
    )
    rendered = render_report(run_state, pack)
    assert "repository was empty" in rendered


def test_could_not_run_domain_stops_the_completeness_banner_claiming_full_coverage() -> (
    None
):
    # A could-not-run domain has no rule_verdicts by design, so it satisfies
    # "no rule left could-not-evaluate" by construction even though zero
    # rules were actually evaluated for it. The banner must never claim full
    # coverage in that case.
    pack = _pack()
    run_state = RunState(
        meta=_meta(),
        config=AuditConfig(selected_domain_ids=["d02"], issue_mode="report"),
        domain_results={
            "d02": DomainResult(
                domain_id="d02", status="could-not-run", reason="repository was empty"
            )
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
    run_state.filed_issue_urls = {"D01-R02#1": "https://example.invalid/issues/1"}
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
    run_state.feedback_issue_url = (
        "https://github.com/rodlunt/engineering-audit/issues/9"
    )
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
        "Reference: D01-R02: invented for test fixtures only, no external source"
        in rendered
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
        '<div class="meta-label">Tool commit</div><div class="meta-value">unknown</div>'
        in rendered
    )
    assert (
        '<div class="meta-label">Rules commit</div><div class="meta-value">unknown</div>'
        in rendered
    )


# ---------------------------------------------------------------------------
# Meta block: Duration row (issue #102, server-measured vs assistant-reported)
# ---------------------------------------------------------------------------


def test_duration_row_shows_server_measurement_alongside_assistant_figure_when_they_agree() -> (
    None
):
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.meta.started = "2026-08-09T09:00:00Z"
    run_state.meta.finished = "2026-08-09T09:10:00Z"  # assistant-reported: 10m0s
    run_state.meta.server_started = "2026-08-09T09:00:02Z"
    run_state.meta.server_finished = (
        "2026-08-09T09:09:55Z"  # server-measured: 9m53s, close enough
    )
    rendered = render_report(run_state, pack)

    assert '<div class="meta-label">Duration</div>' in rendered
    assert "10m0s (server-measured: 9m53s)" in rendered
    assert "disagree" not in rendered


def test_duration_row_flags_divergence_when_assistant_reports_zero_seconds() -> None:
    # The actual defect reported in #102: two real runs recorded started ==
    # finished on audits that took minutes, and the report rendered the
    # zero-second gap without comment. The server-measured span must now be
    # shown and the mismatch called out rather than the zero figure standing
    # alone.
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.meta.started = "2026-08-10T23:41:07Z"
    run_state.meta.finished = "2026-08-10T23:41:07Z"  # assistant-reported: 0s
    run_state.meta.server_started = "2026-08-10T23:41:07Z"
    run_state.meta.server_finished = "2026-08-10T23:44:59Z"  # server-measured: 3m52s
    rendered = render_report(run_state, pack)

    assert "0s as reported by the assistant, but the server measured 3m52s" in rendered
    assert "treat the reported duration with caution" in rendered


def test_duration_row_does_not_flag_ordinary_clock_skew_on_a_short_run() -> None:
    # Two independently-read clocks (the assistant's and the server's)
    # should not be expected to agree to the second even when both are
    # honest; a few seconds of skew on a short run must not read as a
    # disagreement.
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.meta.started = "2026-08-09T09:00:00Z"
    run_state.meta.finished = "2026-08-09T09:00:05Z"  # assistant-reported: 5s
    run_state.meta.server_started = "2026-08-09T09:00:01Z"
    run_state.meta.server_finished = "2026-08-09T09:00:08Z"  # server-measured: 7s
    rendered = render_report(run_state, pack)

    assert "disagree" not in rendered
    assert "5s (server-measured: 7s)" in rendered


def test_duration_row_says_unmeasured_when_server_timestamps_predate_the_field() -> (
    None
):
    # A run-state.json written before this fix has server_started and
    # server_finished as None (never a field it could have populated), not
    # as a zero or an agreeing figure. The row must say the duration could
    # not be checked, not silently fall back to showing the unchecked
    # assistant figure as if it had been.
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.meta.server_started = None
    run_state.meta.server_finished = None
    rendered = render_report(run_state, pack)

    assert "not measured by the server, so this could not be checked" in rendered
    assert "disagree" not in rendered


def test_duration_row_on_a_resumed_run_does_not_flag_the_legitimate_wall_clock_gap() -> (
    None
):
    # A resumed run's server_started is kept from the original begin_run,
    # not reset at resume time (see _resume_run in server.py), so it spans
    # the same real interval as the assistant-reported started/finished,
    # including whatever gap the crash and resume introduced. That gap is
    # not audit work, but it is real time, and an honestly-reported
    # assistant duration should agree with it rather than trip the
    # divergence check just because a resume happened.
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.meta.started = "2026-08-09T09:00:00Z"
    run_state.meta.finished = "2026-08-09T11:00:00Z"  # 2 hours, spans the resume gap
    run_state.meta.server_started = (
        "2026-08-09T09:00:01Z"  # stamped at the original begin_run
    )
    run_state.meta.server_finished = (
        "2026-08-09T11:00:04Z"  # stamped at the final render_report
    )
    rendered = render_report(run_state, pack)

    assert "disagree" not in rendered
    assert "(server-measured:" in rendered


# ---------------------------------------------------------------------------
# Feedback section: tick boxes, embedded JSON, script-injection escaping
# ---------------------------------------------------------------------------


def test_feedback_consent_checkboxes_prefilled_true_from_config() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.config.telemetry_consent = TelemetryConsent(
        coverage=True,
        rollup=True,
        self_assessment=True,
        environment=True,
        consulted_sources=True,
        verdict_distribution=True,
        duration=True,
        rules_fetched=True,
    )
    rendered = render_report(run_state, pack)

    for input_id in (
        "consent-coverage",
        "consent-rollup",
        "consent-self-assessment",
        "consent-environment",
        "consent-consulted-sources",
        "consent-verdict-distribution",
        "consent-duration",
        "consent-rules-fetched",
    ):
        match = re.search(rf'<input type="checkbox" id="{input_id}"([^>]*)>', rendered)
        assert match is not None, f"checkbox {input_id!r} not found"
        assert "checked" in match.group(1), f"checkbox {input_id!r} expected checked"


def test_feedback_consent_checkboxes_prefilled_false_from_config() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.config.telemetry_consent = TelemetryConsent(
        coverage=False,
        rollup=False,
        self_assessment=False,
        environment=False,
        consulted_sources=False,
        verdict_distribution=False,
        duration=False,
        rules_fetched=False,
    )
    rendered = render_report(run_state, pack)

    for input_id in (
        "consent-coverage",
        "consent-rollup",
        "consent-self-assessment",
        "consent-environment",
        "consent-consulted-sources",
        "consent-verdict-distribution",
        "consent-duration",
        "consent-rules-fetched",
    ):
        match = re.search(rf'<input type="checkbox" id="{input_id}"([^>]*)>', rendered)
        assert match is not None, f"checkbox {input_id!r} not found"
        assert "checked" not in match.group(1), (
            f"checkbox {input_id!r} expected unchecked"
        )


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
    # Issue #120: the section key list used to be hand-listed here (nine
    # literal strings), which meant this test verified the sections that
    # existed rather than proving every section build_feedback_sections
    # returns is verified. A tenth key would simply not have been checked,
    # and the test would still have passed. Iterating expected_sections'
    # own keys, and cross-checking them against TelemetryConsent's own
    # fields, means a section wired into one but not the other now fails
    # this test immediately instead of shipping unnoticed.
    pack = _pack()
    run_state = _base_run_state(pack)
    rendered = render_report(run_state, pack)

    data = _extract_json_script(rendered, "feedback-sections-data")
    expected_sections = build_feedback_sections(
        run_state.meta,
        run_state.domain_results,
        rules_fetched_domain_ids=run_state.rules_fetched_domain_ids,
        rules_fetch_unknown_domain_ids=run_state.rules_fetch_unknown_domain_ids,
    )

    for key, expected_text in expected_sections.items():
        assert key in data, (
            f"{key!r} is one of build_feedback_sections' returned keys but is missing "
            "from the report's embedded feedback-sections-data JSON block"
        )
        assert data[key] == expected_text
    assert data["email"] == "rodneylunt79+audit-feedback@gmail.com"

    # Every section except run_metadata (always sent, never a consent
    # choice) must correspond to exactly one TelemetryConsent flag: neither
    # side may have a key the other lacks. This is schema.py's half of the
    # cross-check.
    consent_keys = set(expected_sections) - {"run_metadata"}
    consent_fields = set(TelemetryConsent.model_fields)
    assert consent_keys == consent_fields, (
        "build_feedback_sections' keys and TelemetryConsent's fields have drifted "
        f"apart: sections only has {consent_keys - consent_fields or 'nothing'}, "
        f"TelemetryConsent only has {consent_fields - consent_keys or 'nothing'}"
    )

    # The embedded JSON also carries the same key list explicitly (report.js
    # derives its payload loop from it), so that must agree too.
    assert set(data["consent_keys"]) == consent_keys

    # Cross-check against the MCP path's own builder: with only one section
    # consented, build_feedback_body's output must be exactly the always-on
    # run-metadata chunk plus that one section, joined the same way the
    # report's own JS joins ticked sections.
    for key in consent_keys:
        consent_kwargs = {field: False for field in consent_fields}
        consent_kwargs[key] = True
        body = build_feedback_body(
            None,
            run_state.meta,
            TelemetryConsent(**consent_kwargs),
            run_state.domain_results,
            rules_fetched_domain_ids=run_state.rules_fetched_domain_ids,
            rules_fetch_unknown_domain_ids=run_state.rules_fetch_unknown_domain_ids,
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
        confidence="high", limits="</script><script>alert(1)</script>"
    )
    rendered = render_report(run_state, pack)

    # The raw, case-sensitive sequence "</script>" must only ever appear
    # once in the whole document from this point on: the real closing tag
    # of the feedback JSON block. If the malicious text had broken out, a
    # second literal "</script>" would appear earlier, right after the
    # injected payload.
    start = rendered.index(
        '<script type="application/json" id="feedback-sections-data">'
    )
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


def test_issue_already_filed_via_run_state_renders_unticked_disabled_with_link() -> (
    None
):
    # filed_issue_urls is a field on RunState itself (set by render_report's
    # caller from a previous file_issues call, or carried over from a saved
    # run-state.json), so a finding already filed renders disabled with its
    # link server-side, with no JS needed to discover it.
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.filed_issue_urls = {"D01-R02#1": "https://example.invalid/issues/42"}
    rendered = render_report(run_state, pack)

    assert '<input type="checkbox" id="issue-check-0" disabled>' in rendered
    assert 'href="https://example.invalid/issues/42">already filed</a>' in rendered
    # An already-filed issue must not also render as ticked and selectable.
    assert '<input type="checkbox" id="issue-check-0" checked' not in rendered


def test_two_findings_on_one_rule_each_get_their_own_already_filed_link() -> None:
    # Two findings from the same rule must not share one already-filed link:
    # server.py's file_issues keys them per finding ("<rule id>#<n>"), and
    # RunState.filed_issue_urls has used that same shape since schema_version
    # 3, so each finding's own url must render against its own checkbox.
    pack = _pack()
    d01 = pack.get_domain("d01")
    assert d01 is not None
    verdicts = _all_pass_verdicts(d01)
    verdicts[1] = RuleVerdict(rule_id="D01-R02", verdict=Verdict.FINDING)
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
                        rule_id="D01-R02",
                        severity=Severity.HIGH,
                        title="bed-14 has no shared-bed flag",
                        location="ledger/beds.py:42",
                        body_md="bed-14 holds two gnomes.",
                        issue_title="Set shared-bed flag for bed-14",
                        issue_body="bed-14 has two occupants and no shared-bed flag.",
                    ),
                    Finding(
                        rule_id="D01-R02",
                        severity=Severity.MEDIUM,
                        title="bed-19 has no shared-bed flag",
                        location="ledger/beds.py:57",
                        body_md="bed-19 holds two gnomes.",
                        issue_title="Set shared-bed flag for bed-19",
                        issue_body="bed-19 has two occupants and no shared-bed flag.",
                    ),
                ],
            )
        },
        filed_issue_urls={
            "D01-R02#1": "https://example.invalid/issues/1",
            "D01-R02#2": "https://example.invalid/issues/2",
        },
    )
    rendered = render_report(run_state, pack)

    assert '<input type="checkbox" id="issue-check-0" disabled>' in rendered
    assert '<input type="checkbox" id="issue-check-1" disabled>' in rendered
    assert 'href="https://example.invalid/issues/1">already filed</a>' in rendered
    assert 'href="https://example.invalid/issues/2">already filed</a>' in rendered


def test_schema_version_2_file_with_bare_rule_id_key_migrates_and_report_links_the_first_finding() -> (
    None
):
    # A run-state.json written before schema_version 3 has filed_issue_urls
    # keyed by bare rule id, holding exactly one url per rule: whichever was
    # filed first (see the old projection this replaced, pinned by a test in
    # test_server.py that has since been updated for the new shape).
    # RunState.from_json migrates that bare key to "<rule id>#1" losslessly,
    # and the rendered report must link the first finding using it.
    pack = _pack()
    run_state = _base_run_state(pack)
    raw = json.loads(run_state.to_json())
    raw["schema_version"] = 2
    raw["filed_issue_urls"] = {"D01-R02": "https://example.invalid/issues/1"}
    restored = RunState.from_json(json.dumps(raw))

    assert restored.filed_issue_urls == {
        "D01-R02#1": "https://example.invalid/issues/1"
    }

    rendered = render_report(restored, pack)
    assert '<input type="checkbox" id="issue-check-0" disabled>' in rendered
    assert 'href="https://example.invalid/issues/1">already filed</a>' in rendered


def test_issue_button_rows_present_at_top_and_bottom() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    rendered = render_report(run_state, pack)

    assert rendered.count("Add selected issues to GitHub (requires GitHub PAT)") == 2
    assert (
        rendered.count("Copy selected issues (for pasting into an LLM or editor)") == 2
    )
    # The GitHub-filing form itself appears exactly once, not once per row.
    assert rendered.count('id="github-file-form"') == 1
    assert rendered.count('id="gh-repo"') == 1
    assert rendered.count('id="gh-pat"') == 1


def test_issue_embedded_body_ends_with_shared_trailing_line_byte_identical_to_file_issues() -> (
    None
):
    # build_issue_trailing_line's core sentence (rule, severity, location,
    # reference) is still the exact text file_issues sends to gh issue
    # create for this same finding (see test_server.py's
    # test_file_issues_confirm_files_one_issue_per_finding): the two can
    # never describe those four facts differently. The report's own issues
    # section additionally passes this finding's domain confidence and
    # fetch status (issue #130), which server.py's file_issues call does
    # not (yet) supply, so the report's copy embeds one extra sentence
    # naming them; d01 here has self_assessment confidence "high" and no
    # rules_fetched_domain_ids recorded on the run, i.e. "not recorded".
    expected_body = (
        "bed-14 has two occupants and no shared-bed flag. See ledger/beds.py:42.\n\n"
        "Found by an engineering-practice audit (rule D01-R02, severity high, "
        "at ledger/beds.py:42). This finding's domain: self-assessed confidence "
        "high; whether its rule text was fetched this run is not recorded. "
        "Reference: invented for test fixtures only, no external source"
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
    assert (
        '<button type="button" class="print-button" onclick="window.print()">'
        in rendered
    )


def test_print_stylesheet_hides_interactive_filing_ui_and_forces_light_palette() -> (
    None
):
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


# ---------------------------------------------------------------------------
# #122: computed headline, page order, severity sorting, pre-tick policy, and
# a could-not-run domain that no longer renders as a bare zero.
# ---------------------------------------------------------------------------


def _mixed_severity_run_state(pack) -> RunState:
    """A run where the *later* domain in rules-pack order holds the *higher*
    severity finding, which is the case the old rendering ordered wrongly."""
    d01 = pack.get_domain("d01")
    d02 = pack.get_domain("d02")

    d01_verdicts = _all_pass_verdicts(d01)
    d01_verdicts[1] = RuleVerdict(rule_id="D01-R02", verdict=Verdict.FINDING)
    d02_verdicts = _all_pass_verdicts(d02)
    d02_verdicts[0] = RuleVerdict(rule_id="D02-R01", verdict=Verdict.FINDING)

    return RunState(
        meta=_meta(),
        config=AuditConfig(selected_domain_ids=["d01", "d02"], issue_mode="github"),
        domain_results={
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=d01_verdicts,
                findings=[
                    Finding(
                        rule_id="D01-R02",
                        severity=Severity.LOW,
                        title="A low-severity gnome problem",
                        location="ledger/beds.py:42",
                        body_md="Minor.",
                        issue_title="Low gnome issue",
                        issue_body="Minor gnome issue.",
                    )
                ],
                self_assessment=SelfAssessment(confidence="high", limits=""),
                coverage=Coverage(files_inspected=12, files_skipped=1),
            ),
            "d02": DomainResult(
                domain_id="d02",
                status="completed",
                rule_verdicts=d02_verdicts,
                findings=[
                    Finding(
                        rule_id="D02-R01",
                        severity=Severity.CRITICAL,
                        title="A critical teacup problem",
                        location="routes/teacups.py:9",
                        body_md="Serious.",
                        issue_title="Critical teacup issue",
                        issue_body="Serious teacup issue.",
                    )
                ],
                self_assessment=SelfAssessment(confidence="medium", limits=""),
                coverage=Coverage(files_inspected=5, files_skipped=0),
            ),
        },
    )


def test_headline_leads_with_critical_and_high_and_carries_every_base() -> None:
    # The sentence the report now opens with. Every figure in it ships with
    # the base it came out of: "1 finding" alone would invite the reader to
    # supply their own denominator (D16-R03).
    pack = _pack()
    rendered = render_report(_base_run_state(pack), pack)

    assert (
        "1 high finding needs attention first, out of 1 finding across "
        "7 rules verdicted in 2 of 2 domains." in rendered
    )


def test_headline_block_comes_before_the_meta_grid_and_the_findings_section() -> None:
    pack = _pack()
    rendered = render_report(_base_run_state(pack), pack)

    headline_pos = rendered.index('<div class="headline">')
    assert headline_pos < rendered.index('<div class="meta-grid">')
    assert headline_pos < rendered.index('<section id="findings">')


def test_headline_falls_back_to_a_descriptive_line_with_no_critical_or_high() -> None:
    # D16-R10: a generated sentence must not manufacture urgency the counts
    # do not support. With only a low finding there is no "attention first".
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.domain_results["d01"].findings[0].severity = Severity.LOW
    rendered = render_report(run_state, pack)

    assert "needs attention first" not in rendered
    assert (
        "No critical or high findings. 1 finding of medium or low severity was "
        "recorded, across 7 rules verdicted in 2 of 2 domains." in rendered
    )


def test_headline_says_so_when_nothing_was_found_at_all() -> None:
    pack = _pack()
    d01 = pack.get_domain("d01")
    d02 = pack.get_domain("d02")
    run_state = RunState(
        meta=_meta(),
        config=AuditConfig(selected_domain_ids=["d01", "d02"], issue_mode="report"),
        domain_results={
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=_all_pass_verdicts(d01),
            ),
            "d02": DomainResult(
                domain_id="d02",
                status="completed",
                rule_verdicts=_all_pass_verdicts(d02),
            ),
        },
    )
    rendered = render_report(run_state, pack)

    assert (
        "No findings were recorded, across 7 rules verdicted in 2 of 2 domains."
        in rendered
    )
    # A clean run still says what it did not have to set aside, rather than
    # leaving the reader to assume it.
    assert (
        "No rule was set aside as not applicable, none was left could not "
        "evaluate, and all 2 selected domains ran." in rendered
    )


def test_headline_caveat_names_the_coverage_gaps_with_their_bases() -> None:
    pack = _pack()
    d01 = pack.get_domain("d01")
    reason = "this repository houses no gnomes"
    run_state = RunState(
        meta=_meta(),
        config=AuditConfig(selected_domain_ids=["d01", "d02"], issue_mode="report"),
        domain_results={
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=_all_not_applicable_verdicts(d01, reason),
            ),
            "d02": DomainResult(
                domain_id="d02",
                status="could-not-run",
                reason="ran out of context before reaching this domain",
            ),
        },
    )
    rendered = render_report(run_state, pack)

    assert (
        "4 of 4 rules were set aside as not applicable and 1 of 2 domains did not "
        "run at all, so this is not a clean bill of health" in rendered
    )


def test_headline_contains_no_percentage() -> None:
    # Preserved from before the restructure: no share ships without its base,
    # and the simplest way to keep that true is to ship no shares at all.
    pack = _pack()
    rendered = render_report(_base_run_state(pack), pack)
    headline = rendered[
        rendered.index('<div class="headline">') : rendered.index(
            'class="print-button"'
        )
    ]
    assert "%" not in headline
    assert "percent" not in headline.lower()


def test_tool_performance_summary_now_sits_below_findings_and_issues() -> None:
    # It used to be the first six screens of the report, with the first
    # finding on printed page 7 of 26.
    pack = _pack()
    rendered = render_report(_base_run_state(pack), pack)

    perf_pos = rendered.index('<section id="performance-summary">')
    assert perf_pos > rendered.index('<section id="findings">')
    assert perf_pos > rendered.index('<section id="issues">')
    assert perf_pos < rendered.index('<section id="feedback">')


def test_findings_are_ordered_by_severity_not_by_rules_pack_domain_order() -> None:
    pack = _pack()
    rendered = render_report(_mixed_severity_run_state(pack), pack)

    findings_html = rendered[
        rendered.index('<section id="findings">') : rendered.index(
            '<section id="issues">'
        )
    ]
    critical_pos = findings_html.index("A critical teacup problem")
    low_pos = findings_html.index("A low-severity gnome problem")
    assert critical_pos < low_pos, (
        "the critical finding is in the second domain in rules-pack order and "
        "must still be rendered first"
    )
    # The severity group heading carries its base, like every other count.
    assert "<h3>Critical: 1 of 2 findings</h3>" in findings_html
    assert "<h3>Low: 1 of 2 findings</h3>" in findings_html


def test_each_finding_card_names_its_own_domain() -> None:
    # Severity-first ordering removes the per-domain heading that used to
    # carry this, so the domain moves onto the card rather than being lost.
    pack = _pack()
    rendered = render_report(_mixed_severity_run_state(pack), pack)

    assert '<span class="finding-domain">Teacup Logistics Handling</span>' in rendered
    assert (
        '<span class="finding-domain">Gnome Husbandry Record Keeping</span>' in rendered
    )


def test_only_critical_and_high_issues_are_ticked_on_load() -> None:
    pack = _pack()
    rendered = render_report(_mixed_severity_run_state(pack), pack)

    issues_html = rendered[rendered.index('<section id="issues">') :]
    # The critical finding is index 1 in recorded order (d01's low finding is
    # index 0), and the issues list keeps recorded order so the already-filed
    # keying stays in step with server.py.
    assert '<input type="checkbox" id="issue-check-0" onchange=' in issues_html
    assert '<input type="checkbox" id="issue-check-1" checked onchange=' in issues_html
    assert "1 of 2 issues is ticked: the critical and high findings." in issues_html
    assert "not hidden" in issues_html


def test_a_could_not_run_domain_is_not_a_bare_zero_in_the_rollup() -> None:
    # #122 point 5. _fully_not_applicable_domain_ids deliberately excludes a
    # domain with no verdicts, so the could-not-run branch used to fall
    # through and render "d02: Teacup Logistics Handling: 0", the exact
    # string a domain swept clean renders. The rollup row is a table row
    # since #123; the numeral is still suppressed there.
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.domain_results["d02"] = DomainResult(
        domain_id="d02",
        status="could-not-run",
        reason="ran out of context before reaching this domain",
    )
    rendered = render_report(run_state, pack)

    row = _domain_row(rendered, "d02")
    assert "<strong>did not run, nothing checked</strong>" in row
    assert "did not run</td>" in row
    assert "no coverage reported" in row
    assert "no verdicts to check" in row
    # No count of any kind on this row: there is no denominator one could be
    # a count out of.
    assert "of 0 rules" not in row
    assert "0 of" not in row


def test_domains_with_no_findings_separates_set_aside_from_never_ran() -> None:
    # Three domains produce zero findings for three different reasons, and
    # the report must not tell them apart only by their absence. The
    # swept-clean row is asserted in
    # test_a_wholly_not_applicable_domain_is_distinguishable_from_one_swept_clean
    # above; the fixture pack has only two loadable domains, so the two
    # nothing-was-checked cases are pinned here.
    pack = _pack()
    d01 = pack.get_domain("d01")
    reason = "this repository houses no gnomes"
    run_state = RunState(
        meta=_meta(),
        config=AuditConfig(selected_domain_ids=["d01", "d02"], issue_mode="report"),
        domain_results={
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=_all_not_applicable_verdicts(d01, reason),
            ),
            "d02": DomainResult(
                domain_id="d02",
                status="could-not-run",
                reason="ran out of context before reaching this domain",
            ),
        },
    )
    rendered = render_report(run_state, pack)
    findings_html = rendered[
        rendered.index('<section id="findings">') : rendered.index(
            '<section id="issues">'
        )
    ]

    assert "<h3>Domains with no findings: 2 of 2</h3>" in findings_html
    assert (
        "d01: Gnome Husbandry Record Keeping: <strong>no findings, and nothing "
        "checked</strong>: all 4 of 4 rule(s) in this domain were set aside as not "
        "applicable." in findings_html
    )
    assert (
        "d02: Teacup Logistics Handling: <strong>did not run, nothing "
        "checked</strong>: ran out of context" in findings_html
    )


# ---------------------------------------------------------------------------
# #123: one per-domain table with inline bars, and a base on every number.
# ---------------------------------------------------------------------------


def _domain_table_html(rendered: str) -> str:
    match = re.search(r'<div class="domain-table-wrap">.*?</div>', rendered, re.DOTALL)
    assert match is not None, "no per-domain table found"
    return match.group(0)


def test_one_table_replaces_the_five_per_domain_lists() -> None:
    # The five one-column lists a reader used to join by domain title by eye:
    # coverage, findings by domain, not applicable by domain, self-assessment
    # by domain, rules fetched by domain.
    pack = _pack()
    rendered = render_report(_base_run_state(pack), pack)

    assert "<h3>Coverage</h3>" not in rendered
    assert "<h3>By domain</h3>" not in rendered
    assert "<h3>Self-assessment by domain</h3>" not in rendered

    table = _domain_table_html(rendered)
    for header in (
        "Domain",
        "Rule verdicts",
        "Findings",
        "Files",
        "Confidence",
        "Rules fetched",
    ):
        assert f'<th scope="col">{header}</th>' in table


def test_every_domain_gets_exactly_one_row_including_the_clean_one() -> None:
    pack = _pack()
    rendered = render_report(_base_run_state(pack), pack)
    table = _domain_table_html(rendered)

    assert table.count('<span class="domain-id">') == 2
    assert '<span class="domain-id">d01</span>' in table
    assert '<span class="domain-id">d02</span>' in table


def test_verdict_cell_carries_a_bar_and_the_same_numbers_in_words() -> None:
    # The decision recorded on #123: length carries the quantity (D16-R05),
    # not colour intensity, and the numerals sit in the same cell so nothing
    # in the table is discoverable by colour alone (D16-R16) and the chart
    # has its text alternative for free (D16-R17).
    pack = _pack()
    rendered = render_report(_base_run_state(pack), pack)
    row = _domain_row(rendered, "d01")

    assert '<span class="vbar-track" aria-hidden="true">' in row
    for css_class in ("seg-pass", "seg-finding", "seg-cne"):
        assert f'<span class="vseg {css_class}" style="width:' in row
    # d01: 2 pass, 1 finding, 0 not applicable, 1 could not evaluate, of 4.
    assert (
        "pass: 2, finding: 1, not applicable: 0, could not evaluate: 1, "
        "of 4 rules verdicted" in row
    )


def test_bars_are_drawn_to_one_scale_so_a_smaller_domain_draws_a_shorter_bar() -> None:
    # Stretching every bar to full width would make a 3-rule domain and a
    # 4-rule domain the same length, which is the one thing the bar is there
    # to show.
    pack = _pack()
    rendered = render_report(_base_run_state(pack), pack)

    widths = {}
    for domain_id in ("d01", "d02"):
        match = re.search(
            r'<span class="vbar" style="width:([\d.]+)%"',
            _domain_row(rendered, domain_id),
        )
        assert match is not None, f"no bar drawn for {domain_id}"
        widths[domain_id] = float(match.group(1))

    # d01 verdicted 4 rules, d02 verdicted 3, and 4 is the run's largest.
    assert widths["d01"] == 100.0
    assert widths["d02"] == pytest.approx(75.0)


def test_no_separate_chart_block_is_added_above_the_table() -> None:
    # The inline bar is the chart, placed in the row it describes. A second
    # chart block would need its own text alternative and would be one more
    # thing to keep in step with the table.
    pack = _pack()
    rendered = render_report(_base_run_state(pack), pack)
    assert rendered.count('class="vbar-track"') == 2
    assert "<svg" not in rendered


def test_no_number_in_the_table_ships_without_its_base() -> None:
    # D16-R03. The table's own counts are all "n of m" or "…, of m rules
    # verdicted"; the one cell that could have carried a bare summed figure
    # says why it is not summing instead of going blank.
    pack = _pack()
    rendered = render_report(_base_run_state(pack), pack)
    table = _domain_table_html(rendered)

    assert "%" not in re.sub(r'style="[^"]*"', "", table)
    assert table.count("of 4 rules verdicted") >= 1
    assert table.count("of 3 rules verdicted") >= 1


def test_findings_cell_keeps_all_four_severities_including_the_zeros() -> None:
    pack = _pack()
    rendered = render_report(_base_run_state(pack), pack)

    assert "0 critical, <strong>1 high</strong>, 0 medium, 0 low" in _domain_row(
        rendered, "d01"
    )
    assert "0 critical, 0 high, 0 medium, 0 low" in _domain_row(rendered, "d02")


def test_rules_fetched_column_and_the_block_below_agree_about_a_domain() -> None:
    # One shared status map, so a domain cannot read "no" in the table and be
    # missing from the callout, or the reverse.
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.rules_fetched_domain_ids = ["d01"]
    rendered = render_report(run_state, pack)

    assert _domain_row(rendered, "d01").endswith("<td>yes</td></tr>")
    assert _domain_row(rendered, "d02").endswith("<td>no</td></tr>")
    assert (
        "1 domain(s) recorded rule verdicts without their rule text ever being "
        "fetched this run" in rendered
    )
    assert "Teacup Logistics Handling (d02)" in rendered


def test_rules_fetched_column_says_not_recorded_for_a_legacy_run_state() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.rules_fetched_domain_ids = None
    rendered = render_report(run_state, pack)

    assert _domain_row(rendered, "d01").endswith("<td>not recorded</td></tr>")
    assert _domain_row(rendered, "d02").endswith("<td>not recorded</td></tr>")


def test_totals_row_refuses_to_sum_files_across_domains() -> None:
    # Issue #87's inflated cross-domain file total must not come back in as a
    # tidy-looking table footer.
    pack = _pack()
    rendered = render_report(_base_run_state(pack), pack)
    table = _domain_table_html(rendered)

    assert "All 2 selected domains" in table
    assert (
        "pass: 5, finding: 1, not applicable: 0, could not evaluate: 1, "
        "of 7 rules verdicted" in table
    )
    assert "not summed: a file two domains both opened would count twice" in table
    assert "17 inspected" not in table


def test_self_assessment_limits_survive_the_move_into_the_table() -> None:
    # Confidence became a column; the free-text limits could not, and they
    # are the one part of a self-assessment that can contradict the
    # confidence beside it, so they get their own block rather than being
    # dropped.
    pack = _pack()
    rendered = render_report(_base_run_state(pack), pack)

    assert "1 of 2 domains reported a limit on its own assessment" in rendered
    assert "d02: Teacup Logistics Handling: did not check archived routes" in rendered


def test_self_assessment_limits_block_still_renders_when_nobody_reported_one() -> None:
    # A vanished block and a run where every domain claimed no limits look
    # identical otherwise.
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.domain_results["d02"].self_assessment.limits = ""
    rendered = render_report(run_state, pack)

    assert (
        "None of the 2 selected domains reported a limit on their own assessment."
        in rendered
    )


# ---------------------------------------------------------------------------
# #124: collapse the evidence, never the signal.
# ---------------------------------------------------------------------------


def _summaries(rendered: str) -> list[str]:
    return re.findall(r"<summary>(.*?)</summary>", rendered, re.DOTALL)


def test_every_summary_carries_a_number() -> None:
    # The rule that makes or breaks this issue. A summary reading "Not
    # applicable", where the reader must click to learn anything, undoes the
    # work of #100.
    pack = _pack()
    rendered = render_report(_base_run_state(pack), pack)
    summaries = _summaries(rendered)

    assert summaries, "nothing is collapsed, so nothing to check"
    for summary in summaries:
        assert re.search(r"\d", summary), (
            f"summary carries no number and is not sufficient on its own: {summary!r}"
        )


def test_meta_grid_collapses_behind_a_summary_that_identifies_the_run() -> None:
    pack = _pack()
    rendered = render_report(_base_run_state(pack), pack)

    match = re.search(
        r'<details class="meta-details"><summary>(.*?)</summary>', rendered, re.DOTALL
    )
    assert match is not None, "the meta grid is not collapsed"
    summary = match.group(1)
    assert "widgets-app" in summary
    assert "abc1234" in summary
    assert "claude-sonnet-5" in summary
    assert "13 recorded fields" in summary
    # The grid itself is still there, behind it.
    assert '<div class="meta-grid">' in rendered


def test_domains_with_no_findings_summary_splits_the_three_kinds_of_zero() -> None:
    # "Domains with no findings: 2 of 2" on its own is the sentence that hid
    # the difference in the first place, so the split is on the summary line
    # rather than behind it.
    pack = _pack()
    d01 = pack.get_domain("d01")
    run_state = RunState(
        meta=_meta(),
        config=AuditConfig(selected_domain_ids=["d01", "d02"], issue_mode="report"),
        domain_results={
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=_all_not_applicable_verdicts(d01, "no gnomes here"),
            ),
            "d02": DomainResult(
                domain_id="d02",
                status="could-not-run",
                reason="ran out of context before reaching this domain",
            ),
        },
    )
    rendered = render_report(run_state, pack)

    assert (
        "<summary>Domains with no findings: 2 of 2. 0 audited and clean, 1 with every "
        "rule set aside as not applicable, 1 that did not run at all.</summary>"
        in rendered
    )


def test_rule_id_lists_are_never_put_behind_a_closed_details() -> None:
    # #124's second trap. Find-in-page inside a closed <details> varies by
    # browser, and a reader who cannot find a rule id with Ctrl+F concludes
    # it is not in the report. Domain ids are safe to collapse because the
    # per-domain table always carries them; rule ids have no such
    # always-open home, so their lists stay expanded.
    pack = _pack()
    d01 = pack.get_domain("d01")
    verdicts = _all_pass_verdicts(d01)
    verdicts[0] = RuleVerdict(
        rule_id="D01-R01",
        verdict=Verdict.NOT_APPLICABLE,
        note="this repository ships no gnome roster",
    )
    verdicts[1] = RuleVerdict(
        rule_id="D01-R02",
        verdict=Verdict.COULD_NOT_EVALUATE,
        note="the ledger lives outside this repository",
    )
    run_state = RunState(
        meta=_meta(),
        config=AuditConfig(selected_domain_ids=["d01"], issue_mode="report"),
        domain_results={
            "d01": DomainResult(
                domain_id="d01", status="completed", rule_verdicts=verdicts
            )
        },
    )
    rendered = render_report(run_state, pack)

    # Control: both rule ids really are in the page, so an absence below
    # would mean something.
    assert "D01-R01 (1 rule)" in rendered
    assert "D01-R02 (1 rule)" in rendered

    # Searched over the document body only: the stylesheet above it cites
    # d16 rule ids in its own comments, and "<details {" in a CSS selector
    # is not an element.
    body = rendered[rendered.index("</style>") :]
    for match in re.finditer(
        r'<details(?: class="[^"]*")?>(.*?)</details>', body, re.DOTALL
    ):
        assert not re.search(r"\bD\d{2}-R\d{2}\b", match.group(1)), (
            f"a rule id was put behind a collapsed <details>: {match.group(1)[:200]!r}"
        )


def test_collapsed_blocks_are_closed_by_default() -> None:
    # An always-open <details> would be a disclosure widget that discloses
    # nothing, which is furniture.
    pack = _pack()
    rendered = render_report(_base_run_state(pack), pack)
    assert "<details open" not in rendered


# ---------------------------------------------------------------------------
# Issue #128: markdown emphasis is stripped, never rendered.
#
# Decision recorded on the issue: strip, do not render. Rendering untrusted
# assistant-authored finding text into HTML would need a mature CommonMark
# renderer plus a mature HTML sanitiser, two dependencies this project does
# not carry, for a gain that is purely cosmetic. citation() text (our own,
# from the rules pack) and finding text (assistant-authored, untrusted) are
# treated the same way at the point each reaches a reader: every run of
# literal asterisks is removed.
# ---------------------------------------------------------------------------


def test_nested_markdown_emphasis_in_a_citation_is_stripped_from_the_reference_line(
    tmp_path: Path,
) -> None:
    # Real rules-pack footers wrap the whole Source: fragment in markdown
    # emphasis and often nest a second pair around a cited work's own
    # title, e.g. "*Source: ... (Halpin, *Object-Role Modeling: an
    # overview*, orm.net), CSDP step 1. Rule id: ...*". citation() already
    # drops the outer wrapper's leading asterisk (it starts matching after
    # "Source:"), but the inner pair around the title used to survive
    # verbatim into the rendered reference line.
    pack = _write_single_rule_pack(
        tmp_path,
        "A paper (Halpin, *An Overview of a Method*, example.invalid), step 1.",
    )
    rendered = render_report(_single_finding_run_state("D01-R01"), pack)

    reference_match = re.search(
        r'<div class="finding-reference">(.*?)</div>', rendered, re.DOTALL
    )
    assert reference_match is not None
    assert "*" not in reference_match.group(1)
    assert "An Overview of a Method" in reference_match.group(1)


def test_finding_body_markdown_emphasis_renders_as_clean_prose() -> None:
    # A real tester run's finding bodies looked exactly like this: an
    # assistant defaulting to markdown even though the report never renders
    # it, leaving literal "**" in what the reader sees.
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.domain_results["d01"].findings[0].body_md = (
        "**The issue**: bed-14 holds two gnomes.\n\n"
        "**Why it matters**: the census undercounts.\n\n"
        "**Suggested fix**: set the shared-bed flag."
    )
    rendered = render_report(run_state, pack)

    body_match = re.search(
        r'<div class="finding-body">(.*?)</div>\s*<div class="finding-reference">',
        rendered,
        re.DOTALL,
    )
    assert body_match is not None
    body_html = body_match.group(1)
    assert "*" not in body_html
    assert "The issue: bed-14 holds two gnomes." in body_html
    assert "Suggested fix: set the shared-bed flag." in body_html


def test_finding_title_markdown_emphasis_is_stripped() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.domain_results["d01"].findings[0].title = "Two **gnomes** share bed-14"
    rendered = render_report(run_state, pack)
    assert "<strong>Two gnomes share bed-14</strong>" in rendered
    assert "**gnomes**" not in rendered


def test_issues_data_payload_strips_markdown_from_title_and_body() -> None:
    # Both markdown sources (citations and finding text) flow into the
    # issues-data payload too (issue #128): before this fix, a filed
    # GitHub issue rendered the assistant's markdown fine while the report
    # the user read did not, so the two artefacts disagreed about the same
    # text. Stripping both makes them agree on plain prose instead.
    pack = _pack()
    run_state = _base_run_state(pack)
    finding = run_state.domain_results["d01"].findings[0]
    finding.issue_title = "**Set shared-bed flag** for bed-14"
    finding.issue_body = (
        "**The issue**: bed-14 has two occupants.\n\n**Suggested fix**: set the flag."
    )
    rendered = render_report(run_state, pack)

    data = _extract_json_script(rendered, "issues-data")
    issue = data["issues"][0]
    assert "*" not in issue["title"]
    assert "*" not in issue["body"]
    assert issue["title"] == "Set shared-bed flag for bed-14"
    assert "The issue: bed-14 has two occupants." in issue["body"]


def test_no_literal_asterisk_survives_into_findings_or_issues_sections(
    tmp_path: Path,
) -> None:
    # The comprehensive check: every field that can carry markdown (a
    # citation with a nested title, a finding's title, body, issue_title
    # and issue_body) carries it in this run, and no literal '*' reaches
    # the Findings or Issues sections at all. Scoped to those two sections
    # (not the whole page) because report.js, this tool's own script, is
    # legitimately allowed a literal '*' in a comment or a regex.
    pack = _write_single_rule_pack(
        tmp_path,
        "A paper (Halpin, *An Overview*, example.invalid), step 1.",
    )
    run_state = RunState(
        meta=_meta(),
        config=AuditConfig(selected_domain_ids=["d01"], issue_mode="report"),
        domain_results={
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=[RuleVerdict(rule_id="D01-R01", verdict=Verdict.FINDING)],
                findings=[
                    Finding(
                        rule_id="D01-R01",
                        severity=Severity.LOW,
                        title="A **bold** title",
                        location="x.py",
                        body_md=(
                            "**The issue**: x.\n\n**Why it matters**: y.\n\n"
                            "**Suggested fix**: z."
                        ),
                        issue_title="A **bold** issue title",
                        issue_body="**The issue**: x.\n\n**Suggested fix**: z.",
                    )
                ],
            )
        },
    )
    rendered = render_report(run_state, pack)

    findings_and_issues = rendered[
        rendered.index('<section id="findings">') : rendered.index(
            '<p class="print-only-note">'
        )
    ]
    assert "*" not in findings_and_issues


# ---------------------------------------------------------------------------
# Issue #129: the four severity levels are defined next to the by-severity
# list, and the report says they were assigned, not measured.
# ---------------------------------------------------------------------------


def test_severity_definitions_render_between_the_by_severity_and_verdict_lists() -> (
    None
):
    pack = _pack()
    rendered = render_report(_base_run_state(pack), pack)

    sev_list_pos = rendered.index("<h3>Findings by severity</h3>")
    definitions_pos = rendered.index('<dl class="severity-definitions">')
    verdicts_heading_pos = rendered.index("<h3>Rule verdicts</h3>")
    assert sev_list_pos < definitions_pos < verdicts_heading_pos

    for severity, definition in report_module._SEVERITY_DEFINITIONS.items():
        assert f"<dt>{severity}</dt>" in rendered
        assert f"<dd>{definition}.</dd>" in rendered


def test_severity_definitions_state_the_assistant_assigned_them_not_measured() -> None:
    pack = _pack()
    # _meta() defaults assistant="claude-code", model="claude-sonnet-5".
    rendered = render_report(_base_run_state(pack), pack)

    definitions_block = rendered[
        rendered.index('<dl class="severity-definitions">') : rendered.index(
            "<h3>Rule verdicts</h3>"
        )
    ]
    assert "claude-code" in definitions_block
    assert "claude-sonnet-5" in definitions_block
    assert "judged each finding's severity" in definitions_block
    assert "not a measurement" in definitions_block


def test_severity_definitions_match_audit_md() -> None:
    # Pinned so the report's copy and AUDIT.md's own guidance for the agent
    # choosing a finding's severity cannot silently drift apart (issue
    # #129). This parses AUDIT.md's own "- **<severity>**: ..." bullets and
    # asserts they still equal report._SEVERITY_DEFINITIONS word for word.
    audit_md = (Path(__file__).parent.parent / "AUDIT.md").read_text(encoding="utf-8")
    pattern = re.compile(
        r"- \*\*(critical|high|medium|low)\*\*: (.+?)\.\n(?=\s*- )", re.DOTALL
    )
    matches = pattern.findall(audit_md)
    assert len(matches) == 4, (
        f"expected exactly 4 severity bullets in AUDIT.md, found {len(matches)}: {matches}"
    )
    from_audit_md = {sev: " ".join(body.split()) for sev, body in matches}
    assert from_audit_md == report_module._SEVERITY_DEFINITIONS


# ---------------------------------------------------------------------------
# Issue #130: domain confidence and rules-fetched status reach the findings
# they qualify, and the issues filed from them.
# ---------------------------------------------------------------------------


def test_finding_card_marks_a_never_fetched_domain_visibly() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.domain_results["d01"].self_assessment = SelfAssessment(
        confidence="low", limits=""
    )
    # Recorded (not None) but d01 is absent from it: its rules were never
    # fetched this run.
    run_state.rules_fetched_domain_ids = ["d02"]
    rendered = render_report(run_state, pack)

    note_match = re.search(
        r'<div class="finding-location">ledger/beds\.py:42</div>(.*?)'
        r'<div class="finding-body">',
        rendered,
        re.DOTALL,
    )
    assert note_match is not None
    note_html = note_match.group(1)
    # Visible, not muted (issue #130's "mark its findings visibly"): weight
    # carries the emphasis, not colour, matching _severity_cell's own
    # convention for a nonzero critical/high count.
    assert '<div class="finding-domain-note"><strong>' in note_html
    assert "self-assessed confidence low" in note_html
    assert "never fetched from the server this run" in note_html
    assert "treat this finding as unsupported until the domain is redone" in note_html
    # Wording discipline carried over from issue #110: fetched is not read
    # or applied, in either direction.
    assert "was read" not in note_html
    assert "was applied" not in note_html


def test_finding_card_note_is_muted_when_the_domain_was_fetched() -> None:
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.rules_fetched_domain_ids = ["d01", "d02"]
    rendered = render_report(run_state, pack)

    note_match = re.search(
        r'<div class="finding-location">ledger/beds\.py:42</div>(.*?)'
        r'<div class="finding-body">',
        rendered,
        re.DOTALL,
    )
    assert note_match is not None
    note_html = note_match.group(1)
    assert '<div class="finding-domain-note muted">' in note_html
    assert "its rule text was fetched from the server this run" in note_html


def test_issue_body_carries_the_same_domain_confidence_note_as_the_finding_card() -> (
    None
):
    # Issue #130's own words: this is where it bites hardest. A user files
    # an issue from the report's copy-to-clipboard or PAT-filing text, and
    # that text must carry the same warning the finding card does.
    pack = _pack()
    run_state = _base_run_state(pack)
    run_state.domain_results["d01"].self_assessment = SelfAssessment(
        confidence="low", limits=""
    )
    run_state.rules_fetched_domain_ids = []  # nothing fetched this run
    rendered = render_report(run_state, pack)

    data = _extract_json_script(rendered, "issues-data")
    body = data["issues"][0]["body"]
    assert "self-assessed confidence low" in body
    assert "never fetched from the server this run" in body
    assert "treat this finding as unsupported until the domain is redone" in body


def test_unfetched_critical_finding_is_unticked_despite_severity() -> None:
    # Composition test: issue #122 pre-ticks critical/high by default;
    # issue #130's unfetched-domain rule must compose with that, not
    # replace it. A critical finding from a domain whose rules were never
    # fetched this run ends up unticked despite its severity, while a
    # critical finding from a domain that was fetched stays ticked exactly
    # as #122 left it.
    pack = _pack()
    d01 = pack.get_domain("d01")
    d02 = pack.get_domain("d02")
    d01_verdicts = _all_pass_verdicts(d01)
    d01_verdicts[1] = RuleVerdict(rule_id="D01-R02", verdict=Verdict.FINDING)
    d02_verdicts = _all_pass_verdicts(d02)
    d02_verdicts[0] = RuleVerdict(rule_id="D02-R01", verdict=Verdict.FINDING)

    run_state = RunState(
        meta=_meta(),
        config=AuditConfig(selected_domain_ids=["d01", "d02"], issue_mode="report"),
        domain_results={
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=d01_verdicts,
                findings=[
                    Finding(
                        rule_id="D01-R02",
                        severity=Severity.CRITICAL,
                        title="fetched-domain finding",
                        location="ledger/beds.py:1",
                        body_md="x",
                        issue_title="x",
                        issue_body="x",
                    )
                ],
            ),
            "d02": DomainResult(
                domain_id="d02",
                status="completed",
                rule_verdicts=d02_verdicts,
                findings=[
                    Finding(
                        rule_id="D02-R01",
                        severity=Severity.CRITICAL,
                        title="unfetched-domain finding",
                        location="crates/manifest.py:1",
                        body_md="x",
                        issue_title="x",
                        issue_body="x",
                    )
                ],
            ),
        },
        rules_fetched_domain_ids=["d01"],  # d02's rules were never fetched
    )
    rendered = render_report(run_state, pack)

    assert (
        '<input type="checkbox" id="issue-check-0" checked '
        'onchange="updateGithubFileButtonLabel()">' in rendered
    )
    assert (
        '<input type="checkbox" id="issue-check-1" onchange="updateGithubFileButtonLabel()">'
        in rendered
    )
    assert '<input type="checkbox" id="issue-check-1" checked' not in rendered
    assert (
        "1 critical or high finding from a domain whose rules were never fetched "
        "this run is listed unticked too" in rendered
    )
