# engineering-audit

An engineering-practice audit tool for agentic coding CLIs, delivered as a local MCP server.
It is rules-pack-agnostic: the server points at a local directory of rule documents and serves
them to the agent driving the audit. The agent supplies the judgement; this package supplies
everything around it: rules plumbing, schema-validated finding capture, a local configuration
page, a self-contained HTML report, GitHub issue filing, and a feedback channel.

## Two delivery modes

**Inline mode.** One-line triggers merged into your assistant's instruction context
(`AGENTS.md`, `GEMINI.md`, or Claude Code skills) tell it to load the relevant rule domain at
the moment of a decision: designing a schema, cutting a branch, shaping an API. The rules are
fetched on demand through the MCP server's `get_domain` tool, so the trigger lines stay tiny.

**Standalone audit mode.** The assistant sweeps a repository against the rule domains you
select and produces a self-contained HTML report. Every rule gets an honest verdict (`pass`,
`finding`, `not-applicable`, or `could-not-evaluate` with a reason); a rule the agent did not
check can never be recorded as a pass, and the report refuses to render with silent gaps.
Findings can be filed as GitHub issues on the audited repository (with an explicit confirmation
step) or listed in the report as copy-paste issue text.

## Support matrix

| Assistant | Inline mode | Standalone audit |
|---|---|---|
| Claude Code | proven (in daily use via skills) | proven (proving run 2026-08-09) |
| OpenAI Codex CLI | documented, untested | documented, untested |
| Gemini CLI | documented, untested | documented, untested |
| GitHub Copilot | unsupported | unsupported |

"Proven" means a recorded end-to-end run exists. "Documented, untested" means the integration
follows the assistant's official documentation (with individually verified pieces labelled in
the integration README) but no full audit has been exercised on it here. Copilot is
deliberately unsupported rather than silently absent.

## Install

The server is a Python package (managed with `uv`, requires Python 3.10+). Run it with `uvx`
straight from this repository, or from a local clone:

```sh
# via uvx (no clone needed)
uvx --from git+https://github.com/rodlunt/engineering-audit engineering-audit-mcp \
    --rules-dir /path/to/your/rules-clone/domains

# or from a local clone
uv --directory /path/to/engineering-audit run engineering-audit-mcp \
    --rules-dir /path/to/your/rules-clone/domains
```

Per-assistant wiring, including MCP registration and the audit entry point:

- [integrations/claude-code/](integrations/claude-code/) : `claude mcp add` plus the `audit` skill
- [integrations/codex/](integrations/codex/) : `codex mcp add` plus AGENTS.md trigger fragment
- [integrations/gemini/](integrations/gemini/) : extension package plus GEMINI.md fragment

Inline trigger fragments are generated from your rules pack with
`engineering-audit-fragments --rules-dir <path> --out-dir <path>`.

The audit procedure itself is [AUDIT.md](AUDIT.md); assistants follow it through the MCP tools.
Headless and CI runs skip the interactive configuration page by pointing the
`ENGINEERING_AUDIT_CONFIG` environment variable at a saved configuration file.

## Rules access

This repository contains no rule content, and the audit cannot run without a rules pack. The
author's rules pack (16 engineering-practice domains, roughly 260 sourced rules) lives in a
private repository; access is granted per user as a read-only collaborator. Open an issue here
to ask. The tooling itself works with any rules directory that follows the expected document
format (a `**Trigger:**` header line and `### N. Title` rules with `Rule id:` footers).

## Feedback

The configuration page includes an optional free-text feedback field and per-section consent
tick boxes for what run telemetry accompanies it. Feedback arrives as a labelled issue on this
repository (filed through your own `gh` auth), or as an email you send yourself when `gh` is
unavailable. Run metadata is always included when sending; finding text never leaves your
machine through this channel, only counts. Details: [docs/feedback.md](docs/feedback.md).

## Development

```sh
uv sync
uv run pytest -q
```

Tests run against an invented fixture rules pack; no private rule content exists in this
repository. The report renderer and configuration page are deterministic and fully testable
without any LLM involvement.

## Roadmap

- A thin CLI wrapper that drives an agent CLI headlessly end to end (the artefacts here are
  already shaped for it).
- Remotely served rules with revocable access.
- Codex and Gemini rows moving to proven once live runs are recorded.

## Licence

To be finalised before the repository is made public. All rights reserved in the interim.
