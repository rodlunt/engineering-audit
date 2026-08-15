# Engineering Grill

Engineering Grill is a guided conversation for the beginning of a project. Before code is
written, your AI assistant asks the questions an experienced engineering team would normally ask:
who the project is for, what could go wrong, what data it handles, how it will be tested, and what
must be true before it is safe to launch.

You do not need to understand the engineering framework or choose its domains yourself. The
assistant does that work and explains its recommendations in ordinary language.

## Choose one planning entry path

For a project that has not been built yet, choose one of the two planning skills and use that
entry point once:

- **Engineering Grill (Codex and Claude Code):** an explicit-invocation, comprehensive interview.
  It builds a dependency-aware decision tree, classifies every returned domain, and records
  confirmed terminology, coverage, decisions, risks, and build gates in project documents.
- **Claude Code `interrogate` (BETA):** a Claude-only plan workflow. It derives questions from
  every relevant returned domain, asks the three highest-impact questions first, and then offers
  the remaining questions as a deep dive in the plan file. It is deliberately lighter and its
  cross-domain workflow is still unproven.

Do not run both for the same project or in the same planning session. They ask similar questions
with different records and pacing; choose the one whose handoff you want. `interrogate` is
documented in [the Claude Code integration guide](../claude-code/README.md).

## Four terms used in this guide

- **AI assistant:** Codex or Claude Code, the application you talk to while planning and coding.
- **MCP connection:** the link that lets the assistant ask engineering-audit for rules. Registering
  the MCP means adding that link to the assistant once.
- **Rules pack:** a folder containing the engineering guidance.
- **Domain:** one subject in that guidance, such as requirements, testing, or data modelling.

## What happens during a grill

1. The assistant reads the project idea and any files that already exist.
2. It asks engineering-audit for every domain in the connected rules pack.
3. It sorts the domains into four groups:

   - **active now:** decisions that need attention in the current conversation;
   - **required later:** important work with a clear future trigger;
   - **not applicable:** the intended project genuinely does not contain the thing covered;
   - **unknown:** one answer is needed before the assistant can decide.

4. Before reading full domain documents, it shows a first cost preview. The preview names the
   active-now domain count, the full documents and rule inputs it plans to read, and a provisional
   turn range. It asks whether you want to continue with that scope and estimate or adjust them.
   It recalculates the preview after any adjustment.
5. After you approve the scope, it reads the full rules for the active domains. A full response
   saved to a spool file is still a successful load, so the assistant reads that file to the end.
6. It then derives the dependency-aware decision tree. Before the first deep question, it states
   the retained derived total or a refined turn range. The range names its ready frontier and every
   reachable branch that could add questions. Generic candidates are filtered before they enter the
   derived total.
7. It considers only decisions whose prerequisites are settled, then ranks that ready frontier by
   risk, including likely harm, reach, and difficulty of undoing the decision.
   Blocked branches stay in open questions until their prerequisite is settled.
8. It asks one question per turn. Every question is project-specific, includes a recommended
   answer, and explains why the decision matters.
9. It applies a generic-question filter before showing a question. The filter removes questions
   that do not name a concrete project decision, actor, data, boundary, or outcome. A rule that
   needs no user choice can instead become inspected evidence or a named build or verification
   gate.
10. After each confirmed answer, it checkpoints the decisions, coverage map, fact map, and next
   frontier. At each checkpoint, completion, and early exit it reports honest question counts for
   derived, asked, answered, deferred, and not asked. The counts obey `asked = answered + deferred`
   and `derived = asked + not_asked`.
11. If the session stops or is interrupted, it leaves an explicit incomplete marker, the open
    questions, and the next frontier so the next session can recover honestly. A question already
    shown but unanswered is deferred with its reason and resume trigger. A dependency-held question
    is not asked and remains in the not asked count.
12. It records confirmed decisions, terms, build checks, and known risks in project documents.

An empty project folder does not make domains disappear. The assistant judges the system you
intend to build, not only the files that happen to exist today.

## What it produces

The assistant writes these files inside the project being planned, not inside the
`engineering-audit` checkout:

- `CONTEXT.md`: a small dictionary of agreed project language;
- `docs/engineering-coverage.md`: the domain map, checkpoint, decision ledger, deferred triggers,
  and what must be built or verified;
- `docs/adr/`: occasional decision records for important choices that would otherwise be hard to
  understand later.

Files are created only after material is confirmed. A later grill reads and updates existing
documents, checks them against the conversation, and continues ADR numbering. It does not replace
them blindly. The grill does not write project code, start an audit, make pass/fail claims, or
file GitHub issues.

