# engineering-audit for Claude Code

Wires the `engineering-audit` MCP server and its `audit` skill into Claude Code.

## Install the MCP server

**From the published git repository** (no local clone needed):

```
claude mcp add engineering-audit --scope user -- uvx --from git+https://github.com/rodlunt/engineering-audit@v0.14.0 engineering-audit-mcp --rules-dir <path-to-rules-clone>
```

`--scope user` registers the server for every repository. Without it `claude mcp add` defaults
to local scope, which registers it for the current directory alone and leaves it unavailable
everywhere else.

`@v0.14.0` pins the install to the current tagged release rather than the moving `main` branch;
find the latest tag on the [Releases page](https://github.com/rodlunt/engineering-audit/releases).

### Updating to a newer tag

**Remove first.** `claude mcp add` refuses to overwrite an existing name, so changing the tag
in the command above and running it again fails with `MCP server engineering-audit already
exists in user config`. VERIFIED on claude-code 2.1.232. (Codex differs here: `codex mcp add`
overwrites silently, so its own README's instruction to re-add is correct for that host.)

```
claude mcp remove engineering-audit
claude mcp add engineering-audit --scope user -- uvx --from git+https://github.com/rodlunt/engineering-audit@v0.14.0 engineering-audit-mcp --rules-dir <path-to-rules-clone>
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

## Install the interrogate skill (BETA)

**Beta, and the label is literal.** New in v0.13.0, reshaped in v0.14.0 to run every relevant
domain and triage globally rather than capping at three domains. Question derivation has been
exercised on three of the sixteen domains against a single brief and the hook's failure paths have
been tested, but **the cross-domain triage has never been run, and nobody has yet completed a full
interactive interrogation**. The shape of the session (the relevance judgement, the triage, the
deep-dive offer, the bail-out record) is unproven with a real person answering, and question
quality is sampled rather than measured. Expect it to change, and please
report anything that reads like a generic quiz rather than a question about your actual work:
that is the failure mode this design is most likely to have.

`interrogate` is the pre-build counterpart to `audit`. Where `audit` sweeps the rules over a
repository that already exists, `interrogate` turns them into questions about work that has not
started yet, and records the answers, the deferrals and the gaps into the host's plan file.

```
ln -s "$(pwd)/integrations/claude-code/interrogate" ~/.claude/skills/interrogate
```

Same discovery convention and the same absolute-path requirement as `audit` above.

It uses `list_domains` and `get_domain` only, and never calls `begin_run`. Both of those tools
work with no run in progress, which is what lets the skill run against a directory that is not a
repository yet, or against nothing but a description. Nothing it does touches run state, so an
interrogation and an audit cannot interfere with each other.

The two skills answer different questions and neither replaces the other:

| | `audit` | `interrogate` |
|---|---|---|
| Runs against | a repository | intent, or a drafted plan |
| Produces | `report.html` plus findings | questions, and a record of what was answered |
| Needs a git commit | yes | no |
| Rule coverage | every rule in every selected domain | every relevant domain, triaged to the top three questions first |

`interrogate` is exhaustive in what it *derives* and deliberately not in what it *asks*. It runs
every domain whose trigger fires, with no cap, and works out the full question set. It then asks
only the three most impactful from the whole pool, because an interrogation nobody finishes
teaches nothing, and offers the rest as a deep dive with the real number attached ("that is the
top three of 47").

Nothing derived is thrown away. The record carries a per-domain table of derived, asked and not
asked, and the domains judged irrelevant are named too, so a short session cannot be mistaken for
a complete one and a skipped domain cannot be mistaken for a covered one.

The cost lands in the fan-out: one read-only subagent per relevant domain, each reading a document
of 300 to 800 lines. The skill tells you how many that is and asks before spending it, because
that is the only point where the cost is knowable in advance.

### Optional: offer it automatically when plan mode starts

The skill above is invoked deliberately. If you would rather be asked, `interrogate-offer.sh` in
this directory injects a one-off prompt when a session enters plan mode, offering three choices:
interrogate first, interrogate the finished draft afterwards, or skip.

```
"hooks": {
  "UserPromptSubmit": [
    { "hooks": [ { "type": "command",
        "command": "s=\"$HOME/path/to/engineering-audit/integrations/claude-code/interrogate-offer.sh\"; [ -x \"$s\" ] && bash \"$s\"; exit 0",
        "timeout": 10 } ] }
  ],
  "PreToolUse": [
    { "matcher": "EnterPlanMode",
      "hooks": [ { "type": "command",
        "command": "s=\"$HOME/path/to/engineering-audit/integrations/claude-code/interrogate-offer.sh\"; [ -x \"$s\" ] && bash \"$s\"; exit 0",
        "timeout": 10 } ] }
  ]
}
```

**Keep the trailing `exit 0`, and do not simplify the command to a bare `bash <script>`.** A shell
script containing a syntax error exits **2** without running a line, and on `UserPromptSubmit` an
exit code of 2 does not merely fail, it **erases the prompt you just typed**. The script's own
never-exit-non-zero discipline cannot protect against that, because a broken script never starts.
The `exit 0` is the only thing that holds in that case.

Two legs, because there are two ways in. `UserPromptSubmit` gated on `permission_mode == "plan"`
is the mechanism: there is no hook event for a permission-mode change, so the first prompt seen in
plan mode stands in for the transition, and a session-keyed stamp makes every later one a no-op.
That also covers `claude --permission-mode plan`, where the session starts in plan mode and there
is no transition at all. The `EnterPlanMode` leg catches a model-initiated entry one turn earlier;
it works but is undocumented upstream, so treat it as a bonus rather than a guarantee.

The hook never blocks and never denies. It reports rather than fails silently: if it cannot read
its payload, cannot find `jq`, cannot write its state directory, or finds the skill directory
present but its `SKILL.md` unresolvable (a dangling symlink), it says so once a day and does
nothing. If the skill is simply not installed, it stays quiet rather than offering something that
is not there.

Turn it off without touching your settings:

```
touch "${XDG_STATE_HOME:-$HOME/.local/state}/claude-interrogate-offer/off"
# or set CLAUDE_INTERROGATE_OFFER=0
```

**One thing the hook cannot do:** if you choose "interrogate afterwards", nothing enforces it. The
hook fires once on entry and there is no leg on `ExitPlanMode`, so the commitment is held only by
the assistant for the rest of that session. The skill says so in its own instructions, but it is a
gap, not a mechanism.

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
