"""Tests for the rendering engine that produces the three standards documents.

This module tests the render functions that transform a rule set into concise
imperative markdown for coding agents, verbose markdown for engineers, and
formal markdown for company stakeholders.
"""

from __future__ import annotations

from engineering_audit.standards import Rule, RuleSet
from engineering_audit.rendering import (
    render_agent_standard,
    render_human_standard,
    render_policy,
)


class TestRenderAgentStandard:
    """Tests for render_agent_standard function."""

    def test_render_agent_standard_includes_managed_block_markers(self) -> None:
        """Output includes managed-block markers with agent-standard id."""
        rule_set = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="D06-R01",
                    domain_id="d06",
                    text_short="Use type hints in function signatures",
                    text_body="Use Python 3.9+ type hints on all function parameters and return types.",
                    source="rules-pack",
                    stack_profile=None,
                    status="verified-pass",
                    verified_date="2026-08-25",
                    severity=None,
                    finding_details=None,
                    conflict_with_stack_profile=None,
                    conflict_resolution=None,
                    source_url=None,
                ),
            ],
        )
        output = render_agent_standard(rule_set)
        assert '<!-- audit:start id="agent-standard" -->' in output
        assert "<!-- audit:end -->" in output

    def test_render_agent_standard_groups_rules_by_domain(self) -> None:
        """Rules are grouped by domain, sorted by domain ID then rule ID."""
        rule_set = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="D06-R03",
                    domain_id="d06",
                    text_short="Error handling in API routes",
                    text_body="Every FastAPI route handler must explicitly catch exceptions.",
                    source="rules-pack",
                    stack_profile=None,
                    status="verified-pass",
                    verified_date="2026-08-25",
                    severity=None,
                    finding_details=None,
                    conflict_with_stack_profile=None,
                    conflict_resolution=None,
                    source_url=None,
                ),
                Rule(
                    rule_id="D06-R01",
                    domain_id="d06",
                    text_short="Use type hints in function signatures",
                    text_body="Use Python 3.9+ type hints on all function parameters.",
                    source="rules-pack",
                    stack_profile=None,
                    status="verified-pass",
                    verified_date="2026-08-25",
                    severity=None,
                    finding_details=None,
                    conflict_with_stack_profile=None,
                    conflict_resolution=None,
                    source_url=None,
                ),
                Rule(
                    rule_id="D05-R02",
                    domain_id="d05",
                    text_short="Some rule in d05",
                    text_body="Rule body.",
                    source="rules-pack",
                    stack_profile=None,
                    status="verified-pass",
                    verified_date="2026-08-25",
                    severity=None,
                    finding_details=None,
                    conflict_with_stack_profile=None,
                    conflict_resolution=None,
                    source_url=None,
                ),
            ],
        )
        output = render_agent_standard(rule_set)
        # Rules should be sorted by domain, then rule ID
        d05_pos = output.find("D05-R02")
        d06_r01_pos = output.find("D06-R01")
        d06_r03_pos = output.find("D06-R03")
        assert d05_pos < d06_r01_pos < d06_r03_pos

    def test_render_agent_standard_includes_status_with_date(self) -> None:
        """Status line includes verification date."""
        rule_set = RuleSet(
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
                ),
            ],
        )
        output = render_agent_standard(rule_set)
        assert "verified-pass" in output
        assert "2026-08-25" in output

    def test_render_agent_standard_includes_finding_severity(self) -> None:
        """Finding status includes severity level."""
        rule_set = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="D06-R03",
                    domain_id="d06",
                    text_short="Error handling",
                    text_body="Handle errors properly.",
                    source="rules-pack",
                    stack_profile=None,
                    status="verified-finding",
                    verified_date="2026-08-25",
                    severity="medium",
                    finding_details={
                        "precondition": "FastAPI",
                        "path": "src/api/users.py",
                        "line": 47,
                        "issue_title": "Unguarded exception",
                        "issue_body": "The route does not catch ValueError.",
                    },
                    conflict_with_stack_profile=None,
                    conflict_resolution=None,
                    source_url=None,
                ),
            ],
        )
        output = render_agent_standard(rule_set)
        assert "verified-finding" in output
        assert "medium" in output or "severity" in output

    def test_render_agent_standard_marks_provisional_as_grill_intent_only(self) -> None:
        """Provisional rules are annotated with 'grill intent only, not yet audited'."""
        rule_set = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="S-React-R02",
                    domain_id=None,
                    text_short="Component testing",
                    text_body="Every React component must have at least one unit test.",
                    source="stack-profile",
                    stack_profile="react",
                    status="provisional",
                    verified_date="2026-08-25",
                    severity=None,
                    finding_details=None,
                    conflict_with_stack_profile=None,
                    conflict_resolution=None,
                    source_url=None,
                    grill_intent_note="Recorded from engineering-grill intent; not yet audited.",
                ),
            ],
        )
        output = render_agent_standard(rule_set)
        assert "provisional" in output
        # Assert the exact phrase rendered for provisional rules
        assert "grill intent only, not yet audited" in output

    def test_render_agent_standard_includes_full_text(self) -> None:
        """Full rule text is included in the output."""
        rule_set = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="D06-R01",
                    domain_id="d06",
                    text_short="Type hints",
                    text_body="Use Python 3.9+ type hints on all function parameters and return types. Static type checking with mypy is run on every PR.",
                    source="rules-pack",
                    stack_profile=None,
                    status="verified-pass",
                    verified_date="2026-08-25",
                    severity=None,
                    finding_details=None,
                    conflict_with_stack_profile=None,
                    conflict_resolution=None,
                    source_url=None,
                ),
            ],
        )
        output = render_agent_standard(rule_set)
        assert "Use Python 3.9+ type hints" in output

    def test_render_agent_standard_excludes_not_applicable_rules(self) -> None:
        """Rules with status=verified-not-applicable are excluded."""
        rule_set = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="D11-R02",
                    domain_id="d11",
                    text_short="Multi-server coordination",
                    text_body="Coordinate across servers.",
                    source="rules-pack",
                    stack_profile=None,
                    status="verified-not-applicable",
                    verified_date="2026-08-25",
                    severity=None,
                    finding_details=None,
                    conflict_with_stack_profile=None,
                    conflict_resolution=None,
                    source_url=None,
                ),
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
                ),
            ],
        )
        output = render_agent_standard(rule_set)
        assert "D11-R02" not in output
        assert "D06-R01" in output

    def test_render_agent_standard_is_deterministic(self) -> None:
        """Rendering the same rule set twice produces byte-equal output."""
        rule_set = RuleSet(
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
                ),
                Rule(
                    rule_id="D06-R03",
                    domain_id="d06",
                    text_short="Error handling",
                    text_body="Handle errors.",
                    source="rules-pack",
                    stack_profile=None,
                    status="verified-finding",
                    verified_date="2026-08-25",
                    severity="medium",
                    finding_details={
                        "precondition": "Test",
                        "path": "src/test.py",
                        "line": 1,
                        "issue_title": "Title",
                        "issue_body": "Body",
                    },
                    conflict_with_stack_profile=None,
                    conflict_resolution=None,
                    source_url=None,
                ),
            ],
        )
        output1 = render_agent_standard(rule_set)
        output2 = render_agent_standard(rule_set)
        assert output1 == output2

    def test_render_agent_standard_includes_rule_id_and_short_text(self) -> None:
        """Each rule includes its ID and short text."""
        rule_set = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="D06-R01",
                    domain_id="d06",
                    text_short="Use type hints in function signatures",
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
                ),
            ],
        )
        output = render_agent_standard(rule_set)
        assert "D06-R01" in output
        assert "Use type hints in function signatures" in output

    def test_render_agent_standard_includes_conflict_section_when_conflicted(
        self,
    ) -> None:
        """Conflict section appears for a rule with conflict_with_stack_profile."""
        rule_set = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="D06-R02",
                    domain_id="d06",
                    text_short="API documentation",
                    text_body="Document all API endpoints.",
                    source="rules-pack",
                    stack_profile="fastapi",
                    status="verified-pass",
                    verified_date="2026-08-25",
                    severity=None,
                    finding_details=None,
                    conflict_with_stack_profile={
                        "stack_rule_id": "S-FastAPI-R01",
                        "stack_rule_text": "Include example requests and responses for every endpoint.",
                        "issue": "Rules pack says 'document every endpoint'; stack profile says 'include examples'. Stack is stricter.",
                    },
                    conflict_resolution="Rules pack wins (per decision #7). Follow stack profile requirement.",
                    source_url=None,
                ),
            ],
        )
        output = render_agent_standard(rule_set)
        # Verify conflict section structure
        assert "Conflict:" in output
        assert (
            "Stack profile rule: Include example requests and responses for every endpoint."
            in output
        )
        assert (
            "Issue: Rules pack says 'document every endpoint'; stack profile says 'include examples'. Stack is stricter."
            in output
        )
        assert (
            "Resolution: Rules pack wins (per decision #7). Follow stack profile requirement."
            in output
        )

    def test_render_agent_standard_excludes_conflict_section_when_not_conflicted(
        self,
    ) -> None:
        """No conflict section appears for a rule without conflict_with_stack_profile."""
        rule_set = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="D06-R01",
                    domain_id="d06",
                    text_short="Use type hints",
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
                ),
            ],
        )
        output = render_agent_standard(rule_set)
        # Rule should be rendered but without any "Conflict:" heading
        assert "D06-R01" in output
        # Verify that there's no conflict section for this unconflicted rule
        # Extract just the agent standard content (between managed-block markers)
        start = output.find('<!-- audit:start id="agent-standard" -->')
        end = output.find("<!-- audit:end -->")
        content = output[start:end]
        # Count "Conflict:" occurrences - should be zero for an unconflicted rule
        assert content.count("Conflict:") == 0


