"""Tests for stack detection and extraction.

Tests for the stack_detection module, covering:
- Detection of Python and JavaScript stacks from dependency files
- Evidence collection and normalisation
- Extraction of stacks from RuleSets
- Stack comparison and difference description
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engineering_audit.stack_detection import (
    DetectedStack,
    StackEvidence,
    describe_stack_difference,
    detect_stack,
    grill_stack_from_rule_set,
    normalise_identifier,
    stacks_differ,
)
from engineering_audit.standards import Rule, RuleSet


class TestNormaliseIdentifier:
    """Tests for normalise_identifier."""

    def test_normalises_lowercase_language(self) -> None:
        """Lowercase language names are normalised."""
        assert normalise_identifier("python") == "python"
        assert normalise_identifier("javascript") == "javascript"

    def test_normalises_uppercase_framework(self) -> None:
        """Uppercase framework names are normalised."""
        assert normalise_identifier("FastAPI") == "fastapi"
        assert normalise_identifier("Django") == "django"
        assert normalise_identifier("React") == "react"

    def test_normalises_mixed_case(self) -> None:
        """Mixed case is normalised correctly."""
        assert normalise_identifier("NextJS") == "next"
        assert normalise_identifier("Express") == "express"

    def test_normalises_common_variations(self) -> None:
        """Common package name variations are recognised."""
        assert normalise_identifier("next.js") == "next"
        assert normalise_identifier("vue.js") == "vue"

    def test_returns_none_for_unknown(self) -> None:
        """Unknown identifiers return None."""
        assert normalise_identifier("unknown") is None
        assert normalise_identifier("random-lib") is None

    def test_strips_whitespace(self) -> None:
        """Whitespace is stripped before normalisation."""
        assert normalise_identifier("  python  ") == "python"
        assert normalise_identifier("\tfastapi\n") == "fastapi"


class TestDetectedStack:
    """Tests for DetectedStack dataclass."""

    def test_creates_with_identifiers_and_evidence(self) -> None:
        """DetectedStack can be created with identifiers and evidence."""
        evidence = {
            "python": StackEvidence("pyproject.toml", "dependency"),
            "fastapi": StackEvidence("pyproject.toml", "fastapi"),
        }
        stack = DetectedStack(
            identifiers=("python", "fastapi"),
            evidence=evidence,
        )
        assert stack.identifiers == ("fastapi", "python")  # Sorted
        assert len(stack.evidence) == 2

    def test_sorts_identifiers(self) -> None:
        """Identifiers are always sorted for determinism."""
        evidence = {
            "fastapi": StackEvidence("pyproject.toml", "fastapi"),
            "python": StackEvidence("pyproject.toml", "dependency"),
        }
        stack = DetectedStack(
            identifiers=("fastapi", "python"),
            evidence=evidence,
        )
        assert stack.identifiers == ("fastapi", "python")

    def test_raises_on_evidence_mismatch(self) -> None:
        """Evidence must match identifiers."""
        evidence = {
            "python": StackEvidence("pyproject.toml", "dependency"),
            # Missing fastapi evidence
        }
        with pytest.raises(ValueError, match="Evidence mismatch"):
            DetectedStack(
                identifiers=("python", "fastapi"),
                evidence=evidence,
            )

    def test_empty_stack_is_falsy(self) -> None:
        """Empty DetectedStack is falsy."""
        empty = DetectedStack()
        assert not empty

    def test_nonempty_stack_is_truthy(self) -> None:
        """Non-empty DetectedStack is truthy."""
        stack = DetectedStack(
            identifiers=("python",),
            evidence={"python": StackEvidence("pyproject.toml", "dependency")},
        )
        assert stack

    def test_equality_compares_identifiers(self) -> None:
        """Equality compares identifier sets."""
        evidence1 = {
            "python": StackEvidence("pyproject.toml", "dep1"),
            "fastapi": StackEvidence("pyproject.toml", "fastapi"),
        }
        stack1 = DetectedStack(
            identifiers=("python", "fastapi"),
            evidence=evidence1,
        )

        evidence2 = {
            "fastapi": StackEvidence("requirements.txt", "fastapi"),
            "python": StackEvidence("requirements.txt", "dep2"),
        }
        stack2 = DetectedStack(
            identifiers=("fastapi", "python"),
            evidence=evidence2,
        )

        # Same identifiers, different evidence -> equal
        assert stack1 == stack2

    def test_hash_based_on_identifiers(self) -> None:
        """Hash is based on identifiers, so equal stacks have same hash."""
        evidence1 = {
            "python": StackEvidence("pyproject.toml", "dep1"),
        }
        stack1 = DetectedStack(
            identifiers=("python",),
            evidence=evidence1,
        )

        evidence2 = {
            "python": StackEvidence("requirements.txt", "dep2"),
        }
        stack2 = DetectedStack(
            identifiers=("python",),
            evidence=evidence2,
        )

        assert hash(stack1) == hash(stack2)


class TestDetectStackPython:
    """Tests for detect_stack with Python dependency files."""

    def test_detects_python_from_pyproject_toml(self, tmp_path: Path) -> None:
        """Python is detected from pyproject.toml."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("""
