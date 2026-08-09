"""Deterministic HTML report renderer.

Builds a single self-contained HTML document from a :class:`RunState` and the
:class:`RulesPack` it was audited against. Every number in the report is
computed here from the run state itself (sums over findings and coverage
records); nothing is accepted as a pre-computed, decorative count. All
interpolated content is passed through ``html.escape`` before it reaches the
page, and the only rules-pack content that ever appears is a rule id or a
rule's short heading title, never a rule's full body text: the rules pack is
private and a shared report must not leak it.
"""

from __future__ import annotations

import html
import json
import re
import string
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from engineering_audit.feedback import (
    FEEDBACK_EMAIL,
    build_feedback_sections,
    build_issue_trailing_line,
    feedback_subject,
)
from engineering_audit.rules import citation, Rule, RulesPack
from engineering_audit.schema import (
    DomainResult,
    Finding,
    IncompleteResultError,
    RunState,
    Verdict,
    validate_completeness,
)

__all__ = ["ReportError", "render_report", "write_report"]

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "report.html"

# A repo field is only ever prefilled from run metadata when it already looks
# like a plausible 'owner/name' GitHub slug; anything else is left blank
# rather than risk pre-populating the GitHub-filing form with a string that
# was never meant to be a repository identifier.
_REPO_SLUG_RE = re.compile(r"^[\w.-]+/[\w.-]+$")

