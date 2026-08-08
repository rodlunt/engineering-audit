# engineering-audit

A rules-pack-agnostic engineering practice audit tool, delivered as a local MCP server for
agentic coding CLIs (Claude Code, OpenAI Codex CLI, Gemini CLI).

Two delivery modes:

1. **Inline mode**: one-line triggers injected into the assistant's context tell it to load the
   relevant rule domain at the moment of a decision (schema design, branch cut, API shape).
2. **Standalone audit mode**: the assistant sweeps a repository against selected rule domains
   and produces a self-contained HTML report, with optional GitHub issue filing and a feedback
   channel to the developer.

The judgement is performed by the LLM agent; this package supplies the rules plumbing,
schema-validated finding capture, configuration UI, report rendering and issue filing as MCP
tools.

**Rules packs are distributed separately.** This repository contains no rule content. The tool
points at any local directory of rule documents following the expected format; access to the
author's rules pack is granted per user. Open an issue to ask.

Status: pre-release scaffold. Full README (install per assistant, support matrix, feedback
process) lands with v0.1.0.

Licence: to be finalised before first release. All rights reserved in the interim.
