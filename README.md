# engineering-audit

![CI](https://img.shields.io/github/actions/workflow/status/rodlunt/engineering-audit/ci.yml?branch=main&label=ci)
![Latest release](https://img.shields.io/github/v/release/rodlunt/engineering-audit)
![Licence](https://img.shields.io/github/license/rodlunt/engineering-audit)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776ab)
![Checked with ruff and mypy](https://img.shields.io/badge/checked%20with-ruff%20%2B%20mypy-4b8bbe)

**TL;DR:** an engineering-practice audit tool for AI coding assistants (Claude Code, Codex
CLI, Gemini CLI). Your assistant sweeps a repository against a pack of sourced engineering
rules and produces a self-contained HTML report: every finding says what is wrong, why it
matters, and how to fix it, with the citation behind the claim attached. Findings can be
filed as GitHub issues (from the assistant, or straight from the report with a PAT) or
copied out for pasting anywhere. It also works inline, nudging your assistant to load the
relevant rules at the moment of a decision. If you want audited-by-evidence engineering
practice checks inside the tools you already use, this is for you; you will need access to
a rules pack (see [Rules access](#rules-access)).

| Configure a run | Report |
|---|---|
| ![Configuration page: domain tick boxes, issue delivery, feedback consent](docs/images/config-page.png) | ![Report: run metadata and tool performance summary](docs/images/report.png) |

![Issues with selection tick boxes and GitHub filing, feedback form, footer](docs/images/issues-feedback.png)

## Scope

This documentation states functional behaviour (what the tool does) and the security and
privacy properties documented inline (how the access token is held, what telemetry you opt
into, what leaves your machine and when). The two human-facing surfaces, the configuration page
and the report page, target [WCAG 2.2](https://www.w3.org/TR/WCAG22/) Level AA. Performance is
explicitly out of scope for now: no throughput or latency target is stated or tested anywhere in
this repository.

## How it works

The tool is a local MCP server (Python, stdio). It points at a local directory of rule
documents and serves them to the agent driving the audit. The agent supplies the judgement;
the server supplies everything that must not depend on an LLM's memory: schema-validated
finding capture (a rule the agent did not check can never be recorded as a pass), the
configuration page, deterministic report rendering, source citations attached from the
rules pack itself, and GitHub issue filing with an explicit confirmation step.

Two modes:

- **Standalone audit**: tick the domains to audit on a local configuration page (or supply
  a saved config for headless runs), the agent sweeps the repository, and you get
  `report.html` plus optional GitHub issues.
- **Inline**: one-line triggers merged into your assistant's instruction context tell it to
  call `get_domain(...)` at decision moments (designing a schema, cutting a branch, shaping
  an API), so the rules arrive exactly when they are useful.

## Support matrix

| Assistant | Inline mode | Standalone audit |
|---|---|---|
| Claude Code | proven (in daily use via skills) | proven (recorded run 2026-08-09) |
| OpenAI Codex CLI | documented, untested | documented, untested |
| Gemini CLI | documented, untested | documented, untested |
| GitHub Copilot | unsupported | unsupported |

"Proven" means a recorded end-to-end run exists. "Documented, untested" means the
integration follows the assistant's official documentation, with individually verified
pieces labelled in the integration README, but no full audit has been exercised on it yet.
Copilot is deliberately unsupported rather than silently absent.

## Install

The server runs with [uv](https://docs.astral.sh/uv/) (Python 3.10+), either straight from
this repository via `uvx` or from a local clone. Every assistant needs the same two things:
the MCP server registered, and a rules pack on disk to point it at.

Every `uvx --from git+...` command below is pinned to a release tag (currently `@v0.5.0`), not
to the moving default branch: an unpinned git dependency resolves to whatever is on `main` at
install time, and to whatever `main` has moved to on every later `uvx` cache refresh. To find
the current latest release, check [the Releases
page](https://github.com/rodlunt/engineering-audit/releases) or run `git ls-remote --tags
https://github.com/rodlunt/engineering-audit "v*"`. To update deliberately, change `@v0.5.0`
in the install command below to the new tag and re-register the server (consult your
assistant's MCP docs for how it handles re-registering a name that already exists).

<details>
<summary><strong>Claude Code</strong></summary>

Register the server:

```sh
claude mcp add engineering-audit -- uvx --from git+https://github.com/rodlunt/engineering-audit@v0.5.0 \
    engineering-audit-mcp --rules-dir /path/to/rules-clone/domains
```

Install the audit skill (gives you a natural-language entry point: "audit this repo"):

```sh
ln -s /path/to/engineering-audit/integrations/claude-code/audit ~/.claude/skills/audit
```

Then ask Claude Code to audit the repository you have open. It follows
[AUDIT.md](AUDIT.md): you pick domains on the configuration page, it sweeps, you get the
report. Full details: [integrations/claude-code/](integrations/claude-code/).

</details>

<details>
<summary><strong>OpenAI Codex CLI</strong></summary>

Register the server (verified against codex-cli 0.114.0):

```sh
codex mcp add engineering-audit \
    --env ENGINEERING_AUDIT_RULES_DIR=/path/to/rules-clone/domains \
    -- uvx --from git+https://github.com/rodlunt/engineering-audit@v0.5.0 engineering-audit-mcp
```

Inline mode: generate the trigger fragment and append it to your repo's `AGENTS.md` (or
`~/.codex/AGENTS.md` for all repos):

```sh
uvx --from git+https://github.com/rodlunt/engineering-audit@v0.5.0 engineering-audit-fragments \
    --rules-dir /path/to/rules-clone/domains --out-dir .
cat AGENTS-fragment.md >> AGENTS.md
```

Standalone audit: in a `codex` session, ask it to read AUDIT.md from this repository and
run the audit. The full flow is documented but not yet exercised end to end on Codex; see
[integrations/codex/](integrations/codex/) for headless notes and caveats.

</details>

<details>
<summary><strong>Gemini CLI</strong></summary>

Everything for Gemini ships as an extension (documented, untested: Gemini CLI was not
available to exercise it; check `gemini --help` against the README's flags before an
unattended run):

```sh
gemini extensions install https://github.com/rodlunt/engineering-audit --ref v0.5.0
```

`--ref v0.5.0` pins the install to the current tagged release rather than the moving `main`
branch; see the note at the top of this section for how to find the latest tag.

The extension registers the MCP server, adds an `/audit` command, and carries the inline
trigger fragment as its context file. Manual alternative and details:
[integrations/gemini/](integrations/gemini/).

</details>

<details>
<summary><strong>Headless / CI</strong></summary>

Skip the interactive configuration page by pointing `ENGINEERING_AUDIT_CONFIG` at a saved
configuration JSON (shape documented in [AUDIT.md](AUDIT.md)); `get_config` then returns
immediately. Example driver, Claude Code:

```sh
claude -p "Read AUDIT.md at <path> and audit this repository via the engineering-audit \
MCP tools." --mcp-config mcp.json --allowedTools "mcp__engineering-audit__*,Read,Glob,Grep"
```

</details>

## What a run looks like

A standalone audit is a conversation plus one browser page. From your seat:

1. **Ask for the audit** ("audit this repo against the engineering rules"). The assistant
   gathers run metadata and starts the run.
2. **A configuration page opens in your browser** (`http://127.0.0.1:<port>/`). Opening it
   is best-effort: in a remote or display-less session no tab can appear, so the assistant
   also prints the URL; open it yourself if nothing popped up. Tick the domains to audit,
   choose report-only or GitHub issue filing, and submit. Nothing proceeds until you
   submit: the tool never falls back to a domain selection you did not make.
3. **The assistant sweeps the repository** domain by domain. This is the slow part:
   minutes for a small repository and a few domains, longer for a big selection. You can
   ask for progress; it can report which domains are recorded and which remain.
4. **Everything lands in `audit-output/` inside the audited repository**: `report.html`
   (the deliverable, openable in any browser; the assistant offers to open it when the
   run finishes) and `run-state.json` (the raw machine-readable results, which can
   re-render the same report later via `engineering-audit-render`). If you chose GitHub
   filing, the assistant previews the issues and asks before filing anything.

`audit-output/` belongs to the audited repository, not to this tool. Commit it, ignore it
or delete it as that repository's own conventions dictate.

## What a full run costs

A full sweep is token-hungry, and the configuration page's domain tick boxes are the cost
control: cost scales close to linearly with the domains you tick. Budget from this
recorded example run rather than guessing:

| Measure | Recorded example (this repository, 2026-08-09) |
|---|---|
| Scope | all 16 domains of the standard pack, 260 rules |
| Subagent tokens | 2,010,691 total, roughly 100k to 170k per domain |
| Wall clock | 47 minutes end to end, sweeps running four at a time |
| Findings | 33 (every one filed as a GitHub issue) |

Two caveats from the recorded run: the orchestrating conversation's own tokens are real
overhead on top of the subagent figure and were not separately metered, and findings do
not track cost (the cheapest domain produced the most findings; the dearest concluded
honestly that nothing applied). The full per-domain table and sizing rules of thumb are
in [docs/example-audit-cost.md](docs/example-audit-cost.md).

## The report

Self-contained HTML, generated locally; nothing leaves your machine unless you choose to
send or file it. It contains:

- A **tool performance summary** about the audit run itself: coverage, findings rollup,
  a prominent could-not-evaluate list with reasons, the assistant's own per-domain
  confidence. An unchecked rule is never presented as a pass.
- **Findings**, each in three parts (the issue and location, why it matters, suggested
  fix) with the rule's citation appended automatically from the rules pack. The tool
  refuses to publish a finding whose rule carries no citation.
- **Issues**: tick boxes to select findings, then file them to GitHub directly from the
  report (fine-grained PAT, used in memory only, sent only to api.github.com), or copy the
  selected set for pasting into an LLM or editor, or copy them one at a time.
- **Feedback to the author**: freeform text plus tick-box consent over which run
  statistics accompany it. Finding text never leaves your machine through this channel.

A live example: [docs/demo/report.html](docs/demo/report.html) (download and open locally;
GitHub does not render raw HTML in the browser). Generated from the invented demo rules pack
in `tests/fixture_pack`, not a real audit against a real repository.

## The rules

The author's rules pack covers sixteen decision domains, 260 rules in all, each rule
carrying a cited source, a volatility tier and a verification date, and each domain proven
against a real system before it is trusted:

| # | Domain | Rules | Fires when you are... |
|---|---|---|---|
| d01 | Designing a Data Model | 15 | modelling entities, choosing keys, constraints, normalising, writing DDL or migrations |
| d02 | Eliciting and Specifying Requirements | 16 | deciding what to build, writing requirements or user stories, checking the right problem is being solved |
| d03 | Modelling Structure and Behaviour Before Building | 15 | deciding what to diagram before coding, drawing or reviewing FMC/UML/SysML models |
| d04 | Structuring Code and Applying Design Patterns | 14 | designing classes or modules, weighing a design pattern, choosing data structures or error handling |
| d05 | Choosing What to Test and How Much | 18 | choosing test levels and coverage, weighing testing against risk, planning load or soak tests and CI gates |
| d06 | Structuring a Repo, Branches and CI/CD | 15 | structuring a repository, writing CI/CD workflows, handling automation credentials, cutting releases |
| d07 | Handling Untrusted Input and Secure Coding | 16 | writing code untrusted input can reach: forms, auth flows, credentials, sessions |
| d08 | Threat Modelling and Security Risk Decision-Making | 15 | running a risk assessment, threat-modelling a system, prioritising vulnerabilities, justifying a control |
| d09 | Responding When Something Breaks in Production | 16 | writing incident response plans, defining recovery objectives, running post-incident reviews |
| d10 | Designing APIs and Service Contracts | 14 | creating or extending an HTTP API, choosing verbs and status codes, versioning or deprecating an interface |
| d11 | Choosing Architecture and Deployment Topology | 16 | picking an application architecture, deciding VM/container/serverless topology, planning scaling and rollout |
| d12 | Making an Ethical or Professional Judgement Call | 17 | facing pressure to cut a corner, decisions affecting users or the public, handling personal data |
| d13 | Estimating and Pricing Work | 16 | scoping work before quoting, choosing estimation methods, setting contingency, defending an estimate |
| d14 | Fault Diagnosis of a Running System | 19 | investigating an outage, a slow or wrong-answering service, or an intermittent bug |
| d15 | Interface Design and Prototyping | 17 | laying out a screen, designing a form, writing button and error copy, deciding confirmation vs undo |
| d16 | Presenting Data for Decisions | 21 | putting a number, chart or table in front of somebody who has to decide something |

### Try it right now: the taster pack

Three complete domains (d01, d05, d16, 54 rules with their full source citations) are
published in [examples/taster-rules/](examples/taster-rules/) as point-in-time exports
from the maintained pack. They are a working rules directory: point the server at them
and run a real audit before asking for anything.

```sh
claude mcp add engineering-audit -- uvx --from git+https://github.com/rodlunt/engineering-audit@v0.5.0 \
    engineering-audit-mcp --rules-dir /path/to/engineering-audit/examples/taster-rules
```

### Full access

The full pack lives in a private repository with access granted per user (the maintained
originals, their revision history and proving records). Open an issue here to ask. The
tooling works with any rules directory in the expected format (`**Trigger:**` header,
`### N. Title` rules, `Rule id:` footers with `Source:` fragments), so you can also write
your own pack.

## Development

```sh
uv sync
uv run pytest -q
```

CI runs the same suite on every push and pull request. Tests use an invented fixture rules
pack; no private rule content exists in this repository. The renderer and configuration
page are deterministic and fully testable with no LLM involved.

This is a solo-maintainer repository, and its merge gate is deliberately CI-only: every change
lands via a pull request that must pass the `check` status; no human review requirement is
configured.

### Tracking issues and PRs

Up to now, the PR description has been the deliberate change record for this project: each PR
body explains what changed and why, and that has been treated as sufficient in place of a
separate issue-linked history. From now on, every PR links its tracking issue with a
`Closes #N` line (or `Fixes #N`), so the issue tracker and the merge history stay in step
instead of relying on the PR description alone.

### Eval harness

`evals/` holds a deterministic scorer for audit quality: a small fictional golden repository with
known planted findings and controls, and `engineering-audit-eval` to check a run-state.json
against them. The scorer is CI-safe and has its own tests; the audit run that feeds it calls a
real LLM and is run and checked by hand. See [evals/README.md](evals/README.md).

## Roadmap

- Thin CLI wrapper driving an agent CLI headlessly end to end (a manual, scripted version of this
  now lives in [evals/README.md](evals/README.md); a first-class wrapper command is still open).
- Remotely served rules with revocable access.
- Codex and Gemini support-matrix rows moving to proven once live runs are recorded.

## Licence

The tooling in this repository (the MCP server, the deterministic report renderer, the
configuration page, and every supporting script) is licensed under [Apache-2.0](LICENSE).
Rules packs are licensed separately and are not covered by this repository's licence; see
[Rules access](#rules-access).
