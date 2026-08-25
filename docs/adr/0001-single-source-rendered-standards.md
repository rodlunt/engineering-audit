# ADR 0001: Single source of truth for generated standards

**Status:** accepted  
**Date:** 2026-08-25  
**Framework:** (applies to tooling design, not a domain rule)

## Context

After an engineering audit or grill run, the tooling generates three distinct documents for the audited project: an agent coding standard (for LLM agents), a human coding standard (for engineers), and an engineering policy (for leadership and compliance).

In earlier thinking, these three documents were considered as potentially independent: each maintained separately, each with its own update schedule and semantics. This makes sense on the surface because they have different audiences and purposes. However, they describe the same underlying rules, and the rules change based on the same audit evidence.

If the three documents are authored and maintained separately, they will inevitably drift. A typo fix in one, a rule addition in another, a removal in a third, and within months the policy says Y while the engineering standard says X. This divergence is especially problematic when the documents are used to justify decisions (why did this PR fail? because the policy requires X), and the policy and the standard do not agree.

## Decision

All three documents are rendered from a single machine-readable rule set (`audit-output/rule-set.json`). The rule set is the canonical source of truth. Every rule ID, text, status, and date lives only in the rule set; the three documents display views of it.

The rendering is deterministic and purely syntactic: given the same rule set, the same three documents are produced every time. Hand edits within the rendered sections (managed blocks) are not preserved, but hand edits outside the blocks are.

This ensures:

1. Rule text, status, and dates are always consistent across the three documents.
2. No accidental drift from independent updates.
3. A single point to fix a typo or clarify a rule.
4. Auditability: the rule set is the durable record that flows through audits over time.

## Alternatives considered

**Alternative 1: Three independent documents, with a diff comparison at write time**

Each document is authored and maintained separately. Before writing any of them, the tool compares the three for obvious conflicts (e.g. same rule ID with different status) and warns the user if they diverge. The user is responsible for resolving divergence.

Rejected because: the warning comes too late (after human work has already created the divergence). It does not prevent drift, only detects it after the fact. Over many audit runs, the user will grow tired of resolving conflicts and will start to ignore the warnings.

**Alternative 2: Separate rule sets per document**

Each document has its own rule set schema and file. A top-level "meta rule set" references them and checks for conflicts at audit time.

Rejected because: this adds complexity (three schemas instead of one, plus a meta schema) and still does not prevent drift. If the meta rule set diverges from any of the three, the problem is back.

**Alternative 3: Hand-authored master document, generated documents as views**

A hand-edited master document (e.g. Markdown with rich formatting) is the source of truth. The agent and policy documents are views of it.

Rejected because: it makes the agent standard (which must be concise, imperative, low-ceremony) hard to extract automatically, and it ties the master document to a specific format. If the master is hand-edited markdown, the tool cannot add metadata (dates, status) without complicating the format.

## Consequences

**Benefits:**
- Rule text, status, and dates are always in sync across the three documents.
- The rule set is a compact, version-controllable record of audit history.
- Drift is prevented by construction, not by careful discipline.
- The tool is the single place responsible for keeping them aligned.

**Costs:**
- The rule set is now part of the project's state (like `CLAUDE.md`). It must be version controlled and treated as a durable record.
- A corrupted or manually edited rule set can produce corrupted documents. Validation and safeguards are needed.
- The tool must implement a rendering engine for each of the three document types.
- The merge logic (when an audit updates a prior grill-time rule set) is complex and must be carefully tested.

**Risks:**
- If the rule set becomes corrupted or manually altered, the documents will be corrupted. Mitigation: strict schema validation on load, and warnings to users about hand-editing the JSON.
- If a user hand-edits content inside a managed block (the rendered section), it will be overwritten on the next audit. Mitigation: clear documentation and warnings at write time about what will be overwritten.

