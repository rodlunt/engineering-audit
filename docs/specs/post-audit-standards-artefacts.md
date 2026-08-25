# Post-audit standards artefacts

**Status:** draft  
**Date:** 2026-08-25  
**Branch:** feat/post-audit-standards-artefacts

## Summary and motivation

After an engineering audit run, and also at engineering-grill time (before code exists), the tooling generates three standing documents for the audited project:

1. **Agent coding standard**: written for a coding agent to obey. Short, concise, imperative, low ceremony. Consumed by Claude Code or other agents when they write or review code.
2. **Human coding standard**: the same rules, verbose and well-written, for engineers to read and discuss. Kept in the repository and maintained alongside the codebase.
3. **Engineering policy**: company-facing and grounded in audit evidence. States what the organisation actually enforces, what gates it uses, and what the audit verified.

These three documents are currently generated manually or with ad-hoc scripts, or do not exist at all. A single source of truth allows them to drift only by accident, not by design. At grill time (when no code exists yet), all three are marked provisional; at audit time (when code exists), the audit updates them with verified facts. The merge preserves decisions from the grill, marks them as verified or updated when evidence arrives, and does not silently swap the stack profile.

## Glossary

| Term | Agreed meaning | Not this |
|---|---|---|
| **Agent coding standard** | A machine-readable and human-readable document written for an LLM-based coding agent to follow. Short, imperative, low ceremony. Consumed during code generation and review. | A styleguide only for humans. |
| **Human coding standard** | The same rules as the agent standard, written in prose for engineers to read, debate, and decide to follow or challenge. | A distinct second ruleset. Hand-authored separately. |
| **Engineering policy** | A commitment statement from the organisation: what we enforce, what gates block a release, what this audit verified. Grounded in audit evidence (findings, passes). | An aspirational list of best practices. A compliance checklist. |
| **Stack profile** | A bundle of rules that apply to a particular technology stack (e.g. Python, React, FastAPI), including stack-specific commenting and documentation conventions. Versioned in the rules pack and chosen at grill time based on the project's tech stack. Baked into the pack, not fetched live from the web. | A live web lookup. A generic checklist. Current industry practice outside the pack. |
| **Rule set** | The machine-readable source of truth for all three documents. Contains rule IDs, text, source (rules pack or stack profile), status, dates, and conflict records. Rendered to produce all three artefacts. | Individual hand-authored documents. Separate databases for each artefact. |
| **Provisional** | Status assigned at grill time, when intent is recorded but no code exists to verify against. Marked as such in the artefact. | A draft that will be deleted. A temporary file. |
| **Verified-pass** | Status assigned at audit time: the audit checked this rule and the codebase satisfies it. The date of verification is recorded. | Assumed to pass. Hand-checked without audit evidence. |
| **Verified-finding** | Status assigned at audit time: the audit checked this rule and found a violation. The date, severity, and finding details are recorded. | A theoretical concern. An assumption. |
| **Managed block** | A section of a document (e.g. `<!-- audit:start -->` to `<!-- audit:end -->` in Markdown or HTML) that the tool may rewrite. Hand edits outside the blocks are preserved. Hand edits inside are overwritten when the tool regenerates the document. | The entire document (only the tool writes the file). Any filename with "audit" in it. |
| **Revisit trigger** | A future project event or milestone that reactivates a domain and asks its rules to be reconsidered. Recorded in the engineering policy so readers know when to check whether the decision still holds. Examples: "moving to multi-tenant deployment", "handling personal data". | A rebuild trigger. A deployment step. |

## The three documents

### Purpose and audience

#### Agent coding standard

**Purpose:** Provide an LLM-based coding agent with concise, imperative rules to follow when writing, reviewing, or modifying code in this project.

**Audience:** Coding agents (Claude Code, other LLM-based assistants). Read at agent runtime, not by humans in the normal case.

**Example excerpt (Python stack):**

