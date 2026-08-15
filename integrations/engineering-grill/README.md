# Engineering Grill

Engineering Grill is a guided conversation for the beginning of a project. Before code is
written, your AI assistant asks the questions an experienced engineering team would normally ask:
who the project is for, what could go wrong, what data it handles, how it will be tested, and what
must be true before it is safe to launch.

You do not need to understand the engineering framework or choose its domains yourself. The
assistant does that work and explains its recommendations in ordinary language.

## Four terms used in this guide

- **AI assistant:** Codex or Claude Code—the application you talk to while planning and coding.
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

4. It loads the full rules only for the active domains.
5. It interviews you in short rounds. Every question includes a recommended answer and explains
   why the decision matters.
6. It records confirmed decisions, terms, build checks, and known risks in project documents.

An empty project folder does not make domains disappear. The assistant judges the system you
intend to build, not only the files that happen to exist today.

## What it produces

The assistant writes these files inside the project being planned, not inside the
`engineering-audit` checkout:

- `CONTEXT.md`: a small dictionary of agreed project language;
- `docs/engineering-coverage.md`: which domains apply, which are deferred, and what must be built
  or verified;
- `docs/adr/`: occasional decision records for important choices that would otherwise be hard to
  understand later.

Files are created only after material is confirmed. A later grill reads and updates existing
documents, checks them against the conversation, and continues ADR numbering. It does not replace
them blindly. The grill does not write project code, start an audit, make pass/fail claims, or
file GitHub issues.

## Support and requirements

Engineering Grill currently has installation instructions for **OpenAI Codex and Claude Code**.
Gemini can use the engineering-audit MCP, but this skill has not been packaged or documented for
Gemini. The commands below are for macOS and Linux; Windows installation is not yet documented.

The interview workflow has been forward-tested in Codex with example projects. Installation from
a clean machine and the Claude Code flow are documented but have not yet been exercised end to
end, so follow the verification step below before relying on a new setup.

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

The taster pack returns three domains. The full pack returns its current complete domain list
(sixteen at the time this guide was written). If the number is not what you expected, correct the
rules-folder path in the MCP registration before continuing.

Return to the terminal when the check is finished. You can close the temporary assistant session;
you will start a fresh one in the project you actually want to plan in step 5.

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

### 5. Open the project you want to plan

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
decisions are settled and you confirm the shared understanding.

## How it stays current

The skill contains no fixed domain list. At the start of each session it calls `list_domains`, so
new, removed, or renamed domains appear automatically. When a domain becomes relevant, it calls
`get_domain` and reads the rules currently served by the MCP.

The MCP serves the rules pack it loaded when the assistant session started. After updating a
rules checkout with Git, fully close and reopen the assistant so the MCP reloads it. Engineering
Grill does not download or update the rules pack itself.

## Troubleshooting

- **The skill does not appear:** start a new assistant session and inspect the shortcut with the
  `ls -ld` command from installation step 4.
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
like; the audit later checks the project that was actually built.