_INLINE_SCRIPT = r"""
function _afterCopy(button) {
  if (!button) { return; }
  var original = button.textContent;
  button.textContent = "Copied";
  setTimeout(function () { button.textContent = original; }, 1500);
}

function _fallbackCopyText(text) {
  var ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.top = "-1000px";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  try {
    document.execCommand("copy");
  } catch (err) {
    /* best effort only: nothing more can be done if this fails too */
  }
  document.body.removeChild(ta);
}

function copyText(text, button) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(
      function () { _afterCopy(button); },
      function () { _fallbackCopyText(text); _afterCopy(button); }
    );
  } else {
    _fallbackCopyText(text);
    _afterCopy(button);
  }
}

function copyIssueText(textareaId, button) {
  var el = document.getElementById(textareaId);
  if (!el) { return; }
  el.select();
  copyText(el.value, button);
}

function _readJsonData(elementId) {
  var el = document.getElementById(elementId);
  if (!el) { return null; }
  return JSON.parse(el.textContent);
}

/* Feedback */

function buildFeedbackPayload() {
  var data = _readJsonData("feedback-sections-data");
  var parts = [];
  var textarea = document.getElementById("feedback-textarea");
  var freeText = textarea ? textarea.value.trim() : "";
  if (freeText) { parts.push(freeText); }
  parts.push(data.run_metadata);
  if (document.getElementById("consent-coverage").checked) { parts.push(data.coverage); }
  if (document.getElementById("consent-rollup").checked) { parts.push(data.rollup); }
  if (document.getElementById("consent-self-assessment").checked) { parts.push(data.self_assessment); }
  if (document.getElementById("consent-environment").checked) { parts.push(data.environment); }
  return parts.join("\n\n");
}

function emailFeedback() {
  var data = _readJsonData("feedback-sections-data");
  var payload = buildFeedbackPayload();
  var url = "mailto:" + data.email
    + "?subject=" + encodeURIComponent(data.subject)
    + "&body=" + encodeURIComponent(payload);
  window.location.href = url;
}

function copyFeedback(button) {
  copyText(buildFeedbackPayload(), button);
}

/* Issues */

function _selectedIssueIndexes() {
  var data = _readJsonData("issues-data");
  if (!data) { return []; }
  var indexes = [];
  data.issues.forEach(function (_issue, i) {
    var cb = document.getElementById("issue-check-" + i);
    if (cb && cb.checked && !cb.disabled) { indexes.push(i); }
  });
  return indexes;
}

function updateGithubFileButtonLabel() {
  var button = document.getElementById("gh-file-button");
  if (!button) { return; }
  var n = _selectedIssueIndexes().length;
  button.textContent = "File " + n + " selected issue" + (n === 1 ? "" : "s");
}

function revealGithubFileForm() {
  var form = document.getElementById("github-file-form");
  if (!form) { return; }
  form.style.display = "block";
  updateGithubFileButtonLabel();
}

function copySelectedIssues(button) {
  var data = _readJsonData("issues-data");
  var indexes = _selectedIssueIndexes();
  var chunks = indexes.map(function (i) {
    var issue = data.issues[i];
    return "## " + issue.title + "\n\n" + issue.body + "\n\n---\n\n";
  });
  copyText(chunks.join(""), button);
}

function _githubErrorMessage(status, bodyText) {
  if (status === 401) { return "401 invalid token"; }
  if (status === 404) { return "404 repo not found or token lacks access"; }
  if (status === 410) { return "410 issues disabled"; }
  var message = "";
  try {
    var parsed = JSON.parse(bodyText);
    if (parsed && parsed.message) { message = parsed.message; }
  } catch (err) {
    /* response body was not JSON; fall through with an empty message */
  }
  return status + (message ? " " + message : "");
}

function _parseLinkHeader(headerValue) {
  var links = {};
  if (!headerValue) { return links; }
  headerValue.split(",").forEach(function (part) {
    var match = part.match(/<([^>]+)>\s*;\s*rel="([^"]+)"/);
    if (match) { links[match[2]] = match[1]; }
  });
  return links;
}

function _fetchExistingIssuesPage(url, headers, page, maxPages, accumulated) {
  return fetch(url, { headers: headers }).then(function (response) {
    return response.text().then(function (bodyText) {
      if (!response.ok) {
        throw new Error(_githubErrorMessage(response.status, bodyText));
      }
      var items = JSON.parse(bodyText);
      var combined = accumulated.concat(items);
      var links = _parseLinkHeader(response.headers.get("Link"));
      if (links.next && page < maxPages) {
        return _fetchExistingIssuesPage(links.next, headers, page + 1, maxPages, combined);
      }
      return combined;
    });
  });
}

function fetchExistingIssueTitles(repo, pat) {
  // Cross-session double-filing guard: before filing anything, check what
  // this repository already has under the engineering-audit label, so an
  // issue filed in an earlier browser session (whose filed_issue_urls never
  // made it back into this run's run-state.json) is not filed a second
  // time. Paginates via the Link header, capped at 3 pages, then proceeds
  // with whatever was fetched.
  var headers = {
    "Authorization": "Bearer " + pat,
    "Accept": "application/vnd.github+json"
  };
  var url = "https://api.github.com/repos/" + repo
    + "/issues?state=all&labels=engineering-audit&per_page=100";
  return _fetchExistingIssuesPage(url, headers, 1, 3, []).then(function (items) {
    var titles = {};
    items.forEach(function (issue) {
      titles[issue.title] = issue.html_url;
    });
    return titles;
  });
}

function _markAlreadyFiled(index, existingUrl) {
  var cb = document.getElementById("issue-check-" + index);
  if (cb) { cb.disabled = true; }
  var statusEl = document.getElementById("issue-status-" + index);
  if (statusEl) {
    statusEl.textContent = "";
    var link = document.createElement("a");
    link.href = existingUrl;
    link.textContent = "already filed";
    statusEl.appendChild(link);
  }
}

function fileSelectedIssues() {
  var repoInput = document.getElementById("gh-repo");
  var patInput = document.getElementById("gh-pat");
  var summary = document.getElementById("github-file-summary");
  var repo = repoInput.value.trim();
  var pat = patInput.value;
  var repoPattern = /^[\w.-]+\/[\w.-]+$/;

  if (!repoPattern.test(repo)) {
    summary.textContent = "Enter the repository as owner/name.";
    return;
  }
  if (!pat) {
    summary.textContent = "Enter a personal access token.";
    return;
  }

  var data = _readJsonData("issues-data");
  var indexes = _selectedIssueIndexes();
  var fileButton = document.getElementById("gh-file-button");
  fileButton.disabled = true;
  summary.textContent = "Checking " + repo + " for issues already filed...";

  var filedCount = 0;

  function fileNext(pending) {
    if (pending.length === 0) {
      summary.textContent = "Filed " + filedCount + " of " + indexes.length + " selected issue(s).";
      fileButton.disabled = false;
      return;
    }
    var i = pending[0];
    var rest = pending.slice(1);
    var issue = data.issues[i];
    var statusEl = document.getElementById("issue-status-" + i);
    if (statusEl) { statusEl.textContent = "Filing..."; }

    fetch("https://api.github.com/repos/" + repo + "/issues", {
      method: "POST",
      headers: {
        "Authorization": "Bearer " + pat,
        "Accept": "application/vnd.github+json"
      },
      body: JSON.stringify({ title: issue.title, body: issue.body, labels: ["engineering-audit"] })
    }).then(function (response) {
      response.text().then(function (bodyText) {
        if (!response.ok) {
          var errorMessage = _githubErrorMessage(response.status, bodyText);
          if (statusEl) { statusEl.textContent = "Error: " + errorMessage; }
          summary.textContent = "Filed " + filedCount + " of " + indexes.length
            + " selected issue(s). Stopped after an error on \"" + issue.title + "\": " + errorMessage;
          fileButton.disabled = false;
          return;
        }
        var created = JSON.parse(bodyText);
        filedCount += 1;
        var cb = document.getElementById("issue-check-" + i);
        if (cb) { cb.disabled = true; }
        if (statusEl) {
          statusEl.textContent = "";
          if (created.html_url && created.html_url.indexOf("https://") === 0) {
            var link = document.createElement("a");
            link.href = created.html_url;
            link.textContent = "filed";
            statusEl.appendChild(link);
          } else {
            statusEl.textContent = "filed";
          }
        }
        fileNext(rest);
      });
    }).catch(function () {
      if (statusEl) { statusEl.textContent = "Error: a network failure filing this issue"; }
      summary.textContent = "Filed " + filedCount + " of " + indexes.length
        + " selected issue(s). Stopped after a network error.";
      fileButton.disabled = false;
    });
  }

  fetchExistingIssueTitles(repo, pat).then(function (existingTitles) {
    var toFile = [];
    indexes.forEach(function (i) {
      var issue = data.issues[i];
      var existingUrl = existingTitles[issue.title];
      if (existingUrl) {
        _markAlreadyFiled(i, existingUrl);
      } else {
        toFile.push(i);
      }
    });
    summary.textContent = "";
    fileNext(toFile);
  }).catch(function (err) {
    // Fail closed: a dedup check that could not run must never be treated
    // as "nothing exists yet" and silently fall through to filing
    // duplicates. Nothing is filed until the pre-check itself succeeds.
    var message = (err && err.message) ? err.message : "the pre-check request failed";
    summary.textContent = "Could not check " + repo
      + " for already-filed issues, so nothing was filed: " + message + ".";
    fileButton.disabled = false;
  });
}
""".strip()

