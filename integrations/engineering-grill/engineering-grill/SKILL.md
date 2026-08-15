---
name: engineering-grill
description: Run a project-start interview against the live engineering rules pack, decide which engineering domains apply, and record terminology, decisions, risks, and build gates. Use when a user explicitly asks for an Engineering Grill before building or substantially redesigning a project.
---

# Engineering Grill

Begin only when the user explicitly invokes Engineering Grill.

Run a structured project-start interview grounded in the engineering framework. Reach a shared
understanding of what must be designed, built, and verified. Leave project implementation for a
separate request after the interview.

Run only in a fresh, non-audit session with no audit run active or starting. Do not begin or
continue a Grill from inside an audit session. If an audit is active, finish it and open a new
session before starting the Grill. `list_domains` and `get_domain` are side-effect-free for this
Grill only when no run is active; do not treat them as safe to call from an audit.

Treat the framework as decision support. The intended system determines applicability. An empty
or new repository is evidence about project stage, not evidence that a domain is irrelevant.

## Load the live framework

Use the engineering-audit MCP's read-only tools as the canonical source:

1. Call `list_domains` at the start of every session. Check the returned domains, triggers, rule
   counts, and `skipped_files`. State the number of domains returned. Report skipped files because
   they make the visible coverage incomplete. This call must happen before any framework-derived
   claim or question.
2. After the user approves the provisional scope, call `get_domain("dNN")` for each
   `active-now` domain. Read the full returned document before deriving questions from it.
3. Use only `list_domains` and `get_domain` for framework access. This interview is not an audit
   run and produces no audit verdict or report.

Never call audit lifecycle tools, including `begin_run`, `start_config`, `get_config`,
`record_domain_result`, `file_issues`, or `render_report`. The Grill must not start, resume,
record, or render an audit.

### Full-result and unavailable states

A large `get_domain` response may be reported as spooled to a file, for example with an error that
says the result exceeded the output limit and was saved to `<path>`. A spool is a successful fetch,
not an unavailable MCP. Read that file from start to finish before asking anything from the domain.
Do not derive questions from the part that fitted, ask partial questions, or silently replace the
spooled result with another source. If the spool cannot be read in full, record that domain's
availability as `partially-loaded`, keep its classification in the four-state triage set, and stop
the framework-specific interview rather than guessing.

Only a genuine MCP-unavailable state (for example, the tool is missing or the connection fails)
permits considering a local rules directory. A nearby checkout, `ENGINEERING_AUDIT_RULES_DIR`, or
another local source is not the live MCP result and must never be described as canonical live
coverage. Prefer stopping with a clear `framework unavailable` status. If the user explicitly
authorises a local fallback, label every resulting question and document as `local, non-framework
scaffolding`; do not claim that the framework was loaded or covered, and do not use that fallback
for a spooled response.

If no live source is available, explain what is missing and stop the framework-specific interview.
Persist `framework_state: unavailable` with an incomplete, early-exit checkpoint and do not claim
framework coverage. Never claim framework coverage from model memory.

Read the live list every time. Allow added, removed, renamed, and updated domains to flow from
the rules pack instead of maintaining a domain list in this skill.

## Establish project facts

Inspect available project evidence before asking questions: instructions, context documents,
ADRs, README and design documents, manifests, configuration, representative source, tests, and
delivery workflows. For an idea-only project, use the user's brief as the starting evidence and
treat the intended system as the subject.

Build a fact map covering:

- the problem, outcome, users, stakeholders, and operating environment;
- data, persistence, external interfaces, trust boundaries, and sensitive assets;
- human interfaces, accessibility, reports, dashboards, and decisions supported;
- deployment, availability, recovery, delivery machinery, and expected scale;
- cost, estimates, regulation, safety, privacy, and public impact.

Find repository, environment, and current external facts yourself. Gather user intent,
constraints, trade-offs, and facts that cannot be discovered through the one-question pacing
below.

## Triage every returned domain

Classify every domain returned by `list_domains` before deep questioning:

- **active-now**: its decision moment is present in the current design work;
- **required-later**: the intended project is likely to reach it after a named prerequisite or
  at a later stage;
- **not-applicable**: the intended system lacks its precondition; record the absent precondition;
- **unknown**: one missing fact or decision prevents classification; turn it into a question.

If the returned live domain list contains a requirements domain, activate the returned domain whose
trigger covers requirements for every new system or substantial feature. Claim framework
requirements coverage only when that live returned domain was loaded and read. If the pack has no
requirements domain, baseline questions about the problem, users, outcome, and scope may still
provide project scaffolding, but label them explicitly `non-framework`; never invent a domain or
rule id and never claim requirements coverage from memory or a local fallback. Decide every other
domain from its live trigger and the intended system. Distinguish "not represented in the
repository yet" from "not part of the intended system."

