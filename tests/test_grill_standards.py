"""Tests for grill-side provisional standards generation.

Tests that the grill can generate a provisional rule set from its captured
rules, render the three documents with provisional annotations, write them
to disk, and that a subsequent audit run properly merges the provisional
rules into the rule set with verified statuses.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from engineering_audit.standards import Rule, RuleSet, RuleStatus
from engineering_audit.standards_integration import (
    AGENT_STANDARD_FILENAME,
    ENGINEERING_POLICY_FILENAME,
    HUMAN_STANDARD_FILENAME,
    RULE_SET_FILENAME,
    render_all,
    write_standards,
)
from engineering_audit.standards_merge import merge_rule_set


class TestGenerateProvisionalRuleSet:
    """Tests for generating a provisional rule set from grill-captured rules."""

    def test_grill_captured_rules_to_provisional_rule_set(self) -> None:
        """Grill-captured rules are converted to a rule set with provisional status."""
        grill_rules = [
            Rule(
                rule_id="D06-R01",
                domain_id="d06",
                text_short="Use type hints in function signatures",
                text_body="Use Python 3.9+ type hints on all function parameters and return types.",
                source="rules-pack",
                stack_profile=None,
                status=RuleStatus.PROVISIONAL.value,
                verified_date="2026-08-28",
                severity=None,
                finding_details=None,
                grill_intent_note="Recorded from engineering-grill intent.",
            ),
            Rule(
                rule_id="D06-R03",
                domain_id="d06",
                text_short="Error handling in API routes",
                text_body="Every FastAPI route handler must explicitly catch and log exceptions.",
                source="rules-pack",
                stack_profile=None,
                status=RuleStatus.PROVISIONAL.value,
                verified_date="2026-08-28",
                severity=None,
                finding_details=None,
                grill_intent_note="Recorded from engineering-grill intent.",
            ),
        ]

        rule_set = RuleSet(
            version="1.0",
            project="engineering-audit",
            rules=grill_rules,
        )

        assert rule_set.version == "1.0"
        assert rule_set.project == "engineering-audit"
        assert len(rule_set.rules) == 2
        assert all(r.status == RuleStatus.PROVISIONAL.value for r in rule_set.rules)
        assert all(r.verified_date == "2026-08-28" for r in rule_set.rules)

    def test_provisional_rule_set_with_todays_date(self) -> None:
        """Provisional rule set uses today's date."""
        today = date.today().isoformat()

        rule = Rule(
            rule_id="D06-R01",
            domain_id="d06",
            text_short="Type hints",
            text_body="Use type hints.",
            source="rules-pack",
            stack_profile=None,
            status=RuleStatus.PROVISIONAL.value,
            verified_date=today,
        )

        rule_set = RuleSet(
            version="1.0",
            project="engineering-audit",
            rules=[rule],
        )

        assert rule_set.rules[0].verified_date == today

    def test_provisional_rule_set_preserves_grill_intent_note(self) -> None:
        """Grill intent note is preserved in provisional rules."""
        intent_note = "Grill intent: team decided to use type hints everywhere"

        rule = Rule(
            rule_id="D06-R01",
            domain_id="d06",
            text_short="Type hints",
            text_body="Use type hints.",
            source="rules-pack",
            stack_profile=None,
            status=RuleStatus.PROVISIONAL.value,
            verified_date="2026-08-28",
            grill_intent_note=intent_note,
        )

        rule_set = RuleSet(
            version="1.0",
            project="engineering-audit",
            rules=[rule],
        )

        assert rule_set.rules[0].grill_intent_note == intent_note