_SEVERITY_ORDER = ("critical", "high", "medium", "low")


class ReportError(Exception):
    """Raised when a RunState cannot be rendered into a trustworthy report:
    a selected domain has no result, a completed result fails the
    every-rule-has-a-verdict check, or a finding references a rule id that
    is not in the rules pack. A report is what a human trusts as the record
    of the run; it must never render a plausible-looking page over broken or
    incomplete data."""


def _esc(value: object) -> str:
    return html.escape(str(value))


def _json_script(data: object) -> str:
    """Serialise data for embedding inside an inline
    ``<script type="application/json">`` block.

    A JSON string value can legitimately contain the literal text
    "</script>" (an issue body built from agent-authored text, a
    self-assessment's limits note, an environment value, and so on). The
    HTML parser does not know or care that it is inside a JSON string when
    scanning a <script> element's raw text for that closing tag, so any
    unescaped occurrence would terminate the block early and dump the rest
    of the payload as literal HTML. "/" is a legal JSON string escape, so
    replacing every "</" with the equivalent "<\\/" after serialising is a
    safe, blanket fix: those two characters can only appear together inside
    a quoted string in ``json.dumps`` output, never as JSON structural
    syntax.
    """
    return json.dumps(data).replace("</", "<\\/")


def _require_href_scheme(url: str, allowed: tuple[str, ...], context: str) -> None:
    """Raise ReportError unless url's scheme is one of allowed.

    Used for issue links and the filed-feedback-issue link: both carry a
    URL a filing integration produced from real ``gh`` output, and a
    non-http(s) scheme there is a bug upstream, not a cosmetic issue.
    """
    scheme = urlparse(url).scheme.lower()
    if scheme not in allowed:
        raise ReportError(
            f"{context} has scheme '{scheme or '(none)'}', only "
            f"{'/'.join(allowed)} {'is' if len(allowed) == 1 else 'are'} allowed: {url!r}"
        )


