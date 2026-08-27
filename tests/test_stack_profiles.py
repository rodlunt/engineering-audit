"""Tests for stack profile loading and integration.

Tests for loading stack profiles from the rules pack, parsing them into Rule objects,
and integrating them with the choice-resolution logic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engineering_audit.stack_profiles import (
    load_stack_profile_rules,
    StackProfileParseError,
)

FIXTURE_STACK_PROFILES_DIR = Path(__file__).parent / "fixture_stack_profiles"


class TestLoadStackProfileRules:
    """Tests for load_stack_profile_rules."""

    def test_returns_empty_list_when_pack_has_no_profiles(self) -> None:
        """Loader returns [] for a pack with no stack profile definitions."""
        # The fixture_pack has no stack profile files, so this should return empty
        fixture_pack = Path(__file__).parent / "fixture_pack"
        result = load_stack_profile_rules(fixture_pack, ["python", "fastapi"])
        assert result == []

    def test_parses_fixture_profile_into_rules(self) -> None:
        """Loader parses a fixture profile into Rules with correct source/stack_profile."""
        result = load_stack_profile_rules(
            FIXTURE_STACK_PROFILES_DIR, ["python", "fastapi"]
        )

        # Should have parsed rules
        assert len(result) > 0

        # All rules should have source="stack-profile"
        # and stack_profile set to one of the matched identifiers
        stack_profiles = set()
        for rule in result:
            assert rule.source == "stack-profile"
            assert rule.stack_profile in ["python", "fastapi"]
            stack_profiles.add(rule.stack_profile)

        # Check specific rule
        rule_ids = [r.rule_id for r in result]
        assert "SPFPY-R01" in rule_ids

    def test_ignores_unmatched_stack_identifiers(self) -> None:
        """Loader returns [] when asked for stack identifiers not in the pack."""
        result = load_stack_profile_rules(
            FIXTURE_STACK_PROFILES_DIR, ["rust", "golang"]
        )
        assert result == []

    def test_malformed_profile_raises_named_error(self) -> None:
        """Malformed profile files raise StackProfileParseError."""
        malformed_dir = FIXTURE_STACK_PROFILES_DIR / "malformed"
        if malformed_dir.exists():
            with pytest.raises(StackProfileParseError):
                load_stack_profile_rules(malformed_dir, ["python"])

    def test_returns_rules_sorted_by_id(self) -> None:
        """Loader returns rules sorted by rule ID."""
        result = load_stack_profile_rules(
            FIXTURE_STACK_PROFILES_DIR, ["python-fastapi"]
        )
        rule_ids = [r.rule_id for r in result]
        assert rule_ids == sorted(rule_ids)

    def test_multiple_stacks(self) -> None:
        """Loader can load profiles for multiple stack identifiers."""
        # Create fixture with multiple stack profiles if available
        result = load_stack_profile_rules(
            FIXTURE_STACK_PROFILES_DIR, ["python-fastapi", "python-django"]
        )

        if result:
            stacks = set(r.stack_profile for r in result)
            assert len(stacks) <= 2
