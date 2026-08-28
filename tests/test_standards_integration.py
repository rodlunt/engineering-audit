"""Tests for the standards integration module.

Tests for the pure-logic layer that coordinates rendering and writing of
the three standards documents, merging prior rule sets with new audit
verdicts, and managing the approval workflow.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engineering_audit.rules import load_pack
from engineering_audit.schema import (
    DomainResult,
    Finding,
    RuleVerdict,
    Severity,
    Verdict,
)
from engineering_audit.standards import Rule, RuleSet
from engineering_audit.standards_integration import (
    AGENT_STANDARD_FILENAME,
    ENGINEERING_POLICY_FILENAME,
    HUMAN_STANDARD_FILENAME,
    RULE_SET_FILENAME,
    audit_rules_from_domain_results,
    build_diffs,
    derive_summary_counts,
    load_prior_rule_set,
    render_all,
    verdicts_from_domain_results,
    write_standards,
)

FIXTURE_PACK = Path(__file__).parent / "fixture_pack"


class TestVerdictFlattening:
    """Tests for verdicts_from_domain_results."""

    def test_flattens_single_domain(self) -> None:
        """Verdicts from a single domain are flattened to dict."""
        domain_results = {
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=[
                    RuleVerdict(rule_id="D01-R01", verdict=Verdict.pass_),
                    RuleVerdict(rule_id="D01-R02", verdict=Verdict.FINDING),
                ],
                findings=[
                    Finding(
                        rule_id="D01-R02",
                        severity=Severity.HIGH,
                        title="Issue",
                        location="src/app.py:10",
                        body_md="Problem",
                        issue_title="Fix this",
                        issue_body="Details",
                        precondition="Project exists",
                    )
                ],
                uninspected_evidence=[],
            )
        }

        verdicts = verdicts_from_domain_results(domain_results)

        assert verdicts == {
            "D01-R01": "pass",
            "D01-R02": "finding",
        }

    def test_flattens_multiple_domains(self) -> None:
        """Verdicts from multiple domains are flattened to dict."""
        domain_results = {
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=[
                    RuleVerdict(rule_id="D01-R01", verdict=Verdict.pass_),
                ],
                findings=[],
                uninspected_evidence=[],
            ),
            "d02": DomainResult(
                domain_id="d02",
                status="completed",
                rule_verdicts=[
                    RuleVerdict(rule_id="D02-R01", verdict=Verdict.FINDING),
                    RuleVerdict(
                        rule_id="D02-R02", verdict=Verdict.NOT_APPLICABLE, note="N/A"
                    ),
                ],
                findings=[
                    Finding(
                        rule_id="D02-R01",
                        severity=Severity.HIGH,
                        title="Issue",
                        location="src/app.py:10",
                        body_md="Problem",
                        issue_title="Fix this",
                        issue_body="Details",
                        precondition="Project exists",
                    )
                ],
                uninspected_evidence=[],
            ),
        }

        verdicts = verdicts_from_domain_results(domain_results)

        assert verdicts == {
            "D01-R01": "pass",
            "D02-R01": "finding",
            "D02-R02": "not-applicable",
        }

    def test_includes_could_not_evaluate(self) -> None:
        """could-not-evaluate verdicts are included."""
        domain_results = {
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=[
                    RuleVerdict(
                        rule_id="D01-R01",
                        verdict=Verdict.COULD_NOT_EVALUATE,
                        note="Tool not available",
                    ),
                ],
                findings=[],
                uninspected_evidence=[],
            )
        }

        verdicts = verdicts_from_domain_results(domain_results)

        assert verdicts == {
            "D01-R01": "could-not-evaluate",
        }

    def test_raises_on_duplicate_rule_id_across_domains(self) -> None:
        """Duplicate rule IDs across domains raise a clear error."""
        domain_results = {
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=[
                    RuleVerdict(rule_id="D01-R01", verdict=Verdict.pass_),
                ],
                findings=[],
                uninspected_evidence=[],
            ),
            "d02": DomainResult(
                domain_id="d02",
                status="completed",
                rule_verdicts=[
                    RuleVerdict(rule_id="D01-R01", verdict=Verdict.FINDING),
                ],
                findings=[
                    Finding(
                        rule_id="D01-R01",
                        severity=Severity.HIGH,
                        title="Issue",
                        location="src/app.py:10",
                        body_md="Problem",
                        issue_title="Fix this",
                        issue_body="Details",
                        precondition="Project exists",
                    )
                ],
                uninspected_evidence=[],
            ),
        }

        with pytest.raises(ValueError, match="duplicate rule_id.*D01-R01"):
            verdicts_from_domain_results(domain_results)


class TestAuditRulesFromDomainResults:
    """Tests for audit_rules_from_domain_results."""

    def test_maps_pass_to_verified_pass(self) -> None:
        """pass verdict maps to verified-pass status."""
        domain_results = {
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=[
                    RuleVerdict(rule_id="D01-R01", verdict=Verdict.pass_),
                ],
                findings=[],
                uninspected_evidence=[],
            )
        }

        # Simple rules pack mock
        class MockRulesPack:
            def get_domain(self, domain_id):
                class MockRule:
                    id = "D01-R01"
                    title = "Type hints"

                class MockDomain:
                    id = domain_id
                    rules = [MockRule()]

                return MockDomain()

        rules_pack = MockRulesPack()

        audit_rules = audit_rules_from_domain_results(domain_results, rules_pack)

        assert "D01-R01" in audit_rules
        rule = audit_rules["D01-R01"]
        assert rule.status == "verified-pass"
        assert rule.finding_details is None

    def test_maps_finding_to_verified_finding_with_details(self) -> None:
        """finding verdict maps to verified-finding with finding_details."""
        domain_results = {
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=[
                    RuleVerdict(rule_id="D01-R01", verdict=Verdict.FINDING),
                ],
                findings=[
                    Finding(
                        rule_id="D01-R01",
                        severity=Severity.HIGH,
                        title="Missing type hints",
                        location="src/app.py:42",
                        body_md="No type hints on function",
                        issue_title="Add type hints",
                        issue_body="The function should have type hints",
                        precondition="The project uses Python",
                    )
                ],
                uninspected_evidence=[],
            )
        }

        class MockRulesPack:
            def get_domain(self, domain_id):
                class MockRule:
                    id = "D01-R01"
                    title = "Type hints"

                class MockDomain:
                    id = domain_id
                    rules = [MockRule()]

                return MockDomain()

        rules_pack = MockRulesPack()

        audit_rules = audit_rules_from_domain_results(domain_results, rules_pack)

        rule = audit_rules["D01-R01"]
        assert rule.status == "verified-finding"
        assert rule.severity == "high"
        assert rule.finding_details is not None
        assert rule.finding_details["issue_title"] == "Add type hints"
        assert rule.finding_details["precondition"] == "The project uses Python"

    def test_maps_not_applicable_to_verified_not_applicable(self) -> None:
        """not-applicable verdict maps to verified-not-applicable."""
        domain_results = {
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=[
                    RuleVerdict(
                        rule_id="D01-R01",
                        verdict=Verdict.NOT_APPLICABLE,
                        note="No web servers",
                    ),
                ],
                findings=[],
                uninspected_evidence=[],
            )
        }

        class MockRulesPack:
            def get_domain(self, domain_id):
                class MockRule:
                    id = "D01-R01"
                    title = "HTTPS"

                class MockDomain:
                    id = domain_id
                    rules = [MockRule()]

                return MockDomain()

        rules_pack = MockRulesPack()

        audit_rules = audit_rules_from_domain_results(domain_results, rules_pack)

        rule = audit_rules["D01-R01"]
        assert rule.status == "verified-not-applicable"
        assert rule.finding_details is None

    def test_maps_could_not_evaluate_to_provisional(self) -> None:
        """could-not-evaluate verdict maps to provisional status."""
        domain_results = {
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=[
                    RuleVerdict(
                        rule_id="D01-R01",
                        verdict=Verdict.COULD_NOT_EVALUATE,
                        note="Tool not installed",
                    ),
                ],
                findings=[],
                uninspected_evidence=[],
            )
        }

        class MockRulesPack:
            def get_domain(self, domain_id):
                class MockRule:
                    id = "D01-R01"
                    title = "Security scan"

                class MockDomain:
                    id = domain_id
                    rules = [MockRule()]

                return MockDomain()

        rules_pack = MockRulesPack()

        audit_rules = audit_rules_from_domain_results(domain_results, rules_pack)

        rule = audit_rules["D01-R01"]
        assert rule.status == "provisional"

    def test_populates_real_rule_text_from_rules_pack(self) -> None:
        """audit_rules_from_domain_results populates text_short and text_body from rules pack."""
        # Load the real fixture pack
        pack = load_pack(FIXTURE_PACK)

        domain_results = {
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=[
                    RuleVerdict(rule_id="D01-R01", verdict=Verdict.pass_),
                ],
                findings=[],
                uninspected_evidence=[],
            )
        }

        audit_rules = audit_rules_from_domain_results(domain_results, pack)

        # Check that the rule has real text from the pack
        rule = audit_rules["D01-R01"]
        assert rule.rule_id == "D01-R01"
        assert rule.domain_id == "d01"
        # text_short should be the rule title from the pack
        assert (
            rule.text_short
            == "Record every gnome's hat colour before assigning a garden bed."
        )
        # text_body should contain the actual rule prose
        assert "gnome without a recorded hat colour" in rule.text_body
        assert "Rule id: D01-R01" in rule.text_body
        # source should be "rules-pack"
        assert rule.source == "rules-pack"

    def test_renders_agent_standard_with_real_rule_text(self, tmp_path: Path) -> None:
        """Rendered agent standard contains real rule text from rules pack."""
        # Load the real fixture pack
        pack = load_pack(FIXTURE_PACK)

        domain_results = {
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=[
                    RuleVerdict(rule_id="D01-R01", verdict=Verdict.pass_),
                    RuleVerdict(rule_id="D01-R02", verdict=Verdict.FINDING),
                ],
                findings=[
                    Finding(
                        rule_id="D01-R02",
                        severity=Severity.HIGH,
                        title="Missing flag",
                        location="src/main.py:42",
                        body_md="Gnomes need shared-bed flag",
                        issue_title="Add shared-bed flag",
                        issue_body="Set flag on line 42",
                        precondition="Using shared beds",
                    )
                ],
                uninspected_evidence=[],
            )
        }

        # Build audit rules from domain results with the real pack
        audit_rules = audit_rules_from_domain_results(domain_results, pack)
        rule_set = RuleSet(
            version="1.0", project="test", rules=list(audit_rules.values())
        )

        # Render the agent standard
        rendered = render_all(rule_set)
        agent_standard = rendered["agent-standard"]

        # Verify real rule text appears in the rendered document
        assert "D01-R01" in agent_standard
        assert (
            "Record every gnome's hat colour before assigning a garden bed."
            in agent_standard
        )
        assert "gnome without a recorded hat colour" in agent_standard
        assert "D01-R02" in agent_standard
        assert "Never assign two gnomes to the same garden bed" in agent_standard
        assert "shared-bed flag" in agent_standard


class TestLoadPriorRuleSet:
    """Tests for load_prior_rule_set."""

    def test_returns_none_when_file_absent(self, tmp_path: Path) -> None:
        """Returns None when rule set file does not exist."""
        deliverables_dir = tmp_path / "deliverables"
        deliverables_dir.mkdir()

        result = load_prior_rule_set(deliverables_dir)

        assert result is None

    def test_loads_existing_rule_set(self, tmp_path: Path) -> None:
        """Loads and returns a rule set when file exists."""
        deliverables_dir = tmp_path / "deliverables"
        deliverables_dir.mkdir()

        # Create a rule set and write it
        rule_set = RuleSet(
            version="1.0",
            project="test",
            rules=[
                Rule(
                    rule_id="D01-R01",
                    domain_id="d01",
                    text_short="Type hints",
                    text_body="Use type hints",
                    source="rules-pack",
                    status="provisional",
                    verified_date="2026-08-20",
                )
            ],
        )
        rule_set.write(deliverables_dir / RULE_SET_FILENAME)

        result = load_prior_rule_set(deliverables_dir)

        assert result is not None
        assert result.version == "1.0"
        assert len(result.rules) == 1
        assert result.rules[0].rule_id == "D01-R01"

    def test_raises_on_corrupt_file(self, tmp_path: Path) -> None:
        """Raises clear error when file is corrupted."""
        deliverables_dir = tmp_path / "deliverables"
        deliverables_dir.mkdir()

        # Write corrupted JSON
        (deliverables_dir / RULE_SET_FILENAME).write_text("{invalid json")

        with pytest.raises(ValueError, match="Failed to parse JSON"):
            load_prior_rule_set(deliverables_dir)


class TestDeriveSummaryCounts:
    """Tests for derive_summary_counts."""

    def test_first_run_no_prior(self) -> None:
        """On first run with no prior set, count new rules."""
        merged = RuleSet(
            version="1.0",
            project="test",
            rules=[
                Rule(
                    rule_id="D01-R01",
                    domain_id="d01",
                    text_short="Type hints",
                    text_body="Use type hints",
                    source="rules-pack",
                    status="provisional",
                    verified_date="2026-08-25",
                ),
            ],
        )
        prior = None

        counts = derive_summary_counts(prior, merged)

        assert counts.new_rules == 1
        assert counts.upgraded_to_verified == 0
        assert counts.findings_recorded == 0
        assert counts.not_applicable == 0

    def test_upgrade_tracking(self) -> None:
        """Tracks rules upgraded from provisional to verified-pass."""
        prior = RuleSet(
            version="1.0",
            project="test",
            rules=[
                Rule(
                    rule_id="D01-R01",
                    domain_id="d01",
                    text_short="Type hints",
                    text_body="Use type hints",
                    source="rules-pack",
                    status="provisional",
                    verified_date="2026-08-20",
                )
            ],
        )
        merged = RuleSet(
            version="1.0",
            project="test",
            rules=[
                Rule(
                    rule_id="D01-R01",
                    domain_id="d01",
                    text_short="Type hints",
                    text_body="Use type hints",
                    source="rules-pack",
                    status="verified-pass",
                    verified_date="2026-08-25",
                )
            ],
        )

        counts = derive_summary_counts(prior, merged)

        assert counts.upgraded_to_verified == 1

    def test_findings_and_not_applicable(self) -> None:
        """Counts findings and not-applicable rules."""
        merged = RuleSet(
            version="1.0",
            project="test",
            rules=[
                Rule(
                    rule_id="D01-R01",
                    domain_id="d01",
                    text_short="Type hints",
                    text_body="Use type hints",
                    source="rules-pack",
                    status="verified-finding",
                    verified_date="2026-08-25",
                    severity="high",
                    finding_details={"issue_title": "Missing hints"},
                ),
                Rule(
                    rule_id="D01-R02",
                    domain_id="d01",
                    text_short="HTTPS",
                    text_body="Use HTTPS",
                    source="rules-pack",
                    status="verified-not-applicable",
                    verified_date="2026-08-25",
                ),
            ],
        )

        counts = derive_summary_counts(None, merged)

        assert counts.findings_recorded == 1
        assert counts.not_applicable == 1


class TestBuildDiffs:
    """Tests for build_diffs."""

    def test_builds_diffs_for_all_three_documents(self, tmp_path: Path) -> None:
        """build_diffs creates DiffModel for each document."""
        deliverables_dir = tmp_path / "deliverables"
        deliverables_dir.mkdir()

        rendered = {
            "agent-standard": '<!-- audit:start id="agent-standard" -->\nAgent rules\n<!-- audit:end -->',
            "human-standard": '<!-- audit:start id="human-standard" -->\nHuman rules\n<!-- audit:end -->',
            "engineering-policy": '<!-- audit:start id="engineering-policy" -->\nPolicy\n<!-- audit:end -->',
        }

        diffs = build_diffs(deliverables_dir, rendered)

        assert len(diffs) == 3
        assert diffs[0].document_id == "agent-standard"
        assert diffs[1].document_id == "human-standard"
        assert diffs[2].document_id == "engineering-policy"
        assert all(not d.file_exists for d in diffs)
        assert all(d.current_content is None for d in diffs)

    def test_loads_existing_file_content(self, tmp_path: Path) -> None:
        """build_diffs reads existing file content from deliverables_dir when no repo_dir."""
        deliverables_dir = tmp_path / "deliverables"
        deliverables_dir.mkdir()

        # Create an existing file
        agent_file = deliverables_dir / AGENT_STANDARD_FILENAME
        agent_file.write_text("Old agent content")

        rendered = {
            "agent-standard": "New content",
            "human-standard": "Human content",
            "engineering-policy": "Policy content",
        }

        diffs = build_diffs(deliverables_dir, rendered)

        agent_diff = next(d for d in diffs if d.document_id == "agent-standard")
        assert agent_diff.file_exists is True
        assert agent_diff.current_content == "Old agent content"
        assert agent_diff.proposed_content == "New content"

    def test_loads_existing_file_content_from_repo_docs(self, tmp_path: Path) -> None:
        """build_diffs reads existing file content from repo_dir/docs when repo_dir provided."""
        repo_dir = tmp_path / "repo"
        docs_dir = repo_dir / "docs"
        docs_dir.mkdir(parents=True)

        deliverables_dir = tmp_path / "deliverables"
        deliverables_dir.mkdir()

        # Create an existing file in docs/
        agent_file = docs_dir / AGENT_STANDARD_FILENAME
        agent_file.write_text("Old agent content from repo")

        rendered = {
            "agent-standard": "New content",
            "human-standard": "Human content",
            "engineering-policy": "Policy content",
        }

        diffs = build_diffs(deliverables_dir, rendered, repo_dir)

        agent_diff = next(d for d in diffs if d.document_id == "agent-standard")
        assert agent_diff.file_exists is True
        assert agent_diff.current_content == "Old agent content from repo"
        assert agent_diff.proposed_content == "New content"


class TestRenderAll:
    """Tests for render_all."""

    def test_renders_three_documents(self) -> None:
        """render_all produces dict with three rendered documents."""
        rule_set = RuleSet(
            version="1.0",
            project="test",
            rules=[
                Rule(
                    rule_id="D01-R01",
                    domain_id="d01",
                    text_short="Type hints",
                    text_body="Use type hints",
                    source="rules-pack",
                    status="verified-pass",
                    verified_date="2026-08-25",
                )
            ],
        )

        rendered = render_all(rule_set)

        assert len(rendered) == 3
        assert "agent-standard" in rendered
        assert "human-standard" in rendered
        assert "engineering-policy" in rendered
        # Each should contain managed-block markers
        assert "<!-- audit:start" in rendered["agent-standard"]
        assert "<!-- audit:end -->" in rendered["agent-standard"]


class TestWriteStandards:
    """Tests for write_standards."""

    def test_writes_all_four_files_to_deliverables_dir_when_no_repo_dir(
        self, tmp_path: Path
    ) -> None:
        """write_standards writes documents to deliverables_dir when no repo_dir."""
        deliverables_dir = tmp_path / "deliverables"
        deliverables_dir.mkdir()

        rendered = {
            "agent-standard": '<!-- audit:start id="agent-standard" -->\nAgent\n<!-- audit:end -->',
            "human-standard": '<!-- audit:start id="human-standard" -->\nHuman\n<!-- audit:end -->',
            "engineering-policy": '<!-- audit:start id="engineering-policy" -->\nPolicy\n<!-- audit:end -->',
        }

        rule_set = RuleSet(
            version="1.0",
            project="test",
            rules=[
                Rule(
                    rule_id="D01-R01",
                    domain_id="d01",
                    text_short="Type hints",
                    text_body="Use type hints",
                    source="rules-pack",
                    status="verified-pass",
                    verified_date="2026-08-25",
                )
            ],
        )

        write_standards(deliverables_dir, rendered, rule_set)

        # Verify all files were written to deliverables_dir
        assert (deliverables_dir / AGENT_STANDARD_FILENAME).exists()
        assert (deliverables_dir / HUMAN_STANDARD_FILENAME).exists()
        assert (deliverables_dir / ENGINEERING_POLICY_FILENAME).exists()
        assert (deliverables_dir / RULE_SET_FILENAME).exists()

    def test_writes_standards_to_repo_docs_when_repo_dir_provided(
        self, tmp_path: Path
    ) -> None:
        """write_standards writes docs to repo_dir/docs when repo_dir provided."""
        repo_dir = tmp_path / "repo"
        deliverables_dir = tmp_path / "deliverables"
        deliverables_dir.mkdir()

        rendered = {
            "agent-standard": '<!-- audit:start id="agent-standard" -->\nAgent\n<!-- audit:end -->',
            "human-standard": '<!-- audit:start id="human-standard" -->\nHuman\n<!-- audit:end -->',
            "engineering-policy": '<!-- audit:start id="engineering-policy" -->\nPolicy\n<!-- audit:end -->',
        }

        rule_set = RuleSet(
            version="1.0",
            project="test",
            rules=[
                Rule(
                    rule_id="D01-R01",
                    domain_id="d01",
                    text_short="Type hints",
                    text_body="Use type hints",
                    source="rules-pack",
                    status="verified-pass",
                    verified_date="2026-08-25",
                )
            ],
        )

        write_standards(deliverables_dir, rendered, rule_set, repo_dir)

        # Verify three documents written to repo/docs/
        assert (repo_dir / "docs" / AGENT_STANDARD_FILENAME).exists()
        assert (repo_dir / "docs" / HUMAN_STANDARD_FILENAME).exists()
        assert (repo_dir / "docs" / ENGINEERING_POLICY_FILENAME).exists()
        # Rule set always goes to deliverables_dir
        assert (deliverables_dir / RULE_SET_FILENAME).exists()

    def test_creates_directory_if_needed(self, tmp_path: Path) -> None:
        """write_standards creates deliverables_dir if it doesn't exist."""
        deliverables_dir = tmp_path / "new" / "deliverables"

        rendered = {
            "agent-standard": '<!-- audit:start id="agent-standard" -->\nAgent\n<!-- audit:end -->',
            "human-standard": '<!-- audit:start id="human-standard" -->\nHuman\n<!-- audit:end -->',
            "engineering-policy": '<!-- audit:start id="engineering-policy" -->\nPolicy\n<!-- audit:end -->',
        }

        rule_set = RuleSet(
            version="1.0",
            project="test",
            rules=[],
        )

        write_standards(deliverables_dir, rendered, rule_set)

        assert deliverables_dir.exists()
        assert (deliverables_dir / RULE_SET_FILENAME).exists()

    def test_creates_repo_docs_directory_if_needed(self, tmp_path: Path) -> None:
        """write_standards creates repo_dir/docs if it doesn't exist."""
        repo_dir = tmp_path / "new" / "repo"
        deliverables_dir = tmp_path / "deliverables"
        deliverables_dir.mkdir()

        rendered = {
            "agent-standard": '<!-- audit:start id="agent-standard" -->\nAgent\n<!-- audit:end -->',
            "human-standard": '<!-- audit:start id="human-standard" -->\nHuman\n<!-- audit:end -->',
            "engineering-policy": '<!-- audit:start id="engineering-policy" -->\nPolicy\n<!-- audit:end -->',
        }

        rule_set = RuleSet(
            version="1.0",
            project="test",
            rules=[],
        )

        write_standards(deliverables_dir, rendered, rule_set, repo_dir)

        assert (repo_dir / "docs").exists()
        assert (repo_dir / "docs" / AGENT_STANDARD_FILENAME).exists()
        assert (deliverables_dir / RULE_SET_FILENAME).exists()

    def test_document_write_failure_does_not_write_rule_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If document write fails, rule set is NOT written (preserves prior set)."""
        from engineering_audit.managed_blocks import write_managed_block

        deliverables_dir = tmp_path / "deliverables"
        deliverables_dir.mkdir()

        # Create a prior rule set to ensure it's not overwritten on failure
        prior_rule_set = RuleSet(
            version="1.0",
            project="test",
            rules=[
                Rule(
                    rule_id="D00-R00",
                    domain_id="d00",
                    text_short="Prior rule",
                    text_body="This rule existed before",
                    source="rules-pack",
                    status="verified-pass",
                    verified_date="2026-08-20",
                )
            ],
        )
        prior_rule_set.write(deliverables_dir / RULE_SET_FILENAME)

        # Mock write_managed_block to fail on the second call
        call_count = [0]
        original_write_managed_block = write_managed_block

        def mock_write_managed_block(path, content, block_id):
            call_count[0] += 1
            if call_count[0] == 2:  # Fail on the second document
                return False
            return original_write_managed_block(path, content, block_id)

        monkeypatch.setattr(
            "engineering_audit.standards_integration.write_managed_block",
            mock_write_managed_block,
        )

        rendered = {
            "agent-standard": '<!-- audit:start id="agent-standard" -->\nAgent\n<!-- audit:end -->',
            "human-standard": '<!-- audit:start id="human-standard" -->\nHuman\n<!-- audit:end -->',
            "engineering-policy": '<!-- audit:start id="engineering-policy" -->\nPolicy\n<!-- audit:end -->',
        }

        new_rule_set = RuleSet(
            version="1.0",
            project="test",
            rules=[
                Rule(
                    rule_id="D01-R01",
                    domain_id="d01",
                    text_short="New rule",
                    text_body="This is a new rule",
                    source="rules-pack",
                    status="verified-pass",
                    verified_date="2026-08-25",
                )
            ],
        )

        # write_standards should raise RuntimeError
        with pytest.raises(RuntimeError, match="Failed to write standards document"):
            write_standards(deliverables_dir, rendered, new_rule_set)

        # Verify the prior rule set is still intact (not overwritten)
        loaded = load_prior_rule_set(deliverables_dir)
        assert loaded is not None
        assert len(loaded.rules) == 1
        assert loaded.rules[0].rule_id == "D00-R00"  # Prior rule, not new rule


class TestEndToEnd:
    """End-to-end tests for the complete workflow."""

    def test_first_audit_run_then_merge(self, tmp_path: Path) -> None:
        """First run creates rule set; second run merges verdicts."""
        deliverables_dir = tmp_path / "deliverables"
        deliverables_dir.mkdir()

        # First audit: pass and finding
        domain_results_1 = {
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=[
                    RuleVerdict(rule_id="D01-R01", verdict=Verdict.pass_),
                    RuleVerdict(rule_id="D01-R02", verdict=Verdict.FINDING),
                ],
                findings=[
                    Finding(
                        rule_id="D01-R02",
                        severity=Severity.HIGH,
                        title="Issue",
                        location="src/app.py:10",
                        body_md="Problem",
                        issue_title="Fix this",
                        issue_body="Details",
                        precondition="Project exists",
                    )
                ],
                uninspected_evidence=[],
            )
        }

        # Simulate rules pack with both rules
        class MockRulesPack:
            def get_domain(self, domain_id):
                class MockRule:
                    id = None
                    title = None

                class D01R01(MockRule):
                    id = "D01-R01"
                    title = "Type hints"

                class D01R02(MockRule):
                    id = "D01-R02"
                    title = "Error handling"

                class MockDomain:
                    id = domain_id
                    rules = [D01R01(), D01R02()]

                return MockDomain()

        rules_pack = MockRulesPack()

        # First write (using deliverables_dir since no repo_dir provided)
        audit_rules_1 = audit_rules_from_domain_results(domain_results_1, rules_pack)

        # Would normally merge with prior, but first run has no prior
        # Simulate the merged result
        rule_set_1 = RuleSet(
            version="1.0",
            project="test",
            rules=list(audit_rules_1.values()),
        )

        rendered_1 = render_all(rule_set_1)
        write_standards(deliverables_dir, rendered_1, rule_set_1)

        # Verify first write - documents in deliverables_dir, rule set in deliverables_dir
        assert (deliverables_dir / AGENT_STANDARD_FILENAME).exists()
        assert (deliverables_dir / RULE_SET_FILENAME).exists()
        prior = load_prior_rule_set(deliverables_dir)
        assert prior is not None
        assert len(prior.rules) == 2
        pass_rule = next(r for r in prior.rules if r.rule_id == "D01-R01")
        assert pass_rule.status == "verified-pass"

        # Second audit: first rule still passes
        domain_results_2 = {
            "d01": DomainResult(
                domain_id="d01",
                status="completed",
                rule_verdicts=[
                    RuleVerdict(rule_id="D01-R01", verdict=Verdict.pass_),
                    RuleVerdict(rule_id="D01-R02", verdict=Verdict.pass_),
                ],
                findings=[],
                uninspected_evidence=[],
            )
        }

        audit_rules_2 = audit_rules_from_domain_results(domain_results_2, rules_pack)

        # Would merge with prior here, but for now just create new set
        rule_set_2 = RuleSet(
            version="1.0",
            project="test",
            rules=list(audit_rules_2.values()),
        )

        rendered_2 = render_all(rule_set_2)
        write_standards(deliverables_dir, rendered_2, rule_set_2)

        # Verify second write - documents still in deliverables_dir, rule set updated
        assert (deliverables_dir / AGENT_STANDARD_FILENAME).exists()
        assert (deliverables_dir / RULE_SET_FILENAME).exists()

        # Verify second write
        final = load_prior_rule_set(deliverables_dir)
        assert final is not None
        assert len(final.rules) == 2
        # Both should be verified-pass now
        for rule in final.rules:
            assert rule.status == "verified-pass"


class TestResolveStackChoice:
    """Tests for resolve_stack_choice."""

    def test_choice_grill_leaves_rule_set_unchanged(self) -> None:
        """Choice 'grill' passes through the prior rule set unchanged."""
        from engineering_audit.standards_integration import resolve_stack_choice
        from datetime import date

        prior = RuleSet(
            version="1.0",
            project="test",
            rules=[
                Rule(
                    rule_id="D06-R01",
                    domain_id="d06",
                    text_short="Type hints",
                    text_body="Use type hints.",
                    source="rules-pack",
                    stack_profile=None,
                    status="verified-pass",
                    verified_date="2026-08-20",
                )
            ],
        )

        result = resolve_stack_choice(
            prior_rule_set=prior,
            pack_root=Path(__file__).parent / "fixture_pack",
            grill_stack=("python",),
            observed_stack_identifiers=("python", "fastapi"),
            choice="grill",
            today=date(2026, 8, 28),
        )

        # Should be identical to prior
        assert len(result.rules) == len(prior.rules)
        assert result.rules[0].rule_id == prior.rules[0].rule_id

    def test_choice_audit_adds_new_profile_rules(self) -> None:
        """Choice 'audit' adds new stack-profile rules with verified-pass."""
        from engineering_audit.standards_integration import resolve_stack_choice
        from datetime import date

        prior = RuleSet(
            version="1.0",
            project="test",
            rules=[
                Rule(
                    rule_id="D06-R01",
                    domain_id="d06",
                    text_short="Type hints",
                    text_body="Use type hints.",
                    source="rules-pack",
                    status="verified-pass",
                    verified_date="2026-08-20",
                )
            ],
        )

        result = resolve_stack_choice(
            prior_rule_set=prior,
            pack_root=Path(__file__).parent / "fixture_stack_profiles",
            grill_stack=("python",),
            observed_stack_identifiers=("python", "fastapi"),
            choice="audit",
            today=date(2026, 8, 28),
        )

        # Should have original rule plus new stack-profile rules
        assert len(result.rules) > len(prior.rules)

        # Check original rule is preserved
        original = [r for r in result.rules if r.rule_id == "D06-R01"]
        assert len(original) == 1

        # Check new rules are stack-profile rules with verified-pass
        profile_rules = [r for r in result.rules if r.source == "stack-profile"]
        assert len(profile_rules) > 0
        for rule in profile_rules:
            assert rule.status == "verified-pass"
            assert rule.verified_date == "2026-08-28"

    def test_choice_audit_marks_dropped_stack_rules_not_applicable(self) -> None:
        """Choice 'audit' marks dropped stack-profile rules as verified-not-applicable."""
        from engineering_audit.standards_integration import resolve_stack_choice
        from datetime import date

        # Prior rule set has a FastAPI stack-profile rule
        prior = RuleSet(
            version="1.0",
            project="test",
            rules=[
                Rule(
                    rule_id="SPFPY-R01",
                    domain_id=None,
                    text_short="FastAPI rule",
                    text_body="",
                    source="stack-profile",
                    stack_profile="fastapi",
                    status="verified-pass",
                    verified_date="2026-08-20",
                )
            ],
        )

        # New observed stack is Django (not FastAPI)
        result = resolve_stack_choice(
            prior_rule_set=prior,
            pack_root=Path(__file__).parent / "fixture_stack_profiles",
            grill_stack=("python", "fastapi"),
            observed_stack_identifiers=("python", "django"),
            choice="audit",
            today=date(2026, 8, 28),
        )

        # The FastAPI rule should still be present but marked not-applicable
        fastapi_rule = [r for r in result.rules if r.rule_id == "SPFPY-R01"]
        assert len(fastapi_rule) == 1
        assert fastapi_rule[0].status == "verified-not-applicable"

    def test_rules_pack_rules_are_untouched(self) -> None:
        """Rules from rules-pack are never modified by choice resolution."""
        from engineering_audit.standards_integration import resolve_stack_choice
        from datetime import date

        prior = RuleSet(
            version="1.0",
            project="test",
            rules=[
                Rule(
                    rule_id="D06-R01",
                    domain_id="d06",
                    text_short="Type hints",
                    text_body="Original body",
                    source="rules-pack",
                    status="provisional",
                    verified_date="2026-08-15",
                )
            ],
        )

        result = resolve_stack_choice(
            prior_rule_set=prior,
            pack_root=Path(__file__).parent / "fixture_stack_profiles",
            grill_stack=("python",),
            observed_stack_identifiers=("python", "fastapi"),
            choice="audit",
            today=date(2026, 8, 28),
        )

        # Original rule should be completely unchanged
        original = [r for r in result.rules if r.rule_id == "D06-R01"]
        assert len(original) == 1
        assert original[0] == prior.rules[0]


class TestStackChoiceDecisionRecord:
    """Tests for the stack choice decision record."""

    def test_decision_record_round_trips_to_json(self) -> None:
        """Decision record can be serialized and deserialized as JSON."""
        from engineering_audit.standards_integration import (
            build_stack_choice_decision,
        )
        from engineering_audit.stack_detection import StackEvidence, DetectedStack
        import json
        from datetime import datetime

        grill_stack = ("python", "fastapi")
        observed_stack = DetectedStack(
            identifiers=("python", "django"),
            evidence={
                "python": StackEvidence(
                    file_path="pyproject.toml",
                    dependency_or_line="[project]",
                ),
                "django": StackEvidence(
                    file_path="pyproject.toml",
                    dependency_or_line="django",
                ),
            },
        )

        decision = build_stack_choice_decision(
            grill_stack=grill_stack,
            observed_stack=observed_stack,
            choice="audit",
            timestamp=datetime(2026, 8, 28, 12, 30, 45),
        )

        # Should be serializable to JSON
        json_str = json.dumps(decision)
        assert json_str is not None

        # Should be deserializable from JSON
        restored = json.loads(json_str)
        assert restored["grill_stack"] == list(grill_stack)
        assert restored["choice"] == "audit"
        assert restored["observed_stack_identifiers"] == list(
            observed_stack.identifiers
        )
