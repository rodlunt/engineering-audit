# What a full audit costs: a recorded example

A full 16-domain audit is token-hungry. This page records the actual consumption of one
real run so nobody is surprised by the bill. The numbers below are from the recorded
self-audit of this repository (2026-08-09, tool v0.4.0, all 16 domains of the standard
rules pack, 260 rules total).

## Run shape

- **Orchestrator**: Claude Code with Fable 5 (`claude-fable-5`) driving the run: fetching
  config, dispatching one sweep agent per domain in four waves of four, validating each
  result, recording it, filing issues, rendering the report.
- **Sweep agents**: one Claude Sonnet subagent per domain, each loading its domain's rule
  text via `get_domain` and sweeping the repository read-only.
- **Repository under audit**: this one, roughly 4,100 lines of Python plus docs, tests and
  CI. A larger repository will cost more per domain; domain count is the bigger lever.
- **Wall clock**: 47 minutes end to end (begin_run 10:39 UTC to report 11:26 UTC),
  including issue filing and user confirmation pauses. Sweeps ran four at a time.

## Per-domain consumption

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

## What is not in the table

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

## Sizing a run

Rules of thumb from this run, for a small-to-medium repository:

- Budget roughly **100k to 170k subagent tokens per domain**, plus orchestrator overhead.
- A full 16-domain sweep lands around **2M subagent tokens** and under an hour of wall
  clock with four-way parallel sweeps.
- Auditing fewer domains scales cost close to linearly: the config page's domain tick
  boxes are the cost control.
