# Engineering Grill documentation formats

Create these files lazily. Write only confirmed material and keep framework rule text in the
rules pack.

## Contents

- [Checkpoint and recovery](#checkpoint-and-recovery)
- [Question accounting](#question-accounting)
- [Engineering coverage](#engineering-coverage)
- [Project language](#project-language)
- [Architecture decision record](#architecture-decision-record)

## Checkpoint and recovery

Keep one current checkpoint with the confirmed project records. When a project location exists,
write or update it in `docs/engineering-coverage.md` under `## Grill checkpoint`. When no project
location exists, keep the full record in the conversation draft. Show its complete accounting
summary for `derived`, `asked`, `answered`, `deferred`, and `not_asked` before asking where to save
it or asking the next deep question. The draft is a checkpoint
even though it is not yet on disk. After the user supplies a location, write or update the
checkpoint there. Replace every placeholder before saving. Keep the field names and the list entries
so another session can resume without relying on conversation history.

Use this exact record format:

```yaml
status: incomplete | complete
checkpoint_kind: checkpoint | completion | early-exit
exit_reason: none | user-early-exit | framework-unavailable | interrupted | partially-loaded
round: <integer>
time: <ISO-8601 timestamp with timezone>
resume_marker: "Status: incomplete: checkpoint after round <round>; resume from <next frontier>"

fact_map_summary:
  problem_outcome: <confirmed problem and intended outcome>
  users_stakeholders_environment: <users, stakeholders, and operating environment>
  data_persistence_interfaces: <data, persistence, and external interfaces>
  trust_boundaries_sensitive_assets: <trust boundaries and sensitive assets>
  human_interfaces_and_decisions: <human interfaces, accessibility, reports, and decisions>
  delivery_and_operations: <deployment, availability, recovery, delivery, and scale>
  constraints_and_impact: <cost, regulation, safety, privacy, and public impact>

current_design_tree_frontier:
  - id: <decision id>
    title: <decision title>
    prerequisites: <settled prerequisites>
    reversibility: irreversible-once-shipped | expensive-to-change | cheap-to-change
    blast_radius: <plain-language affected users, data, interfaces, operations, or delivery process>

every_returned_domain:
  - id: <domain id returned by list_domains>
    title: <exact domain title returned by list_domains>
    classification: active-now | required-later | not-applicable | unknown
    availability: available | unavailable | partially-loaded
    basis: <project-specific reason or missing fact>
    revisit_trigger: <concrete trigger, or none>
    full_document_read: <true or false>

fully_read_domains:
  - id: <domain id>
    title: <domain title>

deferred_triggers:
  - domain_id: <domain id>
    trigger: <specific project event or prerequisite>

open_questions:
  - id: <question id>
    text: <unsettled project question>
    blocks: <decision, domain, or build gate blocked by the question>
    recovery_note: <why this remains open and what the next session must check>

question_ledger:
  - id: <question id>
    domain_id: <domain id, or none>
    rule_ids: <stable rule ids, or none>
    question: <project-specific decision question text>
    prerequisite: <named prerequisite, or none>
    reversibility: irreversible-once-shipped | expensive-to-change | cheap-to-change
    blast_radius: <plain-language affected users, data, interfaces, operations, or delivery process>
    current_state: ANSWERED | DEFERRED | NOT ASKED
    outcome_or_reason: <confirmed outcome, deferral reason, or not-asked reason>
    revisit_trigger: <specific trigger, or none>

next_frontier:
  - id: <decision id or question id>
    title: <next dependency-ready decision>
    prerequisites_satisfied: <settled prerequisites that make this item ready>

framework_state: framework | non-framework | unavailable
framework_source: <live MCP source, authorised local fallback, or none>
skipped_files: <files reported by list_domains, or none>

question_accounting:
  derived: <integer>
  asked: <integer>
  answered: <integer>
  deferred: <integer>
  not_asked: <integer>
  checks:
    asked: "asked = answered + deferred"
    derived: "derived = asked + not_asked"
```

Use `status: incomplete` with `checkpoint_kind: checkpoint` after confirmed material. Use
`status: complete` with `checkpoint_kind: completion` only after the completion criteria are met.
When the user stops or the framework cannot continue, use `status: incomplete` with
`checkpoint_kind: early-exit`, the matching `exit_reason`, and the exact `resume_marker`. In
particular, `framework_state: unavailable` maps to `status: incomplete`,
`checkpoint_kind: early-exit`, and `exit_reason: framework-unavailable`; it is not framework
coverage. Set `resume_marker: none` for a complete record. `framework_state` must say `framework`,
`non-framework`, or `unavailable`; do not imply live coverage for either of the latter two states.
Include every domain returned by `list_domains`, with exactly one of the four classifications
`active-now`, `required-later`, `not-applicable`, or `unknown`. Record fetch state separately in
`availability`: use `available` for a complete result, `partially-loaded` when a result or spool
could not be read to the end, and `unavailable` when the domain or framework source cannot be
reached. Add a domain to `fully_read_domains` only after its full `get_domain` result has been read,
including any spool file to end of file. A dependency-held item is `NOT ASKED`, not `DEFERRED`. If
an in-flight question is interrupted, mark it `DEFERRED`, record the reason, and give its resume
trigger. Keep only ready items in `next_frontier`; keep blocked branches in `open_questions` with
their unresolved prerequisite. Keep one `question_ledger` entry for every retained item. `NOT ASKED`
can later be shown during a live session. `ASKED` is transient and is never persisted. At
completion or early exit, the persisted `current_state` values are final for that session. Use
`open_questions` for recovery context only; its prose is not a competing ledger state.

## Question accounting

Copy the `question_accounting` object into each checkpoint, completion handoff, and early-exit
handoff. Count each retained question once, after the generic-question filter. `derived` is the
number of retained project-specific questions after the generic-question filter. `asked` is the
number shown to the user. `answered` is the number answered well enough to settle the question.
`deferred` is the number shown but unanswered. `not_asked` is the number retained but not shown,
including items held by a dependency or left at early exit. A shown question with no answer is
deferred, not silently omitted. If an in-flight question is interrupted, include it in `deferred`
with its reason and resume trigger. Questions removed by the generic-question filter are not
derived and do not enter these counts.

The two checks are mandatory and must hold before the record is saved:

```text
asked = answered + deferred
derived = asked + not_asked
```

At completion, explain any non-zero `deferred` or `not_asked` value and show why no reachable
decision remains unresolved. At early exit, retain the incomplete marker and report all five
counts as they stand.

## Engineering coverage

Write `docs/engineering-coverage.md` as a design ledger, not an audit report or compliance claim:

```markdown
# Engineering coverage

**Project:** <name or working title>
**Stage:** idea | discovery | design | implementation | existing system
**Last updated:** YYYY-MM-DD

## Intent

<The problem, intended outcome, users, and operating environment.>

## Domain coverage

| Domain | Status | Availability | Basis | Rules loaded | Revisit trigger |
|---|---|---|---|---|---|
| D01: Designing a Data Model | active-now | available | Persistent customer records are planned. | Full domain (15 rules) | none |
| D09: Incident Response | required-later | available | The service will run in production. | none | Deployment topology settled |
| D16: Presenting Data | not-applicable | available | The product has no decision-support output. | none | Reporting enters scope |

## Confirmed decisions

### <Domain id and title>

#### <Decision title>

- **Framework:** <stable rule ids>
- **Decision:** <what was decided>
- **Reason:** <project-specific rationale and trade-off>
- **Evidence or artifact:** <existing evidence or artifact that must be produced>
- **Status:** confirmed | build-gate | verification-gate
- **Reversibility:** <irreversible-once-shipped | expensive-to-change | cheap-to-change; how to undo or change>
- **Blast radius:** <plain-language affected users, data, interfaces, operations, or delivery process>

## Deferred triggers

- **<Domain>:** Load when <specific project event or prerequisite>.

## Build and verification gates

- [ ] <Observable artifact or acceptance condition> - <rule ids>

## Open items

- <Unsettled question, owner if known, and what it blocks.>

## Residual risks

- <Risk consciously retained after the decision and why.>
```

Keep every domain returned by `list_domains` in the coverage table. Give each
`not-applicable` entry a project-specific absent precondition and each `required-later` entry a
concrete revisit trigger. Use only identifiers read from the current framework source.

## Project language

Write `CONTEXT.md` as a glossary only:

```markdown
# Project language

| Term | Agreed meaning | Not this |
|---|---|---|
| Appointment | A reserved time between an attendee and practitioner. | A calendar reminder |
```

Keep implementation details, requirements, and design decisions out of this file.

## Architecture decision record

Create `docs/adr/NNNN-short-title.md` only when the decision is hard to reverse, surprising
without its history, and the result of a real trade-off:

```markdown
# ADR NNNN: <decision title>

**Status:** accepted
**Date:** YYYY-MM-DD
**Framework:** <stable rule ids>

## Context

<What made a decision necessary.>

## Decision

<What was chosen.>

## Alternatives considered

- **<Alternative>:** <why it was not chosen.>

## Consequences

- <Benefit, cost, limitation, or follow-up obligation.>
```

Do not copy domain documents, rule prose, source footers, or verification trails into project
documentation. Quote a source only when the user needs it to evaluate a contested decision, and
reproduce it from the loaded domain rather than memory.
