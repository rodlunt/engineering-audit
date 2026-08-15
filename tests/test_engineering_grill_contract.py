"""Regression checks for the Engineering Grill's interview contract.

These checks read the integration documents as structured prose.  They use
headings, field names, and short semantic markers instead of asserting whole
paragraphs, so useful wording changes do not make the tests brittle.
"""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent
GRILL_ROOT = REPO_ROOT / "integrations" / "engineering-grill"
SKILL_PATH = GRILL_ROOT / "engineering-grill" / "SKILL.md"
FORMATS_PATH = (
    GRILL_ROOT / "engineering-grill" / "references" / "documentation-formats.md"
)
README_PATH = GRILL_ROOT / "README.md"

SKILL = SKILL_PATH.read_text(encoding="utf-8")
FORMATS = FORMATS_PATH.read_text(encoding="utf-8")
README = README_PATH.read_text(encoding="utf-8")


def _section(document: str, heading: str) -> str:
    """Return one level-two section, including its content but not the next section."""

    match = re.search(
        rf"^##\s+{re.escape(heading)}\s*$((?:(?!^##\s).)*)",
        document,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"expected a level-two section named {heading!r}"
    return match.group(1)


def _first_match(document: str, patterns: tuple[str, ...], label: str) -> re.Match[str]:
    """Return the earliest semantic marker matching one of ``patterns``."""

    matches = [
        re.search(pattern, document, flags=re.IGNORECASE | re.DOTALL)
        for pattern in patterns
    ]
    found = [match for match in matches if match is not None]
    assert found, f"{label} marker is missing"
    return min(found, key=lambda match: match.start())


def test_grill_keeps_all_four_domain_classifications() -> None:
    triage = _section(SKILL, "Triage every returned domain")

    states = re.findall(r"^[-*]\s+\*\*([^*]+)\*\*:", triage, flags=re.MULTILINE)
    assert states == ["active-now", "required-later", "not-applicable", "unknown"], (
        "the triage section must define exactly the four decision states, in order"
    )

    checkpoint = _section(FORMATS, "Checkpoint and recovery")
    classification = re.search(
        r"^\s+classification:\s*(.+)$", checkpoint, flags=re.MULTILINE
    )
    assert classification is not None, (
        "the checkpoint must record domain classification"
    )
    assert [part.strip() for part in classification.group(1).split("|")] == [
        "active-now",
        "required-later",
        "not-applicable",
        "unknown",
    ], "availability must not become a fifth domain classification"
    assert re.search(
        r"^\s+availability:\s*(?:available\s*\|\s*unavailable|.+)$",
        checkpoint,
        flags=re.MULTILINE | re.IGNORECASE,
    ), (
        "domain availability must be recorded separately from its four-state "
        "classification"
    )


def test_cost_preview_has_an_initial_and_a_refined_stage() -> None:
    initial_preview = _first_match(
        SKILL,
        (
            r"(?:initial|rough|preliminary|provisional|first).{0,120}"
            r"(?:cost|question\s+cost|scope\s+preview).{0,120}"
            r"(?:before|prior\s+to).{0,80}(?:read|load|fetch|get_domain)",
            r"(?:before|prior\s+to).{0,80}"
            r"(?:read|load|fetch|get_domain).{0,120}"
            r"(?:initial|rough|preliminary|provisional).{0,80}"
            r"(?:cost|preview)",
            r"before\s+any\s+active-domain\s+`get_domain`\s+call,"
            r".{0,120}(?:provisional\s+scope\s+preview|question\s+turns)",
        ),
        "initial cost preview",
    )
    refined_preview = _first_match(
        SKILL,
        (
            r"(?:refined|updated|revised|final).{0,120}"
            r"(?:cost|question\s+cost)",
            r"(?:cost|question\s+cost).{0,120}"
            r"(?:refined|updated|revised|final).{0,120}"
            r"(?:before|prior\s+to).{0,80}(?:question|asking)",
            r"after\s+approval.{0,220}before\s+the\s+first\s+deep\s+question",
            r"before\s+the\s+first\s+deep\s+question.{0,180}"
            r"(?:updated\s+range|provisional\s+preview)",
        ),
        "refined cost preview",
    )
    first_question = _first_match(
        SKILL,
        (
            r"\bformat\s+each\s+question\s+like\s+this\b",
            r"\bask\s+exactly\s+one\s+decision\s+question\b",
        ),
        "first-question boundary",
    )

    assert initial_preview.start() < refined_preview.start() < first_question.start(), (
        "the initial cost preview must precede the refined preview, and both must "
        "precede deep questioning"
    )


def test_dependency_readiness_precedes_risk_ordering() -> None:
    dependency_readiness = _first_match(
        SKILL,
        (
            r"\bdependency\s+readiness\b",
            r"\breadiness\s+of\s+(?:the\s+)?dependencies\b",
        ),
        "dependency readiness",
    )
    risk_ordering = _first_match(
        SKILL,
        (
            r"\brisk\s+ordering\b",
            r"\brisk\s+(?:ranking|triage)\b",
            r"\border(?:ing|ed)\s+(?:the\s+)?risks\b",
            r"\brank\s+first\s+by\s+reversibility\b",
        ),
        "risk ordering",
    )

    assert dependency_readiness.start() < risk_ordering.start(), (
        "dependency readiness must be established before risks are ordered"
    )


def test_risk_ordering_uses_reversibility_and_blast_radius() -> None:
    risk_ordering = _first_match(
        SKILL,
        (
            r"\brisk\s+ordering\b",
            r"\brisk\s+(?:ranking|triage)\b",
            r"\border(?:ing|ed)\s+(?:the\s+)?risks\b",
            r"\brank\s+first\s+by\s+reversibility\b",
        ),
        "risk ordering",
    )
    risk_context = SKILL[risk_ordering.start() :]

    assert re.search(r"\breversib", risk_context, flags=re.IGNORECASE), (
        "risk ordering must compare how reversible a decision is"
    )
    assert re.search(r"\bblast[_ ]radius\b", risk_context, flags=re.IGNORECASE), (
        "risk ordering must compare blast radius"
    )


def test_each_turn_allows_exactly_one_decision_question() -> None:
    assert re.search(
        r"(?:\b(?:ask|allow|require|have)\s+exactly\s+one\s+"
        r"(?:decision\s+)?question\b|"
        r"\bexactly\s+one\s+(?:decision\s+)?question\b.{0,80}"
        r"(?:is\s+)?asked)\s+per\s+(?:user-facing\s+)?turn\b",
        SKILL,
        flags=re.IGNORECASE | re.DOTALL,
    ), "the interview must require exactly one decision question per turn"


def test_generic_questions_and_count_padding_are_rejected() -> None:
    interview = _section(SKILL, "Interview through a design tree")

    assert re.search(r"\bgeneric\b", interview, flags=re.IGNORECASE), (
        "the interview guidance must identify generic questions as invalid"
    )
    assert re.search(
        r"(?:generic|any\s+project).{0,180}"
        r"(?:filler|reject|invalid|not\s+a\s+question|avoid)"
        r"|(?:filler|reject|invalid|not\s+a\s+question|avoid).{0,180}"
        r"(?:generic|any\s+project)",
        interview,
        flags=re.IGNORECASE | re.DOTALL,
    ), "generic questions must be rejected as filler rather than treated as coverage"
    assert re.search(r"\bpad(?:ding)?\b", interview, flags=re.IGNORECASE), (
        "the interview guidance must forbid count-padding questions"
    )
    assert re.search(
        r"(?:never|do\s+not|don't).{0,80}\bpad(?:ding)?\b"
        r"|\bpad(?:ding)?\b.{0,80}(?:number|count|target)",
        interview,
        flags=re.IGNORECASE | re.DOTALL,
    ), "count-padding must be explicitly rejected"


def test_question_accounting_names_states_and_conservation_rules() -> None:
    interview = _section(SKILL, "Interview through a design tree")
    ledger = re.search(
        r"^###\s+Question ledger\s*$((?:(?!^###\s).)*)",
        interview,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert ledger is not None, "the interview must define a question ledger"
    for state in ("DERIVED", "ASKED", "ANSWERED", "DEFERRED", "NOT ASKED"):
        assert state in ledger.group(1), (
            f"the question ledger must define the {state} state"
        )

    sources = {
        "SKILL.md": SKILL,
        "documentation-formats.md": FORMATS,
        "README.md": README,
    }
    state_pattern = re.compile(
        r"\bderived\b.*\basked\b.*\banswered\b.*\bdeferred\b"
        r".*\bnot[_\s]+asked\b",
        flags=re.IGNORECASE | re.DOTALL,
    )
    state_sources = [
        name for name, text in sources.items() if state_pattern.search(text)
    ]
    assert state_sources, (
        "the Grill record must name derived, asked, answered, deferred, and not "
        "asked states"
    )

    accounting_text = "\n".join(sources[name] for name in state_sources)
    assert re.search(
        r"\basked\b\s*=\s*\banswered\b\s*\+\s*\bdeferred\b",
        accounting_text,
        flags=re.IGNORECASE,
    ), "question accounting must state asked = answered + deferred"
    assert re.search(
        r"\bderived\b\s*=\s*\basked\b\s*\+\s*\bnot(?:\s+|_)asked\b",
        accounting_text,
        flags=re.IGNORECASE,
    ), "question accounting must state derived = asked + not asked"
    assert "asked = answered + deferred" in FORMATS
    assert "derived = asked + not_asked" in FORMATS

    question_accounting = _section(FORMATS, "Question accounting")
    generic_filter = _first_match(
        question_accounting,
        (
            r"generic[- ]question\s+filter",
            r"generic\s+candidates?.{0,120}(?:removed|excluded|not\s+derived)",
        ),
        "generic-question filter",
    )
    derived_definition = _first_match(
        question_accounting,
        (r"`?derived`?\s+is\s+the\s+number",),
        "derived-count definition",
    )
    assert generic_filter.start() < derived_definition.start(), (
        "generic candidates must be excluded before the derived count is defined"
    )
    assert re.search(
        r"(?:removed|excluded).{0,80}(?:not\s+derived|do\s+not\s+enter)",
        question_accounting,
        flags=re.IGNORECASE | re.DOTALL,
    ), "generic candidates must not enter the derived question count"


def test_documentation_formats_define_the_checkpoint_schema() -> None:
    checkpoint = _section(FORMATS, "Checkpoint and recovery")
    fenced_blocks = re.findall(r"```[^\n]*\n(.*?)```", checkpoint, flags=re.DOTALL)
    assert fenced_blocks, (
        "the interview checkpoint must provide a concrete schema block"
    )
    schema = "\n".join(fenced_blocks)

    required_fields = (
        ("status", "checkpoint status"),
        ("checkpoint_kind", "checkpoint kind"),
        ("exit_reason", "exit reason"),
        ("round", "round number"),
        ("time", "timestamp"),
        ("resume_marker", "resume marker"),
        ("fact_map_summary", "fact map"),
        ("current_design_tree_frontier", "design-tree frontier"),
        ("every_returned_domain", "domain classifications"),
        ("fully_read_domains", "domains whose full documents were read"),
        ("deferred_triggers", "deferred triggers"),
        ("open_questions", "open questions"),
        ("question_ledger", "question ledger"),
        ("next_frontier", "next frontier"),
        ("framework_state", "framework state"),
        ("framework_source", "framework source"),
        ("skipped_files", "skipped files"),
        ("question_accounting", "question accounting"),
    )
    for field, label in required_fields:
        assert re.search(rf"^{re.escape(field)}:", schema, flags=re.MULTILINE), (
            f"checkpoint schema is missing {label}: {field}"
        )

    for field in ("derived", "asked", "answered", "deferred", "not_asked"):
        assert re.search(rf"^\s+{re.escape(field)}:", schema, flags=re.MULTILINE), (
            f"question accounting is missing the {field} count"
        )
    assert re.search(
        r"^\s+availability:\s*(?:available\s*\|\s*unavailable|.+)$",
        schema,
        flags=re.MULTILINE | re.IGNORECASE,
    ), "checkpoint schema must keep domain availability separate from classification"

    frontier = re.search(
        r"^current_design_tree_frontier:\s*$((?:(?!^[a-z][a-z0-9_]*:).)*)",
        schema,
        flags=re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    assert frontier is not None, (
        "checkpoint schema must include the current design-tree frontier block"
    )
    assert re.search(
        r"^\s+reversibility:\s*"
        r"irreversible-once-shipped\s*\|\s*expensive-to-change\s*\|\s*cheap-to-change$",
        frontier.group(1),
        flags=re.MULTILINE,
    ), "frontier reversibility must use the skill's exact values"
    assert re.search(
        r"^\s+blast_radius:\s*<plain-language",
        frontier.group(1),
        flags=re.MULTILINE,
    ), "frontier blast radius must remain project-specific plain language"

    next_frontier = re.search(
        r"^next_frontier:\s*$((?:(?!^[a-z][a-z0-9_]*:).)*)",
        schema,
        flags=re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    assert next_frontier is not None, (
        "checkpoint schema must include the next frontier block"
    )
    assert re.search(
        r"^\s+prerequisites_satisfied:",
        next_frontier.group(1),
        flags=re.MULTILINE,
    ), "ready frontier entries must name the prerequisites that are satisfied"
    assert not re.search(
        r"^\s+prerequisites:",
        next_frontier.group(1),
        flags=re.MULTILINE,
    ), "ready frontier entries must not list unresolved prerequisites"

    ledger = re.search(
        r"^question_ledger:\s*$((?:(?!^[a-z][a-z0-9_]*:).)*)",
        schema,
        flags=re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    assert ledger is not None, (
        "checkpoint schema must include the question ledger block"
    )
    ledger_fields = set(
        re.findall(
            r"^\s{2,}(?:-\s+)?([a-z][a-z0-9_]*):",
            ledger.group(1),
            flags=re.MULTILINE | re.IGNORECASE,
        )
    )
    required_ledger_fields = {
        "id",
        "domain_id",
        "rule_ids",
        "question",
        "prerequisite",
        "reversibility",
        "blast_radius",
        "current_state",
        "outcome_or_reason",
        "revisit_trigger",
    }
    assert required_ledger_fields <= ledger_fields, (
        "question ledger is missing fields: "
        + ", ".join(sorted(required_ledger_fields - ledger_fields))
    )

    current_state = re.search(
        r"^\s+current_state:\s*(.+)$", ledger.group(1), flags=re.MULTILINE
    )
    assert current_state is not None, "question ledger must define current_state"
    assert [part.strip() for part in current_state.group(1).split("|")] == [
        "ANSWERED",
        "DEFERRED",
        "NOT ASKED",
    ], "persisted current_state must exclude transient ASKED"
    assert not re.search(
        r"^\s+status:\s*OPEN\b",
        ledger.group(1),
        flags=re.MULTILINE | re.IGNORECASE,
    ), "OPEN must remain recovery prose, not a question ledger state"

    assert re.search(
        r"\bASKED\b.{0,80}\btransient\b.{0,80}\bnever\s+persisted\b",
        checkpoint,
        flags=re.IGNORECASE | re.DOTALL,
    ), "checkpoint prose must say that ASKED is transient and never persisted"
    assert re.search(
        r"\bNOT\s+ASKED\b.{0,100}\bcan\s+later\s+be\s+shown\b"
        r".{0,80}\blive\s+session\b",
        checkpoint,
        flags=re.IGNORECASE | re.DOTALL,
    ), "checkpoint prose must allow NOT ASKED to become shown during a live session"
    assert not re.search(
        r"\bNOT\s+ASKED\b.{0,120}\b(?:terminal|final)\b.{0,120}"
        r"\b(?:live|checkpoint)\b",
        checkpoint,
        flags=re.IGNORECASE | re.DOTALL,
    ), "NOT ASKED must not be described as terminal during a live checkpoint"


def test_checkpoint_draft_fallback_shows_counts_before_location_question() -> None:
    recovery = _section(SKILL, "Checkpoints and recovery")

    location_question = re.search(
        r"After the first confirmed\s+decision,\s+ask exactly one location question",
        recovery,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert location_question is not None, (
        "the no-location fallback must ask one location question after the first decision"
    )
    assert recovery.lower().count("ask exactly one location question") == 1

    before_location = recovery[: location_question.start()]
    assert re.search(
        r"the conversation draft.*?full\s+compact\s+accounting\s+summary.*?"
        r"before any save-location question and before any next deep question",
        before_location,
        flags=re.IGNORECASE | re.DOTALL,
    ), "the draft checkpoint summary must precede location and deep questions"
    for field in ("Derived", "Asked", "Answered", "Deferred", "Not asked"):
        assert field in before_location, (
            f"the draft checkpoint must show the {field} count before asking for a location"
        )
    assert "The draft is a checkpoint even though it is not yet on disk." in recovery
    assert "Do not combine the location question with" in recovery
    assert re.search(
        r"Once the user supplies\s+a\s+location,\s+write or update the checkpoint there\s+and continue",
        recovery,
        flags=re.IGNORECASE,
    ), "the workflow must save the draft after the user supplies a location"

    guide = _section(README, "Progress and stopping")
    assert re.search(
        r"full checkpoint in the conversation\s+draft.*?shows these counts before asking where",
        guide,
        flags=re.IGNORECASE | re.DOTALL,
    ), "the public guide must describe the same draft-first fallback"


def test_framework_access_is_read_only_and_lifecycle_tools_are_forbidden() -> None:
    load_framework = _section(SKILL, "Load the live framework")
    assert re.search(
        r"use\s+only\s+`list_domains`\s+and\s+`get_domain`",
        load_framework,
        flags=re.IGNORECASE,
    ), "the Grill must restrict framework access to list_domains and get_domain"

    lifecycle_tools = (
        "begin_run",
        "start_config",
        "get_config",
        "record_domain_result",
        "file_issues",
        "render_report",
    )
    forbidden_lines = [
        line
        for line in load_framework.splitlines()
        if "Never call audit lifecycle tools" in line
        or "must not start" in line
        or "must not" in line
    ]
    forbidden_text = "\n".join(forbidden_lines)
    for tool in lifecycle_tools:
        assert tool in forbidden_text, (
            f"the Grill must forbid the lifecycle tool {tool}"
        )

    positive_calls = [
        line.strip()
        for line in load_framework.splitlines()
        if any(f"`{tool}`" in line for tool in lifecycle_tools)
        and not re.search(
            r"\b(?:never|must\s+not|do\s+not)\b", line, flags=re.IGNORECASE
        )
    ]
    assert not positive_calls, (
        "the framework-loading instructions must not positively call audit lifecycle "
        "tools: " + "; ".join(positive_calls)
    )


def test_readme_describes_the_same_four_domain_states() -> None:
    for phrase in ("active now", "required later", "not applicable", "unknown"):
        assert phrase in README.lower(), (
            f"the public Grill guide must explain the {phrase!r} domain state"
        )
