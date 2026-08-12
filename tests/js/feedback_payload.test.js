"use strict";

// Executable test for report.js's buildFeedbackPayload (issue #120).
//
// The other three places the feedback section key list lives
// (feedback.py's build_feedback_sections, schema.py's TelemetryConsent, and
// report.py's embedded JSON block and consent rows) are all Python and are
// cross-checked against each other by
// test_feedback_embedded_json_parses_and_matches_build_feedback_sections in
// tests/test_report.py. None of that proves report.js's client-side payload
// builder actually assembles every one of those sections: a section could be
// wired into all three Python places, and still be silently dropped from the
// text the "Copy feedback" and "Email feedback" buttons produce, because
// buildFeedbackPayload never named its checkbox id. That is exactly the
// consent-integrity failure issue #120 exists to close, and it is a failure
// this file's assertions, not the Python ones, must catch.
//
// The key list itself is deliberately not written here as a literal: it is
// passed in by tests/test_report_js.py via the FEEDBACK_SECTION_KEYS
// environment variable, computed at test time from build_feedback_sections'
// own current return value. A tenth section added there is automatically
// exercised here too, with no second place to remember to update.

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const { buildFeedbackPayload } = require(
  path.join(__dirname, "..", "..", "src", "engineering_audit", "static", "report.js")
);

const rawKeys = process.env.FEEDBACK_SECTION_KEYS;
if (!rawKeys) {
  throw new Error(
    "FEEDBACK_SECTION_KEYS is not set. This file is meant to be run via " +
      "tests/test_report_js.py, which computes the current build_feedback_sections " +
      "keys and passes them in; running `node --test` on this file directly checks " +
      "nothing, since there is no key list to check against."
  );
}
const consentKeys = JSON.parse(rawKeys);
if (!Array.isArray(consentKeys) || consentKeys.length === 0) {
  throw new Error(
    "FEEDBACK_SECTION_KEYS parsed to an empty or non-array value: " + rawKeys
  );
}

function fakeDocument(elements) {
  return {
    getElementById: function (id) {
      return Object.prototype.hasOwnProperty.call(elements, id) ? elements[id] : null;
    },
  };
}

function buildFakeSectionsData(keys) {
  var data = { run_metadata: "RUN_METADATA_MARKER", consent_keys: keys };
  keys.forEach(function (key) {
    data[key] = "SECTION_MARKER_" + key;
  });
  return data;
}

test("buildFeedbackPayload includes every current section key when its checkbox is ticked", () => {
  const data = buildFakeSectionsData(consentKeys);
  const elements = {
    "feedback-sections-data": { textContent: JSON.stringify(data) },
    "feedback-textarea": { value: "" },
  };
  consentKeys.forEach(function (key) {
    elements["consent-" + key.split("_").join("-")] = { checked: true };
  });
  global.document = fakeDocument(elements);

  const payload = buildFeedbackPayload();

  assert.ok(
    payload.indexOf(data.run_metadata) !== -1,
    "run_metadata must always be included, regardless of consent"
  );
  consentKeys.forEach(function (key) {
    assert.ok(
      payload.indexOf(data[key]) !== -1,
      "key " + key + " is present in the section data and its checkbox is ticked, " +
        "but buildFeedbackPayload did not include it in the assembled payload"
    );
  });
});

test("buildFeedbackPayload omits a section whose checkbox is unticked", () => {
  const data = buildFakeSectionsData(consentKeys);
  const elements = {
    "feedback-sections-data": { textContent: JSON.stringify(data) },
    "feedback-textarea": { value: "" },
  };
  consentKeys.forEach(function (key) {
    elements["consent-" + key.split("_").join("-")] = { checked: false };
  });
  global.document = fakeDocument(elements);

  const payload = buildFeedbackPayload();

  consentKeys.forEach(function (key) {
    assert.equal(
      payload.indexOf(data[key]),
      -1,
      "key " + key + " was not consented to (checkbox unticked) but appeared in the " +
        "assembled payload anyway"
    );
  });
});
