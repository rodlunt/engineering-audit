"""Pydantic models and validation for audit run state.

The models in this file are the enforcement point for a hardening rule that
runs through the whole project: a rule that was never evaluated must not be
representable as a rule that passed. A :class:`DomainResult` cannot claim
``status="completed"`` while leaving a rule un-verdicted; :func:`validate_completeness`
is the loud check that catches it, listing exactly which rule ids are missing
rather than letting the gap pass unnoticed.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator, model_validator

if TYPE_CHECKING:
    from engineering_audit.rules import Domain

__all__ = [
    "Verdict",
    "Severity",
    "RuleVerdict",
    "Finding",
    "SelfAssessment",
    "Coverage",
    "DomainResult",
    "RunMeta",
    "TelemetryConsent",
    "AuditConfig",
    "RunState",
    "RUN_STATE_SCHEMA_VERSION",
    "RunStateVersionError",
    "IncompleteResultError",
    "validate_completeness",
]

# Bumped whenever RunState gains or changes a field in a way a reader written
# against an older version could not safely ignore. See RunState.from_json
# for the compatibility gate this backs: a run-state file naming a version
# higher than this is refused outright rather than partially parsed.
RUN_STATE_SCHEMA_VERSION = 2


class Verdict(str, Enum):
    """The outcome of checking one rule against the repository."""

    pass_ = "pass"
    FINDING = "finding"
    NOT_APPLICABLE = "not-applicable"
    COULD_NOT_EVALUATE = "could-not-evaluate"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RuleVerdict(BaseModel):
    """The verdict reached for a single rule."""

    rule_id: str
    verdict: Verdict
    note: str | None = Field(
        default=None,
        description="Free text. Required when verdict is could-not-evaluate: the reason.",
    )

    @model_validator(mode="after")
    def _note_required_for_could_not_evaluate(self) -> "RuleVerdict":
        if self.verdict == Verdict.COULD_NOT_EVALUATE and not (self.note and self.note.strip()):
            raise ValueError(
                f"rule {self.rule_id}: verdict is could-not-evaluate but no note (reason) was given"
            )
        return self


# Matches the ':line' or ':start-end' suffix documented on Finding.location,
# so the path segment in front of it can be checked separately from the line
# reference itself.
_LOCATION_LINE_SUFFIX_RE = re.compile(r":(?P<start>\d+)(?:-(?P<end>\d+))?$")


class Finding(BaseModel):
    """A single audit finding: a rule verdicted as 'finding', with detail."""

    rule_id: str
    severity: Severity
    title: str
    location: str = Field(description="'path:line', 'path:start-end' or 'path'")
    body_md: str
    issue_title: str
    issue_body: str

    @model_validator(mode="after")
    def _location_matches_documented_format(self) -> "Finding":
        # A finding is a claim the tool publishes as a GitHub issue; a blank
        # or free-text location undermines the evidence it is meant to give.
        # This only checks the shape (a non-empty path, an optional positive
        # line or line range), not that the path exists in the repository:
        # that check belongs to whoever has repository access, not the model.
        suffix_match = _LOCATION_LINE_SUFFIX_RE.search(self.location)
        path = self.location[: suffix_match.start()] if suffix_match else self.location
        if not path.strip():
            raise ValueError(
                f"rule {self.rule_id}: location {self.location!r} has no file path segment; "
                "the documented format is 'path', 'path:line' or 'path:start-end'"
            )
        if suffix_match:
            start_line = int(suffix_match.group("start"))
            end_line = suffix_match.group("end")
            if start_line < 1:
                raise ValueError(
                    f"rule {self.rule_id}: location {self.location!r} has line number "
                    f"{start_line}, which must be a positive integer"
                )
            if end_line is not None and int(end_line) < 1:
                raise ValueError(
                    f"rule {self.rule_id}: location {self.location!r} has end line "
                    f"{end_line}, which must be a positive integer"
                )
            if end_line is not None and int(end_line) < start_line:
                raise ValueError(
                    f"rule {self.rule_id}: location {self.location!r} has end line "
                    f"{end_line} before start line {start_line}; a range must run forwards"
                )
        return self


class SelfAssessment(BaseModel):
    confidence: str
    limits: str = ""

    @field_validator("confidence")
    @classmethod
    def _confidence_allowed(cls, value: str) -> str:
        allowed = {"high", "medium", "low"}
        if value not in allowed:
            raise ValueError(f"confidence must be one of {sorted(allowed)}, got {value!r}")
        return value


class Coverage(BaseModel):
    files_inspected: int
    files_skipped: int
    note: str | None = None

    @field_validator("files_inspected", "files_skipped")
    @classmethod
    def _non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("file counts cannot be negative")
        return value


class DomainResult(BaseModel):
    """The result of auditing one domain against a repository.

    A rule that has no verdict is not the same thing as a rule that passed,
    which is why 'completed' status is validated at two levels: here (internal
    consistency, findings must have a matching finding-verdict) and again by
    :func:`validate_completeness` (every rule in the domain has a verdict at
    all). Splitting them lets this model validate on its own while
    :func:`validate_completeness` needs the domain's full rule list.
    """

    domain_id: str
    status: str
    rule_verdicts: list[RuleVerdict] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    self_assessment: SelfAssessment | None = None
    coverage: Coverage | None = None
    reason: str | None = Field(
        default=None, description="Required when status is could-not-run: why the domain could not be audited."
    )

    @field_validator("status")
    @classmethod
    def _status_allowed(cls, value: str) -> str:
        allowed = {"completed", "could-not-run"}
        if value not in allowed:
            raise ValueError(f"status must be one of {sorted(allowed)}, got {value!r}")
        return value

    @model_validator(mode="after")
    def _rule_verdict_ids_unique(self) -> "DomainResult":
        # The two consumers that check this list for consistency (this class's
        # own _consistency validator and validate_completeness) both build a
        # set of rule ids first, which quietly discards a duplicate rather
        # than rejecting it. That lets a run's saved output record the same
        # rule as both passed and a finding with nothing catching it, so the
        # duplicate is rejected here, before either check gets a chance to
        # collapse it away.
        counts = Counter(rv.rule_id for rv in self.rule_verdicts)
        duplicates = sorted(rule_id for rule_id, n in counts.items() if n > 1)
        if duplicates:
            raise ValueError(
                f"domain {self.domain_id}: duplicate rule_verdict(s) for rule id(s) "
                f"{duplicates}; each rule may carry at most one verdict per domain result"
            )
        return self

    @model_validator(mode="after")
    def _consistency(self) -> "DomainResult":
        if self.status == "could-not-run":
            if self.rule_verdicts or self.findings:
                raise ValueError(
                    f"domain {self.domain_id}: status is could-not-run but rule_verdicts "
                    "and/or findings are non-empty"
                )
            if not (self.reason and self.reason.strip()):
                raise ValueError(
                    f"domain {self.domain_id}: status is could-not-run but no reason was given"
                )
            return self

        verdicted_finding_ids = {
            rv.rule_id for rv in self.rule_verdicts if rv.verdict == Verdict.FINDING
        }
        finding_rule_ids = {f.rule_id for f in self.findings}
        missing_verdicts = finding_rule_ids - verdicted_finding_ids
        if missing_verdicts:
            raise ValueError(
                f"domain {self.domain_id}: finding(s) for rule id(s) "
                f"{sorted(missing_verdicts)} have no matching rule_verdict with verdict=finding"
            )
        return self


class RunMeta(BaseModel):
    """Metadata about one audit run. Always sent with feedback; not a consent toggle."""

    tool_version: str
    tool_commit: str | None = Field(
        default=None,
        description=(
            "Full git SHA the installed tool build was made from, read from the "
            "package's PEP 610 install record. None means it could not be determined "
            "(e.g. installed from a wheel with no VCS metadata, or a source checkout "
            "never installed at all), never a fabricated value."
        ),
    )
    rules_pack_name: str
    rules_pack_version: str | None = None
    rules_pack_commit: str | None = Field(
        default=None,
        description=(
            "Full git SHA of the rules pack directory's checkout, if it is inside a "
            "git repository. None means it could not be determined (git missing, not "
            "a repo, or the lookup failed), never a fabricated value."
        ),
    )
    update_check: str | None = Field(
        default=None,
        description=(
            "Tri-state result of comparing the installed tool build against the "
            "tool's latest GitHub release tag, prefixed 'current', 'stale', or "
            "'could-not-check'. None means the check was not performed at all (e.g. "
            "an older run-state file predating this field). 'could-not-check' is "
            "deliberately distinct from 'current': it means the comparison could not "
            "be made (no network, git missing, no version tags found), not that the "
            "installed build was confirmed up to date. A failed check must never be "
            "reported as current."
        ),
    )
    assistant: str
    model: str
    repo_name: str
    repo_commit: str
    started: str
    finished: str | None = None
    environment: dict[str, str] | None = None

    @field_validator("started", "finished")
    @classmethod
    def _valid_iso_timestamp(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            # Python 3.10 (the project's minimum, see requires-python in
            # pyproject.toml) has no 'Z' support in fromisoformat: normalise
            # a trailing 'Z' (UTC) to the '+00:00' offset it always accepts.
            normalised = value[:-1] + "+00:00" if value.endswith("Z") else value
            datetime.fromisoformat(normalised)
        except ValueError as exc:
            raise ValueError(f"timestamp {value!r} is not a valid ISO 8601 string") from exc
        return value


class TelemetryConsent(BaseModel):
    """Consent flags for what is bundled into optional feedback.

    Run metadata (:class:`RunMeta`) is always sent with feedback and is
    deliberately absent from this model: it is not a toggle, it is required
    context for the feedback to mean anything.

    Every flag here defaults to False: this is what a fresh configuration
    page renders before a person has ticked anything, and opt-in consent
    means nothing if the box already looks ticked when it first loads.
    """

    coverage: bool = False
    rollup: bool = False
    self_assessment: bool = False
    environment: bool = False


class AuditConfig(BaseModel):
    selected_domain_ids: list[str]
    issue_mode: str
    feedback_text: str | None = None
    telemetry_consent: TelemetryConsent = Field(default_factory=TelemetryConsent)

    @field_validator("issue_mode")
    @classmethod
    def _issue_mode_allowed(cls, value: str) -> str:
        allowed = {"github", "report"}
        if value not in allowed:
            raise ValueError(f"issue_mode must be one of {sorted(allowed)}, got {value!r}")
        return value

    @field_validator("selected_domain_ids")
    @classmethod
    def _at_least_one_domain(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("selected_domain_ids must not be empty")
        return value


class RunState(BaseModel):
    """The full state of one audit run: metadata, config and per-domain results."""

    schema_version: int = RUN_STATE_SCHEMA_VERSION
    meta: RunMeta
    config: AuditConfig
    domain_results: dict[str, DomainResult] = Field(default_factory=dict)
    filed_issue_urls: dict[str, str] = Field(
        default_factory=dict,
        description="Rule id -> GitHub issue URL, for findings already filed this run.",
    )
    feedback_issue_url: str | None = None

    @model_validator(mode="after")
    def _domain_results_keys_match_domain_id(self) -> "RunState":
        mismatched = [
            (key, result.domain_id)
            for key, result in self.domain_results.items()
            if key != result.domain_id
        ]
        if mismatched:
            raise ValueError(
                "domain_results key(s) do not match their DomainResult.domain_id: "
                f"{mismatched}"
            )
        return self

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, data: str) -> "RunState":
        """Parse a run-state JSON document, enforcing the schema-version gate.

        A document with no ``schema_version`` field predates the field
        entirely and is treated as version 1: accepted, with the fields
        introduced since (``filed_issue_urls``, ``feedback_issue_url``)
        taking their defaults. A document naming a version higher than this
        tool understands is refused outright, with both version numbers in
        the message: silently parsing only the fields this version
        recognises would drop whatever the newer version added without
        saying so, and a report built from that partial read would look
        complete while being wrong.

        A document whose top level is valid JSON but not an object (a bare
        array, string, number or ``null``) has no ``schema_version`` to read
        in the first place. That case is deliberately left for
        :meth:`model_validate` below to reject on its own terms, as a
        :class:`~pydantic.ValidationError` naming the actual type it got:
        calling ``.get`` on it here would raise a raw ``AttributeError``
        instead, which is exactly the kind of unhandled crash a caller
        expecting either a ``RunState`` or a named parse error should never
        see.
        """
        raw = json.loads(data)
        if isinstance(raw, dict):
            version = raw.get("schema_version", 1)
            if version > RUN_STATE_SCHEMA_VERSION:
                raise RunStateVersionError(
                    f"run-state file is schema_version {version}, but this version of "
                    f"engineering-audit only understands up to schema_version "
                    f"{RUN_STATE_SCHEMA_VERSION}. Upgrade engineering-audit to a version that "
                    "supports this run-state file."
                )
            raw.setdefault("schema_version", 1)
        return cls.model_validate(raw)


class RunStateVersionError(Exception):
    """Raised when a run-state file declares a schema_version newer than this
    tool understands. A higher version is a genuine incompatibility, not a
    detail to shrug past: silently parsing only the fields this version
    recognises would drop whatever the newer version added without saying
    so. Upgrading the tool is the only correct fix, so the error says that
    explicitly rather than leaving the caller to guess."""


class IncompleteResultError(Exception):
    """Raised when a completed DomainResult does not carry a verdict for every
    rule the domain defines. A rule with no verdict is indistinguishable from
    a passed rule to anything downstream that only counts findings, so this
    is treated as a hard failure rather than a warning."""


def validate_completeness(domain: "Domain", result: DomainResult) -> None:
    """Raise :class:`IncompleteResultError` if a completed result is missing a
    verdict for any rule the domain defines.

    A ``could-not-run`` result is exempt: by construction (see
    :class:`DomainResult`) it carries no verdicts and no findings at all, and
    its ``reason`` field is the record of why the domain was not audited.
    """
    if result.status == "could-not-run":
        return

    domain_rule_ids = {rule.id for rule in domain.rules}
    verdicted_rule_ids = {rv.rule_id for rv in result.rule_verdicts}
    missing = sorted(domain_rule_ids - verdicted_rule_ids)
    unknown = sorted(verdicted_rule_ids - domain_rule_ids)
    problems: list[str] = []
    if missing:
        problems.append(
            f"{len(missing)} rule(s) have no verdict and are not could-not-run: "
            f"{missing}. A skipped rule is not a pass; verdict every rule."
        )
    if unknown:
        problems.append(
            f"{len(unknown)} verdict(s) reference rule id(s) the domain does not "
            f"define: {unknown}. A verdict for a nonexistent rule cannot be "
            "attributed; check the ids against get_domain."
        )
    if problems:
        raise IncompleteResultError(f"domain {domain.id}: " + " ".join(problems))
