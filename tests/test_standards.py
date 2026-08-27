"""Tests for the rule set schema, validation, and persistence.

This module tests the RuleSet class which manages the machine-readable rule
set that persists across audit runs. It validates schema on load and enforces
the constraint that verified-finding rules require finding_details while
other rules must not have it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engineering_audit.standards import RuleSet, Rule


class TestRuleValidation:
    """Tests for individual Rule validation."""

    def test_rule_verified_finding_requires_finding_details(self) -> None:
        """A rule with status=verified-finding must have finding_details."""
        rule_data = {
            "rule_id": "D06-R03",
            "domain_id": "d06",
            "text_short": "Error handling",
            "text_body": "Handle errors properly.",
            "source": "rules-pack",
            "stack_profile": None,
            "status": "verified-finding",
            "verified_date": "2026-08-25",
            "severity": "medium",
            "finding_details": None,
            "conflict_with_stack_profile": None,
            "conflict_resolution": None,
            "source_url": None,
        }
        with pytest.raises(ValueError) as exc_info:
            Rule.model_validate(rule_data)
        assert "D06-R03" in str(exc_info.value)
        assert "verified-finding" in str(exc_info.value)
        assert "finding_details" in str(exc_info.value)

    def test_rule_other_status_forbids_finding_details(self) -> None:
        """A rule with status != verified-finding must not have finding_details."""
        finding_obj = {
            "precondition": "Precondition here",
            "path": "src/file.py",
            "line": 42,
            "issue_title": "Title",
            "issue_body": "Body",
        }
        rule_data = {
            "rule_id": "D06-R01",
            "domain_id": "d06",
            "text_short": "Type hints",
            "text_body": "Use type hints.",
            "source": "rules-pack",
            "stack_profile": None,
            "status": "verified-pass",
            "verified_date": "2026-08-25",
            "severity": None,
            "finding_details": finding_obj,
            "conflict_with_stack_profile": None,
            "conflict_resolution": None,
            "source_url": None,
        }
        with pytest.raises(ValueError) as exc_info:
            Rule.model_validate(rule_data)
        assert "D06-R01" in str(exc_info.value)
        assert "verified-pass" in str(exc_info.value)
        assert "finding_details" in str(exc_info.value)

    def test_rule_provisional_forbids_finding_details(self) -> None:
        """A provisional rule must not have finding_details."""
        finding_obj = {
            "precondition": "Precondition",
            "path": "src/file.py",
            "line": 1,
            "issue_title": "Title",
            "issue_body": "Body",
        }
        rule_data = {
            "rule_id": "S-React-R02",
            "domain_id": None,
            "text_short": "Component testing",
            "text_body": "Test components.",
            "source": "stack-profile",
            "stack_profile": "react",
            "status": "provisional",
            "verified_date": "2026-08-25",
            "severity": None,
            "finding_details": finding_obj,
            "conflict_with_stack_profile": None,
            "conflict_resolution": None,
            "source_url": None,
        }
        with pytest.raises(ValueError) as exc_info:
            Rule.model_validate(rule_data)
        assert "S-React-R02" in str(exc_info.value)
        assert "provisional" in str(exc_info.value)

    def test_rule_not_applicable_forbids_finding_details(self) -> None:
        """A not-applicable rule must not have finding_details."""
        finding_obj = {
            "precondition": "Precondition",
            "path": "src/file.py",
            "line": 1,
            "issue_title": "Title",
            "issue_body": "Body",
        }
        rule_data = {
            "rule_id": "D11-R02",
            "domain_id": "d11",
            "text_short": "Multi-server coordination",
            "text_body": "Coordinate across servers.",
            "source": "rules-pack",
            "stack_profile": None,
            "status": "verified-not-applicable",
            "verified_date": "2026-08-25",
            "severity": None,
            "finding_details": finding_obj,
            "conflict_with_stack_profile": None,
            "conflict_resolution": None,
            "source_url": None,
        }
        with pytest.raises(ValueError) as exc_info:
            Rule.model_validate(rule_data)
        assert "D11-R02" in str(exc_info.value)
        assert "verified-not-applicable" in str(exc_info.value)

    def test_rule_rejects_invalid_status(self) -> None:
        """A rule with an invalid status value is rejected."""
        rule_data = {
            "rule_id": "D06-R01",
            "domain_id": "d06",
            "text_short": "Type hints",
            "text_body": "Use type hints.",
            "source": "rules-pack",
            "stack_profile": None,
            "status": "invalid-status",
            "verified_date": "2026-08-25",
            "severity": None,
            "finding_details": None,
            "conflict_with_stack_profile": None,
            "conflict_resolution": None,
            "source_url": None,
        }
        with pytest.raises(ValueError) as exc_info:
            Rule.model_validate(rule_data)
        error_msg = str(exc_info.value)
        assert "D06-R01" in error_msg
        assert "status" in error_msg.lower()
        assert "invalid-status" in error_msg

    def test_rule_rejects_invalid_source(self) -> None:
        """A rule with an invalid source value is rejected."""
        rule_data = {
            "rule_id": "D06-R01",
            "domain_id": "d06",
            "text_short": "Type hints",
            "text_body": "Use type hints.",
            "source": "invalid-source",
            "stack_profile": None,
            "status": "verified-pass",
            "verified_date": "2026-08-25",
            "severity": None,
            "finding_details": None,
            "conflict_with_stack_profile": None,
            "conflict_resolution": None,
            "source_url": None,
        }
        with pytest.raises(ValueError) as exc_info:
            Rule.model_validate(rule_data)
        error_msg = str(exc_info.value)
        assert "D06-R01" in error_msg
        assert "source" in error_msg.lower()
        assert "invalid-source" in error_msg

    def test_rule_verified_finding_valid_with_finding_details(self) -> None:
        """A rule with status=verified-finding and finding_details is valid."""
        rule_data = {
            "rule_id": "D06-R03",
            "domain_id": "d06",
            "text_short": "Error handling",
            "text_body": "Handle errors properly.",
            "source": "rules-pack",
            "stack_profile": None,
            "status": "verified-finding",
            "verified_date": "2026-08-25",
            "severity": "medium",
            "finding_details": {
                "precondition": "FastAPI in use",
                "path": "src/api/users.py",
                "line": 47,
                "issue_title": "Unguarded exception",
                "issue_body": "The route does not catch ValueError.",
            },
            "conflict_with_stack_profile": None,
            "conflict_resolution": None,
            "source_url": None,
        }
        rule = Rule.model_validate(rule_data)
        assert rule.rule_id == "D06-R03"
        assert rule.status == "verified-finding"
        assert rule.finding_details is not None

    def test_rule_verified_pass_valid_without_finding_details(self) -> None:
        """A rule with status=verified-pass and no finding_details is valid."""
        rule_data = {
            "rule_id": "D06-R01",
            "domain_id": "d06",
            "text_short": "Type hints",
            "text_body": "Use type hints.",
            "source": "rules-pack",
            "stack_profile": None,
            "status": "verified-pass",
            "verified_date": "2026-08-25",
            "severity": None,
            "finding_details": None,
            "conflict_with_stack_profile": None,
            "conflict_resolution": None,
            "source_url": None,
        }
        rule = Rule.model_validate(rule_data)
        assert rule.rule_id == "D06-R01"
        assert rule.status == "verified-pass"
        assert rule.finding_details is None

    def test_rule_provisional_valid_without_finding_details(self) -> None:
        """A provisional rule without finding_details is valid."""
        rule_data = {
            "rule_id": "S-React-R02",
            "domain_id": None,
            "text_short": "Component testing",
            "text_body": "Test components.",
            "source": "stack-profile",
            "stack_profile": "react",
            "status": "provisional",
            "verified_date": "2026-08-25",
            "severity": None,
            "finding_details": None,
            "conflict_with_stack_profile": None,
            "conflict_resolution": None,
            "source_url": None,
        }
        rule = Rule.model_validate(rule_data)
        assert rule.rule_id == "S-React-R02"
        assert rule.status == "provisional"


class TestRuleSetValidation:
    """Tests for RuleSet schema validation."""

    def test_ruleset_round_trip(self) -> None:
        """A rule set can be written and read back preserving all fields."""
        original_data = {
            "version": "1.0",
            "project": "test-project",
            "rules": [
                {
                    "rule_id": "D06-R01",
                    "domain_id": "d06",
                    "text_short": "Type hints",
                    "text_body": "Use type hints.",
                    "source": "rules-pack",
                    "stack_profile": None,
                    "status": "verified-pass",
                    "verified_date": "2026-08-25",
                    "severity": None,
                    "finding_details": None,
                    "conflict_with_stack_profile": None,
                    "conflict_resolution": None,
                    "source_url": "https://example.com/rules/d06.md#R01",
                    "grill_intent_note": None,
                },
                {
                    "rule_id": "D06-R03",
                    "domain_id": "d06",
                    "text_short": "Error handling",
                    "text_body": "Handle errors properly.",
                    "source": "rules-pack",
                    "stack_profile": None,
                    "status": "verified-finding",
                    "verified_date": "2026-08-25",
                    "severity": "medium",
                    "finding_details": {
                        "precondition": "FastAPI in use",
                        "path": "src/api/users.py",
                        "line": 47,
                        "issue_title": "Unguarded exception",
                        "issue_body": "The route does not catch ValueError.",
                    },
                    "conflict_with_stack_profile": None,
                    "conflict_resolution": None,
                    "source_url": "https://example.com/rules/d06.md#R03",
                    "grill_intent_note": None,
                },
            ],
        }
        ruleset = RuleSet.model_validate(original_data)
        json_str = ruleset.to_json()
        restored = RuleSet.from_json(json_str)

        # Verify all fields are preserved
        assert restored.version == ruleset.version
        assert restored.project == ruleset.project
        assert len(restored.rules) == len(ruleset.rules)
        for i, rule in enumerate(restored.rules):
            orig_rule = ruleset.rules[i]
            assert rule.rule_id == orig_rule.rule_id
            assert rule.domain_id == orig_rule.domain_id
            assert rule.text_short == orig_rule.text_short
            assert rule.text_body == orig_rule.text_body
            assert rule.source == orig_rule.source
            assert rule.stack_profile == orig_rule.stack_profile
            assert rule.status == orig_rule.status
            assert rule.verified_date == orig_rule.verified_date
            assert rule.severity == orig_rule.severity
            assert rule.finding_details == orig_rule.finding_details
            assert (
                rule.conflict_with_stack_profile
                == orig_rule.conflict_with_stack_profile
            )
            assert rule.conflict_resolution == orig_rule.conflict_resolution
            assert rule.source_url == orig_rule.source_url
            assert rule.grill_intent_note == orig_rule.grill_intent_note

    def test_ruleset_from_json_rejects_malformed(self) -> None:
        """RuleSet.from_json rejects malformed JSON with actionable errors."""
        malformed_json = "{ invalid json"
        with pytest.raises(ValueError) as exc_info:
            RuleSet.from_json(malformed_json)
        error_msg = str(exc_info.value)
        assert "JSON" in error_msg or "json" in error_msg or "parse" in error_msg

    def test_ruleset_rejects_missing_required_fields(self) -> None:
        """RuleSet rejects rules missing required fields."""
        invalid_data = {
            "version": "1.0",
            "project": "test-project",
            "rules": [
                {
                    # Missing rule_id
                    "domain_id": "d06",
                    "text_short": "Type hints",
                    "text_body": "Use type hints.",
                    "source": "rules-pack",
                    "stack_profile": None,
                    "status": "verified-pass",
                    "verified_date": "2026-08-25",
                    "severity": None,
                    "finding_details": None,
                    "conflict_with_stack_profile": None,
                    "conflict_resolution": None,
                    "source_url": None,
                }
            ],
        }
        with pytest.raises(ValueError) as exc_info:
            RuleSet.model_validate(invalid_data)
        error_msg = str(exc_info.value)
        assert "rule_id" in error_msg or "rules" in error_msg

    def test_ruleset_validates_verified_finding_constraint(self) -> None:
        """RuleSet validation enforces verified-finding finding_details requirement."""
        invalid_data = {
            "version": "1.0",
            "project": "test-project",
            "rules": [
                {
                    "rule_id": "D06-R03",
                    "domain_id": "d06",
                    "text_short": "Error handling",
                    "text_body": "Handle errors.",
                    "source": "rules-pack",
                    "stack_profile": None,
                    "status": "verified-finding",
                    "verified_date": "2026-08-25",
                    "severity": "medium",
                    "finding_details": None,  # Should be required!
                    "conflict_with_stack_profile": None,
                    "conflict_resolution": None,
                    "source_url": None,
                }
            ],
        }
        with pytest.raises(ValueError) as exc_info:
            RuleSet.model_validate(invalid_data)
        error_msg = str(exc_info.value)
        assert "D06-R03" in error_msg
        assert "verified-finding" in error_msg


class TestRuleSetFileIO:
    """Tests for reading and writing rule sets to disk."""

    def test_ruleset_write_and_read(self, tmp_path: Path) -> None:
        """A rule set can be written to a file and read back."""
        rule_set_file = tmp_path / "rule-set.json"
        ruleset = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="D06-R01",
                    domain_id="d06",
                    text_short="Type hints",
                    text_body="Use type hints.",
                    source="rules-pack",
                    stack_profile=None,
                    status="verified-pass",
                    verified_date="2026-08-25",
                    severity=None,
                    finding_details=None,
                    conflict_with_stack_profile=None,
                    conflict_resolution=None,
                    source_url=None,
                )
            ],
        )
        ruleset.write(rule_set_file)
        loaded = RuleSet.load(rule_set_file)

        assert loaded.version == ruleset.version
        assert loaded.project == ruleset.project
        assert len(loaded.rules) == 1
        assert loaded.rules[0].rule_id == "D06-R01"

    def test_ruleset_load_nonexistent_file(self, tmp_path: Path) -> None:
        """RuleSet.load raises FileNotFoundError for missing files."""
        missing_file = tmp_path / "nonexistent.json"
        with pytest.raises(FileNotFoundError):
            RuleSet.load(missing_file)

    def test_ruleset_load_corrupted_json(self, tmp_path: Path) -> None:
        """RuleSet.load rejects corrupted JSON files with actionable errors."""
        corrupted_file = tmp_path / "corrupted.json"
        corrupted_file.write_text("{ bad json syntax")
        with pytest.raises(ValueError) as exc_info:
            RuleSet.load(corrupted_file)
        error_msg = str(exc_info.value)
        # Should mention JSON or parsing
        assert "JSON" in error_msg or "json" in error_msg or "parse" in error_msg

    def test_ruleset_load_invalid_schema(self, tmp_path: Path) -> None:
        """RuleSet.load rejects files with invalid schema and names the bad field."""
        invalid_file = tmp_path / "invalid.json"
        invalid_data = {
            "version": "1.0",
            "project": "test",
            "rules": [
                {
                    "rule_id": "D06-R01",
                    "domain_id": "d06",
                    "text_short": "Type hints",
                    "text_body": "Use type hints.",
                    "source": "rules-pack",
                    "stack_profile": None,
                    "status": "verified-finding",
                    "verified_date": "2026-08-25",
                    "severity": None,
                    "finding_details": None,  # Invalid for verified-finding
                    "conflict_with_stack_profile": None,
                    "conflict_resolution": None,
                    "source_url": None,
                }
            ],
        }
        invalid_file.write_text(json.dumps(invalid_data))
        with pytest.raises(ValueError) as exc_info:
            RuleSet.load(invalid_file)
        error_msg = str(exc_info.value)
        # Should mention the rule and the constraint
        assert "D06-R01" in error_msg or "finding_details" in error_msg

    def test_ruleset_write_creates_parent_directory(self, tmp_path: Path) -> None:
        """RuleSet.write creates parent directories if needed."""
        nested_file = tmp_path / "sub" / "dir" / "rule-set.json"
        ruleset = RuleSet(
            version="1.0",
            project="test-project",
            rules=[],
        )
        ruleset.write(nested_file)
        assert nested_file.exists()
        loaded = RuleSet.load(nested_file)
        assert loaded.version == "1.0"

    def test_ruleset_preserves_all_optional_fields(self, tmp_path: Path) -> None:
        """RuleSet preserves all optional fields including conflict data."""
        rule_set_file = tmp_path / "rule-set.json"
        ruleset = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="D06-R02",
                    domain_id="d06",
                    text_short="API documentation",
                    text_body="Document every endpoint.",
                    source="rules-pack",
                    stack_profile="fastapi",
                    status="verified-pass",
                    verified_date="2026-08-25",
                    severity=None,
                    finding_details=None,
                    conflict_with_stack_profile={
                        "stack_rule_id": "S-FastAPI-R01",
                        "stack_rule_text": "Include examples.",
                        "issue": "Stack profile is stricter.",
                    },
                    conflict_resolution="Rules pack wins",
                    source_url="https://example.com/rules/d06.md#R02",
                    grill_intent_note=None,
                )
            ],
        )
        ruleset.write(rule_set_file)
        loaded = RuleSet.load(rule_set_file)

        rule = loaded.rules[0]
        assert rule.rule_id == "D06-R02"
        assert rule.conflict_with_stack_profile is not None
        assert rule.conflict_with_stack_profile["stack_rule_id"] == "S-FastAPI-R01"
        assert rule.conflict_resolution == "Rules pack wins"
        assert rule.source_url == "https://example.com/rules/d06.md#R02"