def _markdownish(text: str) -> str:
    """Escape then apply the barest paragraph/line-break formatting.

    No markdown library is a dependency here (mcp + pydantic only), so this
    is deliberately not a markdown renderer: it escapes first, then turns
    blank-line-separated chunks into paragraphs and single newlines into
    line breaks.
    """
    # Normalise CRLF (and lone CR) to LF first: a paragraph split on a literal
    # '\n\n' would otherwise miss every blank line in CRLF-sourced text.
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    escaped = html.escape(normalised)
    paragraphs = [p for p in escaped.split("\n\n") if p.strip()]
    if not paragraphs:
        return ""
    return "".join(f"<p>{p.replace(chr(10), '<br>')}</p>" for p in paragraphs)


def _render_meta_block(run_state: RunState) -> str:
    meta = run_state.meta
    rules_pack_label = meta.rules_pack_name
    if meta.rules_pack_version:
        rules_pack_label = f"{rules_pack_label} ({meta.rules_pack_version})"
    rows = [
        ("Repository", meta.repo_name),
        ("Commit", meta.repo_commit),
        ("Rules pack", rules_pack_label),
        ("Assistant", meta.assistant),
        ("Model", meta.model),
        ("Tool version", meta.tool_version),
        ("Started", meta.started),
        ("Finished", meta.finished or "in progress"),
    ]
    rows_html = "".join(
        f'<div class="meta-label">{_esc(label)}</div><div class="meta-value">{_esc(value)}</div>'
        for label, value in rows
    )
    return f'<div class="meta-grid">{rows_html}</div>'


def _coverage_summary(selected: dict[str, DomainResult], domain_titles: dict[str, str]) -> str:
    total_inspected = 0
    total_skipped = 0
    rows = []
    for domain_id, result in selected.items():
        title = domain_titles[domain_id]
        if result.coverage is not None:
            total_inspected += result.coverage.files_inspected
            total_skipped += result.coverage.files_skipped
            note = f" ({_esc(result.coverage.note)})" if result.coverage.note else ""
            rows.append(
                f"<li>{_esc(title)}: {result.coverage.files_inspected} file(s) inspected, "
                f"{result.coverage.files_skipped} skipped{note}</li>"
            )
        else:
            rows.append(f"<li>{_esc(title)}: no coverage reported</li>")
    summary = (
        f"<p>Total files inspected across selected domains: <strong>{total_inspected}</strong>. "
        f"Total files skipped: <strong>{total_skipped}</strong>.</p>"
        f"<ul>{''.join(rows)}</ul>"
    )
    return summary


def _findings_rollup(
    all_findings: list[tuple[str, Finding]], domain_titles: dict[str, str]
) -> str:
    severity_counts: Counter[str] = Counter(f.severity.value for _, f in all_findings)
    # Keyed by domain id, not title: two domains with identical titles (from
    # different rules-pack files) must not merge into one rollup row.
    domain_counts: Counter[str] = Counter(domain_id for domain_id, _ in all_findings)
    total = len(all_findings)

    sev_items = "".join(
        f"<li>{_esc(sev)}: {severity_counts.get(sev, 0)}</li>" for sev in _SEVERITY_ORDER
    )
    domain_items = (
        "".join(
            f"<li>{_esc(domain_id)}: {_esc(domain_titles[domain_id])}: {count}</li>"
            for domain_id, count in domain_counts.items()
        )
        or "<li>No findings.</li>"
    )
    return (
        f"<p>Total findings: <strong>{total}</strong></p>"
        f"<h3>By severity</h3><ul>{sev_items}</ul>"
        f"<h3>By domain</h3><ul>{domain_items}</ul>"
    )


