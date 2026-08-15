---
name: interrogate
description: >-
  Use when the user wants their intent or a drafted plan questioned before the code exists:
  "interrogate this", "question my plan", "what am I not thinking about", or at the start of a
  greenfield feature. Fans out read-only subagents over every relevant engineering-framework
  domain, derives the full question set, triages the three most impactful across all of them, asks
  those one per turn, then offers a deep dive through the rest, recording answers and gaps into
  the plan file.
---

# Interrogate

**BETA.** New in engineering-audit v0.13.0, reshaped in v0.14.0. Question derivation has been
exercised on three returned domains individually; the cross-domain triage in this file has never
been run at all, and the loop below has never been run end to end with a real person answering.
Say so once, early, if the user has not already been told: they should know the shape of the
session may change under them, and that a question reading as a generic quiz rather than as being
about their actual work is a defect worth reporting rather than something to work around.

Turns the rules pack into questions about the work in front of you, before the work is done.
It never audits code, starts an audit run, or runs during an audit. Start it only in a fresh,
non-audit Claude Code session with no audit run active or starting. If an audit is active, finish
it and open a new session before invoking Interrogate.

The rules come from the `engineering-audit` MCP server. Two tools only: `list_domains` and
`get_domain`. They are non-attributed and side-effect-free for Interrogate only when no run is in
progress. When a run is active, `get_domain` can persist fetch metadata, so do not call either tool
from an audit session or while an audit is starting. If the run state is uncertain, stop and start
a fresh non-audit session.

**Never call `begin_run`, `start_config`, `get_config`, `record_domain_result`, `file_issues` or
`render_report`.** Those belong to the `audit` skill. Interrogate must not begin or interact with
an audit, and work that has not started yet has no audit run to operate on.

**Never load a domain's own skill inline.** Those files run 300 to 800 lines each. The whole
point of this skill is that only a subagent ever reads one.

## Flow

1. **Establish the mode.** `before` interrogates intent when no plan exists, and is the primary
   mode. `review` interrogates a drafted plan, naming the decisions it made silently or did not
   make at all. Take it from the argument (`interrogate before`, `interrogate review`). With no
   argument, pick the obvious one (a plan file with real content in it means `review`) and
   confirm in one question. Never assume silently.
2. **State the work in two or three lines and show it back.** Everything downstream keys off
   this, so a misreading is cheapest to fix here. In `review` mode, read the plan file first.
3. **Judge relevance, domain by domain. There is no cap.** Call `list_domains` and use every
   returned trigger; the call returns trigger metadata, not rule text, and is cheap. Put each
   trigger against the work and decide whether it genuinely fires. Every domain that does is in,
   whether that is two or twelve. Do not trim to a number, and do not include a domain because the
   pack would look better covered: a domain that does not fire produces questions with no grip,
   which is the failure this skill is most likely to have.
4. **Show the split and the cost before spending anything.** Print two lines that between them
   name every domain returned by `list_domains`:
   - `Relevant:` every domain that fired, one line of why each.
   - `Not relevant:` the rest, ids and slugs, with a handful of words on why not.

   Then say how many subagents that means and ask one question: go, or adjust. The user may add
   or drop any domain. This is the only point where the cost is knowable in advance, so it is the
   only fair place to ask.
5. **Fan out one read-only subagent per relevant domain, all in parallel.** Each calls
   `get_domain(<id>)`, reads the whole document, and returns the JSON below as its final message
   and nothing else. Tell each one plainly: you may call `get_domain` and read; you may not
   write, edit or create any file including scratch files, run any mutating command, or call any
   other engineering-audit tool.
6. **Triage across the whole pool, not within each domain.** Collect every question from every
   subagent, then pick **the three most impactful in the entire set**, judged on what it costs to
   get wrong. Two of the three may come from one domain and none from another; that is the point
   of triaging globally. A domain's own ranking orders it internally and says nothing about how it
   compares to another domain's worst question.

   Say the totals out loud before asking anything: how many questions came back, from how many
   domains, and that three are being put now with the rest held. **Never present the three as
   though they were all there was.**
7. **Ask those three, one question per turn.** Never batch. Never join two with "and". Record each
   answer as ANSWERED, or DEFERRED with the user's reason. "Not decided yet" is a deferral, not an
   answer; if no reason is offered, ask once, then record `none given`.
8. **Then offer the deep dive, with the real number attached.** "That is the top three of 47.
   Work through the rest?" If they say yes, go through everything held back, grouped by domain,
   still one question per turn, and offer a checkpoint at each domain boundary so they can stop
   without abandoning the record. If they say no, the remainder is NOT ASKED and is counted.
9. **Bail out is unconditional.** On stop, enough, or that will do: write the record
   immediately, mark the session `ended early`, and give the unasked count. A short session must
   never read as a complete one.
10. **Write the record as you go**, after the top three and after each domain in the deep dive,
    not only at the end, so an interrupted session still leaves an honest record rather than none.