```
<!-- audit:start id="agent-standard" -->
# Agent Coding Standard

Rules are identified by rule ID and current verification status.

## Rule D06-R01: Use type hints in function signatures
Status: verified-pass (2026-08-25)

Use Python 3.9+ type hints on all function parameters and return types. Static type checking with `mypy` is run on every PR.

## Rule D06-R03: Error handling in API routes
Status: verified-finding (2026-08-25, severity: medium)

Every FastAPI route handler must explicitly catch and log exceptions before returning a 5xx response. Current finding: route `POST /api/users` in `src/api/users.py:47` does not catch `ValueError` from validation.

## Rule S-React-R02: Component testing (Stack profile: React)
Status: provisional (2026-08-25, grill intent only, not yet audited)

Every React component must have at least one unit test in a `.test.tsx` file.
<!-- audit:end -->
```

#### Human coding standard

**Purpose:** Give engineers a readable, justified explanation of the rules the agent follows. A document they can debate, challenge, and decide to keep or change.

**Audience:** Project engineers, technical leads, engineering managers.

**Example excerpt (Python stack):**

```
<!-- audit:start id="human-standard" -->
# Engineering Standard: Python and FastAPI

This document records the rules we are committed to in our Python backend services. Each rule is identified by its framework rule ID and shows its current verification status.

## Data Modelling and API Design

### Rule D06-R01: Type hints in function signatures

**Status:** verified-pass (2026-08-25)

We use Python type hints on all function signatures. This helps both humans and tools understand what data moves through the code, catches shape mistakes before runtime, and makes refactoring safer.

**Rationale:** Type hints prevent whole categories of data-shape errors. Every function signature serves as inline documentation. Our automated type checking (`mypy`) runs on every PR and blocks merges if the hints do not match the implementation.

**What we verified:** The entire codebase was scanned for function signatures. 1,247 functions were checked; 1,205 have complete type hints (98.6%). The 42 functions without hints are...

### Rule D06-R03: Error handling in API routes

**Status:** verified-finding (2026-08-25, severity: medium)

Every FastAPI route handler must explicitly catch exceptions and log them before returning a 5xx response. This ensures production knows what went wrong.

**Why this matters:** An unguarded exception can return a stack trace to the client (information leakage), hide the actual error in logs (making production issues unsolvable), or crash the worker thread (availability).

**Current finding:** The route `POST /api/users` (line 47 in `src/api/users.py`) does not catch `ValueError` from the validation layer. A malformed request will crash the handler and return a 500 without a log entry explaining why.

**Suggested fix:** Wrap the validation call in a try-except, log the error, and return a 422 Unprocessable Entity with a user-facing message.

**Conflicts:** None recorded.
<!-- audit:end -->
```

#### Engineering policy

**Purpose:** State what the organisation commits to enforcing. Grounded in audit evidence. Visible to stakeholders outside the project.

**Audience:** Engineering leadership, compliance/audit teams, product managers who need to know what gates apply.

**Example excerpt:**

```
<!-- audit:start id="engineering-policy" -->
# Engineering Policy: API and Data Safety

This policy states what we commit to enforce in all API services and data handling. Every rule below has been audited and has a verification status and date.

## Kept Commitments (verified to pass)

### Type hints in function signatures (D06-R01)
- **What we enforce:** All Python functions must carry type hints on parameters and return types.
- **How we verify it:** Automated type checking (`mypy`) runs on every PR and blocks merge if violated.
- **Current status:** Verified to pass on 2026-08-25. 1,205 of 1,247 functions (98.6%) carry complete hints.
- **Revisit trigger:** If the codebase grows to handle more user-supplied polymorphic types, or if we add async streams.

### API endpoint documentation (D06-R02)
- **What we enforce:** Every public API endpoint is documented in OpenAPI 3.1 and the documentation is kept current.
- **How we verify it:** The generated API docs are compared to the live code; mismatches block release.
- **Current status:** Verified to pass on 2026-08-25. All 42 public endpoints have current documentation.
- **Revisit trigger:** Adding new endpoints that use undocumented request/response shapes.

## Outstanding Findings (verified not to pass)

### Error handling in API routes (D06-R03)
- **What we require:** Every FastAPI route handler must catch and log exceptions before returning 5xx.
- **Current finding:** Route `POST /api/users` (src/api/users.py:47) does not catch `ValueError`.
- **Severity:** medium
- **Date found:** 2026-08-25
- **Fix due:** By next release (Sprint 11, estimated 2026-09-15)
- **Ownership:** Backend team, assigned to @alice-dev
- **Revisit trigger:** Once the fix is merged; re-audit before next release.

## Deferred Domains (will apply later)

### Rule D11-R02: Multi-server coordination (not yet applicable)
- **Why deferred:** This project is single-server SaaS today. Multi-server support is planned for Phase 2.
- **Revisit trigger:** When moving from single to multi-region deployment.
<!-- audit:end -->
```