## Progress and stopping

The assistant reports the same five counts at every checkpoint, on completion, and on early exit:

- **derived:** retained project-specific questions after the generic-question filter;
- **asked:** questions shown to you;
- **answered:** questions answered well enough to settle the decision;
- **deferred:** questions shown but unanswered. If a shown question is interrupted, include its
  reason and resume trigger;
- **not asked:** every retained question not shown yet, including dependency-held items and the
  remainder at early exit. Record why it was not shown, such as an unresolved prerequisite or
  early exit.

The counts must always satisfy `asked = answered + deferred` and `derived = asked + not_asked`.
At completion, the assistant explains any non-zero deferred or not asked count and shows why no
reachable decision remains unresolved. On early exit, it keeps the counts, writes the incomplete
marker, and names the open questions and next frontier. It does not hide unanswered questions in a
rounded total. If no project location exists, it keeps the full checkpoint in the conversation
draft, shows these counts before asking where to save it or a next deep question, and asks exactly
one location question in that turn. The checkpoint exists in the draft even though it is not on
disk. Once you supply a location, the assistant writes or updates the checkpoint and continues.

## Support and requirements

Engineering Grill currently has installation instructions for **OpenAI Codex and Claude Code**.
Gemini can use the engineering-audit MCP, but this skill has not been packaged or documented for
Gemini. The commands below are for macOS and Linux; Windows installation is not yet documented.

The interview workflow has been forward-tested in Codex with example projects. Installation from
a clean machine and the Claude Code flow are documented but have not yet been exercised end to
end, so follow the connection and skill-discovery checks below before relying on a new setup.

You need:

1. a local clone of this `engineering-audit` repository;
2. the engineering-audit MCP registered with your assistant;
3. a rules pack connected to that MCP.

Before continuing, check the three programs used by the setup:

```sh
git --version
uvx --version
codex --version
```

