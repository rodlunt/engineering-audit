# Engineering Grill documentation formats

Create these files lazily. Write only confirmed material and keep framework rule text in the
rules pack.

## Contents

- [Engineering coverage](#engineering-coverage)
- [Project language](#project-language)
- [Architecture decision record](#architecture-decision-record)

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

| Domain | Status | Basis | Source | Derived | Asked | Answered | Resolved by cross-reference | Deferred | Not asked | Revisit trigger |
|---|---|---|---|---|---|---|---|---|---|---|
| D01 Designing a Data Model | active-now | Persistent customer records are planned. | mcp | 2 | 1 | 1 | 1 | 0 | 0 | |
| D02 Requirements Elicitation | active-now | New system. | mcp | 2 | 1 | 0 | 0 | 1 | 1 | |
| D08 Threat Modelling and Risk | active-now | Bulk personal data. | fallback | 1 | 1 | 1 | 0 | 0 | 0 | |
| D15 Interface Design | active-now | Operator screens planned. | **none** | n/a | 0 | 0 | 0 | 0 | n/a | |
| D09 Incident Response | required-later | The service will run in production. | | | | | | | | Deployment topology settled |
| D16 Presenting Data | not-applicable | The product has no decision-support output. | | | | | | | | Reporting enters scope |
| **Total** | | | | **5** | **3** | **2** | **1** | **1** | **1** | |

## Question outcomes

Each retained question has exactly one outcome. The four stable outcomes are `answered`,
`resolved-by-cross-reference`, `deferred`, and `not-asked`.

| Session | Question | Domain | Outcome | Decision or answer | Provenance | Reason |
|---|---|---|---|---|---|---|
| fresh | Q1 | D01 | answered | DEC-001: Use the existing customer ledger | direct user answer | The user chose the existing ledger. |
| fresh | Q2 | D01 | resolved-by-cross-reference | DEC-001: Use the existing customer ledger | earlier decision DEC-001: Use the existing customer ledger | The earlier decision resolves this question too. |
| fresh | Q3 | D02 | deferred | - | user was asked | The user deferred this decision to the next release. |
| fresh | Q4 | D02 | not-asked | - | retained but not shown or resolved | - |
| fresh | Q5 | D08 | answered | DEC-002: Require a threat review | direct user answer | The user required a threat review. |
| resumed | Q1 | D01 | answered | DEC-001: Use the existing customer ledger | direct user answer | The user chose the existing ledger. |
| resumed | Q2 | D01 | resolved-by-cross-reference | DEC-001: Use the existing customer ledger | earlier decision DEC-001: Use the existing customer ledger | The earlier decision resolves this question too. |
| resumed | Q3 | D02 | deferred | - | user was asked | The user deferred this decision to the next release. |
| resumed | Q4 | D02 | not-asked | - | retained but not shown or resolved | - |
| resumed | Q5 | D08 | answered | DEC-002: Require a threat review | direct user answer | The user required a threat review. |

## Session totals

| Session | Derived | Asked | Answered | Resolved by cross-reference | Deferred | Not asked |
|---|---:|---:|---:|---:|---:|---:|
| fresh | 5 | 3 | 2 | 1 | 1 | 1 |
| resumed | 5 | 3 | 2 | 1 | 1 | 1 |

When the Hot Seat collapses near-duplicate questions from multiple domains into a single merged
question, the answer counts as asked and answered for every domain whose derived question it
subsumes. Merged questions are noted explicitly in the record (e.g., "Q1 merged from d02/d15");
per-domain row totals may therefore exceed the distinct questions actually put to the user, so
the `**Total**` row reports distinct question count, not the sum of per-domain columns.
If a merged question is `resolved-by-cross-reference`, apply that outcome to every derived
question it subsumes, retain the same earlier decision provenance and reuse reason, and count
those rows as resolved rather than answered.

The count columns are what make a short session legible afterwards. A run that asked
three of five and a run that asked all five are the same document
without them, and the second is the only one that earned its conclusions.

Fill them only for `active-now` domains; the other states have no derived questions
and leave the count cells empty rather than writing a zero, because zero is a
finding and blank is an absence.

`Source` records how the domain was actually read: `mcp` when the tool returned the
document, `fallback` when the tool was unreachable and the rules pack was read
instead, and `none` when no source was reached at all. A `none` domain contributes
`n/a` rather than `0` to Derived and Not asked, and is excluded from the totals. Its
questions are unknown, not zero, and a table that cannot tell those apart is the
reason this column exists.

## Confirmed decisions

### <Domain id and title>

#### <Decision title>

- **Framework:** <stable rule ids>
- **Decision:** <what was decided>
- **Reason:** <project-specific rationale and trade-off>
- **Evidence or artifact:** <existing evidence or artifact that must be produced>
- **Status:** confirmed | build-gate | verification-gate

## Deferred triggers

- **<Domain>:** Load when <specific project event or prerequisite>.

## Build and verification gates

- [ ] <Observable artifact or acceptance condition> (<rule ids>)

## Open items

- <Unsettled question, owner if known, and what it blocks.>

## Residual risks

- <Risk consciously retained after the decision and why.>
```

Keep every domain returned by `list_domains` in the coverage table. Give each
`not-applicable` entry a project-specific absent precondition and each `required-later` entry a
concrete revisit trigger. Use only identifiers read from the current framework source.

The `Question outcomes` table records one stable outcome per retained question. `answered` means
that the user gave a direct answer. `resolved-by-cross-reference` means that the question was not
asked directly because an earlier decision resolves it. This outcome is valid only when its
`Provenance` names the earlier decision identifier and title, and its `Reason` states why that
decision is reused. `deferred` means that the question was asked and postponed with a reason.
`not-asked` means that the question was retained but not shown and not resolved.

When a session resumes, preserve each question's outcome and provenance. A
`resolved-by-cross-reference` outcome does not become `answered` just because the session
resumed. The domain and session totals must satisfy `asked = answered + deferred` and
`derived = asked + resolved-by-cross-reference + not-asked`.

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