class TestProvisionalRenderingAnnotation:
    """Tests that provisional rules carry the correct annotation in rendered output."""

    def test_agent_standard_includes_provisional_annotation(self) -> None:
        """Agent standard marks provisional rules with grill intent annotation."""
        rule_set = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="D06-R01",
                    domain_id="d06",
                    text_short="Use type hints in function signatures",
                    text_body="Use Python 3.9+ type hints on all function parameters.",
                    source="rules-pack",
                    stack_profile=None,
                    status=RuleStatus.PROVISIONAL.value,
                    verified_date="2026-08-28",
                ),
            ],
        )

        rendered = render_all(rule_set)
        agent_output = rendered["agent-standard"]

        # Verify the exact annotation text is present
        assert "grill intent only, not yet audited against code" in agent_output

    def test_human_standard_includes_provisional_annotation(self) -> None:
        """Human standard renders provisional rules with grill intent annotation."""
        rule_set = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="D06-R01",
                    domain_id="d06",
                    text_short="Use type hints in function signatures",
                    text_body="Use Python 3.9+ type hints on all function parameters.",
                    source="rules-pack",
                    stack_profile=None,
                    status=RuleStatus.PROVISIONAL.value,
                    verified_date="2026-08-28",
                ),
            ],
        )

        rendered = render_all(rule_set)
        human_output = rendered["human-standard"]

        # Verify the exact annotation text is present
        assert "grill intent only, not yet audited against code" in human_output

    def test_policy_includes_provisional_rules_section(self) -> None:
        """Engineering policy includes a Provisional Rules section."""
        rule_set = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="D06-R01",
                    domain_id="d06",
                    text_short="Use type hints in function signatures",
                    text_body="Use Python 3.9+ type hints on all function parameters.",
                    source="rules-pack",
                    stack_profile=None,
                    status=RuleStatus.PROVISIONAL.value,
                    verified_date="2026-08-28",
                ),
                Rule(
                    rule_id="D06-R02",
                    domain_id="d06",
                    text_short="API documentation",
                    text_body="Every endpoint must be documented.",
                    source="rules-pack",
                    stack_profile=None,
                    status=RuleStatus.VERIFIED_PASS.value,
                    verified_date="2026-08-28",
                ),
            ],
        )

        rendered = render_all(rule_set)
        policy_output = rendered["engineering-policy"]

        # Should have sections for verified and provisional with exact annotation
        assert "Kept Commitments" in policy_output
        assert "grill intent only, not yet audited against code" in policy_output


class TestWriteProvisionalStandards:
    """Tests for writing provisional standards to disk."""

    def test_write_provisional_standards_creates_all_three_documents(
        self, tmp_path: Path
    ) -> None:
        """Writing provisional standards creates agent, human, and policy documents."""
        rule_set = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="D06-R01",
                    domain_id="d06",
                    text_short="Use type hints",
                    text_body="Use Python 3.9+ type hints.",
                    source="rules-pack",
                    stack_profile=None,
                    status=RuleStatus.PROVISIONAL.value,
                    verified_date="2026-08-28",
                ),
            ],
        )

        rendered = render_all(rule_set)
        deliverables_dir = tmp_path / "deliverables"
        deliverables_dir.mkdir()
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        write_standards(deliverables_dir, rendered, rule_set, repo_dir)

        docs_dir = repo_dir / "docs"
        assert (docs_dir / AGENT_STANDARD_FILENAME).exists()
        assert (docs_dir / HUMAN_STANDARD_FILENAME).exists()
        assert (docs_dir / ENGINEERING_POLICY_FILENAME).exists()

    def test_write_provisional_standards_writes_rule_set(self, tmp_path: Path) -> None:
        """Writing provisional standards writes the rule set JSON."""
        rule_set = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="D06-R01",
                    domain_id="d06",
                    text_short="Use type hints",
                    text_body="Use Python 3.9+ type hints.",
                    source="rules-pack",
                    stack_profile=None,
                    status=RuleStatus.PROVISIONAL.value,
                    verified_date="2026-08-28",
                ),
            ],
        )

        rendered = render_all(rule_set)
        deliverables_dir = tmp_path / "deliverables"
        deliverables_dir.mkdir()
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        write_standards(deliverables_dir, rendered, rule_set, repo_dir)

        rule_set_path = deliverables_dir / RULE_SET_FILENAME
        assert rule_set_path.exists()

        loaded = RuleSet.load(rule_set_path)
        assert loaded.version == "1.0"
        assert len(loaded.rules) == 1
        assert loaded.rules[0].status == RuleStatus.PROVISIONAL.value

    def test_write_provisional_standards_uses_managed_blocks(
        self, tmp_path: Path
    ) -> None:
        """Provisional standards documents use managed-block markers."""
        rule_set = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="D06-R01",
                    domain_id="d06",
                    text_short="Use type hints",
                    text_body="Use Python 3.9+ type hints.",
                    source="rules-pack",
                    stack_profile=None,
                    status=RuleStatus.PROVISIONAL.value,
                    verified_date="2026-08-28",
                ),
            ],
        )

        rendered = render_all(rule_set)
        deliverables_dir = tmp_path / "deliverables"
        deliverables_dir.mkdir()
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        write_standards(deliverables_dir, rendered, rule_set, repo_dir)

        docs_dir = repo_dir / "docs"
        agent_file = docs_dir / AGENT_STANDARD_FILENAME
        content = agent_file.read_text(encoding="utf-8")

        # Should have managed-block markers
        assert '<!-- audit:start id="agent-standard" -->' in content
        assert "<!-- audit:end -->" in content


