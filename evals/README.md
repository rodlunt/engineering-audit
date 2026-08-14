# Eval harness

A way to ask "did this audit find what it should have, and stay quiet where it should have,"
against a small fixture repository whose answers are known in advance.

## The decoupling

An eval run has two halves, and they must never be conflated:

- **The audit itself**: an LLM (Claude Code, or another supported assistant) reads a rules pack
  and sweeps a repository against it, following [AUDIT.md](../AUDIT.md). This step is
  nondeterministic and costed: it calls a real model, it takes real API spend, and two runs of it
  can disagree. It is a manual step, run by hand when this harness needs checking, not on every
  push.
- **Scoring the result**: `engineering-audit-eval` (`src/engineering_audit/evals.py`) compares the
  `run-state.json` the audit produced against a fixed set of expectations
  (`evals/golden/expected.json`) and reports where they agree and disagree. This half is pure
  Python, calls no LLM, reads no repository, and is exactly as deterministic as any other test in
  this project's suite. It has its own tests in `tests/test_evals.py` and runs in CI like anything
  else here.

Nothing in this repository's CI calls an LLM. The scorer is CI-safe on its own; the audit that
feeds it is not, and is run and checked by hand.

## The golden repo

`evals/golden/repo/` is GrindPoints, a small, entirely fictional cafe loyalty analytics service:
a schema, a write-path module, a couple of tests, a performance check and a monthly report, eight
files in all. It deliberately violates seven specific rules from the taster pack
(`examples/taster-rules/`, domains d01, d05 and d16) and deliberately satisfies four more as
controls. Nothing in the golden repo's own files says which is which; that context lives only in
`evals/golden/expected.json`, so a real audit run has to find the same things a human reviewer
would, not read the answer key.

Each expectation in `expected.json` carries a `why`: what was planted or controlled for, and
where, so a disagreement between the spec and a live run is a one-line read, not an archaeology
dig.

## Running it

The scorer alone, against any run-state you already have:

```sh
uv run engineering-audit-eval path/to/run-state.json \
    --expected evals/golden/expected.json \
    --rules-dir examples/taster-rules \
    --out /tmp/eval-result.json
```

`--rules-dir` is optional but recommended: without it, rule-verdict completeness is not checked,
and the output says so explicitly (`could-not-check`) rather than skipping it silently. With it,
the scorer also cross-checks that every expectation's rule id actually belongs to one of the
spec's domains, so an expectation on a rule the run never covered cannot pass by never being
checked at all.

To produce a fresh `run-state.json` and score it in one pass, headless, adapting the main
README's "Headless / CI" recipe:

```sh
# 1. Work on a scratch copy: the audit writes audit-output/, report.html and
#    run-state.json into the repository it audits, and none of that belongs
#    in this repository's own git history.
cp -r evals/golden/repo /tmp/grindpoints-eval

# 2. A preset config: the three domains this eval spec covers, report mode
#    (no GitHub issue filing).
cat > /tmp/grindpoints-eval-config.json <<'JSON'
{"selected_domain_ids": ["d01", "d05", "d16"], "issue_mode": "report"}
JSON

# 3. An MCP config for this run only: the server reads the taster rules pack,
#    and the preset config env var is set on the server itself, since the
#    server process is what reads it.
cat > /tmp/grindpoints-mcp.json <<'JSON'
{
  "mcpServers": {
    "engineering-audit": {
      "command": "uvx",
      "args": [
        "--from", "git+https://github.com/rodlunt/engineering-audit",
        "engineering-audit-mcp",
        "--rules-dir", "/path/to/engineering-audit/examples/taster-rules"
      ],
      "env": {
        "ENGINEERING_AUDIT_CONFIG": "/tmp/grindpoints-eval-config.json"
      }
    }
  }
}
JSON

# 4. Drive the audit headlessly via AUDIT.md.
claude -p "Read AUDIT.md at /path/to/engineering-audit and audit /tmp/grindpoints-eval via the \
engineering-audit MCP tools." --mcp-config /tmp/grindpoints-mcp.json \
    --allowedTools "mcp__engineering-audit__*,Read,Glob,Grep"

# 5. Score whatever the audit produced against the golden spec.
uv run engineering-audit-eval /tmp/grindpoints-eval/audit-output/run-state.json \
    --expected evals/golden/expected.json \
    --rules-dir examples/taster-rules \
    --out /tmp/grindpoints-eval-result.json
```

