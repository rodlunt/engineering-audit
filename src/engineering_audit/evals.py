"""Deterministic scorer for audit-quality evals.

An eval run has two halves that must never be conflated. The audit itself
(an LLM reading a rules pack against a repository and writing a
run-state.json) is nondeterministic and costed: it happens manually, from
the command shown in evals/README.md. Scoring that run-state.json against a
fixed set of expectations is the deterministic half, and it is what this
module does. It never calls an LLM and never reads the repository the audit
was run against; it only reads the two JSON documents (the run state and
the expectations) and reports where they agree and disagree.

The same hardening rules that govern the rest of this project apply here at
full strength, because a scorer that can be wrong in the direction of
"pass" is worse than no scorer at all: it launders a broken or lucky audit
into a green tick nobody re-checks by hand. A run-state file this module
cannot parse is a structural failure, not a zero-findings result, and it
exits loudly and non-zero rather than reading as "nothing wrong". A finding
in the wrong location and a missing finding are both counted as missed,
never silently accepted because the rule id happened to match. A control
whose rule was never actually verdicted "pass" (not-applicable,
could-not-evaluate, or no verdict at all) is not silently treated as held:
that is a dead instrument, not a clean one, and scores as its own failing
outcome. Every finding the run-state carries that this eval spec did not
anticipate is printed, never dropped, because a scorer that only checks the
boxes it was told to check cannot notice an audit that started
hallucinating rule ids. Anything this module cannot itself verify (rule-verdict
completeness, or an expectation's rule id genuinely belonging to a spec
domain) without a supplied rules pack is recorded as could-not-check rather
than silently skipped.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, computed_field, field_validator, model_validator

from engineering_audit.rules import RulesPack, RulesPackError, load_pack
from engineering_audit.run_state_io import RunStateLoadError, load_run_state_file
from engineering_audit.schema import (
    Finding,
    IncompleteResultError,
    RunState,
    Verdict,
    validate_completeness,
)

__all__ = [
    "Expectation",
    "EvalSpec",
    "ExpectationOutcome",
    "UnexpectedFinding",
    "CompletenessNote",
    "EvalResult",
    "EVAL_RESULT_SCHEMA_VERSION",
    "EvalStructuralError",
    "score",
    "main",
]

# Bumped whenever EvalResult gains or changes a field in a way a reader
# written against an older version could not safely ignore. Bumped to 2
# when the "control-not-evaluated" outcome and its counter were added: a
# reader built against version 1 would not know that outcome exists and
# would misread a dead control as neither a pass nor a counted failure.
EVAL_RESULT_SCHEMA_VERSION = 2


class EvalStructuralError(Exception):
    """Raised when the eval cannot be scored at all: a run-state or
    expectations file that will not parse, a spec domain absent from the
    run, an incomplete domain result, or an expectation naming a rule id
    the supplied rules pack does not attribute to a spec domain. A broken
    instrument must never be reported as a clean run, so this is always a
    hard, non-zero exit, never folded into the scored outcome."""


class Expectation(BaseModel):
    """One expectation about a single rule against the golden repo.

    ``location_contains`` is only meaningful when ``expect`` is
    ``"finding"``: a control has nothing to locate. Setting it on a
    no-finding expectation is rejected rather than silently ignored, since
    a silently ignored field on a human-authored spec is exactly the kind
    of skipped check this project's hardening rules exist to catch.
    """

    rule_id: str
    expect: Literal["finding", "no-finding"]
    location_contains: str | None = None
    why: str = Field(description="Human rationale for the plant or control.")

    @field_validator("why")
    @classmethod
    def _why_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "why must not be blank: state what was planted or controlled for, and where"
            )
        return value

    @model_validator(mode="after")
    def _location_contains_only_for_finding(self) -> "Expectation":
        if self.expect == "no-finding" and self.location_contains is not None:
            raise ValueError(
                f"rule {self.rule_id}: location_contains is only meaningful when expect is "
                "'finding'; a no-finding control has nothing to locate"
            )
        return self


class EvalSpec(BaseModel):
    """The full set of expectations for one golden repo."""

    golden_repo: str
    domains: list[str]
    expectations: list[Expectation]

    @field_validator("domains")
    @classmethod
    def _domains_not_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("domains must not be empty")
        return value

    @field_validator("expectations")
    @classmethod
    def _expectations_not_empty(cls, value: list[Expectation]) -> list[Expectation]:
        if not value:
            raise ValueError("expectations must not be empty")
        return value

    @model_validator(mode="after")
    def _rule_ids_unique(self) -> "EvalSpec":
        counts = Counter(e.rule_id for e in self.expectations)
        duplicates = sorted(rule_id for rule_id, n in counts.items() if n > 1)
        if duplicates:
            raise ValueError(f"duplicate rule_id(s) in expectations: {duplicates}")
        return self


# The set of values ExpectationOutcome.outcome accepts. Named once here,
# rather than written twice, so the loop in score() that assigns one of
# these into a plain local variable can be annotated against the same
# literal the model itself validates against, instead of mypy widening the
# variable to plain str and losing the check entirely.
_Outcome = Literal[
    "hit",
    "missed",
    "found-wrong-location",
    "held",
    "false-positive",
    "control-not-evaluated",
]


class ExpectationOutcome(BaseModel):
    """The scored result for one expectation.

    For an ``expect="finding"`` expectation, ``outcome`` is one of ``hit``,
    ``missed`` or ``found-wrong-location``. For an ``expect="no-finding"``
    control, ``outcome`` is one of ``held`` (the rule was explicitly
    verdicted ``pass``), ``false-positive`` (a finding was recorded against
    it) or ``control-not-evaluated`` (the rule was verdicted something
    other than ``pass`` and carries no finding, e.g. not-applicable,
    could-not-evaluate, or no verdict at all: the control never actually
    ran, so it proves nothing).
    """

    rule_id: str
    expect: Literal["finding", "no-finding"]
    outcome: _Outcome
    why: str
    detail: str | None = Field(
        default=None, description="What was actually found (or not), for the human reading the report."
    )


class UnexpectedFinding(BaseModel):
    """A finding in the run-state whose rule id has no expectation."""

    rule_id: str
    title: str
    location: str


class CompletenessNote(BaseModel):
    """A note on a check that could not be run without a supplied rules
    pack. ``domain_id`` names the domain the note concerns, or
    ``"(all domains)"`` for a note about a spec-wide check (the orphan
    rule-id and rule-to-domain-ownership cross-check) rather than one tied
    to a single domain."""

    domain_id: str
    note: str


class EvalResult(BaseModel):
    """The scored outcome of one eval run, written to eval-result.json.

    Every count and ``exit_code`` is a computed field derived from
    ``outcomes`` and ``unexpected_findings`` rather than hand-accumulated
    during scoring, so the numbers in the report can never drift from the
    outcomes list that is also in the same document: there is exactly one
    source of truth for each, and re-deriving them is how a reader checks
    this file's own arithmetic.
    """

    schema_version: int = EVAL_RESULT_SCHEMA_VERSION
    golden_repo: str
    run_state_path: str = ""
    expected_path: str = ""
    outcomes: list[ExpectationOutcome]
    unexpected_findings: list[UnexpectedFinding]
    completeness_notes: list[CompletenessNote]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def expected_hit(self) -> int:
        return sum(1 for o in self.outcomes if o.expect == "finding" and o.outcome == "hit")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def expected_missed(self) -> int:
        return sum(1 for o in self.outcomes if o.outcome == "missed")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def expected_found_wrong_location(self) -> int:
        return sum(1 for o in self.outcomes if o.outcome == "found-wrong-location")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def controls_held(self) -> int:
        return sum(1 for o in self.outcomes if o.outcome == "held")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def controls_false_positive(self) -> int:
        return sum(1 for o in self.outcomes if o.outcome == "false-positive")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def controls_not_evaluated(self) -> int:
        return sum(1 for o in self.outcomes if o.outcome == "control-not-evaluated")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unexpected_findings_count(self) -> int:
        return len(self.unexpected_findings)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def exit_code(self) -> int:
        """0 only if every finding expectation hit and every control held.

        A missed finding, a finding in the wrong location, a false-positive
        control and a control that was never actually evaluated are all
        failures: the last of those is a dead instrument (see the
        ExpectationOutcome docstring), not a clean pass, so it fails the
        run exactly like the other three.
        """
        clean = (
            self.expected_missed == 0
            and self.expected_found_wrong_location == 0
            and self.controls_false_positive == 0
            and self.controls_not_evaluated == 0
        )
        return 0 if clean else 1

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)


_LINE_SUFFIX_RE = re.compile(r":\d+(?:-\d+)?$")


def _location_matches(expected: str, location: str) -> bool:
    """True if expected occurs in location as a whole path segment (or a
    run of them), not as an arbitrary substring.

    A trailing ``:<line>`` or ``:<start>-<end>`` on location is stripped
    first, so ``"schema.sql:18"`` and ``"schema.sql:24-30"`` are matched
    exactly as ``"schema.sql"`` alone would be. Live runs really do emit
    range locations; the second recorded run scored four planted findings
    as found-wrong-location purely because this strip missed them. What remains must then match at path-segment boundaries: the
    character immediately before the match must be the start of the string
    or a ``/``, and, unless expected itself ends with ``/`` (a deliberate
    directory-prefix anchor, whose own trailing slash already supplies the
    boundary), the character immediately after the match must be the end of
    the string or a ``/``. This is what stops ``"schema.sql"`` from matching
    ``"old_schema.sql.bak"``, and ``"tests/"`` from matching
    ``"integration_tests/helpers.py"``, while still matching
    ``"schema.sql"`` against ``"repo/schema.sql:12"`` and ``"tests/"``
    against ``"tests/test_signup_flow.py"``.
    """
    stripped = _LINE_SUFFIX_RE.sub("", location)
    search_from = 0
    while True:
        idx = stripped.find(expected, search_from)
        if idx == -1:
            return False
        left_ok = idx == 0 or stripped[idx - 1] == "/"
        end = idx + len(expected)
        right_ok = expected.endswith("/") or end == len(stripped) or stripped[end] == "/"
        if left_ok and right_ok:
            return True
        search_from = idx + 1


def _check_structural_gates(
    run_state: RunState, spec: EvalSpec, pack: RulesPack | None
) -> list[CompletenessNote]:
    """Raise EvalStructuralError for anything that makes scoring meaningless.

    Every domain the spec names must be present in the run and completed.
    If a rules pack was supplied, every completed domain must also carry a
    verdict for every rule it defines (validate_completeness), and every
    expectation's rule id must actually belong to one of the spec's
    domains, since an expectation on a rule id the run never covered would
    otherwise score as a silent, always-passing control. If no rules pack
    was supplied, none of that second half can be checked, and this
    function records that explicitly rather than skipping it without a
    trace.
    """
    notes: list[CompletenessNote] = []

    for domain_id in spec.domains:
        result = run_state.domain_results.get(domain_id)
        if result is None:
            raise EvalStructuralError(
                f"spec domain '{domain_id}' has no DomainResult in the run state"
            )
        if result.status != "completed":
            raise EvalStructuralError(
                f"spec domain '{domain_id}' has status '{result.status}', not 'completed'"
            )

    if pack is None:
        for domain_id in spec.domains:
            notes.append(
                CompletenessNote(
                    domain_id=domain_id,
                    note="could-not-check: no --rules-dir supplied, so rule-verdict "
                    "completeness was not verified for this domain",
                )
            )
        notes.append(
            CompletenessNote(
                domain_id="(all domains)",
                note="could-not-check: no --rules-dir supplied, so the rule-to-domain "
                "ownership and orphan rule id cross-check was not run; an expectation on "
                "a rule id outside spec.domains cannot be told apart from one genuinely "
                "covered by the run in this mode",
            )
        )
        return notes

    for domain_id in spec.domains:
        domain = pack.get_domain(domain_id)
        if domain is None:
            raise EvalStructuralError(
                f"spec domain '{domain_id}' is not present in the supplied rules pack"
            )
        result = run_state.domain_results[domain_id]
        try:
            validate_completeness(domain, result)
        except IncompleteResultError as exc:
            raise EvalStructuralError(str(exc)) from exc

    orphan_rule_ids = sorted(
        e.rule_id for e in spec.expectations if pack.domain_id_for_rule(e.rule_id) not in spec.domains
    )
    if orphan_rule_ids:
        raise EvalStructuralError(
            f"expectation rule id(s) {orphan_rule_ids} do not belong to any domain in "
            f"spec.domains ({spec.domains}); an expectation on a rule the run never "
            "covered would score as a silently always-passing control"
        )

    return notes


def score(
    run_state: RunState,
    spec: EvalSpec,
    pack: RulesPack | None = None,
    *,
    run_state_path: str = "",
    expected_path: str = "",
) -> EvalResult:
    """Score run_state against spec, raising EvalStructuralError if the run
    cannot be scored at all.

    pack is optional: when supplied, verdict completeness is checked for
    every spec domain and expectation rule ids are cross-checked against
    it; when omitted, both checks are recorded as could-not-check rather
    than silently skipped. run_state_path and expected_path are carried
    through unchanged into the result purely for the report to display;
    score() itself never reads either path.
    """
    completeness_notes = _check_structural_gates(run_state, spec, pack)

    # Scoring only ever looks at the DomainResults the spec's own domains
    # cover. A finding or verdict recorded against a domain outside
    # spec.domains (an extra domain the run happened to also audit) must
    # never count towards an expectation, or a coincidental rule id match
    # in territory this eval does not cover could silently hit a finding
    # expectation or clear a control it was never meant to speak to.
    findings_by_rule: dict[str, list[Finding]] = defaultdict(list)
    verdicts_by_rule: dict[str, list[Verdict]] = defaultdict(list)
    for domain_id in spec.domains:
        result = run_state.domain_results[domain_id]
        for finding in result.findings:
            findings_by_rule[finding.rule_id].append(finding)
        for rule_verdict in result.rule_verdicts:
            verdicts_by_rule[rule_verdict.rule_id].append(rule_verdict.verdict)

    outcomes: list[ExpectationOutcome] = []

    for expectation in spec.expectations:
        matches = findings_by_rule.get(expectation.rule_id, [])

        if expectation.expect == "finding":
            outcome: _Outcome
            if not matches:
                outcome = "missed"
                detail = "no finding recorded for this rule id"
            elif expectation.location_contains is None or any(
                _location_matches(expectation.location_contains, f.location) for f in matches
            ):
                outcome = "hit"
                detail = "; ".join(f.location for f in matches)
            else:
                outcome = "found-wrong-location"
                detail = (
                    f"expected a location containing {expectation.location_contains!r}, "
                    f"found: {'; '.join(f.location for f in matches)}"
                )
        else:
            verdicts = verdicts_by_rule.get(expectation.rule_id, [])
            if Verdict.FINDING in verdicts or matches:
                outcome = "false-positive"
                detail = (
                    "; ".join(f.location for f in matches)
                    if matches
                    else "rule verdicted as finding with no Finding record"
                )
            elif Verdict.pass_ in verdicts:
                outcome = "held"
                detail = None
            else:
                # Not a finding, and never explicitly passed either: the
                # rule was verdicted not-applicable, could-not-evaluate, or
                # carries no verdict at all in this run's spec domains. The
                # control never actually ran, so it cannot be counted as
                # having held.
                outcome = "control-not-evaluated"
                if verdicts:
                    seen = ", ".join(sorted({v.value for v in verdicts}))
                    detail = f"verdicted {seen}, never pass"
                else:
                    detail = "no verdict recorded for this rule id"

        outcomes.append(
            ExpectationOutcome(
                rule_id=expectation.rule_id,
                expect=expectation.expect,
                outcome=outcome,
                why=expectation.why,
                detail=detail,
            )
        )

    # Unexpected findings, unlike the scoring above, are gathered from
    # every domain the run recorded, not just spec.domains: full visibility
    # into what the audit found is the point of this listing, and
    # restricting it to the spec's own domains would silently drop
    # exactly the kind of surprise (a finding in a domain the eval never
    # asked about) it exists to surface.
    expected_ids = {e.rule_id for e in spec.expectations}
    unexpected_findings = [
        UnexpectedFinding(rule_id=finding.rule_id, title=finding.title, location=finding.location)
        for result in run_state.domain_results.values()
        for finding in result.findings
        if finding.rule_id not in expected_ids
    ]

    return EvalResult(
        golden_repo=spec.golden_repo,
        run_state_path=run_state_path,
        expected_path=expected_path,
        outcomes=outcomes,
        unexpected_findings=unexpected_findings,
        completeness_notes=completeness_notes,
    )


def _render_summary(result: EvalResult) -> str:
    lines = [
        f"Eval: {result.golden_repo}",
        f"Run state: {result.run_state_path}",
        f"Expected: {result.expected_path}",
        "",
        f"Expected findings: {result.expected_hit} hit, {result.expected_missed} missed, "
        f"{result.expected_found_wrong_location} found in the wrong location",
        f"Controls: {result.controls_held} held, {result.controls_false_positive} false-positive, "
        f"{result.controls_not_evaluated} not evaluated",
        f"Unexpected findings: {result.unexpected_findings_count}",
        "",
    ]

    for outcome in result.outcomes:
        if outcome.outcome in ("hit", "held"):
            continue
        detail = f" ({outcome.detail})" if outcome.detail else ""
        lines.append(f"{outcome.outcome.upper()}  {outcome.rule_id}{detail}  {outcome.why}")

    if result.unexpected_findings:
        lines.append("")
        lines.append("Unexpected findings (rule id has no expectation):")
        for uf in result.unexpected_findings:
            lines.append(f"  {uf.rule_id}  {uf.title}  {uf.location}")

    if result.completeness_notes:
        lines.append("")
        for note in result.completeness_notes:
            lines.append(f"{note.domain_id}: {note.note}")

    return "\n".join(lines)


def _fail_structural(message: str) -> None:
    print(f"engineering-audit-eval: {message}", file=sys.stderr)
    raise SystemExit(2)


def _load_run_state(path: Path) -> RunState:
    try:
        return load_run_state_file(path)
    except RunStateLoadError as exc:
        _fail_structural(str(exc))
    raise AssertionError("unreachable")  # pragma: no cover


def _load_spec(path: Path) -> EvalSpec:
    if not path.is_file():
        _fail_structural(f"expectations file does not exist: {path}")
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        _fail_structural(f"could not read {path}: {exc}")
    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        _fail_structural(f"{path} is not valid JSON: {exc}")
    try:
        return EvalSpec.model_validate(raw)
    except ValidationError as exc:
        _fail_structural(f"{path} is not a valid eval spec: {exc}")
    raise AssertionError("unreachable")  # pragma: no cover


def _load_pack(rules_dir_arg: str | None) -> RulesPack | None:
    if rules_dir_arg is None:
        return None
    rules_dir = Path(rules_dir_arg).expanduser()
    if not rules_dir.is_dir():
        _fail_structural(f"rules pack directory does not exist or is not a directory: {rules_dir}")
    try:
        return load_pack(rules_dir)
    except RulesPackError as exc:
        _fail_structural(f"could not load rules pack: {exc}")
    raise AssertionError("unreachable")  # pragma: no cover


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="engineering-audit-eval",
        description="Score a run-state.json against a set of eval expectations.",
    )
    parser.add_argument("run_state_path", help="Path to a run-state.json to score.")
    parser.add_argument("--expected", required=True, help="Path to an eval spec (expected.json).")
    parser.add_argument(
        "--rules-dir",
        default=None,
        help="Rules pack directory, for verdict-completeness and rule-ownership checking. If "
        "omitted, both are recorded as could-not-check rather than skipped silently.",
    )
    parser.add_argument("--out", default=None, help="Path to write eval-result.json.")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    run_state_path = Path(args.run_state_path).expanduser()
    expected_path = Path(args.expected).expanduser()

    run_state = _load_run_state(run_state_path)
    spec = _load_spec(expected_path)
    pack = _load_pack(args.rules_dir)

    try:
        result = score(
            run_state,
            spec,
            pack,
            run_state_path=str(run_state_path),
            expected_path=str(expected_path),
        )
    except EvalStructuralError as exc:
        _fail_structural(str(exc))
        raise AssertionError("unreachable")  # pragma: no cover

    print(_render_summary(result))

    if args.out:
        out_path = Path(args.out).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(result.to_json(), encoding="utf-8")

    raise SystemExit(result.exit_code)


if __name__ == "__main__":
    main()
