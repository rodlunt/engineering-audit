# Running an engineering practice audit

This is the procedure for an agent that has the `engineering-audit` MCP server's tools
available and has been asked to audit a repository against a rules pack. It is written to be
assistant-neutral: nothing here assumes Claude Code specifically. If your assistant has its own
notes on wiring this up (see `integrations/<assistant>/`), read those first, then follow this
document for the actual audit logic.

The tools referenced below (`list_domains`, `get_domain`, `begin_run`, `start_config`,
`get_config`, `record_domain_result`, `run_status`, `file_issues`, `submit_feedback`,
`render_report`) are exposed by the `engineering-audit` MCP server. This document assumes it is
already connected.

## 1. Gather run metadata

Before calling any tool, collect:

- **Repository name**: the directory name of the repository being audited, or its `git remote`
  slug if that is more meaningful.
- **HEAD commit**: run `git rev-parse HEAD` (or the short form) in the target repository. If the
  repository is not a git repository, use a clear placeholder such as `"no-git"` rather than
  inventing a commit hash.
- **Your own identity**: the `assistant` field is the name of the tool driving this audit (e.g.
  `"claude-code"`), and `model` is the specific model doing the reasoning. Report these honestly;
  do not guess at a value you cannot confirm.
- **Started timestamp**: the current time as an ISO 8601 string, e.g. via `date -u
  +%Y-%m-%dT%H:%M:%SZ`.

## 2. Begin the run

Call `begin_run` with the metadata above, `output_dir` set to `<target>/audit-output/` (a
directory inside the repository being audited, not inside this tool's own repository), and
`repo_dir` set to the repository being audited's own directory on disk. The tool creates
`output_dir` if it does not exist. `repo_dir` is used later, by `file_issues`, to detect which
GitHub repository to file issues on; pass it now even if you do not yet know whether the user
will choose GitHub issue filing.

If `begin_run` errors because a run is already in progress, that means a previous audit in this
same server process never finished. Either resume by calling the remaining tools in order below
against the existing run, or, if you are deliberately restarting, call `begin_run` again with
`replace=True`.

## 3. Configure the run

Call `start_config`. It responds in one of two modes:

- **`"preset"`**: the `ENGINEERING_AUDIT_CONFIG` environment variable pointed at a valid
  `AuditConfig` JSON file, and it has already been loaded. This is the headless/CI path: no user
  interaction is needed. Proceed straight to step 4 with the domain ids from the response.
- **`"interactive"`**: no preset config was found. The response includes a `url` for a localhost
  configuration page, and an `opened_in_browser` field saying whether the server managed to open
  it in the user's browser itself. If `opened_in_browser` is true, tell the user a configuration
  page has opened in their browser, and still show the URL in case the tab is buried. If it is
  false (a remote or display-less session), **show the URL to the user as a clickable line** and
  ask them to open it. Either way: choose which domains to audit, and submit the form.

In interactive mode, call `get_config` (with a reasonable `timeout_s`, e.g. 300) to wait for the
submission. `get_config` blocks until the user submits, or raises a clear error on timeout.

**If `get_config` times out, tell the user directly that the audit is waiting on them and is not
proceeding.** Never fall back to a default domain selection: an audit that silently picks domains
nobody chose is worse than no audit, because it looks authoritative. Call `get_config` again once
the user confirms they have submitted the form.

Once `get_config` succeeds, note the `selected_domain_ids` in its response: these are the only
domains you are authorised to record results for.

## 4. Sweep each selected domain

For each domain id in `selected_domain_ids`, in order:

1. Call `get_domain(domain_id)` to fetch the full rule text for that domain. Read it properly;
   do not skim. Each rule carries a stable `Rule id: ...` identifier you must use verbatim when
   recording verdicts and findings.
2. Sweep the repository, applying every rule in the domain. For each rule, reach one of four
   honest verdicts:
   - `pass`: you checked, and the repository satisfies the rule.
   - `finding`: you checked, and the repository violates the rule. Attach a `Finding` (see
     below).
   - `not-applicable`: the rule's precondition does not hold in this repository (e.g. a rule
     about API versioning in a repo with no API).
   - `could-not-evaluate`: you could not reach a verdict, **and you say why** in the required
     `note` field. Reasons for could-not-evaluate include: the relevant file does not exist, you
     do not have the access needed to check (e.g. a live deployment), or the rule requires
     information outside a static sweep (e.g. production metrics).

   **A rule you did not actually check is could-not-evaluate, never `pass`.** A `pass` is a
   specific claim that you looked and it was fine; treat it with the same care you would want
   from a human auditor putting their name to a finding.