class TestAuditMergesProvisionalRules:
    """Tests for audit run merging provisional rules into verified status."""

    def test_audit_upgrades_provisional_to_verified_pass(self) -> None:
        """Audit run with pass verdict upgrades provisional rule to verified-pass."""
        prior_rule_set = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="D06-R01",
                    domain_id="d06",
                    text_short="Use type hints",
                    text_body="Use Python 3.9+ type hints.",
                    source="rules-pack",
                    stack_profile=None,
                    status=RuleStatus.PROVISIONAL.value,
                    verified_date="2026-08-28",
                    grill_intent_note="Recorded from grill.",
                ),
            ],
        )

        audit_verdicts = {"D06-R01": "pass"}
        audit_rules = {
            "D06-R01": Rule(
                rule_id="D06-R01",
                domain_id="d06",
                text_short="Use type hints",
                text_body="Use Python 3.9+ type hints.",
                source="rules-pack",
                stack_profile=None,
                status=RuleStatus.VERIFIED_PASS.value,
                verified_date="2026-08-29",
            )
        }

        merged = merge_rule_set(
            prior_rule_set, audit_verdicts, audit_rules, date(2026, 8, 29)
        )

        assert len(merged.rules) == 1
        rule = merged.rules[0]
        assert rule.status == RuleStatus.VERIFIED_PASS.value
        assert rule.verified_date == "2026-08-29"
        # Grill intent should be preserved
        assert rule.grill_intent_note == "Recorded from grill."

    def test_audit_upgrades_provisional_to_verified_finding(self) -> None:
        """Audit run with finding verdict upgrades provisional rule to verified-finding."""
        prior_rule_set = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="D06-R01",
                    domain_id="d06",
                    text_short="Use type hints",
                    text_body="Use Python 3.9+ type hints.",
                    source="rules-pack",
                    stack_profile=None,
                    status=RuleStatus.PROVISIONAL.value,
                    verified_date="2026-08-28",
                    grill_intent_note="Recorded from grill.",
                ),
            ],
        )

        audit_verdicts = {"D06-R01": "finding"}
        audit_rules = {
            "D06-R01": Rule(
                rule_id="D06-R01",
                domain_id="d06",
                text_short="Use type hints",
                text_body="Use Python 3.9+ type hints.",
                source="rules-pack",
                stack_profile=None,
                status=RuleStatus.VERIFIED_FINDING.value,
                verified_date="2026-08-29",
                severity="high",
                finding_details={
                    "issue_title": "Missing type hints",
                    "issue_body": "Some functions lack type hints.",
                    "path": "src/app.py",
                },
            )
        }

        merged = merge_rule_set(
            prior_rule_set, audit_verdicts, audit_rules, date(2026, 8, 29)
        )

        assert len(merged.rules) == 1
        rule = merged.rules[0]
        assert rule.status == RuleStatus.VERIFIED_FINDING.value
        assert rule.verified_date == "2026-08-29"
        assert rule.severity == "high"
        assert rule.finding_details is not None
        # Grill intent should be preserved
        assert rule.grill_intent_note == "Recorded from grill."

    def test_audit_preserves_grill_intent_with_verified_pass(self) -> None:
        """Audit preserves grill intent note when upgrading to verified-pass."""
        intent_note = "Grill intent: team consensus on type hints everywhere"

        prior_rule_set = RuleSet(
            version="1.0",
            project="test-project",
            rules=[
                Rule(
                    rule_id="D06-R01",
                    domain_id="d06",
                    text_short="Use type hints",
                    text_body="Use Python 3.9+ type hints.",
                    source="rules-pack",
                    stack_profile=None,
                    status=RuleStatus.PROVISIONAL.value,
                    verified_date="2026-08-28",
                    grill_intent_note=intent_note,
                ),
            ],
        )

        audit_verdicts = {"D06-R01": "pass"}
        audit_rules = {
            "D06-R01": Rule(
                rule_id="D06-R01",
                domain_id="d06",
                text_short="Use type hints",
                text_body="Use Python 3.9+ type hints.",
                source="rules-pack",
                stack_profile=None,
                status=RuleStatus.VERIFIED_PASS.value,
                verified_date="2026-08-29",
            )
        }

        merged = merge_rule_set(
            prior_rule_set, audit_verdicts, audit_rules, date(2026, 8, 29)
        )

        rule = merged.rules[0]
        assert rule.grill_intent_note == intent_note
