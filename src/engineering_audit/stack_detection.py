"""Detection and extraction of technology stacks from code repositories.

This module provides:
- Detection of tech stacks (language, frameworks) from dependency files
- Extraction of grill-time stacks from RuleSets
- Comparison of stacks to detect mismatches

Stack detection is local file inspection only; no network calls are made.
See ADR 0002 for the decision to bake stack profiles rather than fetch live.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engineering_audit.standards import RuleSet

__all__ = [
    "StackEvidence",
    "DetectedStack",
    "detect_stack",
    "grill_stack_from_rule_set",
    "stacks_differ",
    "describe_stack_difference",
]


# ============================================================================
# Stack identifier normalisation
# ============================================================================
# All stack identifiers are lowercased and use the canonical names defined here.
# Add new frameworks by extending this mapping.

FRAMEWORK_IDENTIFIERS = {
    # Python frameworks
    "fastapi": "fastapi",
    "django": "django",
    "flask": "flask",
    # JavaScript frameworks
    "react": "react",
    "next": "next",
    "next.js": "next",
    "nextjs": "next",
    "vue": "vue",
    "vue.js": "vue",
    "vuejs": "vue",
    "express": "express",
    "svelte": "svelte",
}

LANGUAGE_IDENTIFIERS = {
    # Language detection is based on file extension or package manager
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "js": "javascript",
    "ts": "typescript",
}


def normalise_identifier(identifier: str) -> str | None:
    """Normalise a stack identifier to its canonical form.

    Args:
        identifier: Raw identifier from dependency files (e.g. 'FastAPI', 'React').

    Returns:
        Canonical normalised identifier (lowercase), or None if not recognised.
        Recognised identifiers are languages (python, javascript, typescript)
        and frameworks (fastapi, django, flask, react, next, vue, express, svelte).
    """
    lower_id = identifier.lower().strip()

    # Check frameworks first (more specific)
    if lower_id in FRAMEWORK_IDENTIFIERS:
        return FRAMEWORK_IDENTIFIERS[lower_id]

    # Check languages
    if lower_id in LANGUAGE_IDENTIFIERS:
        return LANGUAGE_IDENTIFIERS[lower_id]

    return None


# ============================================================================
# Stack detection models
# ============================================================================


@dataclass(frozen=True)
class StackEvidence:
    """Evidence for a single stack identifier (e.g. that Python is used).

    Attributes:
        file_path: Relative path to the file from repo root that proves this identifier.
        dependency_or_line: The dependency name or line of code that proves it (e.g.
            'fastapi' in a pyproject.toml [project.dependencies] list, or
            'package.json' for JavaScript).
    """

    file_path: str
    dependency_or_line: str


@dataclass(frozen=True)
class DetectedStack:
    """A detected technology stack from a repository.

    Attributes:
        identifiers: Frozen sorted tuple of normalised stack identifiers
            (lowercase, e.g. ('python', 'fastapi')).
        evidence: Mapping from each identifier to its supporting evidence.
            Keys are identifiers from the identifiers tuple.
    """

    identifiers: tuple[str, ...] = field(default_factory=tuple)
    evidence: dict[str, StackEvidence] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate consistency of identifiers and evidence."""
        # Ensure identifiers are sorted for deterministic comparison
        object.__setattr__(self, "identifiers", tuple(sorted(self.identifiers)))

        # Verify all identifiers have corresponding evidence
        evidence_keys = set(self.evidence.keys())
        identifier_set = set(self.identifiers)
        if evidence_keys != identifier_set:
            missing = identifier_set - evidence_keys
            extra = evidence_keys - identifier_set
            msg = f"Evidence mismatch: missing {missing}, extra {extra}"
            raise ValueError(msg)

    def __bool__(self) -> bool:
        """A stack is truthy if it has identifiers."""
        return bool(self.identifiers)

    def __eq__(self, other: object) -> bool:
        """Compare two stacks."""
        if not isinstance(other, DetectedStack):
            return NotImplemented
        # Compare sorted identifiers for determinism
        return set(self.identifiers) == set(other.identifiers)

    def __hash__(self) -> int:
        """Hash based on sorted identifiers."""
        return hash(frozenset(self.identifiers))


