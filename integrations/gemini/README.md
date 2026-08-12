# engineering-audit for Gemini CLI

**Status: DOCUMENTED, UNTESTED.** Gemini CLI is not installed on the machine this
milestone was built on, so nothing in this directory has been run against a real
Gemini CLI session. Every claim below is sourced from Gemini CLI's own published
docs (`github.com/google-gemini/gemini-cli`: `docs/cli/gemini-md.md`,
`docs/cli/custom-commands.md`, `docs/extensions/reference.md`), not from a live
test. Confirm each step against `gemini --help` and `gemini extensions --help` on
your own install before relying on it.

## What is here

Documentation only. There are no Gemini-specific files to install.

## There is no packaged extension, deliberately

An extension manifest, a `/audit` slash command and a bundled context file used to
live here. They were removed in issue #145 and are not coming back in this
repository.

Gemini CLI's extension reference states that "Each extension must have a
`gemini-extension.json` file in its root directory", with no documented subdirectory
support and no path flag on `gemini extensions install`. Custom commands must sit in
a `commands/` directory at that same root, and the context file alongside it. So a
manifest under `integrations/` is never found: the install silently registers no MCP
server, which is exactly what an external tester hit.

Making it work meant putting three Gemini-specific files in the root of a tool that
also serves Claude Code and Codex, widening the release pin scan to reach them, and
shipping a context file that is a placeholder every user must regenerate before it
does anything. All of that to carry packaging that had never been run against a real
Gemini CLI once.

The two routes below need none of it. If you want the packaged-extension experience,
the pieces are in this repository's history and you are welcome to assemble it in a
repository of your own, where the root is yours to spend.

## Install

**As a manual MCP server entry**, add an entry to
your `~/.gemini/settings.json` (or the project-level `.gemini/settings.json`)
`mcpServers` map:

```json
{
  "mcpServers": {
    "engineering-audit": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/rodlunt/engineering-audit@v0.7.0",
        "engineering-audit-mcp"
      ],
      "env": {
        "ENGINEERING_AUDIT_RULES_DIR": "<path-to-rules-clone>"
      }
    }
  }
}
```

`@v0.7.0` pins the install to the current tagged release rather than the moving `main` branch;
see the root README's [How to use](../../README.md#how-to-use) section for how to find the latest tag
and update to it deliberately.

Setting `ENGINEERING_AUDIT_RULES_DIR` in the `env` map directly, rather than relying
on the environment, is deliberate. Gemini CLI's own docs state that an extension does
not inherit the user's full shell environment: it sees a small set of "safe"
variables plus whatever it explicitly declares. That constraint was one of the
awkward parts of the extension packaging, and the manual entry sidesteps it, because
an `env` map on the server entry is unambiguous and needs nothing declared elsewhere.

**Prompt for the standalone audit.** The extension used to provide a `/audit` slash
command. Without it, start `gemini` in the repository you want audited and paste:

```
Audit this repository against the engineering rules. Read AUDIT.md from the
engineering-audit repository and follow it, driving the engineering-audit MCP tools
through to a rendered report.
```

If you want that as a real slash command, Gemini CLI supports user-level custom
commands independently of extensions: put the same text in a TOML command file under
your own `~/.gemini/commands/`. Check `gemini --help` for the current layout, since
none of this has been exercised here.

Every run also checks this repository's tags for a newer release, on by default. On an
air-gapped or network-restricted machine, set `ENGINEERING_AUDIT_NO_UPDATE_CHECK` the same way as
`ENGINEERING_AUDIT_RULES_DIR` above (via the manifest's `settings` array, or directly in the
`env` map for the manual MCP entry). See the root [SECURITY.md](../../SECURITY.md) for exactly
what the check discloses when it runs.

## Inline mode: GEMINI.md hierarchy

Gemini CLI concatenates context files from three tiers before every prompt:

- **Global**: `~/.gemini/GEMINI.md`, default instructions for every project.
- **Workspace**: `GEMINI.md` found by walking upward from your working directory
  through its parent directories.
- **Just-in-time**: when a tool touches a file or directory, the CLI also scans
  that directory and its ancestors (up to a trusted root) for further `GEMINI.md`
  files.

All discovered files are concatenated and sent with every prompt; `@file.md`
import syntax (relative or absolute paths) lets one `GEMINI.md` pull in another
file's content, which is one way to keep the generated fragment in its own file
while still merging it in. The active file count is shown in the CLI's footer, so
you can confirm the fragment was actually picked up.

Generate the fragment and merge it into whichever tier's `GEMINI.md` you want the
triggers to apply to. This is the whole inline story now, and it never depended on
the extension:

```
uv run engineering-audit-fragments --rules-dir <path-to-rules-clone> --out-dir /tmp/eaf
cat /tmp/eaf/GEMINI-fragment.md >> GEMINI.md
```

## Standalone audit mode

### Host environment metadata

`begin_run` takes an `environment` map, and the assistant should populate it: on this
host that is `{"os": "<from uname -sr or sw_vers>", "host_cli": "gemini",
"host_cli_version": "<from gemini --version>"}`. Those three keys are the whole
accepted set and any other key is refused; omit one you cannot determine rather than
guessing. `AUDIT.md` step 1 has the full rules and the reason the set is closed. The
host CLI and its version are what decide whether a reported bug reproduces, and
neither is derivable from the report header's assistant and model rows.

**Interactive**: start `gemini` in the repository to audit and paste the prompt from
the Install section above, which tells it to follow `AUDIT.md` from the
engineering-audit repository and drive the MCP tools through to a rendered report.

**Headless**: Gemini CLI documents a `-p` flag for non-interactive prompts, but its
exact file-input behaviour (whether it reads a prompt from stdin the way Codex's
`codex exec -` does, or only accepts a prompt argument) is **UNVERIFIED** here.
Check `gemini --help` for the current `-p` behaviour on your install before
building a headless pipeline around it.

**Approval modes**: community sources report flags such as
`--approval-mode=auto_edit` or `--approval-mode=yolo` for controlling how much
Gemini CLI can do without asking, but these are **NOT verified** against a real
install as part of this milestone. Run `gemini --help` and confirm the actual flag
names and semantics before starting an unattended run; treat any unattended,
approval-skipping mode with the same caution as Codex's
`--dangerously-bypass-approvals-and-sandbox` (see `integrations/codex/README.md`):
an explicit, deliberate opt-in, not a default.

## Status, restated

Everything above is **DOCUMENTED, UNTESTED**. Nothing here has been exercised
against a live Gemini CLI session. Treat it as a starting point built from the
project's own published documentation, not a verified runbook.

That label is why the packaged extension is gone rather than fixed. It was carrying
structural cost in this repository's root to ship something nobody had ever run. The
manual MCP entry and the trigger fragment are equally untested, but they cost
nothing to carry and a user can see exactly what they are pasting.
