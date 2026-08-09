"""Tests for the audit state models (src/engineering_audit/schema.py)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from engineering_audit.rules import load_pack
from engineering_audit.schema import (
    RUN_STATE_SCHEMA_VERSION,
    AuditConfig,
    Coverage,
    DomainResult,
    Finding,
    IncompleteResultError,
    RuleVerdict,
    RunMeta,
    RunProgress,
    RunState,
    RunStateVersionError,
    SelfAssessment,
    Severity,
    TelemetryConsent,
    Verdict,
    validate_completeness,
)

from pathlib import Path

FIXTURE_PACK = Path(__file__).parent / "fixture_pack"


def _meta() -> RunMeta:
    return RunMeta(
        tool_version="0.1.0",
        rules_pack_name="fixture-pack",
        assistant="claude-code",
        model="claude-sonnet-5",
        repo_name="engineering-audit",
        repo_commit="deadbeef",
        started="2026-08-09T09:00:00+00:00",
        finished="2026-08-09T09:05:00+00:00",
    )


def _config() -> AuditConfig:
    return AuditConfig(selected_domain_ids=["d01", "d02"], issue_mode="report")


def test_verdict_serialises_to_kebab_strings() -> None:
    assert Verdict.pass_.value == "pass"
    assert Verdict.FINDING.value == "finding"
    assert Verdict.NOT_APPLICABLE.value == "not-applicable"
    assert Verdict.COULD_NOT_EVALUATE.value == "could-not-evaluate"


def test_finding_without_matching_verdict_rejected() -> None:
    with pytest.raises(ValidationError):
        DomainResult(
            domain_id="d01",
            status="completed",
            rule_verdicts=[RuleVerdict(rule_id="D01-R01", verdict=Verdict.pass_)],
            findings=[
                Finding(
                    rule_id="D01-R02",
                    severity=Severity.HIGH,
                    title="Gnomes double-booked",
                    location="beds.py:12",
                    body_md="Two gnomes, one bed, no flag.",
                    issue_title="Double-booked gnome bed",
                    issue_body="See beds.py:12.",
                )
            ],
        )


def test_domain_result_accepts_unique_rule_verdicts() -> None:
    result = DomainResult(
        domain_id="d01",
        status="completed",
        rule_verdicts=[
            RuleVerdict(rule_id="D01-R01", verdict=Verdict.pass_),
            RuleVerdict(rule_id="D01-R02", verdict=Verdict.NOT_APPLICABLE),
        ],
    )
    assert len(result.rule_verdicts) == 2


def test_domain_result_rejects_duplicate_rule_id_in_rule_verdicts() -> None:
    # A domain result recording two verdicts for the same rule id (here pass
    # and not-applicable) is internally contradictory: nothing downstream
    # that de-duplicates by rule id would ever catch it, so it must be
    # rejected at record time, naming the offending rule id.
    with pytest.raises(ValidationError) as excinfo:
        DomainResult(
            domain_id="d01",
            status="completed",
            rule_verdicts=[
                RuleVerdict(rule_id="D01-R01", verdict=Verdict.pass_),
                RuleVerdict(rule_id="D01-R01", verdict=Verdict.NOT_APPLICABLE),
            ],
        )
    assert "D01-R01" in str(excinfo.value)


def test_could_not_run_with_findings_rejected() -> None:
    with pytest.raises(ValidationError):
        DomainResult(
            domain_id="d01",
            status="could-not-run",
            reason="repository unreadable",
            findings=[
                Finding(
                    rule_id="D01-R01",
                    severity=Severity.LOW,
                    title="x",
                    location="a.py",
                    body_md="x",
                    issue_title="x",
                    issue_body="x",
                )
            ],
        )


def test_could_not_run_without_reason_rejected() -> None:
    with pytest.raises(ValidationError):
        DomainResult(domain_id="d01", status="could-not-run")


def _finding(location: str, rule_id: str = "D01-R01") -> Finding:
    return Finding(
        rule_id=rule_id,
        severity=Severity.LOW,
        title="x",
        location=location,
        body_md="x",
        issue_title="x",
        issue_body="x",
    )


def test_finding_accepts_a_bare_path_location() -> None:
    assert _finding("beds.py").location == "beds.py"


def test_finding_accepts_a_path_with_line_number_location() -> None:
    assert _finding("beds.py:42").location == "beds.py:42"


def test_finding_accepts_a_path_with_line_range_location() -> None:
    assert _finding("beds.py:10-20").location == "beds.py:10-20"


def test_finding_rejects_an_empty_location() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _finding("", rule_id="D01-R09")
    assert "D01-R09" in str(excinfo.value)


def test_finding_rejects_a_location_with_no_path_before_the_line_suffix() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _finding(":12", rule_id="D01-R10")
    assert "D01-R10" in str(excinfo.value)


def test_finding_rejects_a_reversed_line_range() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _finding("beds.py:30-24", rule_id="D01-R12")
    assert "D01-R12" in str(excinfo.value)


def test_finding_rejects_a_zero_line_number() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _finding("beds.py:0", rule_id="D01-R11")
    assert "D01-R11" in str(excinfo.value)


def test_could_not_evaluate_without_note_rejected() -> None:
    with pytest.raises(ValidationError):
        RuleVerdict(rule_id="D01-R01", verdict=Verdict.COULD_NOT_EVALUATE)


def test_could_not_evaluate_with_note_accepted() -> None:
    rv = RuleVerdict(
        rule_id="D01-R01", verdict=Verdict.COULD_NOT_EVALUATE, note="repo has no gnome ledger file"
    )
    assert rv.note == "repo has no gnome ledger file"


def test_validate_completeness_raises_listing_missing_rule_ids_when_a_verdict_is_skipped() -> None:
    # This is the "skipped is not a pass" enforcement point: a completed result
    # that leaves a rule un-verdicted must raise, listing exactly which rule
    # was skipped, rather than being indistinguishable from a clean pass.
    pack = load_pack(FIXTURE_PACK)
    d01 = pack.get_domain("d01")
    assert d01 is not None
    result = DomainResult(
        domain_id="d01",
        status="completed",
        rule_verdicts=[
            RuleVerdict(rule_id="D01-R01", verdict=Verdict.pass_),
            RuleVerdict(rule_id="D01-R02", verdict=Verdict.pass_),
            RuleVerdict(rule_id="D01-R03", verdict=Verdict.pass_),
            # D01-R04 has no verdict at all.
        ],
    )
    with pytest.raises(IncompleteResultError) as excinfo:
        validate_completeness(d01, result)
    assert "D01-R04" in str(excinfo.value)


def test_validate_completeness_passes_when_every_rule_has_a_verdict() -> None:
    pack = load_pack(FIXTURE_PACK)
    d01 = pack.get_domain("d01")
    assert d01 is not None
    result = DomainResult(
        domain_id="d01",
        status="completed",
        rule_verdicts=[
            RuleVerdict(rule_id=r.id, verdict=Verdict.pass_) for r in d01.rules
        ],
    )
    validate_completeness(d01, result)  # must not raise


def test_validate_completeness_exempts_could_not_run() -> None:
    pack = load_pack(FIXTURE_PACK)
    d01 = pack.get_domain("d01")
    assert d01 is not None
    result = DomainResult(domain_id="d01", status="could-not-run", reason="no access")
    validate_completeness(d01, result)  # must not raise


def test_telemetry_consent_has_no_run_meta_toggle() -> None:
    # Run metadata is always sent with feedback; it is deliberately not a
    # field on TelemetryConsent, so there is nothing to accidentally disable.
    assert "environment" in TelemetryConsent.model_fields
    assert not hasattr(TelemetryConsent(), "run_meta")


def test_run_state_rejects_domain_results_key_mismatched_with_domain_id() -> None:
    # domain_results is keyed by domain_id; a DomainResult filed under a
    # different key than its own domain_id is silent data corruption (a
    # report renderer trusts the key), so it must be rejected loudly.
    with pytest.raises(ValidationError):
        RunState(
            meta=_meta(),
            config=_config(),
            domain_results={
                "d01": DomainResult(
                    domain_id="d02",
                    status="completed",
                    rule_verdicts=[RuleVerdict(rule_id="D02-R01", verdict=Verdict.pass_)],
                )
            },
        )


def test_run_meta_accepts_trailing_z_timestamp() -> None:
    # Python 3.10 (this project's minimum) rejects a trailing 'Z' in
    # datetime.fromisoformat; the validator must normalise it before parsing.
    meta = RunMeta(
        tool_version="0.1.0",
        rules_pack_name="fixture-pack",
        assistant="claude-code",
        model="claude-sonnet-5",
        repo_name="engineering-audit",
        repo_commit="deadbeef",
        started="2026-08-09T09:00:00Z",
    )
    assert meta.started == "2026-08-09T09:00:00Z"


def test_run_state_round_trip_json() -> None:
    state = RunState(
        meta=_meta(),
        config=_config(),
        domain_results={
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=[RuleVerdict(rule_id="D01-R01", verdict=Verdict.pass_)],
                coverage=Coverage(files_inspected=3, files_skipped=0),
                self_assessment=SelfAssessment(confidence="high", limits=""),
            )
        },
    )
    dumped = state.to_json()
    restored = RunState.from_json(dumped)
    assert restored == state
    assert restored.domain_results["d01"].coverage.files_inspected == 3


def test_validate_completeness_rejects_verdicts_for_unknown_rule_ids() -> None:
    # The symmetric half of skipped-is-not-a-pass: a verdict naming a rule id
    # the domain does not define is unattributable and must be rejected at
    # record time, not discovered at render time.
    pack = load_pack(FIXTURE_PACK)
    d01 = pack.get_domain("d01")
    assert d01 is not None
    verdicts = [
        RuleVerdict(rule_id=rule.id, verdict="pass") for rule in d01.rules
    ] + [RuleVerdict(rule_id="D01-T99", verdict="pass")]
    result = DomainResult(domain_id="d01", status="completed", rule_verdicts=verdicts)
    with pytest.raises(IncompleteResultError) as excinfo:
        validate_completeness(d01, result)
    assert "D01-T99" in str(excinfo.value)
    assert "not define" in str(excinfo.value)


# ---------------------------------------------------------------------------
# RunState schema versioning: schema_version, filed_issue_urls,
# feedback_issue_url (src/engineering_audit/schema.py)
# ---------------------------------------------------------------------------


def test_run_state_defaults_to_current_schema_version_when_freshly_built() -> None:
    state = RunState(meta=_meta(), config=_config())
    assert state.schema_version == RUN_STATE_SCHEMA_VERSION == 2
    assert state.filed_issue_urls == {}
    assert state.feedback_issue_url is None


def test_run_state_serialised_json_carries_schema_version_2() -> None:
    state = RunState(meta=_meta(), config=_config())
    dumped = json.loads(state.to_json())
    assert dumped["schema_version"] == 2


def test_run_state_from_json_missing_schema_version_is_treated_as_version_1() -> None:
    # A run-state.json written before this field existed has no
    # schema_version key at all. It must still be accepted, treated as
    # version 1, with the newer fields defaulted rather than the file being
    # rejected outright.
    state = RunState(meta=_meta(), config=_config())
    raw = json.loads(state.to_json())
    del raw["schema_version"]
    restored = RunState.from_json(json.dumps(raw))
    assert restored.schema_version == 1
    assert restored.filed_issue_urls == {}
    assert restored.feedback_issue_url is None


def test_run_state_from_json_accepts_current_version() -> None:
    state = RunState(
        meta=_meta(),
        config=_config(),
        filed_issue_urls={"D01-R01": "https://example.invalid/issues/1"},
        feedback_issue_url="https://example.invalid/issues/2",
    )
    restored = RunState.from_json(state.to_json())
    assert restored.schema_version == 2
    assert restored == state


def test_run_state_from_json_rejects_a_higher_schema_version_naming_both_numbers() -> None:
    state = RunState(meta=_meta(), config=_config())
    raw = json.loads(state.to_json())
    raw["schema_version"] = 99
    with pytest.raises(RunStateVersionError) as excinfo:
        RunState.from_json(json.dumps(raw))
    message = str(excinfo.value)
    assert "99" in message
    assert str(RUN_STATE_SCHEMA_VERSION) in message
    assert "upgrade" in message.lower()


def test_run_state_still_requires_a_config_after_run_progress_was_added() -> None:
    # RunProgress exists precisely so RunState did not have to relax this:
    # a rendered report is always traceable to a configuration a person chose.
    with pytest.raises(ValidationError):
        RunState(meta=_meta())


# ---------------------------------------------------------------------------
# RunProgress: the crash-recovery record (src/engineering_audit/schema.py)
# ---------------------------------------------------------------------------


def test_run_progress_can_describe_a_run_that_has_no_configuration_yet() -> None:
    # A run exists between begin_run and the user submitting the config page,
    # and that gap is exactly when an interruption is most likely: the audit
    # is waiting on a human. It has to be representable.
    progress = RunProgress(meta=_meta())
    assert progress.config is None
    assert progress.domain_results == {}
    assert progress.filed_issues == {}
    assert progress.completed is False


def test_run_progress_shares_the_run_state_schema_version() -> None:
    progress = RunProgress(meta=_meta(), config=_config())
    assert progress.schema_version == RUN_STATE_SCHEMA_VERSION
    assert json.loads(progress.to_json())["schema_version"] == RUN_STATE_SCHEMA_VERSION


def test_run_progress_from_json_tolerates_fields_and_version_being_absent() -> None:
    # A recovery file written by an older build carries neither the newer
    # fields nor a schema_version. Rejecting it would strand the run it
    # describes, which is the opposite of what the file is for.
    raw = json.loads(RunProgress(meta=_meta(), config=_config()).to_json())
    del raw["schema_version"]
    del raw["filed_issues"]
    del raw["completed"]
    restored = RunProgress.from_json(json.dumps(raw))
    assert restored.schema_version == 1
    assert restored.filed_issues == {}
    assert restored.completed is False


def test_run_progress_from_json_rejects_a_higher_schema_version() -> None:
    raw = json.loads(RunProgress(meta=_meta(), config=_config()).to_json())
    raw["schema_version"] = 99
    with pytest.raises(RunStateVersionError) as excinfo:
        RunProgress.from_json(json.dumps(raw))
    assert "99" in str(excinfo.value)
    assert str(RUN_STATE_SCHEMA_VERSION) in str(excinfo.value)


def test_run_progress_rejects_a_domain_results_key_that_is_not_its_domain_id() -> None:
    with pytest.raises(ValidationError):
        RunProgress(
            meta=_meta(),
            domain_results={"d02": DomainResult(domain_id="d01", status="completed")},
        )


def test_run_state_filed_issue_urls_and_feedback_issue_url_round_trip() -> None:
    state = RunState(
        meta=_meta(),
        config=_config(),
        filed_issue_urls={"D01-R01": "https://example.invalid/issues/7", "D01-R02": "https://example.invalid/issues/8"},
        feedback_issue_url="https://example.invalid/issues/9",
    )
    restored = RunState.from_json(state.to_json())
    assert restored.filed_issue_urls == state.filed_issue_urls
    assert restored.feedback_issue_url == state.feedback_issue_url
    assert restored == state