def _could_not_evaluate_list(
    selected: dict[str, DomainResult], rule_index: dict[str, Rule]
) -> str:
    rows = []
    for domain_id, result in selected.items():
        for rv in result.rule_verdicts:
            if rv.verdict != Verdict.COULD_NOT_EVALUATE:
                continue
            rule = rule_index.get(rv.rule_id)
            if rule is None:
                # Consistent with the findings check below: a verdict for a
                # rule id absent from the pack is a broken run, not a cosmetic
                # gap, and must raise rather than render a placeholder label.
                raise ReportError(
                    f"domain '{domain_id}' has a rule_verdict for rule id "
                    f"'{rv.rule_id}', which is not in the rules pack"
                )
            rows.append(
                f"<li><strong>{_esc(rv.rule_id)}</strong> ({_esc(rule.title)}): {_esc(rv.note)}</li>"
            )
    if not rows:
        return (
            '<h3>Could not evaluate</h3>'
            '<p class="ok">Every selected rule reached a verdict of pass, finding or '
            "not applicable. Nothing was left could-not-evaluate.</p>"
        )
    return (
        f"<h3>Could not evaluate ({len(rows)})</h3>"
        f"<ul>{''.join(rows)}</ul>"
    )


def _self_assessment_list(selected: dict[str, DomainResult], domain_titles: dict[str, str]) -> str:
    rows = []
    for domain_id, result in selected.items():
        title = domain_titles[domain_id]
        if result.status == "could-not-run":
            rows.append(f"<li>{_esc(title)}: could not run, {_esc(result.reason)}</li>")
        elif result.self_assessment is not None:
            sa = result.self_assessment
            limits = f" Limits: {_esc(sa.limits)}." if sa.limits else ""
            rows.append(f"<li>{_esc(title)}: confidence {_esc(sa.confidence)}.{limits}</li>")
        else:
            rows.append(f"<li>{_esc(title)}: no self-assessment reported</li>")
    return f"<ul>{''.join(rows)}</ul>"


def _environment_info(run_state: RunState) -> str:
    environment = run_state.meta.environment
    if not environment:
        return "<p>No environment information reported for this run.</p>"
    rows = "".join(
        f"<li><strong>{_esc(key)}:</strong> {_esc(value)}</li>" for key, value in environment.items()
    )
    return f"<ul>{rows}</ul>"


def _reference_line(rule: Rule) -> str:
    """Build the deterministic reference line appended after every rendered
    finding's body.

    This is added by the report renderer itself, never by the auditing
    agent: the citation grounding a finding comes from the rules pack's own
    parsed ``Source:`` fragment (see rules.py), not from whatever the agent
    recalls about the rule. A finding is a published claim, and this tool
    does not publish claims without evidence: a rule with no parsed source
    is refused upstream (see the render_report gate), so by the time this
    runs the source is always present.
    """
    if not rule.source:
        raise ReportError(
            f"finding references rule {rule.id}, which has no cited source in the "
            "rules pack. A finding is a published claim; this tool does not publish "
            "claims without evidence. Fix the rule's Source: footer or drop the finding."
        )
    return f"Reference: {rule.id}: {citation(rule.source)}"


