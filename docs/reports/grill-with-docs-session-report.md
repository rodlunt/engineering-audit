# Grill-with-Docs Session Report — AI Tutor Project

**Source:** reconstructed from the raw Claude Code session transcript
(`~/.claude/projects/-Users-charliemac-Desktop-Coding-AI-Tutor---grill-me-with-docs/d262f479-198a-4958-989a-eebb5c301cbd.jsonl`),
since no transcript or spec file was saved inside the project itself. This report is built only
from what that transcript actually contains — no answers or decisions have been invented.

**Command run:** `/grill-with-docs using the brief.md`, followed later in the same session by
`/setup-matt-pocock-skills`, `/to-spec`, `/to-tickets`, and two `/implement` runs.

---

## 1. What `grill-with-docs` actually is

`grill-with-docs` is a two-line router skill
(`~/.claude/skills/grill-with-docs/SKILL.md`). It has no rules of its own — it just calls two
other skills in sequence:

1. **`grilling`** — a relentless, round-based interview. It maps the decision space as a
   **design tree**, computes the **frontier** (every question whose prerequisites are already
   settled), and asks the whole frontier in one numbered round with a recommended answer per
   question. It waits for the user's answers before computing the next round. Facts the agent
   can look up itself (e.g. "is this course URL login-gated?") are delegated to a sub-agent
   instead of asked of the user; only real decisions go to the user.
2. **`domain-modeling`** — runs after the interview settles, and turns the agreed language into
   `CONTEXT.md` (a glossary, no implementation detail) and, only when a decision is hard to
   reverse, surprising, and the result of a real trade-off, an ADR under `docs/adr/`.

No engineering rules pack, no domain triage, no risk/reversibility ranking — it is a general
"talk it through, then write it down" tool, not a framework-driven review.

---

## 2. The interview: all 30 questions and answers

The session ran across the brief for a "Claude Code project that ingests an online course and
tutors you through it." Below are all rounds, in order, exactly as asked and answered. "Rec."
is the recommendation the agent gave; "User answered" is your literal reply.

### Round 1 — top-level decisions (grilling skill)

| # | Question | Rec. | User answered |
|---|---|---|---|
| Q1 | Personal tool or product for others? | Personal tool first, expand later | "v1 is a personal tool to help me learn and commit to a course, but intended to be a product for others down the track." |
| Q2 | Platform for Phase 1 (local web app / CLI / chat-driven)? | Local web app | "a local web app initially, simple UI with plugins to Google calendar for planning, Gmail for email reminders, access to AI plugin of your choice…" |
| Q3 | Which of the brief's 5 steps are must-have for v1? | Steps 1–3 (curriculum, source curation, active study/testing) | "curriculum build combined with source curation and active study/testing are the must haves to start" |
| Q4 | Build custom logic vs. glue existing tools (NotebookLM, Zapier, ChatGPT plugins)? | Custom app with its own LLM calls, standard RAG instead of NotebookLM | "a thin orchestrator to start, leveraging off existing tools/APIs… potentially using the Firebase MCP to scrape material and then build a study plan and tutoring sessions with ChatGPT" |
| Q5 | Which LLM backend? | Claude (Anthropic API) | (answered as part of Q1–Q4 combined reply; Claude confirmed explicitly in Round 4) |

### Round 2 — architecture pinning (grilling skill)

| # | Question | Rec. | User answered |
|---|---|---|---|
| Q1 | Direct API calls (A) vs. leaning on ChatGPT's own connected apps (B) for Calendar/Gmail? | A — direct APIs | **B** |
| Q2 | Which LLM powers curriculum-building and tutoring? | Claude | "I'm thinking I make a project folder inside either codex or claude. Use the firecrawl MCP to gather the course material and then have the LLM do the curriculum build and tutoring." |
| Q3 | Did "Firebase MCP" actually mean Firecrawl? | Firecrawl | "yes, firecrawl" |
| Q4 | What's the first course (URL)? | *(fact only the user has)* | `https://academy.towardsai.net/courses/take/beginner-to-advanced-llm-dev/multimedia/59791028-from-beginner-to-advanced-llm-developer-the-towards-ai-academy` |
| Q5 | Does Phase 1 need its own local database? | Yes, lightweight SQLite | *(answered next round — see below)* |
| Q6 | Auto-discover supplementary sources in Phase 1? | No — one course only for v1 | *(answered next round)* |