class TestRenderHumanStandard:
    """Tests for render_human_standard function."""

    def test_render_human_standard_includes_managed_block_markers(self) -> None:
        """Output includes managed-block markers with human-standard id."""
        rule_set = RuleSet(
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
                ),
            ],
        )
        output = render_human_standard(rule_set, None)
        assert '<!-- audit:start id="human-standard" -->' in output
        assert "<!-- audit:end -->" in output

    def test_render_human_standard_groups_rules_by_domain(self) -> None:
        """Rules are grouped by domain, sorted by domain ID then rule ID."""
        rule_set = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="D06-R03",
                    domain_id="d06",
                    text_short="Error handling",
                    text_body="Handle errors.",
                    source="rules-pack",
                    stack_profile=None,
                    status="verified-pass",
                    verified_date="2026-08-25",
                    severity=None,
                    finding_details=None,
                    conflict_with_stack_profile=None,
                    conflict_resolution=None,
                    source_url=None,
                ),
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
                ),
            ],
        )
        output = render_human_standard(rule_set, None)
        # Rules should appear in order
        r01_pos = output.find("D06-R01")
        r03_pos = output.find("D06-R03")
        assert r01_pos < r03_pos

    def test_render_human_standard_includes_status_with_date(self) -> None:
        """Status line includes verification date."""
        rule_set = RuleSet(
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
                ),
            ],
        )
        output = render_human_standard(rule_set, None)
        assert "verified-pass" in output
        assert "2026-08-25" in output

    def test_render_human_standard_includes_audit_findings_and_fix_suggestions(
        self,
    ) -> None:
        """Audit findings include severity, path, line, and fix suggestions."""
        rule_set = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="D06-R03",
                    domain_id="d06",
                    text_short="Error handling",
                    text_body="Handle errors properly.",
                    source="rules-pack",
                    stack_profile=None,
                    status="verified-finding",
                    verified_date="2026-08-25",
                    severity="medium",
                    finding_details={
                        "precondition": "FastAPI in use",
                        "path": "src/api/users.py",
                        "line": 47,
                        "issue_title": "Unguarded exception",
                        "issue_body": "The route does not catch ValueError. Wrap the call in try-except.",
                    },
                    conflict_with_stack_profile=None,
                    conflict_resolution=None,
                    source_url=None,
                ),
            ],
        )
        output = render_human_standard(rule_set, None)
        assert "src/api/users.py" in output
        assert "47" in output
        assert "medium" in output

    def test_render_human_standard_is_deterministic(self) -> None:
        """Rendering the same rule set twice produces byte-equal output."""
        rule_set = RuleSet(
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
                ),
            ],
        )
        output1 = render_human_standard(rule_set, None)
        output2 = render_human_standard(rule_set, None)
        assert output1 == output2

    def test_render_human_standard_excludes_not_applicable_rules(self) -> None:
        """Rules with status=verified-not-applicable are excluded."""
        rule_set = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="D11-R02",
                    domain_id="d11",
                    text_short="Multi-server coordination",
                    text_body="Coordinate across servers.",
                    source="rules-pack",
                    stack_profile=None,
                    status="verified-not-applicable",
                    verified_date="2026-08-25",
                    severity=None,
                    finding_details=None,
                    conflict_with_stack_profile=None,
                    conflict_resolution=None,
                    source_url=None,
                ),
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
                ),
            ],
        )
        output = render_human_standard(rule_set, None)
        assert "D11-R02" not in output
        assert "D06-R01" in output


