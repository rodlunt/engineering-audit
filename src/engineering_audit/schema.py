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

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

if TYPE_CHECKING:
    from engineering_audit.rules import Domain

__all__ = [
    "Verdict",
    "Severity",
    "RuleVerdict",
    "Finding",
    "SelfAssessment",
    "Coverage",
    "ConsultedSource",
    "DomainResult",
    "RunMeta",
    "TelemetryConsent",
    "AuditConfig",
    "RunState",
    "RunProgress",
    "RUN_STATE_SCHEMA_VERSION",
    "NOT_APPLICABLE_NOTE_SCHEMA_VERSION",
    "LEGACY_NOT_APPLICABLE_CONTEXT_KEY",
    "RunStateVersionError",
    "IncompleteResultError",
    "UnknownRuleIdError",
    "validate_completeness",
    "validate_consulted_sources",
    "validate_environment",
    "ENVIRONMENT_KEYS",
    "MAX_ENVIRONMENT_VALUE_CHARS",
]

# Bumped whenever RunState gains or changes a field in a way a reader written
# against an older version could not safely ignore. See RunState.from_json
# for the compatibility gate this backs: a run-state file naming a version
# higher than this is refused outright rather than partially parsed.
#
# RunProgress (the crash-recovery record) shares this constant deliberately
# rather than versioning itself separately: it carries the same component
# models, so a bump that makes an old reader unsafe for one makes it unsafe
# for the other, and one number cannot drift out of step with itself.
#
# Bumped to 3 when RunState.filed_issue_urls switched from rule-id keys to
# the per-finding "<rule id>#<n>" keys RunProgress.filed_issues already
# used: RunState.from_json migrates a schema_version <= 2 file's bare
# rule-id keys to "<rule id>#1" (see below), which only a reader that knows
# about the change can do safely.
#
# Bumped to 4 when a not-applicable verdict began requiring a note, the same
# way could-not-evaluate always has (see RuleVerdict below, and issue #100).
# No field changed shape for that one: the version number is what tells a
# reader whether a file was written before or after the constraint existed,
# and there is nothing else in the document that could. A file at 3 or below
# genuinely predates the requirement and is loaded with it relaxed, so a
# saved run-state stays re-renderable by engineering-audit-render; a file at
# 4 or above was written by a build that enforced it and is held to it.
RUN_STATE_SCHEMA_VERSION = 4

# The first schema version whose not-applicable verdicts must carry a note.
# Named rather than written as a bare 4 in the two from_json methods, so the
# next bump cannot silently move this line with it.
NOT_APPLICABLE_NOTE_SCHEMA_VERSION = 4

# Validation-context key that relaxes the not-applicable note requirement.
# Set only by RunState.from_json and RunProgress.from_json, only for a file
# that predates NOT_APPLICABLE_NOTE_SCHEMA_VERSION, and never by the tools
# that record a fresh verdict: a note that was never demanded cannot be
# invented on load, and refusing to read the file instead would break
# re-rendering every run-state saved before this constraint landed.
LEGACY_NOT_APPLICABLE_CONTEXT_KEY = "allow_unjustified_not_applicable"


