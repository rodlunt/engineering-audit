"""Tests for the audit state models (src/engineering_audit/schema.py)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from engineering_audit.rules import load_pack
from engineering_audit.schema import (
    AuditConfig,
    Coverage,
    DomainResult,
    Finding,
    IncompleteResultError,
    RuleVerdict,
    RunMeta,
    RunState,
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
