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

# 3. Register the MCP server against the taster rules pack, then drive the
#    audit headlessly via AUDIT.md.
claude mcp add engineering-audit -- uvx --from git+https://github.com/rodlunt/engineering-audit \
    engineering-audit-mcp --rules-dir /path/to/engineering-audit/examples/taster-rules

ENGINEERING_AUDIT_CONFIG=/tmp/grindpoints-eval-config.json \
claude -p "Read AUDIT.md at /path/to/engineering-audit and audit /tmp/grindpoints-eval via the \
engineering-audit MCP tools." --mcp-config mcp.json \
    --allowedTools "mcp__engineering-audit__*,Read,Glob,Grep"

# 4. Score whatever the audit produced against the golden spec.
uv run engineering-audit-eval /tmp/grindpoints-eval/audit-output/run-state.json \
    --expected evals/golden/expected.json \
    --rules-dir examples/taster-rules \
    --out /tmp/grindpoints-eval-result.json
```

Adjust `/path/to/engineering-audit` to wherever this repository is checked out. The private,
maintained rules pack can be substituted for the taster pack in step 3, but `expected.json`'s rule
ids are taster-pack ids, so scoring a private-pack run against it would need its own mapping;
this harness ships tested only against the taster pack.

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