def _not_applicable_note_relaxed(context: object) -> bool:
    """True when the current validation was handed the legacy context flag.

    Anything other than a mapping carrying that key set truthy is treated as
    "enforce the requirement": an unrecognised context must fail closed, not
    open a hole in the constraint by accident.
    """
    return bool(isinstance(context, dict) and context.get(LEGACY_NOT_APPLICABLE_CONTEXT_KEY))


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
        description=(
            "Free text. Required when verdict is could-not-evaluate: the reason. "
            "Required when verdict is not-applicable: the precondition of the rule "
            "that does not hold in this repository."
        ),
    )

    @model_validator(mode="after")
    def _note_required_for_could_not_evaluate(self) -> "RuleVerdict":
        if self.verdict == Verdict.COULD_NOT_EVALUATE and not (self.note and self.note.strip()):
            raise ValueError(
                f"rule {self.rule_id}: verdict is could-not-evaluate but no note (reason) was given"
            )
        return self

    @model_validator(mode="after")
    def _note_required_for_not_applicable(self, info: ValidationInfo) -> "RuleVerdict":
        # not-applicable used to be the one verdict that cost nothing to
        # emit: no reason demanded here, and nothing rendered in the report
        # either. A 16-domain run came back with 172 of 260 rules waved away
        # with "note": null, nine whole domains of it, and the report showed
        # those domains as "0 findings", exactly like a domain that was
        # swept and came back clean (issue #100). The precondition that does
        # not hold is a specific, cheap claim to write down, so it is now
        # demanded, the same way could-not-evaluate's reason is above.
        #
        # The one exemption is a run-state or run-progress file written
        # before this requirement existed; see
        # LEGACY_NOT_APPLICABLE_CONTEXT_KEY for why loading such a file must
        # not fail, and note that it is the loader that opts in, never a
        # caller recording a fresh verdict.
        if self.verdict == Verdict.NOT_APPLICABLE and not (self.note and self.note.strip()):
            if _not_applicable_note_relaxed(info.context):
                return self
            raise ValueError(
                f"rule {self.rule_id}: verdict is not-applicable but no note (reason) was "
                "given; say which precondition of the rule does not hold in this repository"
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


class ConsultedSource(BaseModel):
    """A source consulted outside the rules pack while reaching a verdict:
    documentation, a standard, a paper, anything fetched or read that is not
    the pack itself.

    The MCP server has no way to observe the driving agent's own web or file
    activity, so this list is schema-demanded self-reporting, not something
    the server can verify happened. That is a real limit, not a hidden one:
    a source the agent never records here is a source this tool can never
    know about, the same way a rule the agent never verdicts can never be
    recorded as a pass.
    """

    rule_id: str
    url: str
    title: str
    why: str
    accessed: str

    @field_validator("accessed")
    @classmethod
    def _valid_iso_timestamp(cls, value: str) -> str:
        # Same normalisation as RunMeta.started/finished below (a trailing
        # 'Z' has no native fromisoformat support on Python 3.10, this
        # project's minimum), duplicated rather than shared: this field is
        # required and never None, unlike RunMeta's optional timestamps, so
        # sharing one validator would need a branch neither side actually
        # needs.
        try:
            normalised = value[:-1] + "+00:00" if value.endswith("Z") else value
            datetime.fromisoformat(normalised)
        except ValueError as exc:
            raise ValueError(f"accessed timestamp {value!r} is not a valid ISO 8601 string") from exc
        return value

    @model_validator(mode="after")
    def _fields_not_blank(self) -> "ConsultedSource":
        # A source consulted claim is only as good as what it points at: a
        # blank url, title or why is a citation nobody can check.
        for field_name in ("url", "title", "why"):
            if not getattr(self, field_name).strip():
                raise ValueError(
                    f"consulted source for rule {self.rule_id}: {field_name} must not be blank"
                )
        return self


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
    consulted_sources: list[ConsultedSource] = Field(
        default_factory=list,
        description=(
            "Sources consulted outside the rules pack while reaching this domain's "
            "verdicts. Optional and self-reported; see validate_consulted_sources for "
            "the one check applied against it (every rule_id must be one of this "
            "domain's own rules)."
        ),
    )
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
            "Result of comparing the installed tool build against the tool's latest "
            "GitHub release tag, prefixed 'current', 'stale', 'could-not-check' or "
            "'not-checked'. None means the check was not performed at all (e.g. an "
            "older run-state file predating this field). 'could-not-check' is "
            "deliberately distinct from 'current': it means the comparison could not "
            "be made (no network, git missing, no version tags found), not that the "
            "installed build was confirmed up to date. 'not-checked' is distinct again: "
            "the check was turned off deliberately (--no-update-check or "
            "ENGINEERING_AUDIT_NO_UPDATE_CHECK), not attempted and failed. A failed or "
            "skipped check must never be reported as current."
        ),
    )
    pack_update_check: str | None = Field(
        default=None,
        description=(
            "Result of comparing the loaded rules pack against its own remote's "
            "latest release tag, prefixed 'current', 'stale', 'could-not-check' or "
            "'not-checked'. Same contract and same discipline as update_check, which "
            "does this for the tool itself: 'could-not-check' and 'not-checked' are "
            "both distinct from 'current', and neither a failed nor a skipped check "
            "may read as freshness. None means the field predates this build, e.g. an "
            "older run-state file.\n\n"
            "The tool checked itself for staleness and did not check its ruleset, which "
            "is the thing that decides what actually gets audited."
        ),
    )
    assistant: str
    model: str
    earlier_contributors: list[str] = Field(
        default_factory=list,
        description=(
            "Assistant/model pairs that worked on this run before the current one, "
            "oldest first, each formatted 'assistant/model'. Populated only when a "
            "resume is picked up by a different assistant or model from the one that "
            "started the run. Empty for the normal case of a run finished by whoever "
            "began it.\n\n"
            "This exists because 'assistant' and 'model' name the CURRENT worker, and "
            "a resumed run genuinely has more than one. Before this field, a resume "
            "kept the original pair and silently discarded the caller's, so the "
            "report's provenance header credited whichever model happened to start "
            "the run rather than the one that produced the findings. Overwriting "
            "without recording the earlier pair would have been the same bug pointing "
            "the other way; the header needs to be able to name every contributor."
        ),
    )
    repo_name: str
    repo_commit: str
    started: str
    finished: str | None = None
    server_started: str | None = Field(
        default=None,
        description=(
            "UTC timestamp the server itself stamped when begin_run first created this "
            "run, independent of the assistant-supplied 'started' value above. Kept "
            "unchanged across a resume, the same way 'started' is: it names when this "
            "run's story began, not when a resumed session picked it back up, so a "
            "resume's legitimate wall-clock gap lands in both durations equally rather "
            "than in only one of them.\n\n"
            "None means this run predates the field, or was resumed from a run-state "
            "file that did: an unmeasured span is unknown, and unknown must never render "
            "as zero or as agreement with the assistant-supplied figure."
        ),
    )
    server_finished: str | None = Field(
        default=None,
        description=(
            "UTC timestamp the server itself stamped when render_report was called, "
            "independent of the assistant-supplied 'finished' value above. Same None "
            "contract as server_started: absent means unmeasured, never zero."
        ),
    )
    environment: dict[str, str] | None = Field(
        default=None,
        description=(
            "Host facts the report header cannot carry, keyed by ENVIRONMENT_KEYS "
            "('os', 'host_cli', 'host_cli_version'). Validated at the tool boundary by "
            "validate_environment, not by this model: this model also has to load "
            "run-state files written by older builds, and refusing to read a finished "
            "run's record because it carries a key that is no longer accepted would "
            "turn a stale key into an unreadable report."
        ),
    )

    @field_validator("started", "finished", "server_started", "server_finished")
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
    # Off by default is not just the general rule above, it carries its own
    # reason here: the URLs a domain sweep fetched can hint at what a
    # private repository is about, even though the finding text itself never
    # leaves the machine. The configuration page's label must say this.
    consulted_sources: bool = False


