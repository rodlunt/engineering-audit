---
name: interrogate
description: Use when the user wants their intent or a drafted plan questioned before the code exists: "interrogate this", "question my plan", "what am I not thinking about", or at the start of a greenfield feature. Selects at most three engineering-framework domains, fans out read-only subagents to derive ranked questions from the rules, asks them one per turn, and records the answers and the gaps into the plan file.
---

# Interrogate

Turns the rules pack into questions about the work in front of you, before the work is done.
It never audits code and it never starts an audit run.

The rules come from the `engineering-audit` MCP server. Two tools only: `list_domains` and
`get_domain`. Both work with no run in progress, verified in `server.py`, where every run side
effect inside `get_domain` sits behind `if run is not None`.

**Never call `begin_run`, `record_domain_result`, `file_issues` or `render_report`.** Those
belong to the `audit` skill. They demand a repository name and commit, and work that has not
started yet has neither.

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
3. **Shortlist.** Call `list_domains`: sixteen triggers, no rule text, cheap. Score each trigger
   against the work and take **at most three**. Prefer the design-time domains: d02, d03, d01,
   d11, d10, d15, d08, d05, d13, d12. The other six are eligible only when the work plainly
   fires their trigger.
4. **Show the cut before acting on it.** Print three lines that between them name all sixteen:
   - `Selected:` up to three, one line of why each.
   - `Cut at the cap:` every domain whose trigger fired but lost a slot, one line each on what
     it would have covered. This line is the honesty-critical one. A relevant domain that
     vanishes without being named reads as coverage.
   - `Not relevant:` the rest, ids and slugs only, no prose.

   Then ask one question: accept, or swap. The user may name any domain to swap in, and may
   raise the cap. **You may never raise it yourself.** If they raise it, say so in the record.
5. **Fan out one read-only subagent per selected domain, in parallel.** Each calls
   `get_domain(<id>)`, reads the whole document, and returns the JSON below as its final message
   and nothing else. Tell each one plainly: you may call `get_domain` and read; you may not
   write, edit or create any file including scratch files, run any mutating command, or call any
   other engineering-audit tool.
6. **Ask.** One question per turn, in shortlist order. Never batch. Never join two questions with
   "and". Record each answer as ANSWERED, or DEFERRED with the user's reason. "Not decided yet"
   is a deferral, not an answer; if no reason is offered, ask once, then record `none given`.
7. **Tranches.** Ask ranks 1 to 3 of a domain, then spend one turn on the routing question: go
   deeper here, or move to the next domain. Deeper serves the next three by rank, and the offer
   repeats at every boundary until the set runs out, at which point say so with the number
   ("that is all d02 had, eight questions"). Moving on marks the remainder NOT ASKED, counted.
8. **Bail out is unconditional.** On stop, enough, or that will do: write the record
   immediately, mark the session `ended early`, and give the unasked count per domain. A short
   session must never read as a complete one.
9. **Write the record after each domain finishes**, not only at the end, so an interrupted
   session still leaves an honest record rather than none.

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
          "cost_if_unanswered": "You build it well for nobody and find out at the demo."
        }
      ]
    }

**Rank by cost of getting it wrong, never by rule order.** Rule order teaches; it does not weigh
risk. Ranks 1 to 3 go to the decisions that are expensive or impossible to reverse once code
exists: data shape and identifiers, published contracts, trust boundaries, what "done" means, who
the user actually is, and failures that surface late and somewhere else. Deeper ranks take
refinements, technique choices and process hygiene, the things that stay cheap to change.

Ranks run 1 to N with no gaps and no ties. Return five to ten questions, eight is a good target.
Fold several rules into one question where they ask the same thing of this work, and cite the
strongest rule id. Drop rules this work does not touch.

**A question that could be asked of any project is not a question, it is filler.** Never pad to
reach a number. This is the failure mode that kills the skill: rules restated without grip on
the actual work, producing a quiz instead of an interrogation.

## The record

Written into this session's plan file under `## Design decisions (interrogate)`. Never overwrite
an earlier section: a second run appends `### Second pass (<date>)`.

    ## Design decisions (interrogate)

    Mode: before. Run: 2026-08-15. Status: ended early by the user after 7 of 24 questions.
    Cap: 3 domains (default).

    | Domain | Asked | Answered | Deferred | Not asked |
    |---|---|---|---|---|
    | d02 requirements-elicitation | 6 of 8 | 5 | 1 | 2 |
    | d01 data-modelling | 1 of 7 | 1 | 0 | 6 |
    | d08 threat-modelling-risk | 0 of 9 | 0 | 0 | 9 |

    Cut at the cap: d10 api-design (fired, lost a slot: verbs, errors, versioning),
    d11 architecture-deployment (fired, lost a slot: topology and scaling).
    Not relevant: d03, d04, d05, d06, d07, d09, d12, d13, d14, d15, d16.

    ### d02 requirements-elicitation

    **D02-R01 ANSWERED. Who is the one user this fails for, and what do they lose?**
    Small clinics with one receptionist. They lose the afternoon to re-keying bookings.

    **D02-R04 DEFERRED (waiting on Tuesday's client call). What must be true for this to be done?**

    **Not asked: 2 of 8 remain (ranks 7 to 8). The user moved on at the second tranche boundary.**

The coverage table comes first because a missing question is an absence, and absences do not draw
the eye. A number in a `Not asked` column does.

Every selected domain gets a heading, including one that was never reached: a heading with a zero
row under it is the point. A domain cut at the cap gets no heading and appears only on the cut
line.

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
- **One question per turn is a standing preference, not a style note.** Two questions in one turn
  gets you one answer and a silent gap in the record.
- **Use `AskUserQuestion` for the routing and shortlist questions**, where the options are fixed
  and few. Use plain prose for the interrogation questions themselves, which are open by design
  and must not be reduced to a multiple choice.
- **Write into this session's plan file, the one the host named.** If there is none, ask for the
  slug before writing. Never open a second file for work that already has one.
