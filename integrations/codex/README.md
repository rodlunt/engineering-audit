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
  -- uvx --from git+https://github.com/rodlunt/engineering-audit@v0.15.0 engineering-audit-mcp
```

`@v0.15.0` pins the install to the current tagged release rather than the moving `main` branch;
find the latest tag on the [Releases page](https://github.com/rodlunt/engineering-audit/releases).

### Updating to a newer tag

**Change the tag and run the same command again.** `codex mcp add` overwrites an existing
registration of the same name without complaint, so no removal step is needed. VERIFIED on
codex-cli 0.114.0 by registering one name twice and confirming the second command replaced the
first.

This differs from Claude Code, where `claude mcp add` refuses to overwrite and a
`claude mcp remove` has to come first. Do not copy that host's two-step form here; it is not
needed, and `codex mcp remove` followed by `codex mcp add` is simply a longer way to the same
place.

Confirm the change with `codex mcp list`, and check the run's own `meta.update_check`, which
the server computes on every run and which names the latest release when the installed build
is behind it.

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

### Host environment metadata

`begin_run` takes an `environment` map, and Codex should populate it: on this host
that is `{"os": "<from uname -sr or sw_vers>", "host_cli": "codex",
"host_cli_version": "<from codex --version>"}`. Those three keys are the whole
accepted set and any other key is refused; omit one you cannot determine rather
than guessing. `AUDIT.md` step 1 has the full rules and the reason the set is
closed.

This is not bookkeeping for its own sake. [#85](https://github.com/rodlunt/engineering-audit/issues/85)
was caused by a host-side MCP timeout specific to a Codex CLI version, and the
reporter had to hand-type "macOS" and "Codex CLI 0.147.0" into the issue because the
report could not supply either: the header's `Assistant: codex` and
`Model: gpt-5.6-luna` rows identify neither.

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

### Preset configuration for headless runs

**Set `ENGINEERING_AUDIT_CONFIG` for any unattended `codex exec` run.** Without it,
the run takes the interactive path: `start_config` opens a localhost page and the
audit stops dead until a human ticks domains in a browser, which is not a thing an
unattended run has. Point it at a valid `AuditConfig` JSON file and `start_config`
loads it directly instead:

```
ENGINEERING_AUDIT_CONFIG=/path/to/config.json codex exec - < AUDIT.md
```

An `AuditConfig` JSON file looks like:

```json
{
  "selected_domain_ids": ["d01", "d02"],
  "issue_mode": "report",
  "telemetry_consent": {
    "coverage": true,
    "rollup": true,
    "self_assessment": true,
    "environment": false
  }
}
```

`selected_domain_ids` must be non-empty and every id must exist in the loaded rules
pack, or `start_config` refuses the file with a clear error rather than falling back
to a default selection. See `src/engineering_audit/schema.py`'s `AuditConfig` model
for the full field list.

This section exists because its absence caused
[#85](https://github.com/rodlunt/engineering-audit/issues/85): the Claude Code
integration README documented the preset path and this one did not, so a headless
Codex run went down the interactive path and stalled.

Every run also checks this repository's tags for a newer release, on by default. On an
air-gapped or network-restricted CI runner, add `--env ENGINEERING_AUDIT_NO_UPDATE_CHECK=1` to
the `codex mcp add` command above (or `--no-update-check` on the `engineering-audit-mcp` command
line if you are running it directly rather than via `codex mcp add`). See the root
[SECURITY.md](../../SECURITY.md) for exactly what the check discloses when it runs.

### `tool_timeout_sec` and the configuration wait

Codex applies a per-server MCP tool timeout, `mcp_servers.<name>.tool_timeout_sec`
in `~/.codex/config.toml`, and cancels any tool call that outlives it. In an
interactive run the tool call that waits longest is `get_config`, because it is
waiting on a person.

`get_config` does not hold a single call open for the whole of that wait. It blocks
for about 25 seconds, returns `status: "waiting"`, and expects the assistant to call
it again (see `AUDIT.md` step 3). That sits comfortably under any plausible
`tool_timeout_sec`, so the default needs no adjustment for the sake of this tool.

If you have lowered `tool_timeout_sec` below about 30 seconds for other reasons,
raise it back for this server:

```toml
[mcp_servers.engineering-audit]
tool_timeout_sec = 60
```

What a cancelled call costs is worth knowing, because it is not just one failed
tool call: `codex exec` exits after the failure, which terminates the stdio MCP
process and the localhost configuration server it was hosting. The browser page
stays open and still looks usable, but submitting it reaches nothing. The page now
notices this itself, disables its submit button and says so, and the domains you
had ticked are restored into the fresh page a resumed run opens; the run is
recoverable from `audit-output/run-state.progress.json` either way.

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