class AuditConfig(BaseModel):
    selected_domain_ids: list[str]
    issue_mode: str
    feedback_text: str | None = None
    telemetry_consent: TelemetryConsent = Field(default_factory=TelemetryConsent)
    deliverables_dir: str | None = Field(
        default=None,
        description=(
            "Where render_report writes report.html and run-state.json, as an absolute, "
            "already-resolved path (see engineering_audit.output_location.resolve_deliverables_dir). "
            "None means the default: the run's own output_dir, unchanged from how every run "
            "before this field existed behaved. output_dir itself is never affected by this "
            "field: it stays the run's working directory for the crash-recovery progress file "
            "regardless of where the finished deliverables end up (issue #109)."
        ),
    )

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

    @field_validator("deliverables_dir")
    @classmethod
    def _deliverables_dir_not_blank(cls, value: str | None) -> str | None:
        # Syntactic only: a blank string is never a real choice, just an
        # empty form field that made it through. Whether the path itself is
        # usable (parent exists, is writable, no report already there) is a
        # filesystem question, deliberately not asked here; see
        # output_location.validate_deliverables_dir and its docstring for why
        # this model stays I/O-free.
        if value is not None and not value.strip():
            raise ValueError("deliverables_dir must not be blank when provided")
        return value


def _parse_versioned(data: str, label: str) -> tuple[object, int]:
    """Parse a run-state or run-progress JSON document and enforce the
    schema-version gate, returning the raw object for the caller to validate
    along with the version the document declared.

    Shared by :meth:`RunState.from_json` and :meth:`RunProgress.from_json` so
    the two files on disk cannot end up with two different ideas of what a
    version number means; ``label`` is the only difference between them, and
    only in the error text.

    The version travels back to the caller because it decides more than
    whether the file is readable: it also decides which validation rules the
    file predates (see :data:`NOT_APPLICABLE_NOTE_SCHEMA_VERSION`). A
    document whose top level is not an object has no version to read, and is
    reported as the current one so that nothing is relaxed for it; the
    caller's own ``model_validate`` rejects it on its own terms.
    """
    raw = json.loads(data)
    if isinstance(raw, dict):
        version: int = raw.get("schema_version", 1)
        # bool is an int subclass, and `True > 2` is a valid comparison that
        # would sail through the gate below; a non-integer version is a
        # corrupt file, and must be named as one rather than raising a raw
        # TypeError out of the comparison or being read as version 1.
        if not isinstance(version, int) or isinstance(version, bool):
            raise RunStateVersionError(
                f"{label} has schema_version {version!r}, which is not an integer version "
                f"number. The file is corrupt or was not written by engineering-audit."
            )
        if version > RUN_STATE_SCHEMA_VERSION:
            raise RunStateVersionError(
                f"{label} is schema_version {version}, but this version of "
                f"engineering-audit only understands up to schema_version "
                f"{RUN_STATE_SCHEMA_VERSION}. Upgrade engineering-audit to a version that "
                f"supports this {label}."
            )
        raw.setdefault("schema_version", 1)
        return raw, version
    return raw, RUN_STATE_SCHEMA_VERSION