[project]
name = "test"
dependencies = [
    "requests>=2.0",
    "fastapi>=0.100",
]
""")
        stack = detect_stack(tmp_path)

        assert "python" in stack.identifiers
        assert "fastapi" in stack.identifiers
        assert stack.evidence["python"].file_path == "pyproject.toml"
        assert stack.evidence["fastapi"].file_path == "pyproject.toml"

    def test_detects_poetry_dependencies(self, tmp_path: Path) -> None:
        """Poetry [tool.poetry.dependencies] is detected."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("""
[tool.poetry.dependencies]
python = "^3.9"
fastapi = "^0.100"
django = "^4.0"
""")
        stack = detect_stack(tmp_path)

        assert "python" in stack.identifiers
        assert "fastapi" in stack.identifiers
        assert "django" in stack.identifiers

    def test_detects_from_requirements_txt(self, tmp_path: Path) -> None:
        """Python is detected from requirements.txt."""
        req = tmp_path / "requirements.txt"
        req.write_text("""
requests>=2.0
flask==2.0.0
""")
        stack = detect_stack(tmp_path)

        assert "python" in stack.identifiers
        assert "flask" in stack.identifiers
        assert stack.evidence["python"].file_path == "requirements.txt"
        assert stack.evidence["flask"].file_path == "requirements.txt"

    def test_detects_from_requirements_dev_txt(self, tmp_path: Path) -> None:
        """requirements-dev.txt is detected."""
        req_dev = tmp_path / "requirements-dev.txt"
        req_dev.write_text("django>=3.0\n")
        stack = detect_stack(tmp_path)

        assert "python" in stack.identifiers
        assert "django" in stack.identifiers

    def test_detects_from_setup_cfg(self, tmp_path: Path) -> None:
        """Python is detected from setup.cfg."""
        setup_cfg = tmp_path / "setup.cfg"
        setup_cfg.write_text("""
[metadata]
name = test

[options]
install_requires =
    fastapi>=0.100
    requests
""")
        stack = detect_stack(tmp_path)

        assert "python" in stack.identifiers
        assert "fastapi" in stack.identifiers
        assert stack.evidence["python"].file_path == "setup.cfg"

    def test_detects_from_pipfile(self, tmp_path: Path) -> None:
        """Python is detected from Pipfile."""
        pipfile = tmp_path / "Pipfile"
        pipfile.write_text("""