def _findings_section(
    selected: dict[str, DomainResult],
    domain_titles: dict[str, str],
    rule_index: dict[str, Rule],
) -> str:
    blocks = []
    for domain_id, result in selected.items():
        title = domain_titles[domain_id]
        if result.status == "could-not-run":
            blocks.append(
                f"<h3>{_esc(title)}</h3><p class='muted'>This domain could not be run: "
                f"{_esc(result.reason)}</p>"
            )
            continue
        if not result.findings:
            blocks.append(f"<h3>{_esc(title)}</h3><p>No findings.</p>")
            continue
        items = []
        for finding in result.findings:
            severity = finding.severity.value
            badge = f'<span class="severity-badge severity-{_esc(severity)}">{_esc(severity)}</span>'
            # render_report has already confirmed every finding's rule_id is
            # in the pack, so this lookup cannot miss.
            rule = rule_index[finding.rule_id]
            items.append(
                f'<div class="finding sev-{_esc(severity)}">'
                f'<div class="finding-head">{badge} <strong>{_esc(finding.title)}</strong> '
                f'<span class="finding-rule">({_esc(finding.rule_id)})</span></div>'
                f'<div class="finding-location">{_esc(finding.location)}</div>'
                f'<div class="finding-body">{_markdownish(finding.body_md)}</div>'
                f'<div class="finding-reference">{_esc(_reference_line(rule))}</div>'
                "</div>"
            )
        blocks.append(f"<h3>{_esc(title)}</h3>{''.join(items)}")
    return "".join(blocks) or "<p>No domains selected.</p>"


def _issue_button_row() -> str:
    return (
        '<div class="issue-actions">'
        '<button type="button" onclick="revealGithubFileForm()">'
        "Add selected issues to GitHub (requires GitHub PAT)</button> "
        '<button type="button" onclick="copySelectedIssues(this)">'
        "Copy selected issues (for pasting into an LLM or editor)</button>"
        "</div>"
    )


def _github_file_form(repo_prefill: str) -> str:
    return (
        '<div id="github-file-form" class="github-file-form" style="display:none">'
        '<p class="muted">Files each selected issue directly from your browser to '
        "api.github.com over HTTPS, using the REST API. The token is used only in memory on "
        "this page: it is never stored (no localStorage, sessionStorage or cookies). A "
        "fine-grained personal access token with Issues read and write access on the one "
        "target repository is enough.</p>"
        '<label>Repository (owner/name)<br>'
        f'<input type="text" id="gh-repo" value="{_esc(repo_prefill)}" placeholder="owner/name">'
        "</label><br>"
        '<label>Personal access token<br>'
        '<input type="password" id="gh-pat" autocomplete="off">'
        "</label><br>"
        '<button type="button" id="gh-file-button" onclick="fileSelectedIssues()">'
        "File 0 selected issues</button>"
        '<p id="github-file-summary" class="muted"></p>'
        "</div>"
    )


def _issues_section(
    selected: dict[str, DomainResult],
    rule_index: dict[str, Rule],
    issue_urls: dict[str, str] | None,
    repo_prefill: str,
) -> str:
    all_findings = [f for result in selected.values() for f in result.findings]
    if not all_findings:
        return "<p>No findings, so nothing to file as an issue.</p>"

    issue_urls = issue_urls or {}
    issues_data: list[dict[str, str]] = []
    blocks = []
    for index, finding in enumerate(all_findings):
        # render_report has already confirmed every finding's rule_id is in
        # the pack and carries a cited source, so this lookup and the
        # trailing-line build below cannot fail.
        rule = rule_index[finding.rule_id]
        trailing_line = build_issue_trailing_line(finding, rule)
        body_with_trailing = f"{finding.issue_body}\n\n{trailing_line}"
        full_text = f"{finding.issue_title}\n\n{body_with_trailing}"

        issues_data.append(
            {"rule_id": finding.rule_id, "title": finding.issue_title, "body": body_with_trailing}
        )

        filed_url = issue_urls.get(finding.rule_id)
        textarea_id = f"issue-text-{index}"
        status_id = f"issue-status-{index}"

        if filed_url:
            _require_href_scheme(
                filed_url, ("http", "https"), f"issue url for rule id '{finding.rule_id}'"
            )
            checkbox_html = (
                f'<input type="checkbox" id="issue-check-{index}" disabled> '
                f'<a href="{_esc(filed_url)}">already filed</a>'
            )
        else:
            checkbox_html = (
                f'<input type="checkbox" id="issue-check-{index}" checked '
                'onchange="updateGithubFileButtonLabel()">'
            )

        blocks.append(
            '<div class="issue-block">'
            f'<label class="issue-select">{checkbox_html}</label>'
            f'<p><strong>{_esc(finding.issue_title)}</strong></p>'
            f'<textarea id="{textarea_id}" readonly rows="6">{_esc(full_text)}</textarea>'
            f"<button type=\"button\" onclick=\"copyIssueText('{textarea_id}', this)\">"
            "Copy issue text</button> "
            f'<span class="issue-status" id="{status_id}"></span>'
            "</div>"
        )

    button_row = _issue_button_row()
    data_script = (
        '<script type="application/json" id="issues-data">'
        f"{_json_script({'issues': issues_data})}"
        "</script>"
    )

    return (
        f"{button_row}"
        f"{_github_file_form(repo_prefill)}"
        f"{''.join(blocks)}"
        f"{button_row}"
        f"{data_script}"
    )


