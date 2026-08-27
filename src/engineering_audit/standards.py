"""The machine-readable rule set that persists across audit runs.

The rule set is the single source of truth from which the three standards
documents (agent coding standard, human coding standard, and engineering policy)
are rendered. Each rule is validated on load, and the constraint that
verified-finding rules require finding details is enforced: a corrupted or
malformed rule set is rejected with actionable errors citing the specific
field and value that failed.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

__all__ = [
    "RuleStatus",
    "RuleSource",
    "Rule",
    "RuleSet",
]


class RuleStatus(str, Enum):
    """The verification status of a rule."""

    PROVISIONAL = "provisional"
    VERIFIED_PASS = "verified-pass"
    VERIFIED_FINDING = "verified-finding"
    VERIFIED_NOT_APPLICABLE = "verified-not-applicable"


class RuleSource(str, Enum):
    """The source of a rule."""

    RULES_PACK = "rules-pack"
    STACK_PROFILE = "stack-profile"


class Rule(BaseModel):
    """A single rule in the rule set.

    Each rule carries its ID, domain, text, source, status, date, severity,
    finding details (if status is verified-finding), and any conflict with
    a stack profile.
    """

    rule_id: str = Field(description="Unique identifier for the rule, e.g. D06-R01")
    domain_id: str | None = Field(
        description="Domain this rule belongs to, or null for stack-profile-only rules"
    )
    text_short: str = Field(description="Short description of the rule")
    text_body: str = Field(description="Full text of the rule")
    source: str = Field(
        description="Source of the rule: either 'rules-pack' or 'stack-profile'"
    )
    stack_profile: str | None = Field(
        default=None,
        description="Stack profile name if source is stack-profile, otherwise null",
    )
    status: str = Field(
        description="Verification status: provisional, verified-pass, verified-finding, or verified-not-applicable"
    )
    verified_date: str = Field(description="Date the rule was verified (ISO 8601)")
    severity: str | None = Field(
        default=None,
        description="Severity level if status is verified-finding: critical, high, medium, or low",
    )
    finding_details: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Details of the finding if status is verified-finding. Must be null "
            "for all other statuses. Contains: precondition, path, line, "
            "issue_title, issue_body."
        ),
    )
    conflict_with_stack_profile: dict[str, Any] | None = Field(
        default=None,
        description="Conflict details if this rule conflicts with a stack profile rule",
    )
    conflict_resolution: str | None = Field(
        default=None,
        description="How the conflict with a stack profile was resolved",
    )
    source_url: str | None = Field(
        default=None, description="URL to the rule definition in the rules pack"
    )
    grill_intent_note: str | None = Field(
        default=None,
        description="Note recorded from engineering-grill intent for provisional rules",
    )

    @model_validator(mode="after")
    def _status_must_be_valid(self) -> "Rule":
        """Status must be one of the four allowed values."""
        valid_statuses = {s.value for s in RuleStatus}
        if self.status not in valid_statuses:
            raise ValueError(
                f"rule {self.rule_id}: status is '{self.status}' but must be one of "
                f"{', '.join(sorted(valid_statuses))}"
            )
        return self

    @model_validator(mode="after")
    def _source_must_be_valid(self) -> "Rule":
        """Source must be one of the two allowed values."""
        valid_sources = {s.value for s in RuleSource}
        if self.source not in valid_sources:
            raise ValueError(
                f"rule {self.rule_id}: source is '{self.source}' but must be one of "
                f"{', '.join(sorted(valid_sources))}"
            )
        return self

    @model_validator(mode="after")
    def _verified_finding_requires_finding_details(self) -> "Rule":
        """Rules with status=verified-finding must have finding_details."""
        if self.status == RuleStatus.VERIFIED_FINDING.value:
            if self.finding_details is None:
                raise ValueError(
                    f"rule {self.rule_id}: status is verified-finding but finding_details "
                    "is null; provide finding details for this rule"
                )
        return self

    @model_validator(mode="after")
    def _other_status_forbids_finding_details(self) -> "Rule":
        """Rules with status != verified-finding must not have finding_details."""
        if self.status != RuleStatus.VERIFIED_FINDING.value:
            if self.finding_details is not None:
                raise ValueError(
                    f"rule {self.rule_id}: status is {self.status} but finding_details "
                    "is not null; finding_details is only for verified-finding rules"
                )
        return self


class RuleSet(BaseModel):
    """A machine-readable rule set that persists across audit runs.

    The rule set is the single source of truth for all three standards documents.
    It is validated on load and rejects corrupted or malformed files with
    actionable errors citing the specific field and value that failed.
    """

    version: str = Field(description="Schema version of the rule set, e.g. '1.0'")
    project: str = Field(description="Project identifier that the rule set applies to")
    rules: list[Rule] = Field(
        default_factory=list,
        description="List of rules in the rule set",
    )

    def to_json(self) -> str:
        """Serialise the rule set to JSON string."""
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, data: str) -> "RuleSet":
        """Deserialise a rule set from JSON string.

        Raises ValueError if the JSON is malformed or does not match the schema.
        Error messages cite the specific field and value that failed validation.
        """
        try:
            raw = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Failed to parse JSON: {exc}") from exc
        return cls.model_validate(raw)

    def write(self, path: Path) -> None:
        """Write the rule set to a JSON file.

        Creates parent directories if needed. Overwrites existing file.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "RuleSet":
        """Load a rule set from a JSON file.

        Raises FileNotFoundError if the file does not exist.
        Raises ValueError if the file is corrupted or does not match the schema.
        Error messages cite the specific field and value that failed validation.
        """
        if not path.exists():
            raise FileNotFoundError(f"rule set file not found: {path}")
        data = path.read_text(encoding="utf-8")
        return cls.from_json(data)
