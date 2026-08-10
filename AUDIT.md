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
- **Host environment**: the three facts the report header cannot carry, passed to `begin_run` as
  `environment`. The key set is closed; `begin_run` refuses any other key outright.
  - `os`: the operating system of the machine this is running on, e.g. `"macOS 15.2"`,
    `"Ubuntu 24.04"`, `"Windows 11"`. Read it from the machine (`uname -sr`, `sw_vers`,
    `/etc/os-release`); do not infer it.
  - `host_cli`: the CLI application driving this audit, e.g. `"codex"`, `"claude-code"`,
    `"gemini"`.
  - `host_cli_version`: that CLI's version string, e.g. `"0.147.0"`, read from its own
    `--version` output.

  Omit any key you cannot determine rather than guessing at it: an omitted fact and an invented
  one are not the same thing, and the report says "no environment information reported" rather
  than pretending otherwise. Do **not** put the assistant, the model or the tool version in here;
  all three are already fixed rows in the report header, and duplicating them is why this field
  sat empty in every run until now. This metadata is included in feedback issues filed publicly
  on the tool's own repository, which is why the key set is closed and the values are capped in
  length: it is the one part of a run whose disclosure surface is not under the user's eye at
  submission time.

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

### 2a. If a previous run was interrupted

A run's progress is saved to a `run-state.progress.json` file in its `output_dir` as it goes:
after `begin_run`, once the configuration is resolved, after every `record_domain_result`, after
every issue filed, and after feedback is sent. So an audit that was cut short (the host
application restarted, the connection dropped, the machine slept) can be picked up where it
stopped instead of run again from the beginning.

When `begin_run` finds such a file for an unfinished run in `output_dir`, **it starts nothing**
and returns a description of what it found instead: `run_started` is `false`, there is no `meta`
key, and `resumable` says whether that run can be continued. Check `run_started` before reading
anything else out of a `begin_run` response.

- **`resumable: true`**: `prior_run` tells you when it started, which domains it already
  recorded (`recorded_domain_ids`), which are still outstanding (`missing_domain_ids`), how many
  findings it holds, and how many issues it already filed. **Show this to the user and ask
  whether to continue it.** Then call `begin_run` again with the same arguments plus
  `resume=True` to continue it, or `resume=False` to throw it away and start fresh. This is not
  your decision to make silently: `resume=False` permanently discards audit work that was
  already done, and continuing means the report is attributed to the run's original start time,
  assistant and model.
- **`resumable: false`**: the `reason` says why. Either the saved run audits a **different
  repository** (almost always a wrong `output_dir`: point it at a directory inside the
  repository you are auditing), or its saved state **cannot be read** (its results are not
  recoverable; tell the user, quote the error, and do not start over until they say to). In both
  cases `resume=True` is refused outright, and only an explicit `resume=False` will overwrite
  the file. Choosing a different `output_dir` leaves it alone entirely.

On a successful resume, the response carries `resumed: true`, the recovered `config`, and
`recorded_domain_ids` / `missing_domain_ids`. Skip step 3 (the configuration is already
resolved: do not call `start_config` again) and audit only the domains in
`missing_domain_ids`. Do not re-audit the recovered domains unless the user asks. Any `warnings`
in the response are things the user needs to hear before you carry on: most commonly that the
repository's HEAD commit or the rules pack has changed since the run started, in which case the
run keeps the original commit, because that is what the recorded results were actually checked
against.

If any later tool response carries a `warnings` entry about crash-recovery state not being
saved, pass it on to the user: the run itself is fine and its results are intact, but from that
point it can no longer be resumed if the server stops.

`begin_run`'s response also includes `meta.update_check`, the result of a best-effort check
against this tool's latest tagged release on GitHub. If it starts with `stale` or
`could-not-check`, tell the user before continuing: a stale tool still audits fine, but the user
decides whether to update first, and that is not your call to make silently.

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

In interactive mode, **call `get_config` in a loop.** It does not hold one call open until the
user submits: it waits internally for about 25 seconds, then returns and expects to be called
again. Branch on the `status` field of its response, and on nothing else:

| `status` | What it means | What to do |
|---|---|---|
| `"configured"` | The configuration is resolved. `config` and `selected_domain_ids` are in the response. | Stop calling `get_config` and go to step 4. |
| `"waiting"` | The page is up and nobody has submitted it yet. This is neither a configuration nor a failure. | Call `get_config` again. Keep going while the status says `waiting`. |
| an error is raised | The overall deadline (`timeout_s`) elapsed with no submission. | Stop. Tell the user the audit is waiting on them and is not proceeding. |

So the loop is: call `get_config`, and while the response's `status` is `"waiting"`, call it
again. Nothing else in the run may start until the status is `"configured"`. **Never fall back to
a default domain selection**, on a `waiting` response or on a timeout: an audit that silently
picks domains nobody chose is worse than no audit, because it looks authoritative.

The first `waiting` response is the one to speak up on: tell the user the configuration page is
open at the response's `url` and the audit is waiting on them there. After that, keep polling
quietly rather than narrating every call.

`timeout_s` is the run's **overall** waiting budget, not a per-call one. It is measured from the
moment the page opened and is enforced cumulatively across every call, so polling more often does
not buy the user more time and polling less often does not cost them any. Pass a value that
reflects how long the user might reasonably take (300 is the default; a sixteen-domain choice may
deserve more). If it expires and the user still intends to submit, calling `get_config` again with
a **larger** `timeout_s` resumes waiting: the page is still up and their submission is still
accepted.