# ============================================================================
# Stack detection from repository
# ============================================================================

# Mapping from package manager dependency names to canonical framework identifiers
_PY_FRAMEWORKS = {
    "fastapi": "fastapi",
    "django": "django",
    "flask": "flask",
}

_JS_FRAMEWORKS = {
    "react": "react",
    "next": "next",
    "next.js": "next",
    "vue": "vue",
    "vue.js": "vue",
    "express": "express",
    "svelte": "svelte",
}


def detect_stack(repo_dir: Path) -> DetectedStack:
    """Detect the technology stack from a repository.

    Inspects dependency and configuration files to identify:
    - Languages: python, javascript, typescript
    - Frameworks: fastapi, django, flask, react, next, vue, express, svelte

    No network access is used. Unreadable or malformed files are skipped
    silently (detection continues). A missing repo_dir returns an empty stack.

    Args:
        repo_dir: Path to the repository root.

    Returns:
        DetectedStack with normalised identifiers and supporting evidence.
    """
    identifiers: dict[str, StackEvidence] = {}

    if not repo_dir.exists() or not repo_dir.is_dir():
        return DetectedStack()

    # Detect Python stack
    py_lang, py_frameworks = _detect_python_stack(repo_dir)
    if py_lang:
        identifiers["python"] = py_lang
    identifiers.update(py_frameworks)

    # Detect JavaScript/TypeScript stack
    js_lang, js_frameworks = _detect_js_stack(repo_dir)
    if js_lang:
        # Add typescript if detected, otherwise javascript
        lang_key = (
            "typescript"
            if "typescript" in js_lang.dependency_or_line.lower()
            else "javascript"
        )
        identifiers[lang_key] = js_lang
    identifiers.update(js_frameworks)

    # Convert to sorted tuple
    sorted_ids = tuple(sorted(identifiers.keys()))
    return DetectedStack(
        identifiers=sorted_ids,
        evidence={k: v for k, v in identifiers.items()},
    )


