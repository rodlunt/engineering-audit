---
name: engineering-grill
description: Run a project-start interview against the live engineering rules pack, decide which engineering domains apply, put the highest-consequence questions first, and record terminology, decisions, risks, and build gates. Use when a user explicitly asks for an Engineering Grill before building or substantially redesigning a project, or asks for their intent or a drafted plan to be questioned before the code exists.
---

# Engineering Grill

Begin only when the user explicitly invokes Engineering Grill.

Run a structured project-start interview grounded in the engineering framework. Reach a shared
understanding of what must be designed, built, and verified. Leave project implementation for a
separate request after the interview.

Treat the framework as decision support. The intended system determines applicability. An empty
or new repository is evidence about project stage, not evidence that a domain is irrelevant.

**Say this once, early, in your own words.** This flow has never been run end to end
with a real person answering. Question derivation has been exercised against a single
brief on a few domains; the cross-domain triage and the deep dive have not been run at
all. The user should know that before they invest an hour in it, and should be asked to
report anything that reads like a generic quiz rather than a question about their actual
work, because that is the failure mode this design is most likely to have.

Two modes, and the difference is only what evidence you start from. `before` questions intent
when no plan exists, and is the primary mode. `review` questions a drafted plan, naming the
decisions it made silently or did not make at all. Take the mode from the invocation if given.
With no argument, pick the obvious one (a plan file with real content in it means `review`) and
confirm in one question. Never assume silently.

## Load the live framework

Use the engineering-audit MCP's read-only tools as the canonical source:

1. Call `list_domains` at the start of every session. Check the returned domain triggers, rule
   counts, and `skipped_files`. State how many domains were loaded. Report skipped files because
   they make the visible coverage incomplete.
2. Call `get_domain("dNN")` when a domain becomes `active-now`. Read the full returned document
   before deriving questions from it.
3. Use only `list_domains` and `get_domain`. **Never call `begin_run`,
   `record_domain_result`, `file_issues` or `render_report`.** Those belong to the `audit`
   skill. They demand a repository name and a commit, and work that has not started yet has
   neither. This interview is not an audit run and produces no audit verdict or report.

If the MCP tools are unavailable, locate the configured rules pack from project instructions,
`ENGINEERING_AUDIT_RULES_DIR`, or a nearby rules checkout. Read domain `Trigger` metadata for
triage and the full domain file when activated. If no source exists, explain what is missing and
stop the framework-specific interview. Never claim framework coverage from model memory.