Adjust `/path/to/engineering-audit` to wherever this repository is checked out. The private,
maintained rules pack can be substituted for the taster pack in step 3, but `expected.json`'s rule
ids are taster-pack ids, so scoring a private-pack run against it would need its own mapping;
this harness ships tested only against the taster pack.

## Control semantics

A finding expectation scores `hit`, `missed` or `found-wrong-location` (the last of those counts
as missed): straightforward, since a finding either exists at the right place or it does not.

A no-finding control (`expect: "no-finding"`) is stricter than "no finding was recorded". It only
scores `held` when the rule's own verdict is explicitly `pass`. A finding recorded against it
scores `false-positive`. Anything else, not-applicable, could-not-evaluate, or no verdict at all
for that rule, scores `control-not-evaluated`, and that counts as a failure exactly like a
false-positive does. The reasoning: a control exists to prove the auditor actually looked and
found nothing wrong. A rule marked not-applicable or could-not-evaluate was not looked at in the
way the control needs, and a rule with no verdict at all was not looked at either; treating any of
those as `held` would let a control pass by never having run, which is the same shape of bug this
project's own hardening rules exist to catch elsewhere (a skipped check must never be
representable as a pass). `control-not-evaluated` gives that failure its own name instead of
folding it into a false clean bill of health.

## Known limitations

- **A single run is a point estimate, not a trend.** One audit run against GrindPoints tells you
  whether that run, on that day, against that model, found the seven planted findings and stayed
  quiet on the four controls. It says nothing about variance across runs; running it once and
  calling the result representative is exactly the average-hides-the-tail mistake this domain's
  own rules warn against (see D05-R17). Repeat runs over time, not a single green tick, are what
  build confidence.
- **LLM nondeterminism is real and unaddressed here.** The same model, the same rules pack and the
  same repository can produce different verdicts on different days. This harness has no retry
  logic, no majority vote across runs and no variance tracking; it scores whatever one run
  produced.
- **The golden repo's answers are public.** `evals/golden/expected.json` is committed in the open,
  in the same repository the audit reads. A future model with this repository in its training data
  could match the planted findings from having seen the answer key rather than from auditing the
  repository, which would make a passing score mean less than it appears to. There is no fix for
  this short of keeping the golden repo and its answers private, which was deliberately not done
  here so the harness itself could ship in the open.

## Recorded runs

### First run

2026-08-09, Claude Code headless on Sonnet, taster pack, against GrindPoints: 6 of 7 planted
findings hit, 1 missed (D05-R08, the test-suite-layering judgement call; the auditor recorded
adjacent d05 findings instead of that one), 3 of 4 controls held, 1 false positive (D01-R07) and
10 unexpected findings listed. The false positive turned out, on inspection, to be the auditor
being right and the fixture being wrong: `customers.points_balance` in `schema.sql` had no lower
bound, so the control's claim that every domain-constrained attribute was bounded did not hold.
Fixed in the fixture the same day (see `schema.sql` and the D01-R07 entry in `expected.json`). One
run is not a trend (see Known limitations above), but this is the harness doing the thing it
exists to do: catching a defect in its own fixture on day one, from a real disagreement between
the spec and a live audit, rather than from a human re-reading the schema by hand.

### Second run

2026-08-09 (later the same day), same setup: 7 of 7 planted findings hit, including D05-R08,
the first run's miss, which now looks like ordinary run-to-run variance rather than a defective
plant. 3 of 4 controls held. The run surfaced two more defects on our side, neither of them in
the auditor:

