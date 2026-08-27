"""Tests for the grill MCP tool and standards integration functions.

Tests both the write_grill_standards_artefacts MCP tool that the grill skill
calls to generate provisional standards from grill-captured rules, and the
underlying standards_integration functions (render_all, write_standards).
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

from engineering_audit.standards import RuleSet
from engineering_audit.server import build_server

FIXTURE_PACK = Path(__file__).parent / "fixture_pack"


def _call(mcp, name: str, arguments: dict):
    """Call an MCP tool and return its structured content."""
    result = asyncio.run(mcp.call_tool(name, arguments))
    return result.structured_content


def test_write_standards_generates_provisional_rule_set(tmp_path: Path) -> None:
    """write_standards generates a rule set with provisional status."""
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


def test_write_standards_writes_all_documents(tmp_path: Path) -> None:
    """write_standards writes all three standards documents."""
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


def test_write_standards_uses_managed_blocks(tmp_path: Path) -> None:
    """write_standards uses managed-block markers in output documents."""
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


def test_write_standards_preserves_grill_intent_note(tmp_path: Path) -> None:
    """write_standards preserves grill intent note through render and write."""
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


# MCP tool tests (write_grill_standards_artefacts)


def test_write_grill_standards_artefacts_rejects_invalid_json(tmp_path: Path) -> None:
    """MCP tool rejects invalid JSON in grill_rules parameter."""
    mcp, _state = build_server(FIXTURE_PACK)
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    # Call the tool with invalid JSON
    result = _call(
        mcp,
        "write_grill_standards_artefacts",
        {
            "grill_rules": "not valid json {",
            "output_dir": str(output_dir),
        },
    )

    assert result["success"] is False
    assert len(result["errors"]) > 0
    error_message = result["errors"][0]
    # Check that error message is actionable: mentions JSON and how to fix
    assert "JSON" in error_message or "json" in error_message
    assert (
        "syntax" in error_message
        or "formatted" in error_message
        or "proper" in error_message
    )


def test_write_grill_standards_artefacts_rejects_non_array_grill_rules(
    tmp_path: Path,
) -> None:
    """MCP tool rejects grill_rules that is not a JSON array."""
    mcp, _state = build_server(FIXTURE_PACK)
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    # Call the tool with valid JSON but not an array
    result = _call(
        mcp,
        "write_grill_standards_artefacts",
        {
            "grill_rules": json.dumps({"rule_id": "D06-R01"}),  # Object, not array
            "output_dir": str(output_dir),
        },
    )

    assert result["success"] is False
    assert len(result["errors"]) > 0
    error_message = result["errors"][0]
    # Check that error message is actionable: tells them it must be an array
    assert "array" in error_message


def test_write_grill_standards_artefacts_rejects_rule_missing_required_fields(
    tmp_path: Path,
) -> None:
    """MCP tool rejects rule object missing required fields."""
    mcp, _state = build_server(FIXTURE_PACK)
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    # Create a rule missing required fields (missing 'text_short')
    incomplete_rule = {
        "rule_id": "D06-R01",
        "domain_id": "d06",
        # Missing text_short, text_body, source
        "text_body": "Use Python 3.9+ type hints.",
    }

    result = _call(
        mcp,
        "write_grill_standards_artefacts",
        {
            "grill_rules": json.dumps([incomplete_rule]),
            "output_dir": str(output_dir),
        },
    )

    assert result["success"] is False
    assert len(result["errors"]) > 0
    error_message = result["errors"][0]
    # Check that error message is actionable: mentions required fields
    assert (
        "required" in error_message
        or "missing" in error_message
        or "invalid" in error_message
    )
    # If it's a KeyError, should include the field name
    assert "text_short" in error_message or "required fields" in error_message


def test_write_grill_standards_artefacts_handles_write_failure(
    tmp_path: Path,
) -> None:
    """MCP tool handles write failure gracefully with actionable error."""
    mcp, _state = build_server(FIXTURE_PACK)

    # Create a file where a directory should be, to force a write failure
    output_dir = tmp_path / "blockeddir"
    output_dir.touch()  # Create a file instead of directory

    valid_rule = {
        "rule_id": "D06-R01",
        "domain_id": "d06",
        "text_short": "Use type hints",
        "text_body": "Use Python 3.9+ type hints.",
        "source": "rules-pack",
    }

    result = _call(
        mcp,
        "write_grill_standards_artefacts",
        {
            "grill_rules": json.dumps([valid_rule]),
            "output_dir": str(output_dir),
        },
    )

    assert result["success"] is False
    assert len(result["errors"]) > 0
    error_message = result["errors"][0]
    # Check that error message is actionable: mentions directory and what to check
    assert "directory" in error_message or "output" in error_message
    assert "writable" in error_message or "exists" in error_message