Match the trigger's subject, not a nearby keyword. Generic operational risk does not activate
security threat modelling without an adversary, asset, vulnerability, or security-control
decision. A commercial product does not activate estimating and pricing until a cost, value,
estimate, price, or business-case decision is current. Ordinary command output does not activate
presenting data unless someone must interpret it to make a decision.

Present the initial coverage map before reading any active-domain document. Give a short reason
and revisit trigger for each classification. Recompute the map after every answer and whenever the
project changes shape.

Before any active-domain `get_domain` call, show a provisional scope preview from the
`list_domains` metadata and the fact map. State:

- `Active-now domains: <N>`.
- `Full documents/rule inputs to read: <domain ids or slugs and their rule counts>`.
- `Provisional question turns: <low>-<high>`. Base the low end on named, known decision areas in
  the fact map. Add each named uncertainty that could open a distinct decision to form the high
  end. State the assumptions. Do not imply an exact count before full derivation, and do not use
  a target or minimum.

Ask exactly one scope decision: whether to continue with this coverage and provisional estimate or
adjust the scope. Wait for the answer. If the scope changes, recompute the map, active-domain
count, rule inputs, and provisional range before asking again.

After approval, read every active-domain document in full and derive the design tree. Reject
generic candidates before they enter the question ledger. Before the first deep question, state
the retained derived total, or an updated range when named unresolved branches still change it.
For an updated range, define the low end as retained items ready on the current frontier and the
high end as those items plus every named reachable branch. Explain any change from the provisional
preview.

## Interview through a design tree

Map the work as a design tree: each settled decision unlocks the decisions that depend on it.
The **frontier** is every decision whose prerequisites are settled. Keep blocked branches in open
questions with their unresolved prerequisite, not in the frontier. In persisted checkpoints, record
the settled prerequisites that make each `next_frontier` item ready as
`prerequisites_satisfied`.

Use dependency readiness as the first ordering rule. Keep a decision behind its named
prerequisite until that prerequisite is settled. Among decisions on the current frontier, rank
first by reversibility, then by blast radius:

- `irreversible-once-shipped`: changing it later needs migration, compatibility work, or cannot
  fully restore the old behaviour;
- `expensive-to-change`: changing it later needs substantial rework, coordination, or disruption;
- `cheap-to-change`: changing it later is local and low-risk.

Treat **blast radius** as the parts of the product, data, users, operations, or delivery process
that must change or may be harmed if the decision is wrong. Within one reversibility value, put
the widest project-specific blast radius first. Use the domain wave below only as tie guidance
after readiness and risk:

1. requirements and success conditions;
2. ethical, regulatory, commercial, and estimation constraints;
3. system models, architecture, and deployment boundaries;
4. data, service contracts, human interfaces, and decision outputs;
5. secure coding, trust boundaries, threats, and security risk;
6. code structure and testing strategy;
7. repository, delivery, production readiness, and incident response;
8. fault diagnosis when the work concerns an existing or running system.

Derive only decisions with a project fact, design choice, dependency, user, artefact, or failure
scenario. Reject a candidate that could be asked unchanged of any project before assigning it an
item id. Keep generic filler out of `Derived` and `Not asked` counts. Never pad a count to reach a
target.

Format each question like this:

```markdown
❓ **Q1** - **<decision title>** `[domain and rule ids]`: <project-specific question>

<Why this matters and the failure it prevents.>

⚠️ **Risk:** **Reversibility:** <allowed value>. **Blast radius:** <plain-language scope>.
**Cost if wrong:** <project-specific consequence>.

➡️ <Recommended answer for this project and the trade-off behind it.>
```

Ask exactly one decision question per user-facing turn. Do not batch a frontier or join separate
decisions with "and". After the user answers, update the fact map, design tree, domain
classifications, deferred triggers, and next frontier before asking the next question.

### Question ledger

Track every retained, project-specific question or decision item with a stable item id, domain and
rule ids, prerequisite, risk data, and its ledger state:

- `DERIVED`: item retained in the cumulative derived set. This is an inclusion marker, not a
  persisted current status.
- `ASKED`: transient marker while the question is shown and its answer is pending. Never persist
  this state at a checkpoint.
- `ANSWERED`: question was shown, answered, and the decision was captured.
- `DEFERRED`: question was shown but not answered. Record the reason and a revisit trigger.
- `NOT ASKED`: retained item has not been shown at this checkpoint or will not be shown in this
  session. A question held behind a prerequisite remains `NOT ASKED` until it is shown, then it
  may become transient `ASKED`.

Generic candidates are rejected before this ledger, so they have no item id and never enter any
count. Use `DERIVED` when an item enters the set, then transient `ASKED`, then `ANSWERED` or
`DEFERRED` after the user turn. Persist an unshown retained item as `NOT ASKED` at each checkpoint
until it is shown. If a later answer reopens a decision, derive a new item rather than overwriting
an existing item.

At every persisted checkpoint, completion, and early exit, show a compact human-readable summary by
domain and for the whole interview. Count distinct retained items, not repeated turns:

- `Derived` is cumulative retained items.
- `Asked` is cumulative retained items shown at least once.
- `Answered` and `Deferred` are current outcomes for asked items.
- `Not asked` is retained items not yet shown at that checkpoint.

The totals must satisfy `Asked = Answered + Deferred` and `Derived = Asked + Not asked`. Persist
one current state per retained item at each checkpoint: `NOT ASKED`, `ANSWERED`, or `DEFERRED`.
Keep `ASKED` transient. At completion or early exit, make those current states final for the
session. If an in-flight `ASKED` item is interrupted, convert it to `DEFERRED` with reason
`interrupted before answer` and record a resume trigger. Keep this arithmetic visible without
making the user read raw JSON. Do not invent a target count.

For every rule in a loaded domain, choose one treatment:

- answer it from inspected evidence and state that evidence;
- validate an already-settled decision;
- ask a user decision question;
- record why its precondition does not apply;
- convert an implementation-time obligation into a named build or verification gate;
- keep it open behind an unsettled prerequisite.

Translate rules into concrete decisions. Ask neither "should we follow best practice?" nor a
verbatim checklist. Add stable rule identifiers to framework-derived questions, explain the
failure being prevented in plain language, and recommend an answer grounded in both the rule and
the project. Preserve uncertainty where the framework has a gap or credible sources conflict.

Load a newly active domain before deriving or asking questions from it. Do not derive questions
from a partial domain document.

## Checkpoints and recovery

After each user response that changes facts, scope, or an item status, checkpoint before asking the
next question. The checkpoint includes the round number or timestamp, fact map, design-tree
frontier, every returned domain's current classification and separate availability, domains whose
full documents were read, deferred triggers, the question ledger and its status totals, open
questions, and the ready next frontier. Persist the current item states and cumulative totals with
the confirmed project records when a project location exists; otherwise keep the same checkpoint in
the conversation draft. The draft is a checkpoint even though it is not yet on disk. Show its full
compact accounting summary, including `Derived`, `Asked`, `Answered`, `Deferred`, and `Not asked`,
before any save-location question and before any next deep question. After the first confirmed
decision, ask exactly one location question in that turn. Do not combine the location question with
a deep decision question. Once the user supplies a location, write or update the checkpoint there
and continue.

If the user stops, the MCP becomes unavailable, a tool result cannot be read in full, or the
session is interrupted, write an explicit incomplete marker at the checkpoint boundary:
`Status: incomplete: checkpoint after round <N>; resume from <next frontier>`. Include what was
confirmed, what was only scaffolding, which domains were unavailable or only partially loaded,
the questions still open, and the visible status totals. An incomplete marker is not a completion
or coverage claim. On interruption, convert any in-flight `ASKED` item to `DEFERRED` with reason
`interrupted before answer` and a resume trigger. On an early exit, mark every remaining unshown
retained item `NOT ASKED` with the early exit reason. A question is `DEFERRED` only after it was
shown and not answered, and only when its reason and revisit trigger are recorded.

On recovery, read the checkpoint and incomplete marker first, start a fresh non-audit session,
reload `list_domains`, and reconcile changed or renamed domains before continuing from the saved
frontier. Never present a recovered incomplete interview as complete until all completion criteria
below and the user's confirmation are satisfied.

## Capture confirmed decisions

Document only confirmed material. If no project location exists yet, keep a conversation draft
until the first decision is confirmed, then ask where to save it.

Read [the documentation formats](references/documentation-formats.md) before writing. Maintain:

- `CONTEXT.md` as a glossary of agreed project language without implementation detail;
- `docs/engineering-coverage.md` as the domain map, decision ledger, build gates, and risks;
- an ADR under `docs/adr/` only for a hard-to-reverse, surprising decision made through a real
  trade-off.

Cross-check confirmed statements against existing code and documents. Surface contradictions
instead of silently choosing one version. Capturing design documents is part of the interview;
leave product code, configuration, infrastructure, and delivery systems unchanged.

## Complete the grill

Finish only when:

- every returned domain has an explicit, reasoned classification;
- every `active-now` domain's full rules were read;
- every applicable rule became inspected evidence, a confirmed decision, a build or verification
  gate, or an explicitly open item;
- no `unknown` domain or reachable design-tree branch remains silent;
- every retained question item has a terminal outcome: `ANSWERED`, `DEFERRED` with a named reason
  and trigger, or `NOT ASKED` with a reason; no in-flight `ASKED` marker remains;
- the cumulative status totals are shown and satisfy `Asked = Answered + Deferred` and
  `Derived = Asked + Not asked`;
- the glossary, qualifying ADRs, and engineering coverage document match the conversation;
- the user confirms the shared understanding.

End with a compact handoff: project intent, active and deferred domains, important decisions,
build gates, residual risks, open items, and the final question status totals. Recommend a
post-build engineering audit as the verification stage. Start that audit only after a separate
user request.