**Agent side-check between rounds:** the agent independently verified the course URL and found
it login-gated (Thinkific sign-in wall) — this was discovered by the agent, not asked of the user.

### Round 3 — resolving the fact-check fallout (grilling skill)

| # | Question | Rec. | User answered |
|---|---|---|---|
| Q1 | Is this actually a Claude Code / Codex agent project, not a coded web app? | Agent project | "I think MVP is a LLM project to start." |
| Q2 | Claude Code or Codex? | Claude Code | "I have both a Claude Code and Codex subscription." *(left open — resolved next round)* |
| Q3 | How to get past the course's login wall — (A) browser automation via claude-in-chrome, (B) manual copy-paste, (C) skip gated courses? | A | **A** |
| Q4 | Real Calendar/Gmail MCP integration now, or defer? | Defer | "Real MCP integration I already have access." (i.e. do it now, not deferred) |

### Round 4 — closing the last fork (grilling skill)

| # | Question | Rec. | User answered |
|---|---|---|---|
| Q1 | Codex or Claude Code — final pick | Claude Code | **Claude** |
| Q2 | One skill for all 3 steps, or separate skills per stage? | Separate skills | **Several skills** |
| Q3 | Where does course/progress data live? | Plain markdown/JSON files | **plain markdown progress file** |
| Q4 | Build for one course only, or structure for multiple from day one? | Structure folders for multiple now | "Build for one course to model, a product down the track will allow users to add any course material." (i.e. one course now, folder structure for many is *not* built yet) |
| Q5 | Study/test session = one-question-at-a-time Socratic loop, text only? | Yes | "Claude app has voice" *(confirms text-only for this app; voice deferred to the Claude app itself)* |

**Checkpoint:** agent restated the full shared understanding and asked for confirmation before
moving to domain-modeling. **User: "nope lets continue"** (i.e. no objection — proceed).

Session paused here at the user's request ("can you create a progrss.md and we'll finish at a
later date") — the agent wrote `progress.md` as a resume point, then the user said "let's
continue where we left off" to resume in the same sitting.

### Round 5 — domain-modeling terms

| # | Question | Rec. | User answered |
|---|---|---|---|
| Q1 | Atomic unit of study: (A) Topic = one lesson, or (B) Topic = one concept spanning lessons? | A | **A** (accepted the recommendation — implicit from the summary that followed) |
| Q2 | Does one StudySession cover one Topic or several? | One Topic per session | Accepted rec. |
| Q3 | What does "mastered" mean — 3-state status or a numeric score? | 3-state (`not-started`/`in-progress`/`mastered`), no score | Accepted rec. |
| Q4 | Is `curriculum.md` overwritten on rebuild, or versioned? | Overwrite, no versioning | **"yes lock it in"** |

### Round 6 — skill boundaries and file layout (domain-modeling, resumed session)

| # | Question | Rec. | User answered |
|---|---|---|---|
| Q1 | Is scheduling its own skill (`schedule-curriculum`), or folded into `build-curriculum`? | Separate skill | Accepted rec. (B) |
| Q2 | File layout: `course/lessons/`, `course/curriculum.md`, `course/progress.md`? | As proposed | Accepted — "Matches" |
| Q3 | `progress.md` structure (status / last studied / gaps per Topic)? | As proposed | **"yes lock it in"** |