Why the loop exists, so nobody "simplifies" it back: host applications impose their own per-tool
timeouts, independent of `timeout_s` (Codex has `mcp_servers.<name>.tool_timeout_sec`; the run
reported in issue #85 was cancelled by it after 300 seconds). A single tool call held open longer
than the host's limit is cancelled by the host, and that cancellation can take the whole MCP
process down with it, along with the configuration page the user was in the middle of filling in.

Once `get_config` reports `"configured"`, note the `selected_domain_ids` in its response: these
are the only domains you are authorised to record results for.

If the user reports that the configuration page says the audit process is no longer running, that
page is telling the truth and its URL is dead for good. Start again from step 2: `begin_run` will
find the saved run and offer to resume it, and a fresh configuration page opens on a new URL with
their domain selection already restored.

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
4. If reaching a verdict on a rule involved fetching or reading anything **outside the rules
   pack itself** (a documentation page, a standard, a spec, a paper, anything not already inside
   `get_domain`'s text), record it in that `DomainResult`'s `consulted_sources`: `{rule_id, url,
   title, why, accessed}`. `rule_id` must be one of this domain's own rule ids; `why` is a
   one-line reason the source was consulted, not a summary of its contents. This server cannot
   see what you fetched or read outside its own pack: **an unrecorded source is a claim it
   cannot back.** Do this for every rule as you go, not as a memory exercise at the end of the
   sweep. Leave `consulted_sources` empty for a rule you verdicted from the rules pack and the
   repository alone, which is the common case.
5. Once every rule in the domain has a verdict, decide the domain's overall `status`:
   - `completed`, if you were able to sweep the repository at all (even if some individual rules
     ended up could-not-evaluate).
   - `could-not-run`, only if the whole domain could not be attempted (e.g. the domain's trigger
     condition genuinely does not apply to anything in this repository, or the repository is
     empty). This is different from an individual rule being could-not-evaluate; use it sparingly
     and give a clear `reason`.
6. Call `record_domain_result` with the full `DomainResult` payload. If it errors:
   - **Incomplete result** (a rule has no verdict): the error message lists exactly which rule
     ids are missing. Go back, verdict them, and resubmit. Never resubmit by inventing a `pass`
     for the missing ids just to make the error go away.
   - **Unknown rule id in consulted_sources**: a source names a `rule_id` that is not one of
     this domain's own rules. Fix the id (it must be the domain being recorded, not another
     one) and resubmit.
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

  Filed issues carry an `engineering-audit` label, which the tool creates on the target
  repository if it is not already there. The response's `label` field reports `present`,
  `created` or `unavailable`. On `unavailable` the issues are still filed, just without the
  label, and `warnings` carries one line saying why: pass that on to the user, because they are
  the one who can add the label and re-label the issues.

## 7. Render the report

Call `render_report` with a `finished` ISO timestamp (the current time, same format as `started`
in step 1). It writes `report.html` and `run-state.json` into the run's `output_dir` and returns
their paths along with a findings summary. Any issues filed in step 6 are linked automatically,
and any `consulted_sources` recorded in step 4 appear in the report's own "Sources consulted
this run" section, grouped by rule id; there is nothing further to pass for either. The run's
`run-state.progress.json` recovery file is removed at this point: `run-state.json` is the
record from here, and a later `begin_run` on the same `output_dir` starts a clean run rather
than offering to resume this one.

Tell the user directly where `report.html` is, give them a one-line summary of what was
found (e.g. "3 findings: 1 high, 2 medium, across 2 domains"), and offer to open the report
for them (`xdg-open` on Linux, `open` on macOS, `start` on Windows); if opening fails or the
session is remote, the path is the fallback. Do not just say "the audit is done"; the
report's location and headline numbers are the actual deliverable.

## 8. Offer to send feedback

If the user supplied feedback text on the configuration page (`config.feedback_text`), call
`submit_feedback`. It sends that text, plus a run-metadata section and whichever telemetry
sections the user consented to (coverage, findings rollup, self-assessment, the three host
environment facts from step 1, consulted sources by rule id/URL/why; never finding text), to the
tool author's repository via
`gh`. Every section defaults off until the user ticks it; the consulted-sources one carries its
own reason on the configuration page too, since URLs fetched while auditing a private
repository can hint at what that repository is about. If it returns `mode: "mailto"` (gh was
unavailable or filing failed), tell the user their feedback was not lost: offer to open the
`mailto_url`, and if that fails or is unavailable, offer the `body` text for them to paste into an
email themselves. The rendered report also carries this same mailto fallback in its Feedback
section, so nothing is lost even if this step is skipped.

Calling `submit_feedback` at this point, after the report is already written, is supported: the
run just finished stays reachable for exactly this, until the next `begin_run`. When the feedback
is filed as an issue, `report.html` and `run-state.json` are rewritten so both carry its link, and
the response's `report_updated` field says whether that rewrite succeeded. If it is `false`, the
response carries a warning explaining that the issue is filed but the report could not be updated:
pass that on to the user rather than resending the feedback, which would file it twice. If the
user already has the report open in a browser, tell them to reload it to see the link.

Do not call `submit_feedback` if the user gave no feedback text and did not ask you to send
anything: an unprompted, empty submission is not consent.
