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

// Set once by stopFilingIssues() and read by fileNext() below. Module-level
// (not local to fileSelectedIssues) so the button's onclick handler, fired
// from a fresh call stack, can reach the flag a running fileSelectedIssues()
// call is checking.
var _fileStopRequested = false;

function stopFilingIssues() {
  // The in-flight fetch is left to finish; only the *next* one is skipped
  // (see the check at the top of fileNext below). There is no way to abort
  // a fetch already sent to GitHub without risking a filed issue whose
  // network response never arrives back at this page.
  _fileStopRequested = true;
  var stopButton = document.getElementById("gh-stop-button");
  if (stopButton) {
    stopButton.disabled = true;
    stopButton.textContent = "Stopping...";
  }
}

function _setFilingInProgress(inProgress) {
  var fileButton = document.getElementById("gh-file-button");
  var stopButton = document.getElementById("gh-stop-button");
  if (fileButton) { fileButton.disabled = inProgress; }
  if (stopButton) {
    stopButton.style.display = inProgress ? "inline-block" : "none";
    stopButton.disabled = false;
    stopButton.textContent = "Stop";
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
  _fileStopRequested = false;
  _setFilingInProgress(true);
  summary.textContent = "Checking " + repo + " for issues already filed...";

  var filedCount = 0;

  function fileNext(pending) {
    if (_fileStopRequested) {
      summary.textContent = "Filing stopped early. Filed " + filedCount + " of "
        + indexes.length + " selected issue(s).";
      _setFilingInProgress(false);
      return;
    }
    if (pending.length === 0) {
      summary.textContent = "Filed " + filedCount + " of " + indexes.length + " selected issue(s).";
      _setFilingInProgress(false);
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
          _setFilingInProgress(false);
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
      _setFilingInProgress(false);
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
    _setFilingInProgress(false);
  });
}