def _legacy_validation_context(version: int) -> dict[str, bool] | None:
    """The validation context for a document declaring ``version``, or None
    when it is held to every current rule.

    One place, used by both from_json methods, so a file that is legacy for
    one model cannot be legacy for the other.
    """
    if version < NOT_APPLICABLE_NOTE_SCHEMA_VERSION:
        return {LEGACY_NOT_APPLICABLE_CONTEXT_KEY: True}
    return None


class RunState(BaseModel):
    """The full state of one audit run: metadata, config and per-domain results."""

    schema_version: int = RUN_STATE_SCHEMA_VERSION
    meta: RunMeta
    config: AuditConfig
    domain_results: dict[str, DomainResult] = Field(default_factory=dict)
    filed_issue_urls: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Finding key ('<rule id>#<n>') -> GitHub issue URL, for findings already "
            "filed this run. Since schema_version 3, keyed per finding rather than per "
            "rule id, the same shape RunProgress.filed_issues has always used, so a rule "
            "with two findings carries both their urls rather than losing one."
        ),
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

        A document at schema_version 2 or below has ``filed_issue_urls``
        keyed by bare rule id, from before the schema_version 3 switch to
        per-finding ``"<rule id>#<n>"`` keys. Such a file holds exactly one
        url per rule, deterministically the first one filed (server.py used
        to project the finding-keyed bookkeeping down to that shape when
        writing it, a projection pinned by a test), so migrating a bare key
        to ``"<rule id>#1"`` is lossless and truthful, never a guess standing
        in for a second url the old file never recorded.

        A document whose top level is valid JSON but not an object (a bare
        array, string, number or ``null``) has no ``schema_version`` to read
        in the first place. That case is deliberately left for
        :meth:`model_validate` below to reject on its own terms, as a
        :class:`~pydantic.ValidationError` naming the actual type it got:
        calling ``.get`` on it here would raise a raw ``AttributeError``
        instead, which is exactly the kind of unhandled crash a caller
        expecting either a ``RunState`` or a named parse error should never
        see.

        A document below schema_version 4 predates the requirement that a
        not-applicable verdict carry a note, and is validated with that one
        rule relaxed (see :data:`LEGACY_NOT_APPLICABLE_CONTEXT_KEY`).
        Rejecting such a file would make every run-state saved before the
        change unreadable to ``engineering-audit-render``, and inventing the
        missing reasons on load would be worse still: the report renders
        them as reasons nobody recorded, which is what they are. Every other
        rule, including could-not-evaluate's own note requirement, still
        applies in full.
        """
        raw, version = _parse_versioned(data, "run-state file")
        if isinstance(raw, dict) and raw.get("schema_version", 1) <= 2:
            filed_issue_urls = raw.get("filed_issue_urls")
            if isinstance(filed_issue_urls, dict):
                raw["filed_issue_urls"] = {
                    (key if "#" in key else f"{key}#1"): url
                    for key, url in filed_issue_urls.items()
                }
        return cls.model_validate(raw, context=_legacy_validation_context(version))


class RunProgress(BaseModel):
    """The crash-recovery record of a run that is still in progress.

    Written to the run's output directory as it advances, so a server that is
    killed mid-run loses at most the domain in flight rather than every result
    recorded so far. It is deliberately a sibling of :class:`RunState` rather
    than a reuse of it: ``config`` is optional here, where RunState requires
    it. A run genuinely exists between begin_run and the user submitting the
    configuration page, and RunState's required ``config`` is what lets
    everything downstream trust that a rendered report was produced against a
    configuration a person actually chose.

    ``filed_issues`` is keyed by finding (``"<rule id>#<n>"``), the same shape
    RunState.filed_issue_urls has used since schema_version 3. The two fields
    were not always the same shape: before that bump, RunState's map was keyed
    by rule id and knowingly lossy where one rule carried two findings.
    Resuming from a lossy map would have re-filed an already-filed issue on
    the user's repository, which is why this field kept the full-fidelity
    shape even while RunState's did not.

    Everything else is shared with RunState verbatim: the same component
    models, the same schema_version constant and the same version gate.
    """

    schema_version: int = RUN_STATE_SCHEMA_VERSION
    meta: RunMeta
    config: AuditConfig | None = None
    config_mode: str | None = Field(
        default=None,
        description="'preset' or 'interactive', so a resumed run does not re-open a config page.",
    )
    repo_dir: str | None = Field(
        default=None,
        description="The audited repository's path on disk, as given to begin_run.",
    )
    domain_results: dict[str, DomainResult] = Field(default_factory=dict)
    filed_issues: dict[str, str] = Field(
        default_factory=dict,
        description="Finding key ('<rule id>#<n>') -> GitHub issue URL, for issues already filed.",
    )
    feedback_issue_url: str | None = None
    completed: bool = Field(
        default=False,
        description=(
            "True once render_report has written the run's report and run-state. A completed "
            "record is never offered as resumable work: the file is normally removed at that "
            "point, and this flag is what stops a removal that failed from resurrecting a run "
            "that is already finished."
        ),
    )

    @model_validator(mode="after")
    def _domain_results_keys_match_domain_id(self) -> "RunProgress":
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
    def from_json(cls, data: str) -> "RunProgress":
        """Parse a run-progress JSON document, enforcing the same
        schema-version gate as :meth:`RunState.from_json`.

        A recovery file this tool cannot fully understand must never be read
        as a partial run and resumed from: the domains it appears to be
        missing would be re-audited, and whatever the newer version recorded
        would be dropped without a word.

        A file below schema_version 4 gets the same one relaxation
        :meth:`RunState.from_json` grants, and for the same reason: a run
        interrupted under an older build must still be resumable. Domains
        recorded after the resume go through the current rules in full, so
        the exemption stops at the verdicts that were already on disk.
        """
        raw, version = _parse_versioned(data, "run-progress file")
        return cls.model_validate(raw, context=_legacy_validation_context(version))


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


# The complete, closed set of keys begin_run will accept for a run's
# environment metadata, and the reason the field is a fixed vocabulary rather
# than the free-form dict it used to be: this metadata ships inside feedback
# issues filed publicly on the tool's own repository, and the driving
# assistant that supplies it is untrusted input. An open dict populated by a
# model is an unbounded disclosure surface when the repository being audited
# is private; three named keys are not.
#
# These three, specifically, are the facts the report header cannot already
# carry. Assistant, model and tool version are fixed header rows, so naming
# them here would duplicate the header and add nothing, which is exactly why
# nobody ever populated the free-form version of this field.
ENVIRONMENT_KEYS = ("os", "host_cli", "host_cli_version")

# A per-value character cap. The three keys hold an OS name, a CLI name and a
# version string, none of which is long. The cap exists because constraining
# the key set alone still leaves the values model-written and unbounded: a
# closed vocabulary of keys with a paragraph of repository detail stuffed into
# one of them would disclose exactly as much as the open dict did.
MAX_ENVIRONMENT_VALUE_CHARS = 200


def validate_environment(environment: dict[str, str] | None) -> dict[str, str] | None:
    """Return ``environment`` unchanged, or raise ValueError describing what is
    wrong with it.

    None and an empty dict both pass through: not reporting the environment is
    a legitimate state (the assistant genuinely could not determine it), and it
    renders as "No environment information reported for this run" rather than
    as anything resembling a confirmed fact.

    Rejection is deliberate rather than filtering the unknown keys out
    silently: an assistant that sent a key this tool does not accept has
    misunderstood the contract, and quietly dropping the value would leave it
    believing the fact was recorded when it was not.
    """
    if environment is None:
        return None

    unknown = sorted(set(environment) - set(ENVIRONMENT_KEYS))
    if unknown:
        raise ValueError(
            f"environment carries key(s) this tool does not accept: {unknown}. "
            f"The accepted keys are exactly {list(ENVIRONMENT_KEYS)}. This metadata is "
            "included in feedback issues filed publicly, so the key set is closed; put "
            "nothing else in it."
        )

    problems: list[str] = []
    for key in ENVIRONMENT_KEYS:
        if key not in environment:
            continue
        value = environment[key]
        if not isinstance(value, str):
            problems.append(f"{key} must be a string, got {type(value).__name__}")
            continue
        if not value.strip():
            problems.append(
                f"{key} is empty. Omit the key entirely rather than sending a blank value: "
                "an omitted fact and a fact reported as blank are not the same thing"
            )
            continue
        if len(value) > MAX_ENVIRONMENT_VALUE_CHARS:
            problems.append(
                f"{key} is {len(value)} characters, over the "
                f"{MAX_ENVIRONMENT_VALUE_CHARS} character limit. This field takes an OS "
                "name, a host CLI name and a version string, nothing longer"
            )
    if problems:
        raise ValueError("environment is not acceptable: " + "; ".join(problems) + ".")
    return environment


class UnknownRuleIdError(Exception):
    """Raised when a DomainResult's consulted_sources cites a rule id that is
    not one of the domain's own rules. A citation cannot be attributed to a
    rule that does not exist in this domain, the same way a rule_verdict for
    a nonexistent rule id is rejected by :func:`validate_completeness`."""


def validate_consulted_sources(domain: "Domain", result: DomainResult) -> None:
    """Raise :class:`UnknownRuleIdError` if any of result.consulted_sources
    cites a rule id that is not one of domain's own rules.

    Checked independently of DomainResult.status, unlike
    :func:`validate_completeness`: a source consulted while deciding a
    domain could not run at all is still attributed to a rule in this
    domain, and its rule id still has to name a real one.
    """
    domain_rule_ids = {rule.id for rule in domain.rules}
    unknown = sorted({source.rule_id for source in result.consulted_sources} - domain_rule_ids)
    if unknown:
        raise UnknownRuleIdError(
            f"domain {domain.id}: consulted_sources reference rule id(s) this domain "
            f"does not define: {unknown}. A consulted source is attributed to a specific "
            "rule; check the ids against get_domain."
        )