def _consent_row(input_id: str, label: str, checked: bool) -> str:
    checked_attr = " checked" if checked else ""
    return (
        f'<label class="consent-row"><input type="checkbox" id="{input_id}"{checked_attr}> '
        f"{_esc(label)}</label>"
    )


def _feedback_section(run_state: RunState, feedback_issue_url: str | None) -> str:
    config = run_state.config
    consent = config.telemetry_consent
    text = config.feedback_text or ""

    filed_html = ""
    if feedback_issue_url:
        _require_href_scheme(feedback_issue_url, ("http", "https"), "feedback issue url")
        filed_html = (
            f'<p>Feedback for this run was already filed as <a href="{_esc(feedback_issue_url)}">'
            "an issue</a> on the tool author's repository. Further feedback can still be sent "
            "below.</p>"
        )

    sections = build_feedback_sections(run_state.meta, run_state.domain_results)
    feedback_data = {
        "email": FEEDBACK_EMAIL,
        "subject": feedback_subject(run_state.meta),
        "run_metadata": sections["run_metadata"],
        "coverage": sections["coverage"],
        "rollup": sections["rollup"],
        "self_assessment": sections["self_assessment"],
        "environment": sections["environment"],
    }

    # Same wording as the configuration page's consent section, so a user
    # who saw one recognises the other.
    consent_rows = (
        _consent_row(
            "consent-coverage",
            "Coverage statistics (files inspected, files skipped)",
            consent.coverage,
        )
        + _consent_row(
            "consent-rollup",
            "Findings rollup (counts by severity and domain, not the finding text)",
            consent.rollup,
        )
        + _consent_row(
            "consent-self-assessment",
            "Self assessment (confidence and limits per domain)",
            consent.self_assessment,
        )
        + _consent_row(
            "consent-environment",
            "Environment information (assistant, model, tool version)",
            consent.environment,
        )
        + '<label class="consent-row locked"><input type="checkbox" checked disabled> '
        "Run metadata (always included when sending feedback)</label>"
    )

    return (
        f"{filed_html}"
        '<div class="feedback-form">'
        '<label for="feedback-textarea">Freeform feedback</label>'
        f'<textarea id="feedback-textarea" rows="6">{_esc(text)}</textarea>'
        '<div class="consent-rows">'
        f"{consent_rows}"
        "</div>"
        '<div class="feedback-actions">'
        '<button type="button" onclick="emailFeedback()">Email feedback</button> '
        '<button type="button" onclick="copyFeedback(this)">Copy feedback</button>'
        "</div>"
        "</div>"
        '<script type="application/json" id="feedback-sections-data">'
        f"{_json_script(feedback_data)}"
        "</script>"
    )


def _render_footer(run_state: RunState) -> str:
    meta = run_state.meta
    rules_pack_label = meta.rules_pack_name
    if meta.rules_pack_version:
        rules_pack_label = f"{rules_pack_label} ({meta.rules_pack_version})"
    finished = meta.finished or "in progress"
    return (
        "<p>"
        f"Generated by engineering-audit v{_esc(meta.tool_version)} against rules pack "
        f"{_esc(rules_pack_label)}, finished {_esc(finished)}."
        "</p>"
        '<p><a href="https://github.com/rodlunt">rodlunt on GitHub</a> | '
        '<a href="https://github.com/rodlunt/engineering-audit">engineering-audit on GitHub</a>'
        "</p>"
        "<p>This report was generated locally. Nothing in it leaves your machine unless you "
        "choose to send or file it.</p>"
    )