When you stop for a missing framework on a host with a `claude mcp` CLI, name the most likely
cause before asking how to proceed: the server registered without `--scope user`, which ties it
to the one directory it was added from and makes it silently invisible everywhere else (issue
#245). Give the user the concrete check and fix, not just the symptom: run `claude mcp list` in
this project; if `engineering-audit` is absent here but the server works in another directory,
re-register it with `claude mcp remove engineering-audit` then the documented add command with
`--scope user`. On other hosts, name the equivalent: the server's registration is not visible
from this project's configuration.

Read the live list every time. Allow added, removed, renamed, and updated domains to flow from
the rules pack instead of maintaining a domain list in this skill.

**A large domain will exceed the tool-result limit and be spooled to a file.** The full documents
carry verification trails that the generated skill files strip, so they run far larger than those
files suggest: one domain measured 155,706 characters. That is not a failure and not an empty
fetch. Read the spooled file in full and carry on. Never treat it as a reason to fall back to the
pack directory, and never derive questions from the part that fitted: a domain half-read produces
a question set that looks complete and is not.

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

Find repository, environment, and current external facts yourself. Ask the user for intent,
constraints, trade-offs, and facts that cannot be discovered.

State the work back in two or three lines and show it to the user before going further.
Everything downstream keys off this, so a misreading is cheapest to fix here.

## Triage every returned domain

Classify every domain returned by `list_domains` before deep questioning:

- **active-now**: its decision moment is present in the current design work;
- **required-later**: the intended project is likely to reach it after a named prerequisite or
  at a later stage;
- **not-applicable**: the intended system lacks its precondition; record the absent precondition;
- **unknown**: one missing fact or decision prevents classification; turn it into a question.

Activate requirements elicitation for every new system or substantial feature. Decide every
other domain from its live trigger and the intended system. Distinguish "not represented in the
repository yet" from "not part of the intended system."

Match the trigger's subject, not a nearby keyword. Generic operational risk does not activate
security threat modelling without an adversary, asset, vulnerability, or security-control
decision. A commercial product does not activate estimating and pricing until a cost, value,
estimate, price, or business-case decision is current. Ordinary command output does not activate
presenting data unless someone must interpret it to make a decision.

**There is no cap on how many domains are active.** Every domain whose trigger genuinely fires is
in, whether that is two or twelve. Do not trim to a number, and do not include a domain because
the pack would look better covered: a domain that does not fire produces questions with no grip,
which is the failure this skill is most likely to have.

Present the initial coverage map before the deep interview. Give a short reason and revisit
trigger for each classification, and invite corrections. Recompute the map after every answer
and whenever the project changes shape.

## Derive the questions

**The invariant: no full domain document enters the conversation with the user.** A domain
document is far larger than its generated skill file suggests, and reading several into the
conversation you are trying to have with a person is the failure this step exists to avoid.

**Never load a domain's own generated skill file inline either.** Those run 300 to 800
lines each, and pulling one into the conversation defeats the same budget the invariant
protects, while giving you a stripped text rather than the full document.

The invariant is normative. The mechanism is per-host packaging:

- Where the host has constrained parallel sub-agents, fan out one read-only sub-agent per
  `active-now` domain. Tell each plainly: you may call `get_domain` and read; you may not write,
  edit or create any file including scratch files, run any mutating command, or call any other
  engineering-audit tool.
- Where the host has no sub-agents, read one domain at a time and discard it before the next.
  This trades wall-clock for context and satisfies the same invariant.

**Show the cost before spending it.** Say how many domains are active and what that means in
sub-agents or serial reads, then ask one question: go, or adjust. The user may add or drop any
domain. This is the only point where the cost is knowable in advance, so it is the only fair
place to ask.

Each derivation returns:

    {
      "domain_id": "d02",
      "domain_slug": "requirements-elicitation",
      "source": "mcp",
      "rules_total": 16,
      "questions": [
        {
          "rank": 1,
          "rule_id": "D02-R01",
          "question": "Who is the one user this fails for, and what do they lose?",
          "why": "A problem statement with no named user cannot be tested against anything.",
          "cost_if_unanswered": "You build it well for nobody and find out at the demo.",
          "reversibility": "irreversible-once-shipped",
          "blast_radius": "every screen, the data model, and what 'done' means"
        }
      ]
    }

`source` is required and is one of:

- `mcp`: `get_domain` returned the document, including the spooled-to-file case above, which is a
  successful read and not a fallback.
- `fallback`: the tool was unreachable and the documented fallback was used. Name the path read in
  a `source_detail` field. The questions are real but derived from a smaller text than the full
  domain document, so they must not be presented as equivalent.
- `none`: no source was reached at all.

The whole payload for a domain that reached nothing is this shape, and nothing more:

    {
      "domain_id": "d15",
      "domain_slug": "interface-design",
      "source": "none",
      "source_detail": "get_domain unavailable; rules directory not present",
      "rules_total": null,
      "questions": []
    }

**A derivation returning `source: "none"` returns no questions.** It must never substitute a
smaller source silently. Without this the parent cannot tell a fallback read from a real one: the
run completes, the counts look right, and a smaller question set is presented as a full
interrogation.

`reversibility` is one of `irreversible-once-shipped`, `expensive-to-change` or
`cheap-to-change`, and `blast_radius` names in a few words what else has to move if the answer
turns out wrong. These two exist so questions can be compared **across** domains, which a
within-domain rank cannot support: d01's third-best question may matter far more than d15's
first, and nothing in either ranking says so. Judge reversibility against this work as described,
not in the abstract.

Rank by cost of getting it wrong, never by rule order. Rule order teaches; it does not weigh
risk. **Ranks run 1 to N with no gaps and no ties.** Return five to ten questions per domain, eight is a good target. Fold several rules into one
question where they ask the same thing of this work, and cite the strongest rule id. Drop rules
this work does not touch.

**A question that could be asked of any project is not a question, it is filler.** Never pad to
reach a number. This is the failure mode that kills the skill: rules restated without grip on the
actual work, producing a quiz instead of an interrogation.

## The Hot Seat

The highest-consequence questions, asked first, one at a time.

**State the arithmetic before the first question.** How many questions were derived, from how many
domains, and how many are being put now with the rest held. Questions presented without that
sentence read as the whole interview, and the user calibrates their trust accordingly.

The arithmetic must separate domains that were read from domains that were not. Any domain
returning `source: "none"` is named and excluded from the derived total, because counting it as
zero questions makes an unreadable domain look like a domain with nothing to ask. Any domain
returning `source: "fallback"` is named too, with the caveat that its questions came from a
smaller text. Say it plainly: "47 questions from 5 of 6 active domains. d15 interface-design could
not be read and is not in that total."

Triage across the whole pool, not within each domain. Collect every question, then pick the most
impactful in the entire set, in this order: everything `irreversible-once-shipped`, widest
`blast_radius` first; then `expensive-to-change`; then the rest. Two of the three may come from
one domain and none from another; that is the point of triaging globally. **Do not take one
from each domain for the sake of a tidy spread.** Three questions from one domain is the
correct answer when that is where the irreversible decisions are, and forcing variety buries
a real question to make room for a cosmetic one.

**Ask these one per turn. Never batch. Never join two with "and".** The reason is mechanical
rather than stylistic: ask two in one turn and you reliably get one answer, the second is dropped,
and it is recorded as answered because a reply arrived. That is a silent gap, and silent gaps are
the thing this skill exists to prevent.

Record each answer as ANSWERED, or DEFERRED with the user's reason. "Not decided yet" is a
deferral, not an answer; if no reason is offered, ask once, then record `none given`.

**Bail-out is unconditional.** On stop, enough, or that will do: write the record immediately,
mark the session ended early, and give the unasked count. A short session must never read as a
complete one.

## The deep dive

After the Hot Seat, offer the rest with the real number attached: "that is the top three of 47,
work through the rest?" If they decline, the remainder is NOT ASKED and is counted. Nothing here
was judged unimportant; it was simply never put.

Ask in the same turn as that offer how they want the running totals shown: a table, a plain list,
or not shown in conversation at all. The record keeps its table regardless, because it is a
durable artefact that other people read and compare between runs. This costs no extra turn, which
is why it belongs here and not in a question of its own.

The deep dive is where **rounds** are permitted, and this is the one place the two halves of this
skill genuinely differed. Rounds are safe here because these questions are not the
irreversible-once-shipped ones; those were put one at a time in the Hot Seat.

Map the work as a design tree: each settled decision unlocks the decisions that depend on it.
The **frontier** is every decision whose prerequisites are settled. Ask the whole frontier in a
round, then wait for the user's answers before continuing.

Work in dependency waves so later questions do not assume unsettled earlier choices. Prefer this
order when the project supports it:

1. requirements and success conditions;
2. ethical, regulatory, commercial, and estimation constraints;
3. system models, architecture, and deployment boundaries;
4. data, service contracts, human interfaces, and decision outputs;
5. secure coding, trust boundaries, threats, and security risk;
6. code structure and testing strategy;
7. repository, delivery, production readiness, and incident response;
8. fault diagnosis when the work concerns an existing or running system.

**A round is bounded by the next real dependency seam, not by a fixed number.** Where a frontier
runs long, split it at the seam and make that prerequisite explicit. Never take an arbitrary first
seven. Offer a checkpoint at each domain boundary so the user can stop without abandoning the
record.

Format each question like this:

```markdown
❓ **Q1** - **<decision title>** `[domain and rule ids]`: <project-specific question>

<Why this matters and the failure it prevents.>

➡️ <Recommended answer for this project and the trade-off behind it.>
```

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

After each round, update the fact map, design tree, domain classifications, deferred triggers,
and next frontier. Load a newly active domain before asking questions from it.

## Capture confirmed decisions

Document only confirmed material. If no project location exists yet, keep a conversation draft
and ask where to save it after the first decision is confirmed.

**Write the record as you go**, after the Hot Seat and after each domain in the deep dive, not
only at the end, so an interrupted session still leaves an honest record rather than none.

Read [the documentation formats](references/documentation-formats.md) before writing. Maintain:

- `CONTEXT.md` as a glossary of agreed project language without implementation detail;
- `docs/engineering-coverage.md` as the domain map, decision ledger, build gates, and risks;
- an ADR under `docs/adr/` only for a hard-to-reverse, surprising decision made through a real
  trade-off.

**A later grill reads and updates existing documents, checks them against the conversation, and
continues ADR numbering. It does not replace them blindly.** A second run appends rather than
overwriting; never destroy an earlier session's record to write this one.

`docs/engineering-coverage.md` carries the per-domain counts: how many questions were derived,
asked, answered, deferred and never put, plus the `source` each domain was read from. A run that
asked four of forty-seven and a run that asked all forty-seven must not look the same afterwards.

Cross-check confirmed statements against existing code and documents. Surface contradictions
instead of silently choosing one version. Capturing design documents is part of the interview;
leave product code, configuration, infrastructure, and delivery systems unchanged.

## Complete the grill

Finish only when:

- every returned domain has an explicit, reasoned classification;
- every `active-now` domain's full rules were read, or its failure to be read is recorded;
- every applicable rule became inspected evidence, a confirmed decision, a build or verification
  gate, or an explicitly open item;
- no `unknown` domain or reachable design-tree branch remains silent;
- the glossary, qualifying ADRs, and engineering coverage document match the conversation;
- the user confirms the shared understanding.

A session ended early satisfies none of these and must say so. Report what was asked, what was
held, and what was never reached.

End with a compact handoff: project intent, active and deferred domains, important decisions,
build gates, residual risks, and open items. Recommend a post-build engineering audit as the
verification stage. Start that audit only after a separate user request.

## Host notes

- **The MCP tools may be deferred.** Load both before starting, and tell any sub-agent to do the
  same for `get_domain`. A sub-agent that cannot see the tool will otherwise improvise from the
  rules directory on disk, which is not the same thing. The required `source` field is what makes
  that difference visible rather than leaving it to be inferred from a payload that looks
  identical either way.
- **Where the host offers a fixed-option prompt**, use it for the coverage-map confirmation, the
  cost confirmation, and the deep-dive offer, where the options are few and known. Use plain prose
  for the interview questions themselves, which are open by design and must not be reduced to a
  multiple choice.
- **Where the host has a plan file**, write the record into the one the host named. If there is
  none, ask for the location before writing. Never open a second file for work that already has
  one.
- **If the user chose to run this AFTER planning, nothing enforces that but you.** On Claude
  Code the offer comes from a hook that fires once on entry to plan mode, and there is no
  second hook on `ExitPlanMode`. So a choice of "afterwards" is a commitment held only inside
  this conversation: run the review pass before you present the plan, and if you reach the end
  and realise it was missed, say so plainly rather than quietly presenting an uninterviewed
  plan.
