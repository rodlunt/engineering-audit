# engineering-audit for Claude Code

Wires the `engineering-audit` MCP server and its `audit` skill into Claude Code.

## Install the MCP server

**From the published git repository** (no local clone needed):

```
claude mcp add engineering-audit --scope user -- uvx --from git+https://github.com/rodlunt/engineering-audit@v0.11.0 engineering-audit-mcp --rules-dir <path-to-rules-clone>
```

`--scope user` registers the server for every repository. Without it `claude mcp add` defaults
to local scope, which registers it for the current directory alone and leaves it unavailable
everywhere else.

`@v0.11.0` pins the install to the current tagged release rather than the moving `main` branch;
find the latest tag on the [Releases page](https://github.com/rodlunt/engineering-audit/releases).

### Updating to a newer tag

**Remove first.** `claude mcp add` refuses to overwrite an existing name, so changing the tag
in the command above and running it again fails with `MCP server engineering-audit already
exists in user config`. VERIFIED on claude-code 2.1.232. (Codex differs here: `codex mcp add`
overwrites silently, so its own README's instruction to re-add is correct for that host.)

```
claude mcp remove engineering-audit
claude mcp add engineering-audit --scope user -- uvx --from git+https://github.com/rodlunt/engineering-audit@v0.11.0 engineering-audit-mcp --rules-dir <path-to-rules-clone>
```

**The change only takes effect in a new session.** The session you are in keeps the server it
started with, and will report the old `tool_version` while appearing to work normally, so
verify after restarting rather than before:

```
claude mcp list | grep engineering-audit    # must show the tag you just set
```

`begin_run`'s response states `tool_version` too. If it names the old version, the session
predates the re-registration and the run is not exercising the build you think it is. The
server also checks its own currency on every run and reports it as `meta.update_check`.

`<path-to-rules-clone>` is a local checkout of a rules pack: a directory of `NN-slug.md` domain
files. Rules packs are distributed separately from this tool (see the repository root README);
this command does not fetch one for you.

**From a local clone of this repository** (for development, or before the package is published):

```
git clone https://github.com/rodlunt/engineering-audit
cd engineering-audit
claude mcp add engineering-audit -- uv run engineering-audit-mcp --rules-dir <path-to-rules-clone>
```

`uv run` picks up the project's own `pyproject.toml` from the current directory, so run that
command from inside the cloned `engineering-audit` checkout.

Both commands above match the project's actual CLI entry point (`engineering-audit-mcp`, see
`pyproject.toml`'s `[project.scripts]`) and `claude mcp add`'s documented syntax. For whether
this flow has actually been exercised end to end against a real Claude Code install, check the
root README's [support matrix](../../README.md#support-matrix) rather than a status note kept
here: that table is the single, current source of truth, and it is what gets updated when a
flow is actually run. Confirm the server appears in `claude mcp list` and responds to
`list_domains` before relying on a fresh install.

## Install the skill

Symlink the skill directory into Claude Code's user-level skills folder so it is available in
every project:

```
ln -s "$(pwd)/integrations/claude-code/audit" ~/.claude/skills/audit
```

Run this from inside a local clone of this repository (the symlink target must be an absolute
path). Claude Code picks up skills from `~/.claude/skills/*/SKILL.md`; after linking, `audit`
should appear whenever you ask Claude Code to audit a repository, run the engineering audit, or
run a practice audit (see the skill's own frontmatter for the exact trigger phrasing).

The symlink target and Claude Code's skill discovery convention above are correct as documented;
see the root README's [support matrix](../../README.md#support-matrix), same as above, for
whether this has actually been exercised against a live Claude Code session.

## Host environment metadata

`begin_run` takes an `environment` map, and the skill instructs Claude Code to populate it: on
this host that is `{"os": "<from uname -sr or sw_vers>", "host_cli": "claude-code",
"host_cli_version": "<from claude --version>"}`. Those three keys are the whole accepted set and
any other key is refused, because this metadata is included in feedback issues filed publicly.
`AUDIT.md` step 1 has the full rules.

## Headless / CI runs

Set `ENGINEERING_AUDIT_CONFIG` to the path of a valid `AuditConfig` JSON file before starting the
audit, and the `start_config` tool loads it directly instead of starting the interactive
localhost configuration page:

```
ENGINEERING_AUDIT_CONFIG=/path/to/config.json claude ...
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

`selected_domain_ids` must be non-empty and every id must exist in the loaded rules pack, or
`start_config` refuses the file with a clear error rather than falling back to a default
selection. See `src/engineering_audit/schema.py`'s `AuditConfig` model for the full field list.

Every run also checks this repository's tags for a newer release, on by default. On an
air-gapped or network-restricted CI runner, add `--no-update-check` to the `claude mcp add`
command above, or set `ENGINEERING_AUDIT_NO_UPDATE_CHECK` in the environment `claude` runs in.
See the root [SECURITY.md](../../SECURITY.md) for exactly what the check discloses when it runs.

## Current status

GitHub issue filing (`issue_mode: "github"`) is implemented, not a stub: the `file_issues` tool
previews the issues it would create and requires explicit user confirmation before filing
anything, then files each one via the user's own authenticated `gh` CLI (see
`src/engineering_audit/issues.py` and `file_issues` in `src/engineering_audit/server.py`).
Findings can also be copied out of the report as text instead, if you would rather not file
issues at all.

For whether the install and skill flows described above have actually been run end to end
against a real Claude Code install, check the root README's
[support matrix](../../README.md#support-matrix): that is the single, current source of truth
for "proven" versus "documented, untested", rather than a status note kept in this file that can
go stale the moment a flow is actually exercised.
