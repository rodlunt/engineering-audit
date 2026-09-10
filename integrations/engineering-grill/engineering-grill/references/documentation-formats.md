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
**Question style:** prose | multiple-choice
**Last updated:** YYYY-MM-DD

## Intent

<The problem, intended outcome, users, and operating environment.>

## Domain coverage

| Domain | Status | Basis | Source | Derived | Asked | Answered | Resolved | Deferred | Not asked | Revisit trigger |
|---|---|---|---|---|---|---|---|---|---|---|
| D01 Designing a Data Model | active-now | Persistent customer records are planned. | mcp | 9 | 1 | 0 | 0 | 1 | 8 | |
| D02 Requirements Elicitation | active-now | New system. | mcp | 8 | 2 | 2 | 0 | 0 | 6 | |
| D08 Threat Modelling and Risk | active-now | Bulk personal data. | fallback | 7 | 0 | 0 | 1 | 0 | 6 | |
| D15 Interface Design | active-now | Operator screens planned. | **none** | n/a | 0 | 0 | 0 | 0 | n/a | |
| D09 Incident Response | required-later | The service will run in production. | | | | | | | | Deployment topology settled |
| D16 Presenting Data | not-applicable | The product has no decision-support output. | | | | | | | | Reporting enters scope |
| **Total** | | | | **24** | **3** | **2** | **1** | **1** | **20** | |

When the Hot Seat collapses near-duplicate questions from multiple domains into a single merged
question, the answer counts as asked and answered for every domain whose derived question it
subsumes. Merged questions are noted explicitly in the record (e.g., "Q1 merged from d02/d15");
per-domain row totals may therefore exceed the distinct questions actually put to the user, so
the `**Total**` row reports distinct question count, not the sum of per-domain columns.

A derived question is `Resolved` rather than `Answered` when it was never put to the user because
an earlier already-confirmed decision — from this run, or from a prior run this one is resuming —
already settled it. Resolved never appears as a bare label: cite the resolving decision inline
(e.g., "resolved by D02 — single-tenant deployment, no cached third-party state" or a reference to
its ADR or coverage-table row) so a reader can trace the answer back to where it was actually
decided. `Resolved` is mutually exclusive with `Answered`: a question is one or the other, never
both, and a domain's `Resolved` count is not added into its `Answered` count. Folding a resolved
question into `Answered` would make a domain settled entirely by cross-reference look identical to
one where the user genuinely engaged with every question put to them, and those are different
findings that the ledger must keep distinguishable.

The count columns are what make a short session legible afterwards. A run that asked
three of twenty-four and a run that asked all twenty-four are the same document
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

`Question style` records the run-level setting fixed once at the start of the grill: `prose`
unless the user opted into `multiple-choice`. It never varies per domain or per question within
one run.

## Confirmed decisions

### <Domain id and title>

#### <Decision title>

- **Framework:** <stable rule ids>
- **Decision:** <what was decided>
- **Reason:** <project-specific rationale and trade-off>
- **Evidence or artifact:** <existing evidence or artifact that must be produced>
- **Status:** confirmed | build-gate | verification-gate
- **Answer style:** fixed-option | prose-fallback (only recorded when the run's question style is
  `multiple-choice`; omit entirely for a `prose` run)
- **User justification:** <the user's one-line reason for picking the recommended option, or `no
  reason given`> (only recorded in multiple-choice mode when the recommended option was picked;
  omit entirely otherwise)
- **Resolves:** <domain and question(s) settled by this decision without being asked directly>
  (only recorded when this decision resolved another domain's derived question; omit otherwise)

## Deferred triggers

- **<Domain>:** Load when <specific project event or prerequisite>.

## Build and verification gates

- [ ] <Observable artifact or acceptance condition> (<rule ids>)

## Open items

- <Unsettled question, owner if known, and what it blocks.>

## Residual risks

- <Risk consciously retained after the decision and why.>
```

`Answer style` mirrors the `Source` transparency pattern above, at the level of a single answer
rather than a whole domain: `fixed-option` when the host's fixed-option prompt tool actually
presented the question, `prose-fallback` when it fell back to prose because the tool was
unavailable for that question. It records which path was used, not whether the tool call
genuinely fired in that turn; confirming the latter is a separate, unresolved question.

`User justification` is not `Reason`: `Reason` is the recommendation's own project-specific
rationale, fixed before the user ever answered; `User justification` is the user's own reason for
accepting that recommendation, captured after the fact by the anti-rubber-stamp checkpoint. Only a
multiple-choice-mode answer that picked the recommended option carries it. It may read `no reason
given` when the user could not produce one after two attempts, in which case the answer is also
flagged for review.

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

**Default to one ADR per decision.** Bundle two or more decisions into a single ADR only when all
three hold: they were decided together in the same conversation, they share one causal narrative
(the same "what made this necessary" paragraph genuinely explains all of them), and reversing one
in isolation would not make sense without reconsidering the others. When in doubt, prefer more,
smaller ADRs over one bundled one. Each decision inside a bundle must independently clear the hard
to reverse, surprising without history, real trade-off bar on its own; a decision that only looks
weighty because it is filed next to three others in the same document has not cleared the bar, it
has borrowed weight from its neighbours, and belongs in its own record or in none at all.

Do not copy domain documents, rule prose, source footers, or verification trails into project
documentation. Quote a source only when the user needs it to evaluate a contested decision, and
reproduce it from the loaded domain rather than memory.