class TestRenderPolicy:
    """Tests for render_policy function."""

    def test_render_policy_includes_managed_block_markers(self) -> None:
        """Output includes managed-block markers with engineering-policy id."""
        rule_set = RuleSet(
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
                ),
            ],
        )
        output = render_policy(rule_set)
        assert '<!-- audit:start id="engineering-policy" -->' in output
        assert "<!-- audit:end -->" in output

    def test_render_policy_groups_by_status_verified_pass(self) -> None:
        """Verified-pass rules are grouped under 'Kept Commitments'."""
        rule_set = RuleSet(
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
                ),
            ],
        )
        output = render_policy(rule_set)
        # Should include the rule and some indication it passed
        assert "D06-R01" in output
        assert "Type hints" in output

    def test_render_policy_groups_by_status_verified_finding(self) -> None:
        """Verified-finding rules are grouped under 'Outstanding Findings'."""
        rule_set = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="D06-R03",
                    domain_id="d06",
                    text_short="Error handling",
                    text_body="Handle errors.",
                    source="rules-pack",
                    stack_profile=None,
                    status="verified-finding",
                    verified_date="2026-08-25",
                    severity="medium",
                    finding_details={
                        "precondition": "Test",
                        "path": "src/test.py",
                        "line": 1,
                        "issue_title": "Title",
                        "issue_body": "Body",
                    },
                    conflict_with_stack_profile=None,
                    conflict_resolution=None,
                    source_url=None,
                ),
            ],
        )
        output = render_policy(rule_set)
        assert "D06-R03" in output
        assert "medium" in output

    def test_render_policy_groups_by_status_verified_not_applicable(self) -> None:
        """Verified-not-applicable rules are grouped under 'Deferred Domains'."""
        rule_set = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="D11-R02",
                    domain_id="d11",
                    text_short="Multi-server coordination",
                    text_body="Coordinate across servers.",
                    source="rules-pack",
                    stack_profile=None,
                    status="verified-not-applicable",
                    verified_date="2026-08-25",
                    severity=None,
                    finding_details=None,
                    conflict_with_stack_profile=None,
                    conflict_resolution=None,
                    source_url=None,
                ),
            ],
        )
        output = render_policy(rule_set)
        assert "D11-R02" in output
        # Should be marked as not applicable or deferred
        assert "not applicable" in output.lower() or "deferred" in output.lower()

    def test_render_policy_includes_provisional_rules(self) -> None:
        """Provisional rules are included in the output."""
        rule_set = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="S-React-R02",
                    domain_id=None,
                    text_short="Component testing",
                    text_body="Test components.",
                    source="stack-profile",
                    stack_profile="react",
                    status="provisional",
                    verified_date="2026-08-25",
                    severity=None,
                    finding_details=None,
                    conflict_with_stack_profile=None,
                    conflict_resolution=None,
                    source_url=None,
                ),
            ],
        )
        output = render_policy(rule_set)
        assert "S-React-R02" in output

    def test_render_policy_is_deterministic(self) -> None:
        """Rendering the same rule set twice produces byte-equal output."""
        rule_set = RuleSet(
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
                ),
                Rule(
                    rule_id="D06-R03",
                    domain_id="d06",
                    text_short="Error handling",
                    text_body="Handle errors.",
                    source="rules-pack",
                    stack_profile=None,
                    status="verified-finding",
                    verified_date="2026-08-25",
                    severity="medium",
                    finding_details={
                        "precondition": "Test",
                        "path": "src/test.py",
                        "line": 1,
                        "issue_title": "Title",
                        "issue_body": "Body",
                    },
                    conflict_with_stack_profile=None,
                    conflict_resolution=None,
                    source_url=None,
                ),
            ],
        )
        output1 = render_policy(rule_set)
        output2 = render_policy(rule_set)
        assert output1 == output2

    def test_render_policy_includes_all_statuses(self) -> None:
        """Policy includes rules with all statuses (except not-applicable are grouped separately)."""
        rule_set = RuleSet(
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
                ),
                Rule(
                    rule_id="D06-R03",
                    domain_id="d06",
                    text_short="Error handling",
                    text_body="Handle errors.",
                    source="rules-pack",
                    stack_profile=None,
                    status="verified-finding",
                    verified_date="2026-08-25",
                    severity="high",
                    finding_details={
                        "precondition": "Test",
                        "path": "src/test.py",
                        "line": 1,
                        "issue_title": "Title",
                        "issue_body": "Body",
                    },
                    conflict_with_stack_profile=None,
                    conflict_resolution=None,
                    source_url=None,
                ),
                Rule(
                    rule_id="D11-R02",
                    domain_id="d11",
                    text_short="Multi-server",
                    text_body="Coordinate.",
                    source="rules-pack",
                    stack_profile=None,
                    status="verified-not-applicable",
                    verified_date="2026-08-25",
                    severity=None,
                    finding_details=None,
                    conflict_with_stack_profile=None,
                    conflict_resolution=None,
                    source_url=None,
                ),
            ],
        )
        output = render_policy(rule_set)
        # All rules should appear
        assert "D06-R01" in output
        assert "D06-R03" in output
        assert "D11-R02" in output

    def test_render_policy_includes_revisit_trigger_for_passed_rules(self) -> None:
        """Passed rules include revisit trigger if present."""
        rule_set = RuleSet(
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
                    revisit_trigger="If codebase grows significantly.",
                ),
            ],
        )
        output = render_policy(rule_set)
        assert "revisit trigger" in output.lower()
        assert "codebase grows" in output

    def test_render_policy_includes_fix_due_for_findings(self) -> None:
        """Finding rules include fix due date if present."""
        rule_set = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="D06-R03",
                    domain_id="d06",
                    text_short="Error handling",
                    text_body="Handle errors.",
                    source="rules-pack",
                    stack_profile=None,
                    status="verified-finding",
                    verified_date="2026-08-25",
                    severity="medium",
                    finding_details={
                        "precondition": "Test",
                        "path": "src/test.py",
                        "line": 1,
                        "issue_title": "Title",
                        "issue_body": "Body",
                    },
                    conflict_with_stack_profile=None,
                    conflict_resolution=None,
                    source_url=None,
                    fix_due="2026-09-15",
                ),
            ],
        )
        output = render_policy(rule_set)
        assert "fix due" in output.lower()
        assert "2026-09-15" in output

    def test_render_policy_includes_ownership_for_findings(self) -> None:
        """Finding rules include ownership if present."""
        rule_set = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="D06-R03",
                    domain_id="d06",
                    text_short="Error handling",
                    text_body="Handle errors.",
                    source="rules-pack",
                    stack_profile=None,
                    status="verified-finding",
                    verified_date="2026-08-25",
                    severity="medium",
                    finding_details={
                        "precondition": "Test",
                        "path": "src/test.py",
                        "line": 1,
                        "issue_title": "Title",
                        "issue_body": "Body",
                    },
                    conflict_with_stack_profile=None,
                    conflict_resolution=None,
                    source_url=None,
                    ownership="Backend team",
                ),
            ],
        )
        output = render_policy(rule_set)
        assert "ownership" in output.lower()
        assert "Backend team" in output