## What each subagent returns

    {
      "domain_id": "d02",
      "domain_slug": "requirements-elicitation",
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

**Rank by cost of getting it wrong, never by rule order.** Rule order teaches; it does not weigh
risk. Ranks run 1 to N with no gaps and no ties. Return five to ten questions, eight is a good
target. Fold several rules into one question where they ask the same thing of this work, and cite
the strongest rule id. Drop rules this work does not touch.

`reversibility` is one of `irreversible-once-shipped`, `expensive-to-change` or
`cheap-to-change`, and `blast_radius` names in a few words what else has to move if the answer
turns out wrong. **These two exist so the parent can compare questions across domains, which a
within-domain rank cannot support**: d01's third-best question may matter far more than d15's
first, and nothing in either ranking says so. Judge reversibility against this work as described,
not in the abstract.

**A question that could be asked of any project is not a question, it is filler.** Never pad to
reach a number. This is the failure mode that kills the skill: rules restated without grip on
the actual work, producing a quiz instead of an interrogation.

## Triage

The parent picks the top three from the pooled set, in this order:

1. Everything marked `irreversible-once-shipped`, widest `blast_radius` first.
2. Then `expensive-to-change`, same tiebreak.
3. `cheap-to-change` reaches the top three only when nothing above it did.

Spread across domains only where the impact genuinely ties. Do not take one from each domain for
the sake of a tidy spread: three questions from one domain is the correct answer when that is
where the irreversible decisions are, and forcing variety buries a real one to make room for a
cosmetic one.

**State the arithmetic before the first question.** How many questions came back, from how many
domains, and that three are being asked now with the rest held. Three questions presented without
that sentence read as the whole interrogation, and the user calibrates their trust accordingly.

## The record

Written into this session's plan file under `## Design decisions (interrogate)`. Never overwrite
an earlier section: a second run appends `### Second pass (<date>)`.

    ## Design decisions (interrogate)

    The counts and domain ids in this example are illustrative. Use the domains and totals
    returned by the current `list_domains` call.

    Mode: before. Run: 2026-08-15.
    Status: top three answered, deep dive declined. 4 of 47 questions asked.

    | Domain | Derived | Asked | Answered | Deferred | Not asked |
    |---|---|---|---|---|---|
    | d02 requirements-elicitation | 8 | 2 | 2 | 0 | 6 |
    | d01 data-modelling | 9 | 1 | 0 | 1 | 8 |
    | d10 api-design | 7 | 1 | 1 | 0 | 6 |
    | d11 architecture-deployment | 8 | 0 | 0 | 0 | 8 |
    | d15 interface-design | 8 | 0 | 0 | 0 | 8 |
    | d08 threat-modelling-risk | 7 | 0 | 0 | 0 | 7 |
    | **Total** | **47** | **4** | **3** | **1** | **43** |

    Not relevant: d03, d04, d05, d06, d07, d09, d12, d13, d14, d16.

    ### Asked (the triaged top three, plus one from the deep dive before it was stopped)

    **D02-R01 ANSWERED. irreversible-once-shipped. Who is the one user this fails for, and what
    do they lose?**
    Small clinics with one receptionist. They lose the afternoon to re-keying bookings.

    **D01-R11 DEFERRED (waiting on Tuesday's client call). Does a sent quote copy its prices or
    point at the live rate card?**

    ### Not asked: 43 of 47

    The deep dive was offered after the top three and declined. Every domain above still holds
    unasked questions; the per-domain counts are in the table. Nothing here was judged
    unimportant, it was simply never put.

## Claude Code notes

- **The MCP tools may be deferred.** Load both in one call before starting: `ToolSearch` with
  `select:mcp__engineering-audit__list_domains,mcp__engineering-audit__get_domain`. Tell each
  subagent to do the same for `get_domain`. A subagent that cannot see the tool will otherwise
  improvise from the rules directory on disk, which is not the same thing and will not be
  reported as a failure.
- **A large domain will exceed the tool-result limit and be spooled to a file.** The full
  documents carry the private `Verification:` trails that the generated skills strip, so they run
  far larger than those files suggest: d15 measured 155,706 characters on 2026-08-15 and came
  back as `Error: result (...) exceeds maximum allowed tokens. Output has been saved to
  <path>`. **That is not a failure and not an empty fetch.** Read the spooled file in full and
  carry on. Never treat it as a reason to fall back to the pack directory, and never derive
  questions from the part that fitted: a domain half-read produces a question set that looks
  complete and is not.
- **One question per turn, and the reason is mechanical rather than stylistic.** Ask two in one
  turn and you reliably get one answer: the second is dropped, and it is recorded as ANSWERED
  because a reply arrived. That is a silent gap, and silent gaps are the one thing this skill
  exists to prevent.
- **Use `AskUserQuestion` for the relevance confirmation and the deep-dive offer**, where the options are fixed
  and few. Use plain prose for the interrogation questions themselves, which are open by design
  and must not be reduced to a multiple choice.
- **Write into this session's plan file, the one the host named.** If there is none, ask for the
  slug before writing. Never open a second file for work that already has one.
- **If the user chose to interrogate AFTER planning, nothing enforces that but you.** The offer, if
  it came from the hook below, fires once on entry to plan mode and there is no second hook on
  `ExitPlanMode`. So a choice of "afterwards" is a commitment held only in this conversation: run
  the review pass before you present the plan, and if you reach the end and realise it was missed,
  say so plainly rather than quietly presenting an uninterrogated plan.