**Total: 30 numbered questions across 6 rounds**, all resolved — no question was left in the
"unknown" state (the grilling skill has no concept of deferral/partial completion the way
engineering-grill does; every round's frontier was closed before moving on).

---

## 3. What the session produced

### Domain artifacts (domain-modeling)

- **`CONTEXT.md`** — glossary only, no implementation detail. Defines: Course, Topic,
  Curriculum, StudySession, Topic status, Progress, Schedule.
- **`progress.md`** — used as the session's own resume/checkpoint file (not the same as the
  in-app `course/progress.md` the product design defines) — it recorded "what we agreed so far"
  and "what is still open" so the session could be resumed with "continue the AI Tutor plan."
- **No ADR was created.** None of the resolved decisions were flagged as meeting all three ADR
  criteria (hard to reverse, surprising, result of a real trade-off) during this session.

### Repo setup (`/setup-matt-pocock-skills`, mid-session)

A worker explored the repo and found: no git repo, no `CLAUDE.md`/`AGENTS.md`, `CONTEXT.md`
already present, no `docs/adr/`, no `docs/agents/`, `triage` skill installed. Three questions
were asked and answered in one line: **"1. Local, 2. Yes default, 3. A"** — meaning:

1. Issue tracker → **local markdown** under `.scratch/<feature>/`
2. Triage labels → **keep the 5 defaults** (`needs-triage`, `needs-info`, `ready-for-agent`,
   `ready-for-human`, `wontfix`)
3. Config file → **CLAUDE.md** (not AGENTS.md)

This produced `CLAUDE.md`'s "Agent skills" block, `docs/agents/issue-tracker.md`,
`docs/agents/triage-labels.md`, and `docs/agents/domain.md` — all still present in the repo
today.

### Spec (`/to-spec`) — **gap found**

`/to-spec` was invoked. The agent proposed a testing seam (one file-based check per skill:
`course/lessons/`, `course/curriculum.md`, Calendar/Gmail output, `course/progress.md`) and
asked the user to confirm it. **No spec.md file was ever written to disk.** The transcript shows
the session moving straight from the seam question into `/to-tickets` without a visible spec
approval step or a `.scratch/ai-tutor-skills/spec.md` file — and none exists in the repo today,
despite the `to-spec` skill's own instruction to "publish it to the project issue tracker." This
looks like a dropped step, not a deliberate skip.

### Tickets (`/to-tickets`)

Four tickets were proposed, and published as local markdown files under
`.scratch/ai-tutor-skills/issues/` — no explicit approval text is visible in the transcript
before publishing, but no objection was raised either.

| # | Title | Blocked by | Status (as of this report) |
|---|---|---|---|
| 01 | Ingest course content | None | **Done** — all 3 acceptance criteria checked, status `ready-for-human` |
| 02 | Build curriculum & initialize progress | 01 | **Done** — all 3 acceptance criteria checked |
| 03 | Schedule curriculum to Calendar & Gmail | 02 | **Not started** — `ready-for-agent` |
| 04 | Run a Socratic study session | 01, 02 | **Not started** — `ready-for-agent` |

Ticket 01's acceptance criteria:
- [x] Skill logs into the course site via browser automation when Firecrawl hits a login wall
- [x] Each lesson saved as `course/lessons/NN-slug.md`
- [x] Re-running the skill updates/adds new lessons without duplicating existing ones

Ticket 02's acceptance criteria:
- [x] `curriculum.md` lists every lesson as a Topic in sequence with a suggested pace
- [x] `progress.md` created with one entry per Topic, status `not-started`, last studied blank
- [x] Re-running overwrites `curriculum.md` cleanly (no duplication)

### Build quality loop (worth noting for the comparison)

Both built skills (`ingest-course`, `build-curriculum`) went through an independent review
worker before being marked done — this was **not because grill-with-docs asked for it**, but
because the session was run under `/orchestrate` (a separate skill invoked mid-session,
independent of grill-with-docs) which mandates a verify pass. The reviewer caught real bugs both
times: wrong MCP tool names and no dedup logic for `ingest-course`; a `progress.md` ordering bug
that could silently reorder or delete real study history for `build-curriculum`. Both were fixed
before being marked done.

