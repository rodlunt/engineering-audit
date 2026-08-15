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

| Domain | Status | Basis | Rules loaded | Revisit trigger |
|---|---|---|---|---|
| D01 — Designing a Data Model | active-now | Persistent customer records are planned. | Full domain (15 rules) | — |
| D09 — Incident Response | required-later | The service will run in production. | — | Deployment topology settled |
| D16 — Presenting Data | not-applicable | The product has no decision-support output. | — | Reporting enters scope |

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

- [ ] <Observable artifact or acceptance condition> — <rule ids>

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