## The single source rule set

The machine-readable rule set is the canonical source from which all three documents are rendered. It is persisted in its own file (not merged into run-state.json) so it survives across audits and can be loaded independently.

Commenting standards are captured as normal rules in this same rule set: baseline expectations come from the rules pack, and stack-specific conventions (for example Python docstrings or JSDoc usage) come from the selected stack profile. No runtime web lookup is used for these standards.

### Name and location

**File:** `audit-output/rule-set.json` (within the audit output directory, same location as run-state.json and report.html)

The file is written only when the user approves the diff on the localhost approval page. It is never written without approval.

### Schema

The rule set is a JSON object with this structure:

```json
{
  "version": "1.0",
  "project": "engineering-audit",
  "rules": [
    {
      "rule_id": "D06-R01",
      "domain_id": "d06",
      "text_short": "Use type hints in function signatures",
      "text_body": "Use Python 3.9+ type hints on all function parameters and return types. Static type checking with `mypy` is run on every PR.",
      "source": "rules-pack",
      "stack_profile": null,
      "status": "verified-pass",
      "verified_date": "2026-08-25",
      "severity": null,
      "finding_details": null,
      "conflict_with_stack_profile": null,
      "conflict_resolution": null,
      "source_url": "https://example.com/rules-pack/d06.md#R01"
    },
    {
      "rule_id": "D06-R03",
      "domain_id": "d06",
      "text_short": "Error handling in API routes",
      "text_body": "Every FastAPI route handler must explicitly catch and log exceptions before returning a 5xx response.",
      "source": "rules-pack",
      "stack_profile": null,
      "status": "verified-finding",
      "verified_date": "2026-08-25",
      "severity": "medium",
      "finding_details": {
        "precondition": "This project uses FastAPI; the route `POST /api/users` exists at src/api/users.py:47.",
        "path": "src/api/users.py",
        "line": 47,
        "issue_title": "Unguarded exception in POST /api/users route handler",
        "issue_body": "The route handler does not catch ValueError from the validation layer. A malformed request crashes the handler and returns a 500 without a log entry. Wrap the validation call in try-except, log the error, and return 422 with a user-facing message."
      },
      "conflict_with_stack_profile": null,
      "conflict_resolution": null,
      "source_url": "https://example.com/rules-pack/d06.md#R03"
    },
    {
      "rule_id": "S-React-R02",
      "domain_id": null,
      "text_short": "Component testing",
      "text_body": "Every React component must have at least one unit test in a `.test.tsx` file.",
      "source": "stack-profile",
      "stack_profile": "react",
      "status": "provisional",
      "verified_date": "2026-08-25",
      "severity": null,
      "finding_details": null,
      "conflict_with_stack_profile": null,
      "conflict_resolution": null,
      "source_url": null,
      "grill_intent_note": "Recorded from engineering-grill intent; not yet audited against code."
    },
    {
      "rule_id": "D06-R02-conflict-example",
      "domain_id": "d06",
      "text_short": "API documentation (conflict: rules pack vs stack profile)",
      "source": "rules-pack",
      "stack_profile": "fastapi",
      "status": "verified-pass",
      "verified_date": "2026-08-25",
      "conflict_with_stack_profile": {
        "stack_rule_id": "S-FastAPI-R01",
        "stack_rule_text": "API documentation must include example requests and responses for every endpoint.",
        "issue": "Rules pack says 'document every endpoint'; stack profile says 'include example requests and responses'. The stack profile is stricter."
      },
      "conflict_resolution": "Rules pack wins (per decision #7). The project will follow the stack profile requirement (examples + responses) because it is stricter, but this choice is recorded here for transparency."
    }
  ]
}
```