[packages]
django = "*"
flask = ">=2.0"
""")
        stack = detect_stack(tmp_path)

        assert "python" in stack.identifiers
        assert "django" in stack.identifiers
        assert "flask" in stack.identifiers
        assert stack.evidence["python"].file_path == "Pipfile"

    def test_skips_malformed_pyproject(self, tmp_path: Path) -> None:
        """Malformed pyproject.toml is skipped without crashing."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("invalid [toml syntax {")

        req = tmp_path / "requirements.txt"
        req.write_text("fastapi\n")

        stack = detect_stack(tmp_path)

        # Python detected from requirements.txt despite broken pyproject
        assert "python" in stack.identifiers
        assert "fastapi" in stack.identifiers

    def test_skips_malformed_setup_cfg(self, tmp_path: Path) -> None:
        """Malformed setup.cfg is skipped without crashing."""
        setup_cfg = tmp_path / "setup.cfg"
        setup_cfg.write_text("[invalid\n")

        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[project]\nname = 'test'\n")

        stack = detect_stack(tmp_path)

        # Python detected from pyproject.toml despite broken setup.cfg
        assert "python" in stack.identifiers

    def test_prefers_pyproject_over_requirements(self, tmp_path: Path) -> None:
        """If both pyproject and requirements exist, pyproject is preferred."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[project]\ndependencies = ['fastapi']\n")

        req = tmp_path / "requirements.txt"
        req.write_text("django\n")

        stack = detect_stack(tmp_path)

        assert "fastapi" in stack.identifiers
        assert stack.evidence["python"].file_path == "pyproject.toml"


class TestDetectStackJavaScript:
    """Tests for detect_stack with JavaScript dependency files."""

    def test_detects_javascript_from_package_json(self, tmp_path: Path) -> None:
        """JavaScript is detected from package.json."""
        package = tmp_path / "package.json"
        package.write_text("""
{
  "name": "test",
  "dependencies": {
    "express": "^4.18.0",
    "react": "^18.0.0"
  }
}
""")
        stack = detect_stack(tmp_path)

        assert "javascript" in stack.identifiers
        assert "express" in stack.identifiers
        assert "react" in stack.identifiers
        assert stack.evidence["javascript"].file_path == "package.json"

    def test_detects_typescript(self, tmp_path: Path) -> None:
        """TypeScript is detected when typescript is in devDependencies."""
        package = tmp_path / "package.json"
        package.write_text("""
{
  "name": "test",
  "dependencies": {
    "next": "^13.0.0"
  },
  "devDependencies": {
    "typescript": "^5.0.0"
  }
}
""")
        stack = detect_stack(tmp_path)

        assert "typescript" in stack.identifiers
        assert "next" in stack.identifiers
        assert "javascript" not in stack.identifiers

    def test_detects_frameworks_from_dev_dependencies(self, tmp_path: Path) -> None:
        """Frameworks in devDependencies are detected."""
        package = tmp_path / "package.json"
        package.write_text("""
{
  "name": "test",
  "dependencies": {},
  "devDependencies": {
    "svelte": "^4.0.0"
  }
}
""")
        stack = detect_stack(tmp_path)

        assert "javascript" in stack.identifiers
        assert "svelte" in stack.identifiers

    def test_skips_malformed_package_json(self, tmp_path: Path) -> None:
        """Malformed package.json is skipped without crashing."""
        package = tmp_path / "package.json"
        package.write_text("{ invalid json")

        stack = detect_stack(tmp_path)

        # Stack is empty but no exception is raised
        assert not stack

    def test_handles_missing_dependencies_keys(self, tmp_path: Path) -> None:
        """package.json without dependencies keys is handled gracefully."""
        package = tmp_path / "package.json"
        package.write_text("""
{
  "name": "test",
  "version": "1.0.0"
}
""")
        stack = detect_stack(tmp_path)

        # Only javascript detected, no frameworks
        assert stack.identifiers == ("javascript",)


class TestDetectStackMultiple:
    """Tests for detect_stack with multiple files and mixed stacks."""

    def test_detects_both_python_and_javascript(self, tmp_path: Path) -> None:
        """A monorepo with both Python and JavaScript is detected correctly."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[project]\ndependencies = ['fastapi']\n")

        package = tmp_path / "package.json"
        package.write_text('{"dependencies": {"react": "^18.0.0"}}')

        stack = detect_stack(tmp_path)

        assert "python" in stack.identifiers
        assert "fastapi" in stack.identifiers
        assert "javascript" in stack.identifiers
        assert "react" in stack.identifiers

    def test_missing_repo_dir_returns_empty(self) -> None:
        """Missing repo_dir returns empty DetectedStack."""
        stack = detect_stack(Path("/nonexistent/path"))
        assert not stack
        assert stack.identifiers == ()

    def test_empty_repo_returns_empty(self, tmp_path: Path) -> None:
        """Repo with no dependency files returns empty stack."""
        stack = detect_stack(tmp_path)
        assert not stack
        assert stack.identifiers == ()

    def test_multiple_requirements_files(self, tmp_path: Path) -> None:
        """Multiple requirements-*.txt files are all checked."""
        req = tmp_path / "requirements.txt"
        req.write_text("django\n")

        req_dev = tmp_path / "requirements-dev.txt"
        req_dev.write_text("pytest\nflask\n")

        stack = detect_stack(tmp_path)

        # Should detect both django and flask
        assert "python" in stack.identifiers
        assert "django" in stack.identifiers
        assert "flask" in stack.identifiers


class TestGrillStackFromRuleSet:
    """Tests for grill_stack_from_rule_set."""

    def test_extracts_stack_profile_rules(self) -> None:
        """Stack profiles are extracted from stack-profile rules."""
        rule_set = RuleSet(
            version="1.0",
            project="test",
            rules=[
                Rule(
                    rule_id="S-Python-R01",
                    domain_id=None,
                    text_short="Python rule",
                    text_body="Use Python best practices.",
                    source="stack-profile",
                    stack_profile="python",
                    status="provisional",
                    verified_date="2026-08-27",
                ),
                Rule(
                    rule_id="S-FastAPI-R01",
                    domain_id=None,
                    text_short="FastAPI rule",
                    text_body="Use FastAPI conventions.",
                    source="stack-profile",
                    stack_profile="fastapi",
                    status="provisional",
                    verified_date="2026-08-27",
                ),
            ],
        )

        stack = grill_stack_from_rule_set(rule_set)

        assert stack == frozenset({"python", "fastapi"})

    def test_ignores_rules_pack_rules(self) -> None:
        """Rules with source='rules-pack' are ignored."""
        rule_set = RuleSet(
            version="1.0",
            project="test",
            rules=[
                Rule(
                    rule_id="D01-R01",
                    domain_id="d01",
                    text_short="Generic rule",
                    text_body="A generic rule.",
                    source="rules-pack",
                    stack_profile=None,
                    status="provisional",
                    verified_date="2026-08-27",
                ),
                Rule(
                    rule_id="S-React-R01",
                    domain_id=None,
                    text_short="React rule",
                    text_body="Use React conventions.",
                    source="stack-profile",
                    stack_profile="react",
                    status="provisional",
                    verified_date="2026-08-27",
                ),
            ],
        )

        stack = grill_stack_from_rule_set(rule_set)

        assert stack == frozenset({"react"})

    def test_ignores_null_stack_profile(self) -> None:
        """Rules with null stack_profile are ignored."""
        rule_set = RuleSet(
            version="1.0",
            project="test",
            rules=[
                Rule(
                    rule_id="D01-R01",
                    domain_id="d01",
                    text_short="Generic rule",
                    text_body="A generic rule.",
                    source="rules-pack",
                    stack_profile=None,
                    status="provisional",
                    verified_date="2026-08-27",
                ),
            ],
        )

        stack = grill_stack_from_rule_set(rule_set)

        assert stack == frozenset()

    def test_empty_rule_set_returns_empty_stack(self) -> None:
        """Empty rule set returns empty frozenset."""
        rule_set = RuleSet(version="1.0", project="test", rules=[])

        stack = grill_stack_from_rule_set(rule_set)

        assert stack == frozenset()

    def test_normalises_stack_profile_names(self) -> None:
        """Stack profile names are normalised."""
        rule_set = RuleSet(
            version="1.0",
            project="test",
            rules=[
                Rule(
                    rule_id="S-Python-R01",
                    domain_id=None,
                    text_short="Python rule",
                    text_body="Use Python best practices.",
                    source="stack-profile",
                    stack_profile="Python",  # Uppercase
                    status="provisional",
                    verified_date="2026-08-27",
                ),
                Rule(
                    rule_id="S-FastAPI-R01",
                    domain_id=None,
                    text_short="FastAPI rule",
                    text_body="Use FastAPI conventions.",
                    source="stack-profile",
                    stack_profile="FastAPI",  # Different case
                    status="provisional",
                    verified_date="2026-08-27",
                ),
            ],
        )

        stack = grill_stack_from_rule_set(rule_set)

        assert stack == frozenset({"python", "fastapi"})


class TestStacksDiffer:
    """Tests for stacks_differ."""

    def test_equal_stacks_do_not_differ(self) -> None:
        """Equal stacks return False."""
        grill = frozenset({"python", "fastapi"})
        observed = DetectedStack(
            identifiers=("python", "fastapi"),
            evidence={
                "python": StackEvidence("pyproject.toml", "dep"),
                "fastapi": StackEvidence("pyproject.toml", "fastapi"),
            },
        )

        assert not stacks_differ(grill, observed)

    def test_different_frameworks_do_differ(self) -> None:
        """Different frameworks are detected as a difference."""
        grill = frozenset({"python", "fastapi"})
        observed = DetectedStack(
            identifiers=("python", "django"),
            evidence={
                "python": StackEvidence("pyproject.toml", "dep"),
                "django": StackEvidence("pyproject.toml", "django"),
            },
        )

        assert stacks_differ(grill, observed)

    def test_empty_grill_is_not_mismatch(self) -> None:
        """Empty grill stack is never a mismatch."""
        grill = frozenset()
        observed = DetectedStack(
            identifiers=("python", "fastapi"),
            evidence={
                "python": StackEvidence("pyproject.toml", "dep"),
                "fastapi": StackEvidence("pyproject.toml", "fastapi"),
            },
        )

        assert not stacks_differ(grill, observed)

    def test_observed_empty_with_nonempty_grill_is_mismatch(self) -> None:
        """Grill non-empty but observed empty is a mismatch."""
        grill = frozenset({"python"})
        observed = DetectedStack()

        assert stacks_differ(grill, observed)

    def test_extra_in_observed_is_mismatch(self) -> None:
        """Extra identifiers in observed is a mismatch."""
        grill = frozenset({"python"})
        observed = DetectedStack(
            identifiers=("python", "fastapi"),
            evidence={
                "python": StackEvidence("pyproject.toml", "dep"),
                "fastapi": StackEvidence("pyproject.toml", "fastapi"),
            },
        )

        assert stacks_differ(grill, observed)

    def test_missing_in_observed_is_mismatch(self) -> None:
        """Missing identifiers in observed is a mismatch."""
        grill = frozenset({"python", "fastapi", "django"})
        observed = DetectedStack(
            identifiers=("python", "fastapi"),
            evidence={
                "python": StackEvidence("pyproject.toml", "dep"),
                "fastapi": StackEvidence("pyproject.toml", "fastapi"),
            },
        )

        assert stacks_differ(grill, observed)

    def test_works_with_tuple_input(self) -> None:
        """stacks_differ works with tuple input as well as frozenset."""
        grill = ("python", "fastapi")
        observed = DetectedStack(
            identifiers=("python", "fastapi"),
            evidence={
                "python": StackEvidence("pyproject.toml", "dep"),
                "fastapi": StackEvidence("pyproject.toml", "fastapi"),
            },
        )

        assert not stacks_differ(grill, observed)


class TestDescribeStackDifference:
    """Tests for describe_stack_difference."""

    def test_describes_mismatch(self) -> None:
        """Difference description is accurate for a mismatch."""
        grill = frozenset({"python", "fastapi"})
        observed = DetectedStack(
            identifiers=("python", "django"),
            evidence={
                "python": StackEvidence("pyproject.toml", "dep"),
                "django": StackEvidence("pyproject.toml", "django"),
            },
        )

        diff = describe_stack_difference(grill, observed)

        assert set(diff["grill"]) == {"python", "fastapi"}
        assert set(diff["observed"]) == {"python", "django"}
        assert diff["only_in_grill"] == ["fastapi"]
        assert diff["only_in_observed"] == ["django"]

    def test_describes_equal_stacks(self) -> None:
        """Difference description for equal stacks has empty difference lists."""
        grill = frozenset({"python", "fastapi"})
        observed = DetectedStack(
            identifiers=("python", "fastapi"),
            evidence={
                "python": StackEvidence("pyproject.toml", "dep"),
                "fastapi": StackEvidence("pyproject.toml", "fastapi"),
            },
        )

        diff = describe_stack_difference(grill, observed)

        assert set(diff["grill"]) == {"python", "fastapi"}
        assert set(diff["observed"]) == {"python", "fastapi"}
        assert diff["only_in_grill"] == []
        assert diff["only_in_observed"] == []

    def test_describes_empty_grill(self) -> None:
        """Description for empty grill shows empty list."""
        grill = frozenset()
        observed = DetectedStack(
            identifiers=("python", "fastapi"),
            evidence={
                "python": StackEvidence("pyproject.toml", "dep"),
                "fastapi": StackEvidence("pyproject.toml", "fastapi"),
            },
        )

        diff = describe_stack_difference(grill, observed)

        assert diff["grill"] == []
        assert set(diff["observed"]) == {"python", "fastapi"}
        assert diff["only_in_grill"] == []
        assert set(diff["only_in_observed"]) == {"python", "fastapi"}

    def test_returns_sorted_lists(self) -> None:
        """Returned lists are sorted for deterministic output."""
        grill = frozenset({"fastapi", "python"})
        observed = DetectedStack(
            identifiers=("django", "python"),
            evidence={
                "python": StackEvidence("pyproject.toml", "dep"),
                "django": StackEvidence("pyproject.toml", "django"),
            },
        )

        diff = describe_stack_difference(grill, observed)

        # Lists should be sorted
        assert diff["grill"] == ["fastapi", "python"]
        assert diff["observed"] == ["django", "python"]
        assert diff["only_in_grill"] == ["fastapi"]
        assert diff["only_in_observed"] == ["django"]


class TestRealWorldScenarios:
    """Tests with realistic repository configurations."""

    def test_fastapi_project_detection(self, tmp_path: Path) -> None:
        """A FastAPI project is detected correctly."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("""
[build-system]
requires = ["poetry-core"]
build-backend = "poetry.core.masonry.api"

[tool.poetry.dependencies]
python = "^3.9"
fastapi = "^0.104"
uvicorn = "^0.24"
pydantic = "^2.0"
""")
        stack = detect_stack(tmp_path)

        assert stack.identifiers == ("fastapi", "python")

    def test_django_project_detection(self, tmp_path: Path) -> None:
        """A Django project is detected correctly."""
        req = tmp_path / "requirements.txt"
        req.write_text("""
Django>=4.0
djangorestframework>=3.14
psycopg2-binary>=2.9
""")
        stack = detect_stack(tmp_path)

        assert stack.identifiers == ("django", "python")

    def test_react_typescript_project(self, tmp_path: Path) -> None:
        """A React + TypeScript project is detected correctly."""
        package = tmp_path / "package.json"
        package.write_text("""
{
  "name": "my-app",
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0"
  },
  "devDependencies": {
    "typescript": "^5.1.0",
    "@types/react": "^18.2.0"
  }
}
""")
        stack = detect_stack(tmp_path)

        assert stack.identifiers == ("react", "typescript")

    def test_nextjs_project(self, tmp_path: Path) -> None:
        """A Next.js project is detected correctly."""
        package = tmp_path / "package.json"
        package.write_text("""
{
  "name": "my-nextapp",
  "dependencies": {
    "next": "^13.5.0",
    "react": "^18.2.0"
  }
}
""")
        stack = detect_stack(tmp_path)

        assert set(stack.identifiers) == {"javascript", "next", "react"}

    def test_grill_workflow_with_stack_mismatch(self) -> None:
        """Simulate a workflow where grill records a stack and audit detects a change."""
        # Grill recorded Python + FastAPI
        rule_set_grill = RuleSet(
            version="1.0",
            project="test",
            rules=[
                Rule(
                    rule_id="S-Python-R01",
                    domain_id=None,
                    text_short="Python rule",
                    text_body="Use Python.",
                    source="stack-profile",
                    stack_profile="python",
                    status="provisional",
                    verified_date="2026-08-25",
                ),
                Rule(
                    rule_id="S-FastAPI-R01",
                    domain_id=None,
                    text_short="FastAPI rule",
                    text_body="Use FastAPI.",
                    source="stack-profile",
                    stack_profile="fastapi",
                    status="provisional",
                    verified_date="2026-08-25",
                ),
            ],
        )

        grill_stack = grill_stack_from_rule_set(rule_set_grill)
        assert grill_stack == frozenset({"python", "fastapi"})

        # But audit now sees Python + Django
        observed = DetectedStack(
            identifiers=("python", "django"),
            evidence={
                "python": StackEvidence("pyproject.toml", "dep"),
                "django": StackEvidence("pyproject.toml", "django"),
            },
        )

        # This is a mismatch!
        assert stacks_differ(grill_stack, observed)

        # Describe the difference
        diff = describe_stack_difference(grill_stack, observed)
        assert "fastapi" in diff["only_in_grill"]
        assert "django" in diff["only_in_observed"]
