# What a full audit costs: recorded examples

A full 16-domain audit is token-hungry. This page records the actual consumption of real
runs so nobody is surprised by the bill. Two runs are recorded so far, on two different
hosts. Read the "not directly comparable" section below before budgeting from either one:
the orchestration models differ enough that a figure from one run does not transfer to the
other.

## Claude Code (2026-08-09)

### Run shape

- **Orchestrator**: Claude Code with Fable 5 (`claude-fable-5`) driving the run: fetching
  config, dispatching one sweep agent per domain in four waves of four, validating each
  result, recording it, filing issues, rendering the report.
- **Sweep agents**: one Claude Sonnet subagent per domain, each loading its domain's rule
  text via `get_domain` and sweeping the repository read-only.
- **Repository under audit**: this one, roughly 4,100 lines of Python plus docs, tests and
  CI. A larger repository will cost more per domain; domain count is the bigger lever.
- **Wall clock**: 47 minutes end to end (begin_run 10:39 UTC to report 11:26 UTC),
  including issue filing and user confirmation pauses. Sweeps ran four at a time.

### Per-domain consumption

Token figures are as reported by Claude Code's Agent tool for each subagent (its total
token usage, including its tool-result context). They are not a billing statement: cache
hits, the orchestrator's own tokens, and provider-side accounting all differ. Treat them
as a sizing guide, not an invoice.

| Domain | Rules | Findings | Subagent tokens | Tool uses | Duration |
|---|---:|---:|---:|---:|---:|
| d01 data-modelling | 15 | 4 | 124,478 | 26 | 7m 10s |
| d02 requirements-elicitation | 16 | 1 | 111,186 | 19 | 5m 25s |
| d03 modelling-before-building | 15 | 1 | 97,324 | 18 | 2m 56s |
| d04 code-structure-patterns | 14 | 2 | 142,545 | 24 | 3m 37s |
| d05 testing-strategy | 18 | 1 | 110,903 | 38 | 6m 04s |
| d06 repo-branches-cicd | 15 | 7 | 81,672 | 15 | 4m 02s |
| d07 secure-coding | 16 | 3 | 138,720 | 29 | 4m 37s |
| d08 threat-modelling-risk | 15 | 0 | 109,979 | 21 | 3m 45s |
| d09 incident-response | 16 | 0 | 140,439 | 20 | 5m 43s |
| d10 api-design | 14 | 2 | 135,137 | 35 | 5m 28s |
| d11 architecture-deployment | 16 | 3 | 121,164 | 40 | 6m 37s |
| d12 ethics-professional-judgement | 17 | 2 | 165,500 | 31 | 7m 09s |
| d13 estimating-and-pricing | 16 | 0 | 87,241 | 10 | 1m 27s |
| d14 fault-diagnosis | 19 | 0 | 115,762 | 35 | 4m 50s |
| d15 interface-design | 17 | 5 | 160,082 | 22 | 9m 23s |
| d16 presenting-data | 21 | 2 | 168,559 | 32 | 6m 40s |
| **Total** | **260** | **33** | **2,010,691** | **415** | **~35m agent time in 4 waves** |

### What is not in the table

- **Orchestrator tokens.** The main Fable 5 conversation (dispatch prompts, result
  validation, recording calls, issue preview and filing, report rendering) was not
  separately metered in this run. It is real overhead on top of the ~2.0M subagent tokens;
  it was not measured, so no number is claimed for it.
- **Domain variance is judgement variance.** Cheap domains are the ones whose rules mostly
  do not apply (d13 estimating: 87k tokens, 1m 27s, every rule not-applicable). Expensive
  ones are where the agent reads deeply or reproduces findings live (d16 rendered reports
  to prove two findings; d15 hand-computed WCAG contrast ratios).
- **Findings do not track cost.** d06 produced 7 findings for the cheapest full sweep
  (82k); d09 spent 140k to conclude honestly that nothing applied. Paying for verified
  clean verdicts is the point of the tool.

### Sizing a run on Claude Code

Rules of thumb from this run, for a small-to-medium repository:

- Budget roughly **100k to 170k subagent tokens per domain**, plus orchestrator overhead.
- A full 16-domain sweep lands around **2M subagent tokens** and under an hour of wall
  clock with four-way parallel sweeps.
- Auditing fewer domains scales cost close to linearly: the config page's domain tick
  boxes are the cost control.

## Codex CLI (2026-08-10)

### Run shape

- **Host**: Codex CLI 0.147.0, model `gpt-5.6-luna`, macOS.
- **Tool version**: 0.5.1, rules pack commit `82edb80`.
- **Repository under audit**: an external React single-page app, roughly 344 files. A
  different repository to the Claude Code run above, so file counts and per-domain figures
  are not comparable to that run's line-count figure even setting the token gap aside.
- **Scope**: all 16 domains of the standard pack.
- **Wall clock**: 61 minutes end to end, 05:41:12Z to 06:42:26Z, as reported in the
  rendered report header.
- **Findings**: 32, with 122 rules recorded as could not evaluate in the findings rollup.

### Token consumption: not captured

No token figure is given for this run, and none should be inferred. The usage report for
this session exists on the Codex host but has not been brought into this repository, so
there is no reliable figure to record here. This is not a zero and it is not the Claude Code
per-domain range above applied by analogy: Codex's orchestration shape (below) means a
transferred figure would not mean the same thing even if one were guessed. When the usage
report is captured, this section will carry an update; until then, budgeting for a Codex
run on token cost alone should treat it as an open unknown, not as similar to Claude Code's
recorded figure.

### Why this run has no per-domain table

Unlike the Claude Code run above, Codex did not fan out to one subagent per domain. The
Claude Code skill dispatches a separate Sonnet subagent per domain, four at a time, which
is what produces both the per-domain token/duration breakdown and the "four waves" shape of
its wall clock. Codex swept all 16 domains without that per-domain fan-out, so there is no
equivalent set of 16 rows to report, and its 61-minute wall clock is not shaped by the same
four-at-a-time parallelism factor as Claude Code's 47 minutes. The two totals are both real
measurements of a full run, but they are measurements of different shapes of work, not the
same shape at two prices.

## Not directly comparable: read before budgeting

Do not read the two runs above as if they were the same experiment on two different
providers. They differ in orchestration model (per-domain subagent fan-out versus a single
non-fanned-out sweep), in repository (this repository's roughly 4,100 lines of Python
versus an external React SPA of roughly 344 files), and one of the two is missing its token
figure entirely. Use each run to size its own host. Interpolating a number between them,
in either direction, is a guess dressed up as a measurement.
