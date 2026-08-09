# engineering-audit for Claude Code

Wires the `engineering-audit` MCP server and its `audit` skill into Claude Code.

## Install the MCP server

**From the published git repository** (no local clone needed):

```
claude mcp add engineering-audit -- uvx --from git+https://github.com/rodlunt/engineering-audit engineering-audit-mcp --rules-dir <path-to-rules-clone>
```

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

**Status: untested.** Both commands above match the project's actual CLI entry point
(`engineering-audit-mcp`, see `pyproject.toml`'s `[project.scripts]`) and `claude mcp add`'s
documented syntax, but neither has been run against a real Claude Code install as part of this
milestone. Confirm the server actually appears in `claude mcp list` and responds to `list_domains`
before relying on it.

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

**Status: untested**, for the same reason as above: the symlink target and Claude Code's skill
discovery convention are both correct as documented, but this has not been exercised against a
live Claude Code session.

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

## What is not built yet

- Issue filing (`issue_mode: "github"`) is accepted by the schema but not wired up to actually
  file anything; that is milestone 3. Findings currently show up in the report as
  copy-to-clipboard text.
- This README describes the intended install flow; it has not been run end to end against a real
  Claude Code install (see the "Status: untested" notes above). Treat it as accurate to the code,
  not as a verified runbook, until someone has actually walked through it.
