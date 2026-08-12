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

// Issue #135: reader_conclusions is the one section answered live, in the
// browser, after the report has actually been read, rather than baked in
// at render time. These two tests exercise that substitution directly,
// using the real placeholder text feedback.py's build_feedback_sections
// writes ("A1: (left blank)" / "A2: (left blank)"), rather than the opaque
// markers the generic tests above use: the tests above prove every section
// survives the consent-integrity contract, but only these prove the
// reader's own typed words actually reach the assembled payload, and that
// the report's own placeholder text does not survive once they have.
if (consentKeys.indexOf("reader_conclusions") !== -1) {
  const READER_CONCLUSIONS_TEXT = [
    "Reader's own conclusions",
    "Answered by the person reading the finished report, in their own words, " +
      "not computed from repository data or verified by the tool.",
    "Q1: In one sentence, what did this report tell you about your repository?",
    "A1: (left blank)",
    "Q2: What would you fix first?",
    "A2: (left blank)",
  ].join("\n");

  test("buildFeedbackPayload substitutes the reader's live-typed answers for the report's placeholders", () => {
    const data = buildFakeSectionsData(consentKeys);
    data.reader_conclusions = READER_CONCLUSIONS_TEXT;
    const elements = {
      "feedback-sections-data": { textContent: JSON.stringify(data) },
      "feedback-textarea": { value: "" },
      "reader-conclusion-headline": { value: "It found three hardcoded secrets." },
      "reader-conclusion-fix-first": { value: "The hardcoded API key in config.py." },
    };
    consentKeys.forEach(function (key) {
      elements["consent-" + key.split("_").join("-")] = { checked: true };
    });
    global.document = fakeDocument(elements);

    const payload = buildFeedbackPayload();

    assert.ok(
      payload.indexOf("A1: It found three hardcoded secrets.") !== -1,
      "the live-typed headline answer did not reach the assembled payload"
    );
    assert.ok(
      payload.indexOf("A2: The hardcoded API key in config.py.") !== -1,
      "the live-typed fix-first answer did not reach the assembled payload"
    );
    assert.equal(
      payload.indexOf("(left blank)"),
      -1,
      "the report's own placeholder text survived even though both questions were answered"
    );
  });

  test("buildFeedbackPayload leaves the report's placeholders untouched when nothing was typed", () => {
    const data = buildFakeSectionsData(consentKeys);
    data.reader_conclusions = READER_CONCLUSIONS_TEXT;
    const elements = {
      "feedback-sections-data": { textContent: JSON.stringify(data) },
      "feedback-textarea": { value: "" },
      "reader-conclusion-headline": { value: "" },
      "reader-conclusion-fix-first": { value: "" },
    };
    consentKeys.forEach(function (key) {
      elements["consent-" + key.split("_").join("-")] = { checked: true };
    });
    global.document = fakeDocument(elements);

    const payload = buildFeedbackPayload();

    assert.ok(
      payload.indexOf(READER_CONCLUSIONS_TEXT) !== -1,
      "the section text must come through unchanged when neither question was answered"
    );
  });
}
