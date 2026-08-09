"use strict";

// Executable tests for the report page's client-side JS
// (src/engineering_audit/static/report.js), run with Node's built-in test
// runner (node --test), not npm or any package. Wired into pytest by
// tests/test_report_js.py so `uv run pytest` exercises this too.
//
// Covers _githubErrorMessage: it turns a failed GitHub API response into a
// human-readable message (401 invalid token, 404 repo not found or token
// lacks access, 410 issues disabled, otherwise the status plus the
// response body's JSON "message" field when present). Previously the test
// suite only checked this function's source text was present somewhere in
// the generated HTML, never that it actually behaves correctly.

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const { _githubErrorMessage } = require(
  path.join(__dirname, "..", "..", "src", "engineering_audit", "static", "report.js")
);

test("401 is reported as an invalid token, regardless of the response body", () => {
  assert.equal(_githubErrorMessage(401, ""), "401 invalid token");
  assert.equal(
    _githubErrorMessage(401, '{"message":"Bad credentials"}'),
    "401 invalid token"
  );
});

test("404 is reported as repo not found or token lacks access", () => {
  assert.equal(_githubErrorMessage(404, ""), "404 repo not found or token lacks access");
});

test("410 is reported as issues disabled", () => {
  assert.equal(_githubErrorMessage(410, ""), "410 issues disabled");
});

test("an unmapped status appends the response body's JSON message field", () => {
  const body = JSON.stringify({ message: "Validation Failed" });
  assert.equal(_githubErrorMessage(422, body), "422 Validation Failed");
});

test("an unmapped status with a non-JSON body falls back to the bare status", () => {
  assert.equal(_githubErrorMessage(500, "<html>not json</html>"), "500");
});

test("an unmapped status with JSON but no message field falls back to the bare status", () => {
  assert.equal(_githubErrorMessage(503, '{"error":"unavailable"}'), "503");
});

test("an unmapped status with an empty body falls back to the bare status", () => {
  assert.equal(_githubErrorMessage(502, ""), "502");
});