### Worked example

```json
{
  "rule_id": "D06-R01",
  "domain_id": "d06",
  "text_short": "Use type hints in function signatures",
  "text_body": "Use Python 3.9+ type hints on all function parameters and return types. Static type checking with `mypy` is run on every PR.",
  "source": "rules-pack",
  "stack_profile": null,
  "status": "verified-pass",
  "verified_date": "2026-08-25",
  "severity": null,
  "finding_details": null,
  "conflict_with_stack_profile": null,
  "conflict_resolution": null,
  "source_url": "https://github.com/rodlunt/engineering-audit/examples/taster-rules/06-code-review.md#R01"
}
```

### Persistence and access

The rule set is written to `audit-output/rule-set.json` after the user approves it on the localhost page. It is intended to persist across audit runs:

1. At grill time, a new rule set is created with all rules marked `provisional`.
2. At audit time, the tool loads the existing rule set (if present) and merges it with the current audit results (see Merge Algorithm below).
3. If no prior rule set exists at audit time, a new one is created from the audit results (all rules marked `verified-pass` or `verified-finding` with their dates).

The file is a new state artefact that lives alongside `run-state.json`. It is versioned by the `version` field; a future version of the tool that changes the schema will migrate old rule sets.

## Rendering pipeline

All three documents are rendered from the rule set. The rendering is deterministic and reversible: given the same rule set, the same three documents are produced every time.

### Rendering steps

1. **Load rule set** from `audit-output/rule-set.json`.
2. **For agent coding standard:**
   - Filter to rules with `status != "not-applicable"` (include provisional, verified-pass, verified-finding).
   - Sort by domain ID, then by rule ID.
   - For each rule, emit: rule ID, short text, status with date, and full text.
   - If `conflict_with_stack_profile` is set, append a "Conflict" section showing both sides.
   - Wrap in a managed block (e.g. `<!-- audit:start id="agent-standard" -->` ... `<!-- audit:end -->`).

3. **For human coding standard:**
   - Same filtering and sorting as agent standard.
   - For each rule, emit: short text as a heading, status with date, the full text, rationale (fetched from the rules pack if not in the rule set), current audit findings if any, and suggested fix.
   - If `conflict_with_stack_profile`, show both positions and explain the resolution.
   - Wrap in a managed block.

4. **For engineering policy:**
   - Include all rules, grouped by status: kept commitments (verified-pass), outstanding findings (verified-finding), deferred domains (not-applicable with a revisit trigger).
   - For each rule: what we enforce (short text), how we verify it (audit gate if applicable), status with date, revisit trigger.
   - For finding rules, include precondition, severity, path:line, issue title and body, and due date (if known).
   - Wrap in a managed block.

### Rendering engine

Create a new module `engineering_audit.standards` with:
- `render_agent_standard(rule_set: RuleSet, template_path: Path) -> str`
- `render_human_standard(rule_set: RuleSet, rules_pack: RulesPack, template_path: Path) -> str`
- `render_policy(rule_set: RuleSet, config: AuditConfig, findings: List[Finding]) -> str`

Each function takes the rule set and any additional context (template, rules pack, audit findings) and returns the rendered markdown. The template system uses the same managed-block markers so hand edits are preserved.

## Merge algorithm

When the tool is about to write the three documents, it checks whether a prior rule set exists. If it does, it merges the new state with the old, applying these rules per rule ID:

### Case 1: Grill-time generation (no prior rule set)

A new rule set is created with all rules marked `provisional` and today's date. The three documents are generated and marked `[provisional]` at the top.

### Case 2: Audit-time generation (first audit, no grill)