class TestRenderHumanStandardRulesPack:
    """Tests for render_human_standard rules pack parameter.

    Note: Rationale rendering from rules pack is not yet implemented because
    the Domain class in rules.py does not have a rationale field. See
    post-audit-standards-artefacts.md for spec gap documentation.
    """

    def test_render_human_standard_accepts_rules_pack_parameter(self) -> None:
        """Human standard accepts rules_pack parameter (for future rationale lookup)."""
        rule_set = RuleSet(
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
                ),
            ],
        )
        # Should work with rules_pack=None (no rationale currently available)
        output = render_human_standard(rule_set, None)
        assert "D06-R01" in output
        assert "Type hints" in output


class TestProvisionalDocumentMarker:
    """Tests for top-of-document provisional marker in all three renderers."""

    def test_agent_standard_includes_provisional_marker_when_all_rules_provisional(
        self,
    ) -> None:
        """Agent standard includes top-of-document provisional marker when all rules are provisional."""
        rule_set = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="S-React-R01",
                    domain_id=None,
                    text_short="Testing",
                    text_body="Test all components.",
                    source="stack-profile",
                    stack_profile="react",
                    status="provisional",
                    verified_date="2026-08-25",
                    severity=None,
                    finding_details=None,
                    conflict_with_stack_profile=None,
                    conflict_resolution=None,
                    source_url=None,
                ),
            ],
        )
        output = render_agent_standard(rule_set)
        assert "[Provisional: intent only, not yet audited]" in output
        # Marker should appear inside managed block
        start = output.find('<!-- audit:start id="agent-standard" -->')
        end = output.find("<!-- audit:end -->")
        content = output[start:end]
        assert "[Provisional: intent only, not yet audited]" in content

    def test_agent_standard_excludes_provisional_marker_when_all_rules_verified(
        self,
    ) -> None:
        """Agent standard excludes top-of-document marker when all rules are verified."""
        rule_set = RuleSet(
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
                ),
            ],
        )
        output = render_agent_standard(rule_set)
        assert "[Provisional: intent only, not yet audited]" not in output

    def test_agent_standard_excludes_provisional_marker_with_mixed_rules(
        self,
    ) -> None:
        """Agent standard excludes marker when rule set contains mixed statuses."""
        rule_set = RuleSet(
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
                ),
                Rule(
                    rule_id="S-React-R01",
                    domain_id=None,
                    text_short="Testing",
                    text_body="Test all components.",
                    source="stack-profile",
                    stack_profile="react",
                    status="provisional",
                    verified_date="2026-08-25",
                    severity=None,
                    finding_details=None,
                    conflict_with_stack_profile=None,
                    conflict_resolution=None,
                    source_url=None,
                ),
            ],
        )
        output = render_agent_standard(rule_set)
        assert "[Provisional: intent only, not yet audited]" not in output

    def test_human_standard_includes_provisional_marker_when_all_rules_provisional(
        self,
    ) -> None:
        """Human standard includes top-of-document provisional marker when all rules are provisional."""
        rule_set = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="S-React-R01",
                    domain_id=None,
                    text_short="Testing",
                    text_body="Test all components.",
                    source="stack-profile",
                    stack_profile="react",
                    status="provisional",
                    verified_date="2026-08-25",
                    severity=None,
                    finding_details=None,
                    conflict_with_stack_profile=None,
                    conflict_resolution=None,
                    source_url=None,
                ),
            ],
        )
        output = render_human_standard(rule_set, None)
        assert "[Provisional: intent only, not yet audited]" in output
        # Marker should appear inside managed block
        start = output.find('<!-- audit:start id="human-standard" -->')
        end = output.find("<!-- audit:end -->")
        content = output[start:end]
        assert "[Provisional: intent only, not yet audited]" in content

    def test_human_standard_excludes_provisional_marker_when_all_rules_verified(
        self,
    ) -> None:
        """Human standard excludes marker when all rules are verified."""
        rule_set = RuleSet(
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
                ),
            ],
        )
        output = render_human_standard(rule_set, None)
        assert "[Provisional: intent only, not yet audited]" not in output

    def test_human_standard_excludes_provisional_marker_with_mixed_rules(
        self,
    ) -> None:
        """Human standard excludes marker when rule set contains mixed statuses."""
        rule_set = RuleSet(
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
                ),
                Rule(
                    rule_id="S-React-R01",
                    domain_id=None,
                    text_short="Testing",
                    text_body="Test all components.",
                    source="stack-profile",
                    stack_profile="react",
                    status="provisional",
                    verified_date="2026-08-25",
                    severity=None,
                    finding_details=None,
                    conflict_with_stack_profile=None,
                    conflict_resolution=None,
                    source_url=None,
                ),
            ],
        )
        output = render_human_standard(rule_set, None)
        assert "[Provisional: intent only, not yet audited]" not in output

    def test_policy_includes_provisional_marker_when_all_rules_provisional(
        self,
    ) -> None:
        """Policy includes top-of-document provisional marker when all rules are provisional."""
        rule_set = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="S-React-R01",
                    domain_id=None,
                    text_short="Testing",
                    text_body="Test all components.",
                    source="stack-profile",
                    stack_profile="react",
                    status="provisional",
                    verified_date="2026-08-25",
                    severity=None,
                    finding_details=None,
                    conflict_with_stack_profile=None,
                    conflict_resolution=None,
                    source_url=None,
                ),
            ],
        )
        output = render_policy(rule_set)
        assert "[Provisional: intent only, not yet audited]" in output
        # Marker should appear inside managed block
        start = output.find('<!-- audit:start id="engineering-policy" -->')
        end = output.find("<!-- audit:end -->")
        content = output[start:end]
        assert "[Provisional: intent only, not yet audited]" in content

    def test_policy_excludes_provisional_marker_when_all_rules_verified(
        self,
    ) -> None:
        """Policy excludes marker when all rules are verified."""
        rule_set = RuleSet(
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
                ),
            ],
        )
        output = render_policy(rule_set)
        assert "[Provisional: intent only, not yet audited]" not in output

    def test_policy_excludes_provisional_marker_with_mixed_rules(
        self,
    ) -> None:
        """Policy excludes marker when rule set contains mixed statuses."""
        rule_set = RuleSet(
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
                ),
                Rule(
                    rule_id="S-React-R01",
                    domain_id=None,
                    text_short="Testing",
                    text_body="Test all components.",
                    source="stack-profile",
                    stack_profile="react",
                    status="provisional",
                    verified_date="2026-08-25",
                    severity=None,
                    finding_details=None,
                    conflict_with_stack_profile=None,
                    conflict_resolution=None,
                    source_url=None,
                ),
            ],
        )
        output = render_policy(rule_set)
        assert "[Provisional: intent only, not yet audited]" not in output
