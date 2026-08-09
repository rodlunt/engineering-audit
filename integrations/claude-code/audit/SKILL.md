---
name: audit
description: Use when the user asks to audit this repo, run the engineering audit, run a practice audit, or check the codebase against the engineering-audit rules pack. Drives the engineering-audit MCP server's tools through a full audit run and produces a self-contained HTML report.
---

# Engineering practice audit

Runs a full engineering-practice audit of the current repository via the `engineering-audit`
MCP server, and produces a self-contained HTML report.

The full, assistant-neutral procedure lives in this tool's own `AUDIT.md` (in the
`engineering-audit` repository this server was installed from). **Follow that document as the
source of truth.** The summary below exists so you do not have to fetch it before starting, but
if the two ever disagree, `AUDIT.md` wins.

## Flow (summary)

1. Gather run metadata: repository name, `git rev-parse HEAD`, your own assistant/model identity,
   and an ISO 8601 started timestamp.
2. Call `begin_run` with that metadata and `output_dir` set to `<repo>/audit-output/`.
3. Call `start_config`.
   - **Preset mode** (`ENGINEERING_AUDIT_CONFIG` env var set): the config is already loaded, skip
     to step 4.
   - **Interactive mode**: the response has a `url`. **Show it to the user as a clickable
     line** (do not open it yourself, and do not try to fetch or wait on it via Bash) and ask
     them to choose domains there. Then call `get_config` with a sensible `timeout_s` to block
     until they submit.
   - **If `get_config` times out**, tell the user plainly that the audit is waiting on them and
     stop. Do not proceed with a guessed domain selection. Call `get_config` again once they
     confirm.
4. For each selected domain id: call `get_domain`, read the full rule text, sweep the repository
   giving every rule an honest verdict (`pass`, `finding`, `not-applicable`, or
   `could-not-evaluate` with a reason: never a guessed `pass`), then call
   `record_domain_result`. If it rejects the payload as incomplete, the error lists the missing
   rule ids; fix and resubmit. Write every finding and issue body terse, in three parts and
   nothing else: the issue, why it matters, suggested fix (one or two sentences each).
5. Call `run_status` and confirm nothing is missing.
6. Call `render_report` with a finished timestamp. Tell the user where `report.html` landed and
   give a one-line findings summary.

## Claude-Code-specific notes

- **Drive the config wait through the `get_config` tool, not Bash.** Do not poll the config
  server with `curl`, and do not try to open the browser yourself. `get_config` already blocks on
  the user's submission and raises a clear error on timeout; that is the whole mechanism.
- **Show the config URL as a clickable line**, e.g. `Open this to choose domains:
  http://127.0.0.1:PORT/`, so the terminal renders it as a link the user can click straight away.
- Severity guidance, the file:line citation rule, and the issue_title/issue_body
  self-containment rule are all in `AUDIT.md`, steps 4 to 6. Do not skip reading them the first
  time you run this: they are the difference between a trustworthy audit and a plausible-looking
  one.
