# Engineering Audit Triggers (merge into GEMINI.md)

**This file is for Gemini CLI only.** It sits in the repository root because Gemini CLI
resolves an extension's context file from the root of the repository it installs, next to
`gemini-extension.json` and `commands/` (see `integrations/gemini/README.md`, and issue
#145 for why it cannot live under `integrations/`). It is not general repository guidance,
it is not loaded by Claude Code or Codex, which read `CLAUDE.md` and `AGENTS.md`
respectively, and nothing outside Gemini CLI should treat it as instructions.

This file ships as a placeholder. The engineering-audit repository contains no
rule content of its own (rules packs are distributed separately, see the
repository root README), so there is nothing real to bake into this context file
at packaging time.

Before installing this extension, generate the real trigger content from your own
rules pack and replace everything below this line with the generator's output:

```
uv run engineering-audit-fragments --rules-dir <path-to-rules-clone> --out-dir <tmp-dir>
```

Then copy `<tmp-dir>/GEMINI-fragment.md` over the section below (it already starts
with a "merge into GEMINI.md" header matching this file).

For the standalone audit flow, see `AUDIT.md` in the engineering-audit repository:
it is the source of truth this extension's `/audit` command points at.