If you use Claude Code, replace the last command with `claude --version`. If a command is not
found, install [Git](https://git-scm.com/downloads), [uv](https://docs.astral.sh/uv/getting-started/installation/),
or your chosen assistant before continuing. Installing `uv` provides the `uvx` command.

The three-domain taster pack in this repository works, but the grill can consider only those
three domains. The full framework is available as described under
[Rules access](../../README.md#rules-access). After receiving access, point the MCP registration
at that rules repository's `domains/` folder.

## Install step by step

Paths beginning with `/replace/with/` below are examples. Replace them with the real folder on
your computer.

### 1. Enter the engineering-audit checkout

If you have not cloned this repository yet, open a terminal and run:

```sh
git clone https://github.com/rodlunt/engineering-audit
cd engineering-audit
pwd
```

If you already have a clone, change into it instead:

```sh
cd /replace/with/engineering-audit
pwd
```

`pwd` prints the folder you are currently in. Confirm that it ends in `engineering-audit` before
continuing. Keep this checkout in the same location after installation because the skill shortcut
will point to it.

### 2. Register engineering-audit

Follow the registration command for [Claude Code](../../README.md#claude-code) or
[OpenAI Codex](../../README.md#openai-codex-cli) in the main guide. For Engineering Grill, you
need only the MCP server registration. The separate audit skill, inline trigger setup, and
headless audit instructions are optional.

Whenever the main guide shows `/path/to/...`, replace it with the real absolute path to the rules
folder. If you are using the included taster pack from this checkout, its path is:

```text
<the folder printed by pwd>/examples/taster-rules
```

### 3. Verify the connection

Use the command for your assistant:

```sh
codex mcp list
```

or:

```sh
claude mcp list
```

Confirm that `engineering-audit` appears. Then start the assistant and ask:

```text
Use the engineering-audit list_domains tool and tell me how many domains are loaded.
```

The taster pack returns three domains. The full pack returns its current complete domain list;
“16 currently” is only an example, so always use the number returned by this session. If it is not
what you expected, correct the rules-folder path in the MCP registration before continuing.

Return to the terminal when the check is finished. You can close the temporary assistant session;
you will start a fresh one in the project you actually want to plan in step 6.

To correct a registration, first remove the old entry:

```sh
codex mcp remove engineering-audit
```

or:

```sh
claude mcp remove engineering-audit
```

Then repeat step 2 with the correct rules-folder path and run this verification again.

### 4. Install the skill

Run one of these from the `engineering-audit` checkout used in step 1. The `ln -s` command creates
a shortcut; it does not copy the skill.

#### OpenAI Codex

```sh
mkdir -p ~/.codex/skills
ln -s "$(pwd)/integrations/engineering-grill/engineering-grill" \
  ~/.codex/skills/engineering-grill
```

#### Claude Code

```sh
mkdir -p ~/.claude/skills
ln -s "$(pwd)/integrations/engineering-grill/engineering-grill" \
  ~/.claude/skills/engineering-grill
```

If the command reports `File exists`, inspect the existing shortcut:

```sh
ls -ld ~/.codex/skills/engineering-grill
```

For Claude Code, replace `.codex` with `.claude`. If the displayed arrow points to this checkout's
`integrations/engineering-grill/engineering-grill` folder, installation is already complete. If
it points elsewhere or is not a shortcut, leave it unchanged until you know who owns it.

Advanced shared-skill setups may use `~/.agents/skills`, but use that location only when your
assistant is already configured to discover it.

### 5. Run the post-install skill discovery smoke test

First check that the shortcut resolves to a real skill file. Use the command for the host you
installed:

```sh
test -f ~/.codex/skills/engineering-grill/SKILL.md && echo "Codex skill file resolves"
```

or:

```sh
test -f ~/.claude/skills/engineering-grill/SKILL.md && echo "Claude skill file resolves"
```

Then start a fresh `codex` or `claude` session and ask: **“Without invoking it, confirm that the
`engineering-grill` skill is available.”** The assistant should name the skill and its purpose
without starting an interview. If it cannot see the skill, close that session, inspect the
shortcut with `ls -ld`, and repeat the check after fixing the link. This is a discovery smoke test,
not a Grill and not an audit.

### 6. Open the project you want to plan

Leave the engineering-audit checkout and enter the project being planned:

```sh
cd /replace/with/my-project
pwd
```

Confirm that `pwd` prints the project you intend to plan. For a brand-new project whose folder
does not exist yet, create and enter it first:

```sh
mkdir -p /replace/with/my-project
cd /replace/with/my-project
pwd
```

Start a new assistant session from that folder so both the skill list and MCP process refresh:

```sh
codex
```

or:

```sh
claude
```

## Start a grill

In Codex:

```text
Use $engineering-grill to help me plan a booking system for a small clinic.
```

In Claude Code:

```text
Use Engineering Grill to help me plan a booking system for a small clinic.
```

Other useful starting prompts include:

- “Use Engineering Grill on this empty project before we start building.”
- “Engineering Grill this feature idea and tell me which engineering areas matter.”
- “Use Engineering Grill to revisit the design of this existing project.”

The assistant first shows its proposed domain map. Correct anything that misrepresents your
intent, then answer the numbered questions. The session continues in rounds until the important
decisions are settled and you confirm the shared understanding. Start from a fresh, non-audit
session: do not invoke Engineering Grill while an audit is active or starting.

## How it stays current

The skill contains no fixed domain list. At the start of each session it calls `list_domains`, so
new, removed, or renamed domains appear automatically. When a domain becomes relevant, it calls
`get_domain` and reads the rules currently served by the MCP. An oversized response saved to a
spool file is a successful fetch: the Grill reads the whole file before asking questions. It does
not use a partial response or silently fall back to a local rules checkout.

The MCP serves the rules pack it loaded when the assistant session started. After updating a
rules checkout with Git, fully close and reopen the assistant so the MCP reloads it. Engineering
Grill does not download or update the rules pack itself. If the MCP is genuinely unavailable, the
Grill reports `framework unavailable` and normally stops; any explicitly authorised local fallback
is labelled non-framework scaffolding, not live coverage.

## Troubleshooting

- **The skill does not appear:** start a new assistant session and run the discovery smoke test in
  installation step 5; inspect the shortcut with `ls -ld` if the file check fails.
- **engineering-audit does not appear:** repeat `codex mcp list` or `claude mcp list`, then repeat
  the MCP registration command if needed.
- **The domain count is unexpected:** the MCP is probably pointing at the taster pack or a
  different rules folder. Remove and repeat the registration as described in verification step
  3.
- **The assistant is reading the wrong project:** close it, change into the intended project
  folder, and start a fresh session there.

## Where it fits

| Project stage | Use | Result |
|---|---|---|
| Before building | Engineering Grill | A reasoned plan, decisions, and build checks |
| While building | Inline decision-time rules | The relevant rules at each engineering decision |
| After building | Full repository audit | An evidence-based report of what was delivered |

The grill and the audit complement each other. The grill helps you decide what good should look
like; the audit later checks the project that was actually built. Inline decision-time lookup is a
separate, lightweight aid while building, not an audit run.