- **A scorer bug.** This run cited findings with line-range locations (`schema.sql:24-30`)
  where the first run used single lines, and the scorer's line-suffix strip only handled
  `:<line>`, so four correctly-placed plants scored `found-wrong-location`. Fixed in the
  scorer; the numbers above are the corrected scoring of the same run-state.
- **The D01-R07 control failed to hold a second time, and the auditor was right again.**
  `redemptions.reward_name` was free text with no rewards catalogue anywhere in the schema for
  it to reference, which is exactly the unbounded-domain-value shape the rule exists to catch.
  The fixture now binds it to the fixed reward menu with a CHECK. That control has now been
  falsified and repaired three times (points_balance, points_spent, reward_name), which is the
  strongest evidence in this file that the controls are being genuinely exercised.

### Third run

2026-08-15, Claude Code headless on Sonnet, **full pack** (`engineering-framework/domains`
v0.6.1+18, not the taster pack the first two used), tool_version 0.10.0 at tool_commit
`2b2d357`, against GrindPoints: **7 of 7 planted findings hit, 4 of 4 controls held, no false
positives, no wrong-location, 9 unexpected findings.** Exit code 0, the first run to score
clean on both halves.

Scoring was controlled before it was believed: a copy of the run-state with every finding
stripped and every finding-verdict flipped to `pass` was scored first and had to fail, which
it did (exit 1, 0 hit, 7 missed). A scorer that has not been seen to fail cannot be read as
having passed.

The full pack was used deliberately. The taster pack and the full pack carry identical rule-id
sets for d01, d05 and d16 (15, 18 and 21 ids, verified by comparing both), so `expected.json`'s
taster-authored ids score a full-pack run without a mapping.

This was the run the 0.10.0 release baton called for, and it earned its keep: it produced the
first `self_assessment` data any run has carried, and putting that beside each domain's own
verdict distribution surfaced **#211** immediately. d05 self-reported `high` confidence while
10 of its 18 rules were verdicted could-not-evaluate, rendering identically to d01, which could
not evaluate 2 of 15, in direct contradiction of README.md's promise that a finding from a
shaky domain does not look identical to one from a solid one. No earlier run could have found
this: before #192 landed there was no self-assessment data to compare against.

Also confirmed for the first time against a real auditor rather than a test: the per-domain
`domain_rules_fetched_at` and `domain_recorded_at` stamps (#205, #206) populate and are
monotonic; `uninspected_evidence` came back `[]` and that claim is truthful (the fixture points
at no external evidence store, verified with a control grep); and each domain's `limits` text
cross-checks against its own verdicts, naming exactly the rules actually recorded as
could-not-evaluate.

### Fourth run

2026-08-15, same setup, on the #211 branch build (tool_commit `f53df48`) to confirm the fix
end-to-end: **6 of 7 planted findings hit, 4 of 4 controls held, no false positives, 9
unexpected findings.** Exit code 1. The #211 fix rendered correctly on a live run, with the
three domains' confidence cells reading distinctly for the first time.

The single miss was D05-R08, verdicted `not-applicable` with reasoning rather than skipped.
That takes D05-R08 to **2 hits in 4 recorded runs**, which retires this file's earlier reading
of the first run's miss as ordinary variance. It is filed as **#213**: the fixture gives the
layering rule no unit-testable seam to be missing, since both functions in
`loyalty_writer.py` take a live connection and every test opens sqlite, so `not-applicable` and
`finding` are both defensible readings of the same fixture. A golden expectation must not be a
judgement call. It is deliberately left unfixed rather than repaired unattended: `expected.json`
is the answer key for every later run, and a single green result after editing it would be
indistinguishable from the coin landing heads again.

These two runs together are the clearest demonstration yet of this file's first Known
limitation. Same fixture, same model, same pack, forty minutes apart, and one scored clean
while the other did not.