def render_report(run_state: RunState, pack: RulesPack) -> str:
    """Render a complete, self-contained HTML report.

    Filed issue urls and any feedback issue link are read from
    ``run_state.filed_issue_urls`` and ``run_state.feedback_issue_url``: the
    RunState is the single source for both, so a report rendered straight
    from a saved run-state.json (see render_cli.py) always matches one
    rendered live from the same run's in-progress tracker.

    Raises ReportError if a selected domain has no matching DomainResult, if
    a completed DomainResult fails :func:`validate_completeness`, or if a
    finding references a rule id that is not in the pack.
    """
    domain_titles: dict[str, str] = {d.id: d.title for d in pack.domains}
    rule_index: dict[str, Rule] = pack.rule_index

    selected: dict[str, DomainResult] = {}
    for domain_id in run_state.config.selected_domain_ids:
        if domain_id not in domain_titles:
            raise ReportError(
                f"selected domain '{domain_id}' is not present in the rules pack at {pack.root}"
            )
        result = run_state.domain_results.get(domain_id)
        if result is None:
            raise ReportError(f"selected domain '{domain_id}' has no DomainResult for this run")
        selected[domain_id] = result

    for domain_id, result in selected.items():
        if result.status == "completed":
            domain = pack.get_domain(domain_id)
            assert domain is not None  # already checked above
            try:
                validate_completeness(domain, result)
            except IncompleteResultError as exc:
                raise ReportError(str(exc)) from exc
        for finding in result.findings:
            if finding.rule_id not in rule_index:
                raise ReportError(
                    f"finding in domain '{domain_id}' references rule id "
                    f"'{finding.rule_id}', which is not in the rules pack"
                )
            if not rule_index[finding.rule_id].source:
                raise ReportError(
                    f"finding in domain '{domain_id}' references rule "
                    f"{finding.rule_id}, which has no cited source in the rules pack. "
                    "A finding is a published claim; this tool does not publish claims "
                    "without evidence. Fix the rule's Source: footer or drop the finding."
                )

    all_findings = [
        (domain_id, finding)
        for domain_id, result in selected.items()
        for finding in result.findings
    ]

    performance_summary = (
        f'<div class="perf-block"><h3>Coverage</h3>{_coverage_summary(selected, domain_titles)}</div>'
        f'<div class="perf-block"><h3>Findings rollup</h3>'
        f"{_findings_rollup(all_findings, domain_titles)}</div>"
        f'<div class="perf-block prominent">{_could_not_evaluate_list(selected, rule_index)}</div>'
        f'<div class="perf-block"><h3>Self-assessment by domain</h3>'
        f"{_self_assessment_list(selected, domain_titles)}</div>"
        f'<div class="perf-block"><h3>Environment</h3>{_environment_info(run_state)}</div>'
    )

    repo_name = run_state.meta.repo_name
    repo_prefill = repo_name if _REPO_SLUG_RE.match(repo_name) else ""

    template = string.Template(_TEMPLATE_PATH.read_text(encoding="utf-8"))
    return template.substitute(
        page_title=f"Engineering practice audit report: {_esc(run_state.meta.repo_name)}",
        meta_block=_render_meta_block(run_state),
        performance_summary=performance_summary,
        findings_section=_findings_section(selected, domain_titles, rule_index),
        issues_section=_issues_section(
            selected, rule_index, run_state.filed_issue_urls or None, repo_prefill
        ),
        feedback_section=_feedback_section(run_state, run_state.feedback_issue_url),
        footer_block=_render_footer(run_state),
        inline_script=_INLINE_SCRIPT,
    )


def write_report(run_state: RunState, pack: RulesPack, out_path: str | Path) -> Path:
    """Render the report and write it to out_path, returning the Path written."""
    rendered = render_report(run_state, pack)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered, encoding="utf-8")
    return out_path
