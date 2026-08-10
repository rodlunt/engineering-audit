# engineering-audit for Gemini CLI

**Status: DOCUMENTED, UNTESTED.** Gemini CLI is not installed on the machine this
milestone was built on, so nothing in this directory has been run against a real
Gemini CLI session. Every claim below is sourced from Gemini CLI's own published
docs (`github.com/google-gemini/gemini-cli`: `docs/cli/gemini-md.md`,
`docs/cli/custom-commands.md`, `docs/extensions/reference.md`), not from a live
test. Confirm each step against `gemini --help` and `gemini extensions --help` on
your own install before relying on it.

## What is here

- `gemini-extension/gemini-extension.json`: an extension manifest that registers
  the `engineering-audit` MCP server and points Gemini CLI at a bundled `GEMINI.md`
  for inline triggers.
- `gemini-extension/GEMINI.md`: a placeholder context file. It ships empty of real
  trigger content because this repository carries no rule content of its own
  (rules packs are distributed separately); regenerate it from your own rules pack
  before installing (instructions inside the file).
- `gemini-extension/commands/audit.toml`: a custom `/audit` command that points the
  agent at this tool's `AUDIT.md` for the standalone audit flow.

## Install

**As a packaged extension**, once you have regenerated `GEMINI.md` from your rules
pack (see the placeholder file for the exact command):

```
gemini extensions install <repo-or-local-path-to-this-extension> --ref v0.7.0
```

Documented syntax: `gemini extensions install <url>` installs an extension from a
git repository URL. A local, unpublished extension directory may need a different
form; check `gemini extensions install --help` on your install. `--ref v0.7.0` pins
the install to the current tagged release rather than the moving `main` branch;
see the root README's [How to use](../../README.md#how-to-use) section for how to find
the latest tag and update to it deliberately. Drop `--ref` (or point it at a
branch) only for a deliberate local/dev install off `main`.

**As a manual MCP server entry**, without the extension packaging, add an entry to
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

This manual route sidesteps the extension's `settings`-array mechanism entirely
(see the note below), so `ENGINEERING_AUDIT_RULES_DIR` is set directly and
unambiguously.

### A note on the extension's environment variable

The manifest declares `ENGINEERING_AUDIT_RULES_DIR` in its `settings` array rather
than hardcoding a path, because Gemini CLI's own docs state that an extension does
not inherit the user's full shell environment: it only sees a small set of "safe"
variables plus whatever it explicitly declares via `settings[].envVar`. Declaring
it there is documented as the way an extension requests pass-through access to a
specific variable. Whether the CLI also requires (or additionally supports) an
explicit `mcpServers.engineering-audit.env` entry naming the same variable is not
stated in the docs consulted for this milestone, so it is left out of the manifest
and left to be verified when Gemini CLI is actually available: set
`ENGINEERING_AUDIT_RULES_DIR` in your shell before running `gemini`, and confirm
with a tool listing that the server picked it up.

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

If you are not using the packaged extension, generate the fragment yourself and
merge it into whichever tier's `GEMINI.md` you want the triggers to apply to:

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

**Interactive**: start `gemini` in the repository to audit and either run `/audit`
(if the extension is installed) or tell it directly to follow `AUDIT.md` from the
engineering-audit repository, driving the MCP tools through to a rendered report.

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

Everything in this directory, the manifest, the placeholder context file, the
`/audit` command, and the guidance above, is **DOCUMENTED, UNTESTED**. Nothing here
has been exercised against a live Gemini CLI session. Treat it as a starting point
built from the project's own published documentation, not a verified runbook.