A new rule set is created from audit verdicts. All rules are marked `verified-pass` or `verified-finding` with today's date. The three documents are generated, not marked provisional.

### Case 3: Audit-time generation (after grill, merging with existing rule set)

For each rule in the new audit result:

- **Rule exists in prior set, status was provisional, audit says pass:** Upgrade to `verified-pass` with today's date. Preserve any hand notes.
- **Rule exists in prior set, status was provisional, audit says finding:** Upgrade to `verified-finding` with today's date, severity, and finding details.
- **Rule exists in prior set, status was verified-pass, audit says pass:** Keep verified-pass; do not change the date. Mark as re-checked this run if the configuration allows.
- **Rule exists in prior set, status was verified-pass, audit says finding:** Keep the old verified-pass date but record a new finding with today's date as a separate audit event. The prior pass is not overwritten.
- **Rule exists in prior set, status was verified-finding, audit says pass:** Record the upgrade from finding to pass with today's date. Do not delete the old finding; mark it as resolved.
- **Rule in prior set but not in new audit:** The audit did not check this rule. Retain it from the prior set unchanged. This is the case when the tool only ran a subset of domains.
- **Rule in new audit but not in prior set:** This is a new rule (e.g. from a stack profile added since the grill). Add it as `verified-pass` or `verified-finding` with today's date.

### Stack mismatch stop

Before merging, the tool compares the stack recorded at grill time to the stack observed during the current audit. The stack is recorded in the rule set under each stack-profile rule's `stack_profile` field.

If the grill said `python + fastapi` and the audit observes `python + django`:

1. **Stop immediately. Do not proceed with the audit.**
2. **Present to the user:** "The engineering grill recorded the tech stack as Python + FastAPI, but the current code uses Python + Django. Stack profiles have changed. Which is correct?"
3. **Wait for the user to choose:**
   - **Use grill stack (Python + FastAPI):** Proceed with the grill-time rules; do not load Django stack profile rules. The code does not match the grill's intent.
   - **Use audit stack (Python + Django):** Reload the stack profiles for the observed stack. This will add Django rules that were not in the grill and remove FastAPI rules. Drop any Django rules that did not exist in the prior rule set from the output (they are new, not prior commitments). Proceed with the merge.
4. **Never silently swap the stack profile.** The reason: stack profiles contain rules, and a silent swap deletes agreed-upon rules without the user's knowledge.

## Approval flow on localhost page

The configuration page (currently at `engineering_audit.config_page.ConfigServer`) is extended to show a diff view before the documents are written.

### New endpoints and flows

1. **POST /approve-standards** (new):
   - Called after the audit is complete, before documents are written.
   - Returns a diff view showing the three documents as they will be written:
     - Left side: the current file on disk (if it exists), or a placeholder if it does not.
     - Right side: what the tool proposes to write.
     - Managed blocks are highlighted so the user sees which parts will be overwritten.
   - Returns HTML with a side-by-side diff viewer and two buttons: **Approve** and **Cancel**.
   - If the current file does not exist, the left side shows "File does not exist yet".

2. **POST /submit-standards** (new):
   - Called when the user clicks Approve.
   - Writes the rule set to `audit-output/rule-set.json`.
   - Writes the three documents to their configured paths.
   - Returns a confirmation page linking to the written files.

### Module changes

In `engineering_audit.config_page.ConfigServer`:

- Add `approve_standards_html()` method to render the diff view. Use the `difflib` module to produce a unified diff and render it with a simple HTML template.
- Add `/approve-standards` POST endpoint handler.
- Add `/submit-standards` POST endpoint handler.
- Pass the `rule_set` and the paths of the three documents to these handlers so they can render and write them.

The diff view must show:
- File path and status (exists / new).
- Managed block markers highlighted (e.g. `<!-- audit:start -->` and `<!-- audit:end -->`).
- The actual diff of the content between the blocks.
- Human-readable summary of what is changing (how many new rules, how many upgraded from provisional, etc.).

## Managed-block write protocol

All three documents use managed blocks to preserve hand edits outside the audit-generated sections.

### Block format

The blocks use HTML/markdown comments as markers:

