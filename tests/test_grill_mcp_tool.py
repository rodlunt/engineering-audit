"""Tests for the grill MCP tool that writes provisional standards.

Tests the write_grill_standards_artefacts tool that the grill skill calls
to generate provisional standards from grill-captured rules.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from engineering_audit.standards import RuleSet

FIXTURE_PACK = Path(__file__).parent / "fixture_pack"


def test_write_grill_standards_artefacts_generates_rule_set(tmp_path: Path) -> None:
    """Tool generates a rule set with provisional status."""
    from engineering_audit.standards import Rule
    from engineering_audit.standards_integration import render_all, write_standards

    grill_rules = [
        Rule(
            rule_id="D06-R01",
            domain_id="d06",
            text_short="Use type hints",
            text_body="Use Python 3.9+ type hints.",
            source="rules-pack",
            stack_profile=None,
            status="provisional",
            verified_date=date.today().isoformat(),
        )
    ]

    rule_set = RuleSet(
        version="1.0",
        project="test-project",
        rules=grill_rules,
    )

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    rendered = render_all(rule_set)
    write_standards(output_dir, rendered, rule_set)

    # Verify rule set was written
    rule_set_path = output_dir / "rule-set.json"
    assert rule_set_path.exists()

    loaded = RuleSet.load(rule_set_path)
    assert loaded.rules[0].status == "provisional"
    assert loaded.rules[0].rule_id == "D06-R01"


def test_write_grill_standards_writes_all_documents(tmp_path: Path) -> None:
    """Tool writes all three standards documents."""
    from engineering_audit.standards import Rule
    from engineering_audit.standards_integration import render_all, write_standards

    grill_rules = [
        Rule(
            rule_id="D06-R01",
            domain_id="d06",
            text_short="Use type hints",
            text_body="Use Python 3.9+ type hints.",
            source="rules-pack",
            status="provisional",
            verified_date=date.today().isoformat(),
        )
    ]

    rule_set = RuleSet(
        version="1.0",
        project="test-project",
        rules=grill_rules,
    )

    output_dir = tmp_path / "output"
    project_dir = tmp_path / "project"
    output_dir.mkdir()
    project_dir.mkdir()

    rendered = render_all(rule_set)
    write_standards(output_dir, rendered, rule_set, project_dir)

    # Verify all three documents exist
    docs_dir = project_dir / "docs"
    assert (docs_dir / "coding-standard.agent.md").exists()
    assert (docs_dir / "engineering-standard.md").exists()
    assert (docs_dir / "engineering-policy.md").exists()


def test_provisional_standards_use_managed_blocks(tmp_path: Path) -> None:
    """Provisional standards documents use managed-block markers."""
    from engineering_audit.standards import Rule
    from engineering_audit.standards_integration import render_all, write_standards

    grill_rules = [
        Rule(
            rule_id="D06-R01",
            domain_id="d06",
            text_short="Use type hints",
            text_body="Use Python 3.9+ type hints.",
            source="rules-pack",
            status="provisional",
            verified_date=date.today().isoformat(),
        )
    ]

    rule_set = RuleSet(
        version="1.0",
        project="test-project",
        rules=grill_rules,
    )

    output_dir = tmp_path / "output"
    project_dir = tmp_path / "project"
    output_dir.mkdir()
    project_dir.mkdir()

    rendered = render_all(rule_set)
    write_standards(output_dir, rendered, rule_set, project_dir)

    docs_dir = project_dir / "docs"
    agent_file = docs_dir / "coding-standard.agent.md"
    content = agent_file.read_text(encoding="utf-8")

    assert '<!-- audit:start id="agent-standard" -->' in content
    assert "<!-- audit:end -->" in content


def test_provisional_rules_carry_grill_intent_note(tmp_path: Path) -> None:
    """Provisional rules preserve grill intent note through render and write."""
    from engineering_audit.standards import Rule
    from engineering_audit.standards_integration import render_all, write_standards

    intent_note = "Team consensus: always use type hints"
    grill_rules = [
        Rule(
            rule_id="D06-R01",
            domain_id="d06",
            text_short="Use type hints",
            text_body="Use Python 3.9+ type hints.",
            source="rules-pack",
            status="provisional",
            verified_date=date.today().isoformat(),
            grill_intent_note=intent_note,
        )
    ]

    rule_set = RuleSet(
        version="1.0",
        project="test-project",
        rules=grill_rules,
    )

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    rendered = render_all(rule_set)
    write_standards(output_dir, rendered, rule_set)

    # Verify note is preserved in rule set
    rule_set_path = output_dir / "rule-set.json"
    loaded = RuleSet.load(rule_set_path)
    assert loaded.rules[0].grill_intent_note == intent_note