---

## 4. Comparison: `grill-with-docs` vs. `engineering-grill`

| | `grill-with-docs` (used here) | `engineering-grill` |
|---|---|---|
| **What grounds the questions** | Nothing external — the agent's own judgment plus the brief/codebase | A live, versioned engineering rules pack (`list_domains` / `get_domain` via the engineering-audit MCP), read fresh every run |
| **Domain coverage** | None — general-purpose interview | Explicit triage of every rules-pack domain into `active-now` / `required-later` / `not-applicable` / `unknown`, with no cap on how many are active |
| **Question source** | Agent-derived, one round per "frontier" of the design tree | Each domain's rules translated into ranked questions, tagged with `rule_id`, `reversibility`, and `blast_radius` |
| **Ranking** | None — a round is just "everything currently askable" | Explicit cross-domain ranking by cost-of-wrong-answer: `irreversible-once-shipped` + widest blast radius first, always one question per turn in the Hot Seat |
| **Pacing** | Whole frontier per round, batched | Hot Seat = strictly one question per turn, never batched; deep dive afterwards permits rounds |
| **Deferral handling** | Not modeled — every question in this session was resolved before moving on | First-class: ANSWERED vs. DEFERRED (with reason) vs. NEVER ASKED are all tracked and reported, and a short session is explicitly barred from looking like a complete one |
| **Facts vs. decisions** | Same principle — sub-agent finds facts (e.g. the course-URL login-gate check here), user only gets real decisions | Same principle, formalized as "Establish project facts" before any question is asked |
| **Output docs** | `CONTEXT.md` (glossary) + ADRs only when the 3-part bar is met | `CONTEXT.md` + `docs/engineering-coverage.md` (domain map, decision ledger, per-domain question/answer/defer counts, build gates) + ADRs |
| **Auditability of the run itself** | None — nothing records how many questions *could* have been asked vs. were | Every domain's `source` (`mcp`/`fallback`/`none`) and full question/answer/defer counts are recorded, specifically so a shallow run can't be mistaken for a thorough one |
| **Explicit self-disclosure** | None | States up front, every run, that the flow is largely unexercised and asks the user to flag anything that reads like generic filler |
| **Scope** | One-shot, whatever the brief covers | Framework-driven: requirements, ethics/regulatory/cost, architecture, data/interfaces, security, code/testing, delivery/production-readiness, incident response — as many as genuinely apply |

**Net effect on this session:** `grill-with-docs` correctly surfaced and settled the
product-shape decisions in the brief (platform, LLM, scope, file layout, skill boundaries) and
caught one real fact-check the user got wrong (Firebase → Firecrawl) and one hazard (login-gated
course) — but it did so with no traceable coverage record. There's no artifact in this repo
today saying which categories of risk (security, data handling of scraped/gated content,
production readiness, delivery) were considered and ruled out versus simply never brought up.
`engineering-grill`'s domain triage would have made that explicit — e.g. whether "browser
automation against a third party's login wall" warranted a security/ToS consideration is not
something `grill-with-docs` has a mechanism to raise. Similarly, the dropped `/to-spec` step
would have been caught by `engineering-grill`'s completion checklist ("every applicable rule
became inspected evidence, a confirmed decision... or an explicitly open item") — here it just
silently vanished from the transcript.

---

## 5. Known gaps in this report

- This report was reconstructed from the session's JSONL transcript after a `/clear` wiped this
  conversation's own memory of it — the transcript is a faithful record of what was typed and
  shown, but any tone/inflection from the live back-and-forth is lost.
- The `/to-spec` gap (§3) is the most notable finding: the skill's own contract says a spec.md
  should exist, and none does. Worth deciding whether to run `/to-spec` properly before
  building tickets 03/04.
- Tickets 03 and 04 are unimplemented — this report only describes what was decided and
  specified for them, not what was built.