```markdown
<!-- audit:start id="<identifier>" -->
[tool-generated content goes here]
<!-- audit:end -->
```

Where `<identifier>` is `agent-standard`, `human-standard`, or `engineering-policy` respectively.

### Write protocol

1. **Load the existing file** (if it exists). If it does not exist, create a new file with the block markers and the generated content inside.
2. **Locate the managed block** by finding the opening and closing markers with the correct id.
3. **If markers are found and well-formed:**
   - Keep all content outside the markers unchanged.
   - Replace the content between the markers with the newly rendered content.
4. **If markers are missing or malformed, log an error and ask for user approval before proceeding:**
   - Missing opening marker: "The file exists but does not contain `<!-- audit:start id='...' -->`. Create new file or manually add the markers?"
   - Missing closing marker: "The opening marker is found but no closing `<!-- audit:end -->` is present. Add the closing marker and try again?"
   - Duplicated markers: "The file contains multiple `<!-- audit:start id='...' -->` sections. Remove duplicates and try again?"
   - Malformed markers (missing id attribute or wrong format): "The markers in this file do not match the expected format. Correct them and try again?"
5. **If the file does not exist:**
   - Create it with a minimal header (title, date, status) and the managed block inside.
   - The user may add content outside the block (introduction, rationale, additional sections) later; the tool will preserve it.

## New or changed MCP tools and skill steps

### Audit side (`integrations/claude-code/audit/SKILL.md`)

After step 6 (render the report), add:

**Step 6a. Generate draft standards artefacts**

- Call a new `generate_standards(run_id, rule_set_input)` MCP tool (or extend `render_report` to accept a `standards: true` flag).
- The tool generates the three documents as drafts and writes them to temporary locations (not yet in the repo).
- Returns the rendered drafts and their proposed paths.

**Step 6b. Present standards diff and request approval**

- Call `show_standards_approval_page(run_id, drafts, paths, existing_content)` (new MCP tool or config-page extension).
- This opens a new URL on the existing localhost config server showing the diff view.
- The user reviews the diffs and clicks Approve or Cancel.
- The tool returns `approved: true/false`.

**Step 6c. Write approved standards artefacts**

- If approved, call `write_standards_to_disk(run_id, rule_set, documents, paths)` (new MCP tool).
- The tool writes the rule set and the three documents, preserving hand edits outside managed blocks.
- Returns the paths where files were written.

**Step 6d. Link standards in CLAUDE.md or AGENTS.md**

- If the user opted to update CLAUDE.md or AGENTS.md, add links to the new documents in a "Standards and policies" section.
- Example: "See [Agent Coding Standard](docs/coding-standard.agent.md), [Engineering Standard](docs/engineering-standard.md), and [Engineering Policy](docs/engineering-policy.md)."

### Grill side (`integrations/engineering-grill/engineering-grill/SKILL.md`)

After the grill completes and the user confirms the shared understanding:

**New step. Generate provisional standards artefacts**

