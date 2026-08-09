# engineering-audit for Codex CLI

Wires the `engineering-audit` MCP server into OpenAI's Codex CLI, in both inline
mode (decision-time triggers in `AGENTS.md`) and standalone audit mode (`AUDIT.md`
driven by the MCP tools).

Claims below marked **VERIFIED** were checked live against `codex-cli 0.114.0` on
this machine; everything else is documented from Codex's own published docs and
help text, and is labelled accordingly. See "Status" at the bottom before relying
on the parts that were not run end to end.

## Install the MCP server

**From the published git repository** (no local clone needed):

```
codex mcp add engineering-audit \
  --env ENGINEERING_AUDIT_RULES_DIR=<path-to-rules-clone> \
  -- uvx --from git+https://github.com/rodlunt/engineering-audit@v0.4.0 engineering-audit-mcp
```

`@v0.4.0` pins the install to the current tagged release rather than the moving `main` branch;
see the root README's [Install](../../README.md#install) section for how to find the latest tag
and update to it deliberately.

`<path-to-rules-clone>` is a local checkout of a rules pack: a directory of
`NN-slug.md` domain files. Rules packs are distributed separately from this tool
(see the repository root README).

**From a local clone of this repository** (for development, or before the package
is published):

```
git clone https://github.com/rodlunt/engineering-audit
codex mcp add engineering-audit \
  -- uv --directory /path/to/engineering-audit run engineering-audit-mcp \
  --rules-dir <path-to-rules-clone>
```

**VERIFIED**: `codex mcp add --env KEY=VALUE -- <command>...` is the registration
syntax on `codex-cli 0.114.0`; `--env` is only valid for stdio servers, which this
one is. Run `codex mcp list` afterwards to confirm `engineering-audit` is
registered and check its command line.

## Inline mode

Generate the fragments with this tool's own generator, then append the Codex one
to whichever `AGENTS.md` you want the triggers to apply to:

```
uv run engineering-audit-fragments --rules-dir <path-to-rules-clone> --out-dir /tmp/eaf
cat /tmp/eaf/AGENTS-fragment.md >> AGENTS.md
```

Use the repository's own `AGENTS.md` for project-scoped triggers, or
`~/.codex/AGENTS.md` for a global install that applies to every Codex session
regardless of repository.

Codex reads `AGENTS.md` from `~/.codex/AGENTS.md` (global) and from the repository
root and nested directories (project- and directory-scoped), merging them, with a
combined cap of roughly 32 KiB across all `AGENTS.md` files it loads (source:
`github.com/openai/codex`, `docs/agents_md.md`). Keep the generated fragment plus
whatever else lives in `AGENTS.md` under that cap; a fragment silently truncated at
the 32 KiB boundary would drop triggers with no warning.

## Standalone audit mode

**Interactive**: start a normal `codex` session in the repository to be audited and
tell it to read `AUDIT.md` (from this tool's repository) and run the audit using the
`engineering-audit` MCP tools. Recommended flags for an interactive run:

```
codex --sandbox workspace-write --ask-for-approval on-request
```

This lets Codex edit files and run commands inside the workspace without asking for
every single command, while still stopping to ask before anything it considers
risky (matches `on-request`'s documented behaviour: "the model decides when to ask
the user for approval").

**Headless**:

```
cat AUDIT.md | codex exec -
```

**VERIFIED**: `codex exec`'s help text states "If not provided as an argument (or if
`-` is used), instructions are read from stdin", so piping `AUDIT.md` straight in
via `cat AUDIT.md | codex exec -` (or `codex exec -` with the same input
redirected) is a supported way to hand it the audit procedure. A short driver
prompt that just points at `AUDIT.md` and the target repository works equally well
if you would rather not pipe the whole document in.

### Approval and sandboxing for headless runs

There is an open upstream issue,
[openai/codex#24135](https://github.com/openai/codex/issues/24135), where headless
`codex exec` runs auto-cancel any tool call that would normally pause for approval,
rather than actually pausing. In practice this means a fully unattended
`codex exec` run under `on-request` (or any approval policy other than `never`) can
silently stop making progress partway through the audit rather than asking you
anything.

Two ways to avoid that trap, in order of preference:

- Pass `--ask-for-approval never` alongside `--sandbox workspace-write`. Codex then
  never pauses for approval, but every command still runs inside the sandbox, so a
  runaway or wrong command is contained to the workspace.
- Only if the sandbox itself is the problem (e.g. a command genuinely needs
  network access or to write outside the workspace), pass
  `--dangerously-bypass-approvals-and-sandbox`. This is an explicit, deliberate
  opt-in, not a default recommendation: Codex's own help text calls it "EXTREMELY
  DANGEROUS" and states it is "intended solely for running in environments that
  are externally sandboxed" (a container or VM you are prepared to throw away, not
  a normal developer machine). Do not reach for this flag just to work around
  issue #24135 without first asking whether `--ask-for-approval never` already
  solves the actual problem.

## Status

- MCP server registration (`codex mcp add`, `--env`, `codex mcp list`) and
  `codex exec`'s stdin behaviour: **VERIFIED** on `codex-cli 0.114.0`, this
  machine.
- The full audit flow described above (an actual Codex session reading `AUDIT.md`
  and driving the MCP tools through to a rendered report): **DOCUMENTED,
  UNTESTED**. The commands match Codex's documented syntax and this tool's actual
  MCP surface, but no end-to-end run against a live Codex session has happened yet.
  Confirm `list_domains` and `get_domain` respond as expected before relying on
  this for a real audit.
- Inline mode's `AGENTS.md` merge behaviour and the 32 KiB cap: sourced from
  Codex's own docs, not re-verified against a live merge on this machine.
