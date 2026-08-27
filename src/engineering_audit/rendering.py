"""Rendering engine for the three standards documents.

Transforms a rule set into three markdown documents:
1. Agent coding standard - concise, imperative markdown for coding agents
2. Human coding standard - verbose markdown for engineers to read and discuss
3. Engineering policy - formal markdown for company stakeholders

All three documents are rendered deterministically: given the same rule set,
the same output is produced every time, byte-for-byte. Rendering is done in
a single module so the three documents cannot drift.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from engineering_audit.managed_blocks import wrap_managed_block
from engineering_audit.standards import Rule, RuleSet, RuleStatus

if TYPE_CHECKING:
    from engineering_audit.rules import RulesPack

__all__ = [
    "render_agent_standard",
    "render_human_standard",
    "render_policy",
]


def _rule_sort_key(rule: Rule) -> tuple[tuple[bool, str | None], str]:
    """Sort key for rules: domain ID then rule ID, with domainless rules last.

    Rules are sorted first by domain (with null domains sorting last), then by
    rule ID within the domain.
    """
    return ((rule.domain_id is None, rule.domain_id), rule.rule_id)


def render_agent_standard(rule_set: RuleSet) -> str:
    """Render the agent coding standard from a rule set.

    Produces concise, imperative markdown for coding agents, grouping rules by
    domain and showing rule ID, short text, status with date, and full text.
    Provisional rules are annotated with "grill intent only, not yet audited".
    Findings include severity. All output is wrapped in managed-block markers.

    Args:
        rule_set: The machine-readable rule set to render.

    Returns:
        Markdown string with managed-block markers.
    """
    # Filter out verified-not-applicable rules
    rules_to_render = [
        rule
        for rule in rule_set.rules
        if rule.status != RuleStatus.VERIFIED_NOT_APPLICABLE.value
    ]

    # Sort by domain_id (null sorts last), then rule_id
    sorted_rules = sorted(rules_to_render, key=_rule_sort_key)

    # Group by domain for rendering
    rules_by_domain: dict[str | None, list[Rule]] = defaultdict(list)
    for rule in sorted_rules:
        rules_by_domain[rule.domain_id].append(rule)

    # Render content between markers
    lines = ["# Agent Coding Standard", ""]
    lines.append("Rules are identified by rule ID and current verification status.")
    lines.append("")

    # Render domain sections
    domains_in_order = sorted(rules_by_domain.keys(), key=lambda d: (d is None, d))
    for domain_id in domains_in_order:
        rules = rules_by_domain[domain_id]
        if domain_id:
            lines.append(f"## Domain {domain_id}")
            lines.append("")

        for rule in rules:
            # Rule heading with ID
            lines.append(f"## Rule {rule.rule_id}: {rule.text_short}")

            # Status line
            status_line = f"Status: {rule.status} ({rule.verified_date}"
            if rule.status == RuleStatus.VERIFIED_FINDING.value:
                status_line += f", severity: {rule.severity}"
            status_line += ")"
            if rule.status == RuleStatus.PROVISIONAL.value:
                status_line += " - grill intent only, not yet audited"
            lines.append(status_line)
            lines.append("")

            # Full text
            lines.append(rule.text_body)
            lines.append("")

            # Conflicts section (if applicable)
            if rule.conflict_with_stack_profile:
                lines.append("Conflict:")
                lines.append("")
                conflict = rule.conflict_with_stack_profile
                if "stack_rule_text" in conflict:
                    lines.append(f"- Stack profile rule: {conflict['stack_rule_text']}")
                if "issue" in conflict:
                    lines.append(f"- Issue: {conflict['issue']}")
                if rule.conflict_resolution:
                    lines.append(f"- Resolution: {rule.conflict_resolution}")
                lines.append("")

    content = "\n".join(lines).rstrip()

    # Wrap in managed-block markers
    return wrap_managed_block(content, "agent-standard")


def render_human_standard(
    rule_set: RuleSet, rules_pack: RulesPack | None = None
) -> str:
    """Render the human coding standard from a rule set.

    Produces verbose markdown for engineers, with status, full text, any audit
    findings with fix suggestions, and conflict notes if the rule conflicts
    with a stack profile.

    Note: Rationale is planned for future versions when the rules pack includes
    rationale fields. Currently unused.

    Args:
        rule_set: The machine-readable rule set to render.
        rules_pack: Optional RulesPack for future rationale lookup (not yet used).

    Returns:
        Markdown string with managed-block markers.
    """
    # Filter out verified-not-applicable rules
    rules_to_render = [
        rule
        for rule in rule_set.rules
        if rule.status != RuleStatus.VERIFIED_NOT_APPLICABLE.value
    ]

    # Sort by domain_id (null sorts last), then rule_id
    sorted_rules = sorted(rules_to_render, key=_rule_sort_key)

    # Group by domain for rendering
    rules_by_domain: dict[str | None, list[Rule]] = defaultdict(list)
    for rule in sorted_rules:
        rules_by_domain[rule.domain_id].append(rule)

    # Render content between markers
    lines = ["# Engineering Standard", ""]
    lines.append(
        "This document records the rules we follow in our code. "
        "Each rule shows its current verification status."
    )
    lines.append("")

    # Render domain sections
    domains_in_order = sorted(rules_by_domain.keys(), key=lambda d: (d is None, d))
    for domain_id in domains_in_order:
        rules = rules_by_domain[domain_id]

        for rule in rules:
            # Rule heading (short text as heading)
            lines.append(f"## Rule {rule.rule_id}: {rule.text_short}")
            lines.append("")

            # Status line
            status_line = f"**Status:** {rule.status} ({rule.verified_date}"
            if rule.status == RuleStatus.VERIFIED_FINDING.value:
                status_line += f", severity: {rule.severity}"
            status_line += ")"
            lines.append(status_line)
            lines.append("")

            # Full text
            lines.append(rule.text_body)
            lines.append("")

            # Audit findings section (if applicable)
            if (
                rule.status == RuleStatus.VERIFIED_FINDING.value
                and rule.finding_details
            ):
                lines.append("**Current finding:**")
                lines.append("")
                finding = rule.finding_details
                if "path" in finding:
                    lines.append(
                        f"- File: {finding['path']}:{finding.get('line', '?')}"
                    )
                if "issue_title" in finding:
                    lines.append(f"- Issue: {finding['issue_title']}")
                if "issue_body" in finding:
                    lines.append(f"- Details: {finding['issue_body']}")
                lines.append("")

            # Conflicts section (if applicable)
            if rule.conflict_with_stack_profile:
                lines.append("**Conflict with stack profile:**")
                lines.append("")
                conflict = rule.conflict_with_stack_profile
                if "stack_rule_text" in conflict:
                    lines.append(f"- Stack profile rule: {conflict['stack_rule_text']}")
                if "issue" in conflict:
                    lines.append(f"- Issue: {conflict['issue']}")
                if rule.conflict_resolution:
                    lines.append(f"- Resolution: {rule.conflict_resolution}")
                lines.append("")

    content = "\n".join(lines).rstrip()

    # Wrap in managed-block markers
    return wrap_managed_block(content, "human-standard")


def render_policy(rule_set: RuleSet) -> str:
    """Render the engineering policy from a rule set.

    Produces formal markdown grounded in audit evidence, stating what the
    organisation commits to enforce. Groups rules by status: kept commitments
    (verified-pass), outstanding findings (verified-finding), and deferred
    domains (verified-not-applicable). Includes verification methods and
    revisit triggers.

    Args:
        rule_set: The machine-readable rule set to render.

    Returns:
        Markdown string with managed-block markers.
    """
    # Separate rules by status
    kept_commitments: list[Rule] = []
    outstanding_findings: list[Rule] = []
    deferred_domains: list[Rule] = []
    provisional_rules: list[Rule] = []

    for rule in rule_set.rules:
        if rule.status == RuleStatus.VERIFIED_PASS.value:
            kept_commitments.append(rule)
        elif rule.status == RuleStatus.VERIFIED_FINDING.value:
            outstanding_findings.append(rule)
        elif rule.status == RuleStatus.VERIFIED_NOT_APPLICABLE.value:
            deferred_domains.append(rule)
        elif rule.status == RuleStatus.PROVISIONAL.value:
            provisional_rules.append(rule)

    # Sort each group by domain_id then rule_id (using consistent idiom)
    for group in [
        kept_commitments,
        outstanding_findings,
        deferred_domains,
        provisional_rules,
    ]:
        group.sort(key=_rule_sort_key)

    # Render content
    lines = ["# Engineering Policy", ""]
    lines.append(
        "This policy states what we commit to enforce in our code. "
        "Every rule below has been audited and has a verification status and date."
    )
    lines.append("")

    # Kept Commitments section
    if kept_commitments:
        lines.append("## Kept Commitments (verified to pass)")
        lines.append("")
        for rule in kept_commitments:
            lines.append(f"### {rule.text_short} ({rule.rule_id})")
            lines.append("")
            lines.append("- **What we enforce:** " + rule.text_body)
            lines.append("- **How we verify it:** Automated audit and code review.")
            lines.append(
                f"- **Current status:** Verified to pass on {rule.verified_date}"
            )
            if rule.revisit_trigger:
                lines.append(f"- **Revisit trigger:** {rule.revisit_trigger}")
            lines.append("")

    # Outstanding Findings section
    if outstanding_findings:
        lines.append("## Outstanding Findings (verified not to pass)")
        lines.append("")
        for rule in outstanding_findings:
            lines.append(f"### {rule.text_short} ({rule.rule_id})")
            lines.append("")
            lines.append("- **What we require:** " + rule.text_body)
            if rule.finding_details:
                finding = rule.finding_details
                if "path" in finding:
                    lines.append(
                        f"- **Current finding:** {finding['path']}:{finding.get('line', '?')}"
                    )
                if "issue_title" in finding:
                    lines.append(f"  - {finding['issue_title']}")
                if "issue_body" in finding:
                    lines.append(f"  - {finding['issue_body']}")
            if rule.severity:
                lines.append(f"- **Severity:** {rule.severity}")
            lines.append(f"- **Date found:** {rule.verified_date}")
            if rule.fix_due:
                lines.append(f"- **Fix due:** {rule.fix_due}")
            if rule.ownership:
                lines.append(f"- **Ownership:** {rule.ownership}")
            if rule.revisit_trigger:
                lines.append(f"- **Revisit trigger:** {rule.revisit_trigger}")
            lines.append("")

    # Deferred Domains section
    if deferred_domains:
        lines.append("## Deferred Domains (will apply later)")
        lines.append("")
        for rule in deferred_domains:
            lines.append(f"### {rule.text_short} ({rule.rule_id})")
            lines.append("")
            lines.append("- **Status:** Not yet applicable")
            lines.append(f"- **Verified date:** {rule.verified_date}")
            if rule.revisit_trigger:
                lines.append(f"- **Revisit trigger:** {rule.revisit_trigger}")
            lines.append("")

    # Provisional Rules section
    if provisional_rules:
        lines.append("## Provisional Rules (grill intent only, not yet audited)")
        lines.append("")
        for rule in provisional_rules:
            lines.append(f"### {rule.text_short} ({rule.rule_id})")
            lines.append("")
            lines.append("- **Intent:** " + rule.text_body)
            lines.append(f"- **Status:** Provisional (recorded {rule.verified_date})")
            if rule.revisit_trigger:
                lines.append(f"- **Revisit trigger:** {rule.revisit_trigger}")
            lines.append("")

    content = "\n".join(lines).rstrip()

    # Wrap in managed-block markers
    return wrap_managed_block(content, "engineering-policy")