def _detect_python_stack(
    repo_dir: Path,
) -> tuple[StackEvidence | None, dict[str, StackEvidence]]:
    """Detect Python language and frameworks.

    Returns:
        Tuple of (language_evidence, frameworks_dict).
        language_evidence is None if Python is not detected.
        frameworks_dict maps framework names to their evidence.
    """
    python_evidence = None
    frameworks = {}

    # Check pyproject.toml (PEP 621 and Poetry)
    pyproject = repo_dir / "pyproject.toml"
    if pyproject.exists():
        try:
            with open(pyproject, "rb") as f:
                data = tomllib.load(f)

            # Detect Python from presence
            python_evidence = StackEvidence(
                file_path="pyproject.toml",
                dependency_or_line="[project] or [tool.poetry.dependencies]",
            )

            # Extract dependencies
            deps = set()
            if "project" in data and "dependencies" in data["project"]:
                for dep_line in data["project"]["dependencies"]:
                    # Extract package name (before version specifier)
                    pkg_name = (
                        dep_line.split("[")[0]
                        .split(">=")[0]
                        .split("==")[0]
                        .split("<")[0]
                        .strip()
                    )
                    deps.add(pkg_name.lower())

            if (
                "tool" in data
                and "poetry" in data["tool"]
                and "dependencies" in data["tool"]["poetry"]
            ):
                for pkg_name in data["tool"]["poetry"]["dependencies"]:
                    deps.add(pkg_name.lower())

            # Map to frameworks
            for dep in deps:
                if dep in _PY_FRAMEWORKS:
                    fw = _PY_FRAMEWORKS[dep]
                    frameworks[fw] = StackEvidence(
                        file_path="pyproject.toml",
                        dependency_or_line=dep,
                    )
        except Exception:
            # Skip on any parse error
            pass

    # Check requirements*.txt
    if not python_evidence:
        req_files = list(repo_dir.glob("requirements*.txt"))
        if req_files:
            python_evidence = StackEvidence(
                file_path=req_files[0].name,
                dependency_or_line="dependency declaration",
            )

            # Extract dependencies from all requirements files
            for req_file in req_files:
                try:
                    content = req_file.read_text(encoding="utf-8")
                    for line in content.splitlines():
                        line = line.strip()
                        if line and not line.startswith("#"):
                            # Extract package name
                            pkg_name = (
                                line.split("[")[0]
                                .split(">=")[0]
                                .split("==")[0]
                                .split("<")[0]
                                .split(">")[0]
                                .strip()
                            )
                            if pkg_name:
                                pkg_name_lower = pkg_name.lower()
                                if pkg_name_lower in _PY_FRAMEWORKS:
                                    fw = _PY_FRAMEWORKS[pkg_name_lower]
                                    frameworks[fw] = StackEvidence(
                                        file_path=req_file.name,
                                        dependency_or_line=pkg_name_lower,
                                    )
                except Exception:
                    pass

    # Check setup.cfg
    setup_cfg = repo_dir / "setup.cfg"
    if setup_cfg.exists():
        try:
            import configparser

            config = configparser.ConfigParser()
            config.read(setup_cfg)

            if not python_evidence and "metadata" in config:
                python_evidence = StackEvidence(
                    file_path="setup.cfg",
                    dependency_or_line="[metadata] or [options]",
                )

            if "options" in config and "install_requires" in config["options"]:
                deps_str = config["options"]["install_requires"]
                for dep_line in deps_str.split("\n"):
                    dep_line = dep_line.strip()
                    if dep_line:
                        pkg_name = (
                            dep_line.split("[")[0]
                            .split(">=")[0]
                            .split("==")[0]
                            .split("<")[0]
                            .strip()
                        )
                        if pkg_name:
                            pkg_name_lower = pkg_name.lower()
                            if pkg_name_lower in _PY_FRAMEWORKS:
                                fw = _PY_FRAMEWORKS[pkg_name_lower]
                                frameworks[fw] = StackEvidence(
                                    file_path="setup.cfg",
                                    dependency_or_line=pkg_name_lower,
                                )
        except Exception:
            pass

    # Check Pipfile
    pipfile = repo_dir / "Pipfile"
    if pipfile.exists():
        try:
            with open(pipfile, "rb") as f:
                data = tomllib.load(f)

            if not python_evidence:
                python_evidence = StackEvidence(
                    file_path="Pipfile",
                    dependency_or_line="[packages]",
                )

            # Extract from [packages] section
            if "packages" in data:
                for pkg_name in data["packages"]:
                    pkg_name_lower = pkg_name.lower()
                    if pkg_name_lower in _PY_FRAMEWORKS:
                        fw = _PY_FRAMEWORKS[pkg_name_lower]
                        frameworks[fw] = StackEvidence(
                            file_path="Pipfile",
                            dependency_or_line=pkg_name_lower,
                        )
        except Exception:
            pass

    return python_evidence, frameworks


