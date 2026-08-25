# ADR 0003: Rules pack takes precedence over stack profile on conflict

**Status:** accepted  
**Date:** 2026-08-25  
**Framework:** (applies to tooling design, not a domain rule)

## Context

Rules come from two sources in the generated standards: the main rules pack (generic engineering rules applicable to most projects) and stack profiles (technology-specific rules for the chosen stack).

When both sources define rules that conflict or overlap, a precedence rule is needed. For example:

- Rule D06-R02 (from rules pack): "API endpoints must be documented in OpenAPI format."
- Rule S-FastAPI-R01 (from stack profile): "API endpoints must be documented in OpenAPI 3.1 format with example requests and responses for every endpoint."

The stack-profile rule is stricter and more specific. But which one is binding? If the project chooses the stack-profile rule (more specific), it makes a high-stakes commitment to example requests/responses for every endpoint. If it chooses the rules-pack rule (more generic), it allows documentation without examples.

The decision is which source wins when they conflict.

## Decision

On conflict, the **rules pack takes precedence**. If D06-R02 and S-FastAPI-R01 both apply, we follow D06-R02. The stack-profile rule is not discarded; the conflict is recorded in the rule set so readers see both positions and understand the choice.

This means:
1. Stack profiles can propose stricter rules, but the user must opt in explicitly.
2. The rules pack defines the baseline that applies universally.
3. The project can choose to adopt stack-profile rules by explicitly amending the rule set or the standards documents.

## Alternatives considered

**Alternative 1: Stack profile takes precedence**

Stack profiles are more specific to the technology. If a stack profile proposes a stricter rule, it likely captures domain-specific best practice that should win over the generic rule.

Rejected because:
- This silently commits the project to the stricter rule without explicit opt-in or awareness.
- A user who does not read the stack-profile rule carefully might not realise the project is committing to it.
- It makes the audit outcome dependent on stack profile design choices, which are outside the user's control.

**Alternative 2: Merge conflicts and ask the user**

When a conflict is detected, prompt the user to choose which rule applies. Record their choice.

Rejected because:
- This adds friction to the grill and audit workflows (users must resolve every conflict).
- It defers a decision that has no clear "right" answer without project context.
- It violates the principle that the framework provides decision support, not a quiz: users should answer questions about their project, not questions about the framework itself.

**Alternative 3: Apply both rules**

Include both the rules-pack rule and the stack-profile rule in the generated standards. The project must satisfy both.

Rejected because:
- This can create impossible commitments (two rules that contradict or over-constrain).
- It adds noise to the standards by including redundant or overlapping rules.
- It muddles the precedence question: if both are included, which one is binding if they diverge later?

## Consequences

**Benefits:**
- Clear precedence: no ambiguity about which rule wins.
- Users opt in explicitly to stack-profile commitments by choosing to follow the stricter rule.
- The rules pack remains stable and predictable, not overridden by stack-specific choices.
- Audit outcomes are reproducible: the same conflict always resolves the same way.

**Costs:**
- Stack profiles cannot enforce stricter commitments automatically. A stricter stack-profile rule is effectively a suggestion unless the user explicitly adopts it.
- Users might not be aware of stack-profile rules that are superseded by the rules pack, leading to suboptimal standards for their tech.

**Risks:**
- A user might want the stack-profile rule to win but not realise they need to explicitly amend the standards to adopt it. Mitigation: the conflict recording in the rule set makes both sides visible; the grill and audit workflows can prompt the user to review conflicts and choose.
- Over time, stack profiles might accumulate rules that conflict with the rules pack. Mitigation: the pack maintainer reviews conflicts regularly and updates the rules pack to resolve them if needed (e.g. "the Python profile's stricter type-hinting rule is actually best practice; update D06-R01 to match").

