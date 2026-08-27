"""Pure-logic layer for standards integration.

This module coordinates the rendering and writing of the three standards
documents, merging prior rule sets with new audit verdicts, and managing
the approval workflow. All functions are pure (no I/O side effects) except
where explicitly documented.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from engineering_audit.managed_blocks import (
    get_managed_block_closing_marker,
    get_managed_block_opening_marker,
    write_managed_block,
)
from engineering_audit.rendering import (
    render_agent_standard,
    render_human_standard,
    render_policy,
)
from engineering_audit.rules import get_domain_text
from engineering_audit.schema import DomainResult
from engineering_audit.standards import Rule, RuleSet, RuleStatus
from engineering_audit.standards_approval import DiffModel, SummaryCount

if TYPE_CHECKING:
    from engineering_audit.rules import RulesPack

__all__ = [
    "AGENT_STANDARD_FILENAME",
    "HUMAN_STANDARD_FILENAME",
    "ENGINEERING_POLICY_FILENAME",
    "RULE_SET_FILENAME",
    "verdicts_from_domain_results",
    "audit_rules_from_domain_results",
    "load_prior_rule_set",
    "derive_summary_counts",
    "build_diffs",
    "render_all",
    "write_standards",
]

# Module constants for document filenames
# Three standards documents go in docs/ subdirectory of audited project root
AGENT_STANDARD_FILENAME = "coding-standard.agent.md"
HUMAN_STANDARD_FILENAME = "engineering-standard.md"
ENGINEERING_POLICY_FILENAME = "engineering-policy.md"
# Rule set stays in deliverables directory
RULE_SET_FILENAME = "rule-set.json"

# Document IDs (from managed_blocks.py titles mapping)
_AGENT_STANDARD_ID = "agent-standard"
_HUMAN_STANDARD_ID = "human-standard"
_ENGINEERING_POLICY_ID = "engineering-policy"

# Mapping from document IDs to filenames
_DOCUMENT_ID_TO_FILENAME = {
    _AGENT_STANDARD_ID: AGENT_STANDARD_FILENAME,
    _HUMAN_STANDARD_ID: HUMAN_STANDARD_FILENAME,
    _ENGINEERING_POLICY_ID: ENGINEERING_POLICY_FILENAME,
}


def _extract_content_from_managed_block(rendered: str, block_id: str) -> str:
    """Extract content from between managed-block markers.

    Args:
        rendered: Rendered markdown with managed-block markers.
        block_id: The block identifier to match.

    Returns:
        Content from between the opening and closing markers.
    """
    opening_marker = get_managed_block_opening_marker(block_id)
    closing_marker = get_managed_block_closing_marker()

    opening_pattern = re.escape(opening_marker)
    closing_pattern = re.escape(closing_marker)

    match = re.search(
        f"{opening_pattern}\n(.*?)\n{closing_pattern}",
        rendered,
        re.DOTALL,
    )

    if match:
        return match.group(1)
    return rendered


def _extract_rule_body(domain_text: str, rule_id: str) -> str:
    """Extract the body text for a specific rule from domain markdown.

    Given the full markdown for a domain and a rule_id, returns the text
    between the rule's heading (### label. title) and either the next
    rule heading or the end of the document.

    Args:
        domain_text: Full markdown text for the domain.
        rule_id: The rule ID to extract (e.g., "D01-R01").

    Returns:
        Rule body text (empty string if rule not found).
    """
    # Find the rule id in the text, looking for the pattern "Rule id: <rule_id>"
    # within a rule block (between ### headings)
    rule_id_pattern = re.compile(
        rf"Rule\s+id:\s*{re.escape(rule_id)}",
        re.IGNORECASE,
    )

    rule_id_match = rule_id_pattern.search(domain_text)
    if not rule_id_match:
        return ""

    # Find the rule heading that precedes this rule id
    # A rule heading is "### <label>. <title>"
    rule_heading_pattern = re.compile(r"^###\s+[A-Za-z]*\d+\.\s+(.+?)$", re.MULTILINE)

    # Find all headings before the rule id
    headings_before = []
    for match in rule_heading_pattern.finditer(domain_text):
        if match.start() < rule_id_match.start():
            headings_before.append(match)

    if not headings_before:
        return ""

    # The rule we want starts at the last heading before the rule id
    rule_heading_match = headings_before[-1]

    # Find the text between this heading's end and the next ### heading
    text_after_heading = domain_text[rule_heading_match.end() :]
    next_heading_match = rule_heading_pattern.search(text_after_heading)

    if next_heading_match:
        # There is another heading after this rule; extract up to it
        rule_body = text_after_heading[: next_heading_match.start()]
    else:
        # This is the last rule; extract to the end
        rule_body = text_after_heading

    # Strip leading/trailing whitespace but preserve internal structure
    return rule_body.strip()


def verdicts_from_domain_results(
    domain_results: Mapping[str, DomainResult],
) -> dict[str, str]:
    """Flatten every RuleVerdict into a rule_id -> verdict string dict.

    Args:
        domain_results: Mapping of domain_id -> DomainResult.

    Returns:
        Dictionary mapping rule_id to verdict string ("pass", "finding",
        "not-applicable", "could-not-evaluate").

    Raises:
        ValueError: If the same rule_id appears in multiple domains.
    """
    verdicts: dict[str, str] = {}

    for domain_result in domain_results.values():
        for rule_verdict in domain_result.rule_verdicts:
            rule_id = rule_verdict.rule_id
            if rule_id in verdicts:
                raise ValueError(
                    f"duplicate rule_id '{rule_id}' across domains; "
                    f"each rule id must appear in only one domain"
                )
            verdicts[rule_id] = rule_verdict.verdict.value

    return verdicts


def audit_rules_from_domain_results(
    domain_results: Mapping[str, DomainResult], rules_pack: RulesPack | None = None
) -> dict[str, Rule]:
    """Build dict[rule_id -> Rule] for audited rules from domain results.

    Maps verdicts to rule statuses:
    - pass -> verified-pass
    - finding -> verified-finding (with finding_details from Finding objects)
    - not-applicable -> verified-not-applicable
    - could-not-evaluate -> provisional

    Args:
        domain_results: Mapping of domain_id -> DomainResult.
        rules_pack: RulesPack to look up rule text and domain. If None, text
            fields will be empty and domain_id will not be populated.

    Returns:
        Dictionary mapping rule_id to Rule objects with status, severity,
        and finding_details populated from audit data.
    """
    audit_rules: dict[str, Rule] = {}
    today = datetime.now().date().isoformat()

    # Build a map of rule_id -> Finding for quick lookup
    findings_by_rule_id: dict[str, object] = {}
    for domain_result in domain_results.values():
        for finding in domain_result.findings:
            findings_by_rule_id[finding.rule_id] = finding

    for domain_result in domain_results.values():
        domain_id = domain_result.domain_id

        for rule_verdict in domain_result.rule_verdicts:
            rule_id = rule_verdict.rule_id
            verdict_str = rule_verdict.verdict.value

            # Look up rule text from rules_pack if available
            text_short = ""
            text_body = ""
            rule_domain_id: str | None = None
            source = "rules-pack"
            severity: str | None = None

            if rules_pack:
                domain = rules_pack.get_domain(domain_id)
                if domain:
                    rule_domain_id = domain.id

                    for pack_rule in domain.rules:
                        if pack_rule.id == rule_id:
                            text_short = pack_rule.title
                            # Extract rule body from domain markdown
                            # (includes the full citation/source from the pack)
                            try:
                                domain_text = get_domain_text(domain)
                                text_body = _extract_rule_body(domain_text, rule_id)
                            except (AttributeError, OSError):
                                # Domain text not available (e.g., in mocks or file errors)
                                text_body = ""
                            # Note: pack_rule.source contains the citation;
                            # source field in Rule model is just "rules-pack"
                            # Use severity from pack if available (volatility in pack)
                            if (
                                hasattr(pack_rule, "volatility")
                                and pack_rule.volatility
                            ):
                                # Map volatility to severity if needed
                                # For now, use it directly if finding
                                pass
                            break

            # Map verdict to status
            status = "provisional"  # default
            finding_details: dict[str, object] | None = None

            if verdict_str == "pass":
                status = RuleStatus.VERIFIED_PASS.value
            elif verdict_str == "finding":
                status = RuleStatus.VERIFIED_FINDING.value
                # Look up finding details
                if rule_id in findings_by_rule_id:
                    finding = findings_by_rule_id[rule_id]  # type: ignore
                    severity = finding.severity.value  # type: ignore
                    finding_details = {
                        "issue_title": finding.issue_title,  # type: ignore
                        "issue_body": finding.issue_body,  # type: ignore
                        "path": finding.location,  # type: ignore
                        "precondition": finding.precondition,  # type: ignore
                    }
                else:
                    # No finding data; use default severity
                    severity = "medium"
                    finding_details = {}
            elif verdict_str == "not-applicable":
                status = RuleStatus.VERIFIED_NOT_APPLICABLE.value
            elif verdict_str == "could-not-evaluate":
                status = RuleStatus.PROVISIONAL.value

            # Create Rule object
            rule = Rule(
                rule_id=rule_id,
                domain_id=rule_domain_id,
                text_short=text_short,
                text_body=text_body,
                source=source,
                status=status,
                verified_date=today,
                severity=severity,
                finding_details=finding_details,
            )

            audit_rules[rule_id] = rule

    return audit_rules


def load_prior_rule_set(deliverables_dir: Path) -> RuleSet | None:
    """Load prior rule set from deliverables directory.

    Args:
        deliverables_dir: Path to directory containing rule-set.json.

    Returns:
        RuleSet if file exists, None if file is absent.

    Raises:
        ValueError: If file exists but is corrupted or does not match schema.
    """
    rule_set_path = deliverables_dir / RULE_SET_FILENAME
    if not rule_set_path.exists():
        return None
    return RuleSet.load(rule_set_path)


def derive_summary_counts(prior: RuleSet | None, merged: RuleSet) -> SummaryCount:
    """Derive summary counts comparing prior and merged rule sets.

    Counts:
    - new_rules: Rules in merged not in prior (all if prior is None)
    - upgraded_to_verified: Rules that were provisional in prior and are
      verified-* in merged
    - findings_recorded: Merged rules with status verified-finding
    - not_applicable: Merged rules with status verified-not-applicable

    Args:
        prior: Prior rule set, or None if this is the first run.
        merged: New merged rule set.

    Returns:
        SummaryCount with the four counts.
    """
    prior_rule_ids = {rule.rule_id for rule in prior.rules} if prior else set()
    prior_rules_by_id = {rule.rule_id: rule for rule in prior.rules} if prior else {}

    new_rules = 0
    upgraded_to_verified = 0
    findings_recorded = 0
    not_applicable = 0

    for rule in merged.rules:
        # Count findings and not-applicable regardless of prior
        if rule.status == RuleStatus.VERIFIED_FINDING.value:
            findings_recorded += 1
        elif rule.status == RuleStatus.VERIFIED_NOT_APPLICABLE.value:
            not_applicable += 1

        # Count new rules (not in prior)
        if rule.rule_id not in prior_rule_ids:
            new_rules += 1
        else:
            # Rule was in prior; check if it was upgraded
            prior_rule = prior_rules_by_id.get(rule.rule_id)
            if prior_rule and prior_rule.status == RuleStatus.PROVISIONAL.value:
                if rule.status in (
                    RuleStatus.VERIFIED_PASS.value,
                    RuleStatus.VERIFIED_FINDING.value,
                ):
                    upgraded_to_verified += 1

    return SummaryCount(
        new_rules=new_rules,
        upgraded_to_verified=upgraded_to_verified,
        findings_recorded=findings_recorded,
        not_applicable=not_applicable,
    )


def build_diffs(
    deliverables_dir: Path, rendered: Mapping[str, str], repo_dir: Path | None = None
) -> list[DiffModel]:
    """Build diff models for each of the three documents.

    For each document, reads existing file content (if present) and builds
    a DiffModel with file_exists status and current/proposed content.

    The three standards documents are looked for in repo_dir/docs/ if repo_dir
    is provided, otherwise in deliverables_dir (for backward compatibility or
    testing without a repo).

    Args:
        deliverables_dir: Path to directory containing or to contain the rule set.
        rendered: Mapping of document_id -> rendered markdown.
        repo_dir: Optional path to audited project root. If provided, standards
            documents are expected in repo_dir/docs/.

    Returns:
        List of DiffModel objects, one per document, in stable order:
        agent-standard, human-standard, engineering-policy.
    """
    diffs: list[DiffModel] = []
    document_ids = [_AGENT_STANDARD_ID, _HUMAN_STANDARD_ID, _ENGINEERING_POLICY_ID]

    # Determine target directory for standards documents
    standards_dir = (repo_dir / "docs") if repo_dir else deliverables_dir

    for document_id in document_ids:
        filename = _DOCUMENT_ID_TO_FILENAME[document_id]
        file_path = standards_dir / filename
        current_content = None
        file_exists = False

        if file_path.exists():
            file_exists = True
            current_content = file_path.read_text(encoding="utf-8")

        proposed_content = rendered.get(document_id, "")

        diff = DiffModel(
            document_id=document_id,
            file_exists=file_exists,
            current_content=current_content,
            proposed_content=proposed_content,
        )
        diffs.append(diff)

    return diffs


def render_all(
    rule_set: RuleSet, rules_pack: RulesPack | None = None
) -> dict[str, str]:
    """Render all three standards documents from a rule set.

    Args:
        rule_set: The rule set to render.
        rules_pack: Optional RulesPack (passed to render_human_standard).

    Returns:
        Dictionary mapping document_id to rendered markdown:
        - "agent-standard": Agent Coding Standard
        - "human-standard": Engineering Standard
        - "engineering-policy": Engineering Policy
    """
    return {
        _AGENT_STANDARD_ID: render_agent_standard(rule_set),
        _HUMAN_STANDARD_ID: render_human_standard(rule_set, rules_pack),
        _ENGINEERING_POLICY_ID: render_policy(rule_set),
    }


def write_standards(
    deliverables_dir: Path,
    rendered: Mapping[str, str],
    rule_set: RuleSet,
    repo_dir: Path | None = None,
) -> None:
    """Write the three standards documents and rule set to disk.

    The three standards documents are written to repo_dir/docs/ if repo_dir
    is provided, otherwise to deliverables_dir (for backward compatibility or
    testing without a repo). The rule set is always written to deliverables_dir.

    Writes via write_managed_block for each document, then writes the
    rule set JSON. If any document write fails (returns False), raises
    a clear error and does NOT write the rule set, leaving the prior
    rule set intact for retry.

    Args:
        deliverables_dir: Path to directory where the rule set will be written.
        rendered: Mapping of document_id -> rendered markdown (with markers).
        rule_set: Rule set to write.
        repo_dir: Optional path to audited project root. If provided, standards
            documents are written to repo_dir/docs/. Otherwise, they are written
            to deliverables_dir.

    Raises:
        RuntimeError: If any directory cannot be created or if any
            document write fails.
    """
    # Ensure deliverables directory exists (for rule set)
    deliverables_dir.mkdir(parents=True, exist_ok=True)

    # Determine target directory for standards documents
    standards_dir = (repo_dir / "docs") if repo_dir else deliverables_dir
    # Ensure standards directory exists
    standards_dir.mkdir(parents=True, exist_ok=True)

    # Write each document via write_managed_block
    document_ids = [_AGENT_STANDARD_ID, _HUMAN_STANDARD_ID, _ENGINEERING_POLICY_ID]
    for document_id in document_ids:
        filename = _DOCUMENT_ID_TO_FILENAME[document_id]
        file_path = standards_dir / filename
        rendered_with_markers = rendered.get(document_id, "")

        # Extract content from between the markers (rendering functions wrap content)
        unwrapped_content = _extract_content_from_managed_block(
            rendered_with_markers, document_id
        )

        success = write_managed_block(file_path, unwrapped_content, document_id)
        if not success:
            raise RuntimeError(
                f"Failed to write standards document '{filename}' (document_id={document_id}). "
                f"The rule set was not written to preserve the prior set for retry."
            )

    # All documents written successfully; now write the rule set
    rule_set_path = deliverables_dir / RULE_SET_FILENAME
    rule_set.write(rule_set_path)