def _detect_js_stack(
    repo_dir: Path,
) -> tuple[StackEvidence | None, dict[str, StackEvidence]]:
    """Detect JavaScript/TypeScript language and frameworks.

    Returns:
        Tuple of (language_evidence, frameworks_dict).
        language_evidence is None if JavaScript/TypeScript is not detected.
        frameworks_dict maps framework names to their evidence.
    """
    js_evidence = None
    frameworks = {}

    package_json = repo_dir / "package.json"
    if not package_json.exists():
        return None, {}

    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except Exception:
        return None, {}

    # Detect JavaScript from presence of package.json
    all_deps = {}

    # Merge dependencies and devDependencies
    if "dependencies" in data and isinstance(data["dependencies"], dict):
        all_deps.update(data["dependencies"])
    if "devDependencies" in data and isinstance(data["devDependencies"], dict):
        all_deps.update(data["devDependencies"])

    # Check for TypeScript
    if "typescript" in all_deps:
        js_evidence = StackEvidence(
            file_path="package.json",
            dependency_or_line="typescript",
        )
    else:
        js_evidence = StackEvidence(
            file_path="package.json",
            dependency_or_line="dependencies",
        )

    # Extract frameworks
    for pkg_name in all_deps:
        pkg_name_lower = pkg_name.lower()
        if pkg_name_lower in _JS_FRAMEWORKS:
            fw = _JS_FRAMEWORKS[pkg_name_lower]
            frameworks[fw] = StackEvidence(
                file_path="package.json",
                dependency_or_line=pkg_name_lower,
            )

    return js_evidence, frameworks


# ============================================================================
# Stack extraction from RuleSet (grill-time stack)
# ============================================================================


def grill_stack_from_rule_set(rule_set: RuleSet) -> frozenset[str]:
    """Extract the grill-time stack from a RuleSet.

    Collects all stack_profile values from rules where source is 'stack-profile'.
    Returns normalised identifiers matching the form used by detect_stack().

    The returned frozenset is unordered but hashable, suitable for set
    operations. Convert to sorted tuple if deterministic output is needed.

    Args:
        rule_set: The RuleSet to extract stacks from.

    Returns:
        Frozenset of normalised stack profile identifiers (e.g. frozenset({'python', 'fastapi'})).
        Empty frozenset if no stack-profile rules exist.
    """
    identifiers = set()

    for rule in rule_set.rules:
        if rule.source == "stack-profile" and rule.stack_profile:
            normalised = normalise_identifier(rule.stack_profile)
            if normalised:
                identifiers.add(normalised)

    return frozenset(identifiers)


# ============================================================================
# Stack comparison
# ============================================================================


def stacks_differ(
    grill: frozenset[str] | tuple[str, ...], observed: DetectedStack
) -> bool:
    """Compare grill-time stack to observed stack.

    Returns True if they differ. Returns False if they match or if the grill
    stack is empty (no stack-profile rules exist yet).

    The edge case: if grill is empty (common today, since stack_profile field
    is not yet populated), this returns False. An empty grill stack is never
    a mismatch, because there was no prior stack commitment to contradict.

    Args:
        grill: Frozenset or tuple of normalised stack identifiers from RuleSet.
        observed: DetectedStack from detect_stack().

    Returns:
        True if the stacks differ (and grill is non-empty), False otherwise.
    """
    if not grill:
        # Empty grill stack is never a mismatch
        return False

    grill_set = set(grill)
    observed_set = set(observed.identifiers)

    return grill_set != observed_set


def describe_stack_difference(
    grill: frozenset[str] | tuple[str, ...],
    observed: DetectedStack,
) -> dict[str, Any]:
    """Describe the difference between grill and observed stacks.

    Returns a dict with:
    - grill: List of stack identifiers from grill
    - observed: List of stack identifiers from observed
    - only_in_grill: Identifiers in grill but not in observed
    - only_in_observed: Identifiers in observed but not in grill

    If stacks are equal, only_in_grill and only_in_observed are empty lists.
    If grill is empty, grill is an empty list and only_in_grill is empty.

    Args:
        grill: Frozenset or tuple of normalised stack identifiers from RuleSet.
        observed: DetectedStack from detect_stack().

    Returns:
        Dict describing the difference with the keys listed above.
    """
    grill_set = set(grill)
    observed_set = set(observed.identifiers)

    return {
        "grill": sorted(grill_set),
        "observed": sorted(observed_set),
        "only_in_grill": sorted(grill_set - observed_set),
        "only_in_observed": sorted(observed_set - grill_set),
    }