- Call the same rendering tools as the audit side, but pass `provisional: true` so all rules are marked with `[provisional]`.
- Generate rule set and write to `audit-output/rule-set.json` (or the project's existing audit output location if known).
- Write the three documents to their configured paths with managed-block markers.
- Mark the top of each document with **[Provisional: intent only, not yet audited]** so readers know no code has been verified yet.

**New step. Offer to link standards in project documents**

- Ask the user whether to add links to the standards documents in CLAUDE.md, AGENTS.md, or a project README.
- If yes, add the links in the appropriate section.

## File placement

### Agent coding standard

**File:** `docs/coding-standard.agent.md`

This is a stand-alone document owned by the tool. It contains only the managed block; the tool writes the entire file (except for hand edits outside the managed block, which are rare in this file).

Links to it from:
- `CLAUDE.md` or `AGENTS.md`: "See [Agent Coding Standard](docs/coding-standard.agent.md) for the rules Claude Code will follow when writing code in this project."
- Comments in pull request templates or CI configurations that mention coding standards.

### Human coding standard

**File:** `docs/engineering-standard.md`

This document is intended to be read and edited by humans. It contains a managed block; the tool regenerates it at audit time, but the user may add sections outside the block (introduction, rationale, team consensus, exceptions approved by leadership).

Links to it from:
- `CLAUDE.md` or `AGENTS.md`: "See [Engineering Standard](docs/engineering-standard.md)."
- README.md or a project wiki as the canonical reference for code quality expectations.

### Engineering policy

**File:** `docs/engineering-policy.md`

This is a formal document. It is read by engineers, managers, and sometimes external auditors. It contains a managed block; the tool regenerates it at audit time. The user may add preamble sections (policy number, approval date, policy owner, review cadence) outside the block.

Links to it from:
- Company intranet or policy registry.
- README.md under "Compliance and auditing".
- CLAUDE.md or AGENTS.md: "See [Engineering Policy](docs/engineering-policy.md)."

## Build order and phasing

The implementation is phased so the merge logic is in place before provisional documents are useful.

### Phase 1: Audit-side standards generation (audit-first)

1. **Implement rule set schema and storage** (`engineering_audit.standards.RuleSet` class, JSON schema, read/write).
2. **Implement rendering pipeline** (three render functions, managed-block handling).
3. **Implement approval flow on config page** (diff view, approve/cancel, write handlers).
4. **Integrate into audit workflow** (call after render_report in step 6a-d above).
5. **Test:** Run an audit on the engineering-audit project itself; verify rule set is created, diff view works, documents are written correctly with managed blocks respected.
6. **Test:** Manually edit content outside a managed block and run audit again; verify hand edits are preserved.

**Definition of done:** An audit run generates the three documents and writes them to disk with user approval. A second audit run merges with the existing rule set correctly.

### Phase 2: Grill-side provisional standards (after merge logic works)

1. **Implement provisional rule set generation** (grill tool creates rule set with all rules marked `provisional`).
2. **Implement provisional document rendering** (same as phase 1, but marked `[provisional]` at the top).
3. **Integrate into grill workflow** (new step after Hot Seat, before deep dive optional continuation).
4. **Test:** Run engineering-grill on a new project; verify provisional rule set is created, provisional documents are written, they carry `[provisional]` marking.
5. **Test:** Follow up with an audit run on the same project; verify rules are upgraded from provisional to verified-pass/verified-finding, old dates and notes are preserved.

**Definition of done:** A grill-then-audit workflow produces coherent standards that evolve from intent to verified fact.

### Deployment

- Phase 1 lands on `main` first (audit side only).
- Phase 2 lands on a second PR (grill side), after phase 1 is live.
- The two PRs may be written in parallel but phase 1 merges first.

## Consequences, risks, and open questions

### Maintenance burden of stack profiles

**Consequence:** The tool ships with stack profiles baked into the rules pack (e.g. Python, React, FastAPI). These must be maintained as the technologies age.

**Risk:** A stack profile that falls out of date silently encodes stale best practice into new projects. The audit will not notice; the user will only notice if they re-examine the rules and find them anachronistic.

**Mitigation:** Stack profiles carry a `maintained_until` date in the pack metadata. When a profile reaches its review date, the tool warns the user at grill time ("The Python 3.10 profile was last reviewed 2024-01-01; consider reviewing it for this project").

### Cost of the localhost approval page sitting on the critical path

**Consequence:** Every audit must wait for the user to review and approve the standards diff before documents are written. If the user closes the browser tab or the page times out, the approval is lost and must be redone.

**Risk:** A user who runs an audit on a large project (many domains, many findings) will see a large diff. They may quickly approve without reading, or may close the browser and lose the approval.

**Mitigation:** The diff view shows a summary count at the top ("3 new rules, 2 rules upgraded from provisional, 1 finding"). The user can collapse sections to review high-level changes without reading every rule. The approval form includes a checkbox "I have reviewed the changes above" to encourage deliberation.

### Risk of three documents drifting if anything is ever hand-rendered

**Consequence:** The three documents must always be rendered from the single rule set. If anyone ever hand-edits a rule entry instead of using the tool, the rule set and the document will diverge.

**Risk:** A developer fixes a typo in the human standard without updating the rule set. The next audit run will overwrite their typo fix. Or they add a rule to the policy that is not in the rule set, and the next audit overwrites it.

**Mitigation:** The managed-block protocol prevents this. Hand edits outside the block are safe; edits inside the block are lost on the next regeneration. The user is warned at approval time about what will be overwritten.

### Rule set persisted across runs is new state that can go stale or be corrupted

**Consequence:** The rule set is stored in `audit-output/rule-set.json` and is meant to be committed to git (or at least retained across audits). It is now part of the project's state, like `CLAUDE.md`.

**Risk:** The file can be manually corrupted, edited by hand without re-running the tool, or left in an inconsistent state if the tool crashes. A corrupted rule set will produce corrupted documents.

**Risk:** A rule is verified as passing, then the code changes to break it, but the rule set is not updated (the audit was never re-run). The document claims the rule passes when it does not.

**Mitigation:** The tool validates the rule set schema on load and rejects it if it is malformed. The tool always re-audits selected domains, so stale verdicts will be discovered if the audit is re-run. The user is advised to commit the rule set to git so diffs are visible and accidental changes are caught by review.

### Stack mismatch handling requires manual user judgment

**Consequence:** If the grill recorded one stack and the audit observes another, the tool stops and asks the user which is correct. There is no automatic recovery.

**Risk:** The user chooses the wrong stack (e.g. "use grill stack" when the code actually uses the observed stack). The audit will then record the wrong rules as met.

**Mitigation:** The tool shows the evidence for both choices. It explains the consequences of each choice (which rules apply, which stack profiles are loaded). The user makes an informed decision.

### Provisional rules with no re-audit remain unverified forever

**Consequence:** A rule marked provisional at grill time is upgraded to verified-pass only if the audit checks it. If the audit runs on a subset of domains, some provisional rules may never be verified.

**Risk:** The documents permanently carry mixed status (some provisional, some verified). Over time, readers stop trusting the provisional markings.

**Mitigation:** The grill and audit workflows are designed to gradually improve coverage. If a domain was never audited, its rules remain provisional, and the document shows "not yet audited" next to them. The user is encouraged to run a full audit eventually.

### Open question: How to handle rules the audit marks not-applicable?

**Decision not yet made, suggested approach:**

At grill time, a rule is provisional. At audit time, the audit may mark it not-applicable (e.g. "this rule assumes the project uses a database; this is a CLI-only tool, so the rule does not apply").

The rule set should record `status: "verified-not-applicable"` with today's date and the reason. The three documents should show it in a "rules that do not apply" section, clearly marked so readers know this is not a defect.

**Consequence:** The engineering policy will grow even when no findings are discovered, because every not-applicable rule is a kept commitment ("we are not required to follow this because it does not apply here").

This is intentional. It shows what was checked and explicitly ruled out, which is transparency.

## Explicit non-goals

1. **Hand-authored documentation.** The three documents are always rendered from the rule set. They are not meant to be written by hand, and are not locations for documentation, rationale, or design decisions outside the rule text itself.
2. **Automated repair.** The tool does not propose fixes to findings or attempt to generate code patches. It reports findings; the team fixes them.
3. **Integration with external policy systems.** The engineering policy document is standalone markdown, not integrated with compliance/audit systems or JIRA. External integration is left to the user.
4. **Support for multiple rule sets or branching.** A project has one rule set at a time. Branching strategies (e.g. "this branch has different rules") are out of scope.
5. **Rollback or versioning of rule sets.** The rule set is immutable once written; it is not versioned within the tool. If the user wants to undo a change, they git revert the file.
6. **Live web lookup of stack profiles or current best practice.** All rules and profiles are baked into the pack. No runtime web requests are made.
7. **Merging of hand-edited content within managed blocks.** If the user edits content inside a managed block, it is overwritten on the next run. This is by design. The block is owned by the tool, and edits there are temporary.

---

**End of specification**
