"""Tests for the audit-quality eval scorer (src/engineering_audit/evals.py)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pytest
from pydantic import ValidationError

from engineering_audit import evals
from engineering_audit.evals import EvalSpec, EvalStructuralError, Expectation, score
from engineering_audit.rules import RulesPack, load_pack
from engineering_audit.schema import (
    AuditConfig,
    DomainResult,
    Finding,
    RuleVerdict,
    RunMeta,
    RunState,
    Severity,
    Verdict,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
TASTER_PACK = REPO_ROOT / "examples" / "taster-rules"
GOLDEN_REPO = REPO_ROOT / "evals" / "golden" / "repo"
EXPECTED_PATH = REPO_ROOT / "evals" / "golden" / "expected.json"

DOMAIN_IDS = ("d01", "d05", "d16")


# Both the taster pack and the committed eval spec are read-only for the
# whole test run (RulesPack is a frozen dataclass; nothing here mutates the
# EvalSpec it loads), so caching them avoids re-reading and re-parsing the
# same two files once per test.
@lru_cache(maxsize=1)
def _load_taster_pack() -> RulesPack:
    return load_pack(TASTER_PACK)


@lru_cache(maxsize=1)
def _load_expected_spec() -> EvalSpec:
    return EvalSpec.model_validate(
        json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    )


# Rule id -> the location the golden repo's planted violation is expected
# at, derived directly from the committed spec rather than hand-mirrored,
# so this dict cannot drift out of step with evals/golden/expected.json.
PLANTED_FINDINGS = {
    e.rule_id: e.location_contains
    for e in _load_expected_spec().expectations
    if e.expect == "finding"
}


def _meta() -> RunMeta:
    return RunMeta(
        tool_version="0.1.0",
        rules_pack_name="taster-rules",
        assistant="claude-code",
        model="claude-sonnet-5",
        repo_name="grindpoints",
        repo_commit="abc1234",
        started="2026-08-09T09:00:00+00:00",
        finished="2026-08-09T09:10:00+00:00",
    )


def _finding(rule_id: str, location: str) -> Finding:
    return Finding(
        rule_id=rule_id,
        severity=Severity.MEDIUM,
        title=f"Planted violation of {rule_id}",
        location=location,
        body_md="Planted for the eval harness test suite.",
        issue_title=f"Fix {rule_id}",
        issue_body="Planted for the eval harness test suite.",
    )


def _build_run_state(
    pack: RulesPack,
    findings: dict[str, str],
    *,
    verdict_overrides: dict[str, Verdict] | None = None,
    omit_verdicts: frozenset[str] = frozenset(),
) -> RunState:
    """Build a RunState against pack covering d01, d05 and d16.

    Every rule verdicts pass, except: rule ids in findings, which verdict
    finding and carry a matching Finding at the given location; rule ids in
    verdict_overrides, which carry the given verdict instead (a required
    note is filled in automatically for could-not-evaluate and for
    not-applicable); and rule ids in omit_verdicts, which carry no verdict at
    all.
    """
    verdict_overrides = verdict_overrides or {}
    domain_results: dict[str, DomainResult] = {}
    for domain_id in DOMAIN_IDS:
        domain = pack.get_domain(domain_id)
        assert domain is not None
        verdicts = []
        result_findings = []
        for rule in domain.rules:
            if rule.id in omit_verdicts:
                continue
            if rule.id in findings:
                verdicts.append(RuleVerdict(rule_id=rule.id, verdict=Verdict.FINDING))
                result_findings.append(_finding(rule.id, findings[rule.id]))
            elif rule.id in verdict_overrides:
                overridden = verdict_overrides[rule.id]
                note = (
                    "planted for the eval harness test suite"
                    if overridden
                    in (Verdict.COULD_NOT_EVALUATE, Verdict.NOT_APPLICABLE)
                    else None
                )
                verdicts.append(
                    RuleVerdict(rule_id=rule.id, verdict=overridden, note=note)
                )
            else:
                verdicts.append(RuleVerdict(rule_id=rule.id, verdict=Verdict.pass_))
        domain_results[domain_id] = DomainResult(
            domain_id=domain_id,
            status="completed",
            rule_verdicts=verdicts,
            findings=result_findings,
        )
    return RunState(
        meta=_meta(),
        config=AuditConfig(selected_domain_ids=list(DOMAIN_IDS), issue_mode="report"),
        domain_results=domain_results,
    )


def _write_run_state(path: Path, run_state: RunState) -> Path:
    path.write_text(run_state.to_json(), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Location matching (segment-anchored, not a bare substring test)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expected", "location", "want"),
    [
        ("schema.sql", "schema.sql", True),
        ("schema.sql", "repo/schema.sql:12", True),
        ("schema.sql", "schema.sql:24-30", True),
        ("schema.sql", "old_schema.sql.bak", False),
        ("schema.sql", "old_schema.sql.bak:24-30", False),
        ("tests/", "tests/test_signup_flow.py", True),
        ("tests/", "tests/test_signup_flow.py:14-21", True),
        ("tests/", "integration_tests/helpers.py", False),
    ],
)
def test_location_matches_is_segment_anchored(
    expected: str, location: str, want: bool
) -> None:
    assert evals._location_matches(expected, location) is want


# ---------------------------------------------------------------------------
# Scorer happy path
# ---------------------------------------------------------------------------


def test_scorer_happy_path_hits_every_expectation_and_exits_zero() -> None:
    pack = _load_taster_pack()
    spec = _load_expected_spec()
    run_state = _build_run_state(pack, PLANTED_FINDINGS)

    result = score(run_state, spec, pack)

    assert result.exit_code == 0
    assert result.expected_missed == 0
    assert result.expected_found_wrong_location == 0
    assert result.controls_false_positive == 0
    assert result.controls_not_evaluated == 0
    assert result.unexpected_findings_count == 0
    expected_finding_count = sum(1 for e in spec.expectations if e.expect == "finding")
    control_count = sum(1 for e in spec.expectations if e.expect == "no-finding")
    assert result.expected_hit == expected_finding_count
    assert result.controls_held == control_count


def test_scorer_happy_path_without_rules_dir_still_scores_and_notes_could_not_check() -> (
    None
):
    pack = _load_taster_pack()
    spec = _load_expected_spec()
    run_state = _build_run_state(pack, PLANTED_FINDINGS)

    result = score(run_state, spec, pack=None)

    assert result.exit_code == 0
    # One could-not-check note per spec domain (completeness), plus one
    # spec-wide note for the orphan rule-id / rule-to-domain ownership
    # cross-check, which also cannot run without a pack.
    assert len(result.completeness_notes) == len(spec.domains) + 1
    for note in result.completeness_notes:
        assert "could-not-check" in note.note
    ownership_note = next(
        n for n in result.completeness_notes if n.domain_id == "(all domains)"
    )
    assert "ownership" in ownership_note.note


# ---------------------------------------------------------------------------
# Scoring failures: finding expectations
# ---------------------------------------------------------------------------


def test_missed_expected_finding_exits_non_zero_with_missed_outcome() -> None:
    pack = _load_taster_pack()
    spec = _load_expected_spec()
    findings = dict(PLANTED_FINDINGS)
    del findings["D01-R06"]  # the auditor never raised this one
    run_state = _build_run_state(pack, findings)

    result = score(run_state, spec, pack)

    assert result.exit_code == 1
    assert result.expected_missed == 1
    outcome = next(o for o in result.outcomes if o.rule_id == "D01-R06")
    assert outcome.outcome == "missed"


def test_found_wrong_location_counts_as_missed_and_exits_non_zero() -> None:
    pack = _load_taster_pack()
    spec = _load_expected_spec()
    findings = dict(PLANTED_FINDINGS)
    findings["D01-R06"] = "somewhere/unrelated.py"  # right rule id, wrong file
    run_state = _build_run_state(pack, findings)

    result = score(run_state, spec, pack)

    assert result.exit_code == 1
    assert result.expected_found_wrong_location == 1
    assert result.expected_missed == 0
    outcome = next(o for o in result.outcomes if o.rule_id == "D01-R06")
    assert outcome.outcome == "found-wrong-location"


# ---------------------------------------------------------------------------
# Scoring failures: control semantics
#
# A control (expect="no-finding") only counts as held when the rule was
# explicitly verdicted pass. A finding recorded against it is a
# false-positive; anything else (not-applicable, could-not-evaluate, or no
# verdict at all) is control-not-evaluated: the control never actually ran,
# so it proves nothing and must not read as a clean pass.
# ---------------------------------------------------------------------------


def test_false_positive_on_a_control_exits_non_zero() -> None:
    pack = _load_taster_pack()
    spec = _load_expected_spec()
    findings = dict(PLANTED_FINDINGS)
    findings["D01-R07"] = "schema.sql"  # D01-R07 is a control: this is a false alarm
    run_state = _build_run_state(pack, findings)

    result = score(run_state, spec, pack)

    assert result.exit_code == 1
    assert result.controls_false_positive == 1
    outcome = next(o for o in result.outcomes if o.rule_id == "D01-R07")
    assert outcome.outcome == "false-positive"


def test_control_verdicted_not_applicable_is_control_not_evaluated_and_exits_non_zero() -> (
    None
):
    pack = _load_taster_pack()
    spec = _load_expected_spec()
    run_state = _build_run_state(
        pack, PLANTED_FINDINGS, verdict_overrides={"D01-R07": Verdict.NOT_APPLICABLE}
    )

    result = score(run_state, spec, pack)

    assert result.exit_code == 1
    assert result.controls_not_evaluated == 1
    assert (
        result.controls_held
        == sum(1 for e in spec.expectations if e.expect == "no-finding") - 1
    )
    outcome = next(o for o in result.outcomes if o.rule_id == "D01-R07")
    assert outcome.outcome == "control-not-evaluated"
    assert "not-applicable" in outcome.detail


def test_control_verdicted_could_not_evaluate_is_control_not_evaluated_and_exits_non_zero() -> (
    None
):
    pack = _load_taster_pack()
    spec = _load_expected_spec()
    run_state = _build_run_state(
        pack,
        PLANTED_FINDINGS,
        verdict_overrides={"D01-R07": Verdict.COULD_NOT_EVALUATE},
    )

    result = score(run_state, spec, pack)

    assert result.exit_code == 1
    assert result.controls_not_evaluated == 1
    outcome = next(o for o in result.outcomes if o.rule_id == "D01-R07")
    assert outcome.outcome == "control-not-evaluated"
    assert "could-not-evaluate" in outcome.detail


def test_control_with_no_verdict_at_all_is_control_not_evaluated_and_exits_non_zero() -> (
    None
):
    # validate_completeness would reject a completed domain missing a
    # verdict for one of its rules, so this scenario (no verdict recorded
    # for the control's rule id at all) can only reach the scorer when no
    # rules pack is supplied, which is exactly when that check does not run.
    pack = _load_taster_pack()
    spec = _load_expected_spec()
    run_state = _build_run_state(
        pack, PLANTED_FINDINGS, omit_verdicts=frozenset({"D01-R07"})
    )

    result = score(run_state, spec, pack=None)

    assert result.exit_code == 1
    assert result.controls_not_evaluated == 1
    outcome = next(o for o in result.outcomes if o.rule_id == "D01-R07")
    assert outcome.outcome == "control-not-evaluated"
    assert "no verdict recorded" in outcome.detail


# ---------------------------------------------------------------------------
# Scoring is restricted to spec.domains
# ---------------------------------------------------------------------------


def test_finding_in_a_domain_outside_spec_domains_does_not_count_towards_scoring() -> (
    None
):
    pack = _load_taster_pack()
    spec = _load_expected_spec()
    findings = dict(PLANTED_FINDINGS)
    del findings["D01-R05"]  # never actually raised in d01
    run_state = _build_run_state(pack, findings)

    # Graft an extra domain result that carries a Finding and a FINDING
    # verdict for D01-R05 under domain id "d02", outside spec.domains. A
    # real audit could never produce this (D01-R05 only exists in domain
    # d01's own rule list), but constructing it directly is the only way to
    # prove the scorer ignores domains outside spec.domains rather than
    # trusting that behaviour by inspection alone.
    extra_domain_result = DomainResult(
        domain_id="d02",
        status="completed",
        rule_verdicts=[RuleVerdict(rule_id="D01-R05", verdict=Verdict.FINDING)],
        findings=[_finding("D01-R05", "schema.sql")],
    )
    run_state = run_state.model_copy(
        update={
            "domain_results": {**run_state.domain_results, "d02": extra_domain_result}
        }
    )

    result = score(run_state, spec, pack)

    outcome = next(o for o in result.outcomes if o.rule_id == "D01-R05")
    assert outcome.outcome == "missed"


def test_unexpected_findings_include_findings_from_domains_outside_spec_domains() -> (
    None
):
    pack = _load_taster_pack()
    spec = _load_expected_spec()
    run_state = _build_run_state(pack, PLANTED_FINDINGS)

    extra_domain_result = DomainResult(
        domain_id="d02",
        status="completed",
        rule_verdicts=[RuleVerdict(rule_id="D02-R99", verdict=Verdict.FINDING)],
        findings=[_finding("D02-R99", "somewhere.py")],
    )
    run_state = run_state.model_copy(
        update={
            "domain_results": {**run_state.domain_results, "d02": extra_domain_result}
        }
    )

    result = score(run_state, spec, pack)

    assert any(uf.rule_id == "D02-R99" for uf in result.unexpected_findings)


# ---------------------------------------------------------------------------
# Unexpected findings
# ---------------------------------------------------------------------------


def test_unexpected_findings_are_listed_in_the_result_and_do_not_affect_exit_code() -> (
    None
):
    pack = _load_taster_pack()
    spec = _load_expected_spec()
    findings = dict(PLANTED_FINDINGS)
    findings["D01-R01"] = "schema.sql"  # not in the spec at all
    run_state = _build_run_state(pack, findings)

    result = score(run_state, spec, pack)

    assert result.exit_code == 0  # not scored against, so it cannot fail the run
    assert result.unexpected_findings_count == 1
    assert result.unexpected_findings[0].rule_id == "D01-R01"


def test_unexpected_findings_reach_eval_result_json(tmp_path: Path) -> None:
    pack = _load_taster_pack()
    findings = dict(PLANTED_FINDINGS)
    findings["D01-R01"] = "schema.sql"
    run_state = _build_run_state(pack, findings)
    run_state_path = _write_run_state(tmp_path / "run-state.json", run_state)
    out_path = tmp_path / "eval-result.json"

    with pytest.raises(SystemExit):
        evals.main(
            [
                str(run_state_path),
                "--expected",
                str(EXPECTED_PATH),
                "--rules-dir",
                str(TASTER_PACK),
                "--out",
                str(out_path),
            ]
        )

    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written["unexpected_findings"] == [
        {
            "rule_id": "D01-R01",
            "title": "Planted violation of D01-R01",
            "location": "schema.sql",
        }
    ]


# ---------------------------------------------------------------------------
# Structural failures
# ---------------------------------------------------------------------------


def test_missing_run_state_file_exits_2(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        evals.main(
            [
                str(tmp_path / "no-such-file.json"),
                "--expected",
                str(EXPECTED_PATH),
            ]
        )
    assert excinfo.value.code == 2


def test_corrupt_run_state_json_exits_2(tmp_path: Path) -> None:
    bad_path = tmp_path / "run-state.json"
    bad_path.write_text("{ not valid json", encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        evals.main([str(bad_path), "--expected", str(EXPECTED_PATH)])
    assert excinfo.value.code == 2


@pytest.mark.parametrize("document", ["[1, 2, 3]", "null", '"just a string"', "42"])
def test_non_dict_top_level_run_state_json_exits_2(
    tmp_path: Path, document: str
) -> None:
    # RunState.from_json used to raise a raw AttributeError for a JSON
    # top level that parses but is not an object (calling .get on a list
    # or None); this must surface as the CLI's usual clean, non-zero exit,
    # never a traceback.
    bad_path = tmp_path / "run-state.json"
    bad_path.write_text(document, encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        evals.main([str(bad_path), "--expected", str(EXPECTED_PATH)])
    assert excinfo.value.code == 2


def test_spec_domain_absent_from_run_raises_structural_error() -> None:
    pack = _load_taster_pack()
    spec = _load_expected_spec()
    run_state = _build_run_state(pack, PLANTED_FINDINGS)
    # Drop d16's result entirely: the spec still expects it.
    run_state = run_state.model_copy(
        update={
            "domain_results": {
                k: v for k, v in run_state.domain_results.items() if k != "d16"
            }
        }
    )

    with pytest.raises(EvalStructuralError):
        score(run_state, spec, pack)


def test_spec_domain_absent_from_run_exits_2_via_cli(tmp_path: Path) -> None:
    pack = _load_taster_pack()
    run_state = _build_run_state(pack, PLANTED_FINDINGS)
    run_state = run_state.model_copy(
        update={
            "domain_results": {
                k: v for k, v in run_state.domain_results.items() if k != "d16"
            }
        }
    )
    run_state_path = _write_run_state(tmp_path / "run-state.json", run_state)

    with pytest.raises(SystemExit) as excinfo:
        evals.main(
            [
                str(run_state_path),
                "--expected",
                str(EXPECTED_PATH),
                "--rules-dir",
                str(TASTER_PACK),
            ]
        )
    assert excinfo.value.code == 2


def test_duplicate_rule_ids_in_spec_rejected_by_validation() -> None:
    with pytest.raises(ValidationError):
        EvalSpec(
            golden_repo="evals/golden/repo",
            domains=["d01"],
            expectations=[
                Expectation(
                    rule_id="D01-R05", expect="finding", location_contains="x", why="a"
                ),
                Expectation(rule_id="D01-R05", expect="no-finding", why="b"),
            ],
        )


def test_location_contains_on_a_control_rejected_by_validation() -> None:
    with pytest.raises(ValidationError):
        Expectation(
            rule_id="D01-R07",
            expect="no-finding",
            location_contains="schema.sql",
            why="a",
        )


def test_expectations_must_not_be_empty() -> None:
    with pytest.raises(ValidationError):
        EvalSpec(golden_repo="evals/golden/repo", domains=["d01"], expectations=[])


def test_expectation_rule_id_not_belonging_to_a_spec_domain_raises_structural_error() -> (
    None
):
    pack = _load_taster_pack()
    run_state = _build_run_state(pack, PLANTED_FINDINGS)
    spec = EvalSpec(
        golden_repo="evals/golden/repo",
        domains=["d01"],  # deliberately excludes d05
        expectations=[
            Expectation(
                rule_id="D05-R08", expect="finding", location_contains="tests/", why="x"
            ),
        ],
    )

    with pytest.raises(EvalStructuralError):
        score(run_state, spec, pack)


# ---------------------------------------------------------------------------
# Golden fixture integrity
# ---------------------------------------------------------------------------


def test_committed_expected_json_round_trips_through_eval_spec() -> None:
    spec = _load_expected_spec()
    assert spec.domains == ["d01", "d05", "d16"]
    assert len(spec.expectations) >= 6


def test_every_rule_id_in_expected_json_exists_in_the_taster_pack() -> None:
    pack = _load_taster_pack()
    spec = _load_expected_spec()
    for expectation in spec.expectations:
        assert expectation.rule_id in pack.rule_index, (
            f"{expectation.rule_id} in evals/golden/expected.json is not a rule id in "
            "examples/taster-rules"
        )


def test_expected_json_covers_at_least_two_rules_per_domain() -> None:
    pack = _load_taster_pack()
    spec = _load_expected_spec()
    finding_expectations = [e for e in spec.expectations if e.expect == "finding"]
    per_domain: dict[str, int] = {}
    for expectation in finding_expectations:
        domain_id = pack.domain_id_for_rule(expectation.rule_id)
        assert domain_id is not None
        per_domain[domain_id] = per_domain.get(domain_id, 0) + 1
    for domain_id in DOMAIN_IDS:
        assert per_domain.get(domain_id, 0) >= 2, (
            f"{domain_id} has fewer than 2 planted findings"
        )


def test_every_location_contains_path_exists_in_the_golden_repo() -> None:
    # Guards against fixture rot: a rename in the golden repo that is not
    # mirrored in expected.json would otherwise go unnoticed until a live
    # run mysteriously stopped hitting an expectation.
    spec = _load_expected_spec()
    for expectation in spec.expectations:
        if expectation.location_contains is None:
            continue
        candidate = GOLDEN_REPO / expectation.location_contains
        assert candidate.exists(), (
            f"{expectation.rule_id}'s location_contains {expectation.location_contains!r} does "
            f"not exist under {GOLDEN_REPO}"
        )


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_eval_result_json_is_byte_identical_across_two_runs(tmp_path: Path) -> None:
    pack = _load_taster_pack()
    run_state = _build_run_state(pack, PLANTED_FINDINGS)
    run_state_path = _write_run_state(tmp_path / "run-state.json", run_state)
    out_a = tmp_path / "eval-result-a.json"
    out_b = tmp_path / "eval-result-b.json"

    for out_path in (out_a, out_b):
        with pytest.raises(SystemExit) as excinfo:
            evals.main(
                [
                    str(run_state_path),
                    "--expected",
                    str(EXPECTED_PATH),
                    "--rules-dir",
                    str(TASTER_PACK),
                    "--out",
                    str(out_path),
                ]
            )
        assert excinfo.value.code == 0

    assert out_a.read_bytes() == out_b.read_bytes()