3. Every `Finding` must:
   - Cite a **real `path:line` location you actually read**. Never fabricate a plausible-looking
     location, and never guess a line number from a search-result snippet without opening the
     file. If a finding spans a whole file rather than a line, `path` alone is acceptable.
   - Have a `severity` chosen with this guidance:
     - **critical**: exploitable now, or causes data loss (a secret committed to history, an
       auth bypass, an unguarded destructive migration).
     - **high**: not on fire today, but will bite soon under normal operation (a race condition
       in a hot path, a silently-swallowed error in a payment flow).
     - **medium**: should be fixed, but is not urgent (a missing test for a common path, an
       inconsistent naming convention that will cause a mistake eventually).
     - **low**: hygiene (a stale comment, a formatting inconsistency, a missing docstring).
   - Carry a self-contained `issue_title` and `issue_body`: written so that someone with **no
     access to the rules pack** (a developer who has never heard of this audit tool) can read the
     issue on its own, understand the problem, and know how to fix it. Do not write "see rule
     D01-R02 for details"; restate the reasoning inline. Write issue text in plain punctuation
     (commas, colons, parentheses); do not use em or en dashes.
   - **Be terse.** Both `body_md` and `issue_body` follow a strict three-part shape and nothing
     else:
     1. **The issue**: what is wrong, with the location. One or two sentences.
     2. **Why it matters**: the concrete consequence. One or two sentences.
     3. **Suggested fix**: what to change. One or two sentences, or a short list.
     No preamble, no restating the rule's full rationale, no hedging filler, no summary of what
     you inspected (coverage belongs in the run's coverage numbers, not in every finding). If a
     finding genuinely needs supporting evidence (a quoted config block, a reproduction), append
     it after the three parts, kept minimal. A finding a developer can absorb in fifteen seconds
     gets fixed; a page of prose gets skimmed. A supporting reference (the rule's cited source, or
     a plain statement that the rule carries none) is appended automatically from the rules pack
     after every rendered finding; do not restate sources or the rule's literature-review rationale
     in the body yourself. Keep the three parts about the repository, not about the literature.
4. Once every rule in the domain has a verdict, decide the domain's overall `status`:
   - `completed`, if you were able to sweep the repository at all (even if some individual rules
     ended up could-not-evaluate).
   - `could-not-run`, only if the whole domain could not be attempted (e.g. the domain's trigger
     condition genuinely does not apply to anything in this repository, or the repository is
     empty). This is different from an individual rule being could-not-evaluate; use it sparingly
     and give a clear `reason`.
5. Call `record_domain_result` with the full `DomainResult` payload. If it errors:
   - **Incomplete result** (a rule has no verdict): the error message lists exactly which rule
     ids are missing. Go back, verdict them, and resubmit. Never resubmit by inventing a `pass`
     for the missing ids just to make the error go away.
   - **Domain not selected**: you are trying to record a domain the user did not choose. Skip it.
   - **Already recorded**: you are re-recording a domain. If this is deliberate (you found a
     mistake in your first pass), pass `replace=True`. If it is not deliberate, you have a bug in
     your loop; do not paper over it with `replace=True`.

## 5. Check status before rendering

Before rendering the report, call `run_status`. Confirm `missing_domain_ids` is empty. If it is
not, go back to step 4 for the missing domains: `render_report` will refuse to produce a report
for a run with a selected domain that has no result, and it should, because a report with a
silent gap is worse than no report.

## 6. File issues, if the user chose GitHub delivery

Check `config.issue_mode` from step 3's `get_config` response.

- **`"report"`**: skip this step entirely. The report's "issues" section carries copy-to-clipboard
  text built from each finding's `issue_title` and `issue_body`, which is why those two fields
  must stand alone (see step 4 above).
- **`"github"`**: call `file_issues` with no arguments (`confirm` defaults to `False`). This never
  files anything and never touches `gh`; it returns a preview: the target repository (if already
  known), how many issues would be filed, and their titles. **Show this preview to the user and
  ask for their explicit approval.** Filing issues on someone's repository is outward-facing; do
  not treat silence or moving on to the next step as approval.

  Only once the user has explicitly agreed, call `file_issues(confirm=True)`. It detects the
  target repository from `repo_dir` (given to `begin_run` in step 2) unless you pass `repo`
  explicitly, and files one issue per finding via the user's own `gh` CLI. If it raises partway
  through, the error names exactly which rule ids were filed (with their URLs) and which were
  not; fix the underlying problem (commonly: `gh` not authenticated, or the detected repository
  is wrong) and call `file_issues(confirm=True)` again. Already-filed findings are skipped
  automatically, so a retry never double-files.

## 7. Render the report

Call `render_report` with a `finished` ISO timestamp (the current time, same format as `started`
in step 1). It writes `report.html` and `run-state.json` into the run's `output_dir` and returns
their paths along with a findings summary. Any issues filed in step 6 are linked automatically;
there is nothing further to pass.

Tell the user directly where `report.html` is, give them a one-line summary of what was
found (e.g. "3 findings: 1 high, 2 medium, across 2 domains"), and offer to open the report
for them (`xdg-open` on Linux, `open` on macOS, `start` on Windows); if opening fails or the
session is remote, the path is the fallback. Do not just say "the audit is done"; the
report's location and headline numbers are the actual deliverable.

## 8. Offer to send feedback

If the user supplied feedback text on the configuration page (`config.feedback_text`), call
`submit_feedback`. It sends that text, plus a run-metadata section and whichever telemetry
sections the user consented to (coverage, findings rollup, self-assessment, environment; never
finding text), to the tool author's repository via `gh`. If it returns `mode: "mailto"` (gh was
unavailable or filing failed), tell the user their feedback was not lost: offer to open the
`mailto_url`, and if that fails or is unavailable, offer the `body` text for them to paste into an
email themselves. The rendered report also carries this same mailto fallback in its Feedback
section, so nothing is lost even if this step is skipped.

Do not call `submit_feedback` if the user gave no feedback text and did not ask you to send
anything: an unprompted, empty submission is not consent.
