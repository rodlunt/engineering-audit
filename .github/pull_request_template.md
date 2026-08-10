<!-- Thanks for the PR. CONTRIBUTING.md is the two-minute read behind each line here. -->

## What and why

<!-- What changed, and the reason it needed to. The body of your commits should
     already say this; a summary here is fine. -->

Closes #

## Checks

- [ ] Ran the three CI gates locally and they pass:
      `uv run --with ruff==0.15.16 ruff check .`
      `uv run --with mypy==2.3.0 mypy src`
      `uv run pytest -q`
- [ ] Behaviour changes carry tests, including a failing case for any new
      validation or check (a rejection must never be representable as a pass)
- [ ] Commits follow conventional commits (`fix:`, `feat:`, `docs:`, ...) with
      the why in the body
- [ ] The idea was raised in Ideas Discussions first, for anything bigger than a
      bug fix
- [ ] Prose is Australian English with no em or en dashes

<!-- The merge gate is CI-only, but every diff is read before merging; a clear
     what-and-why above is what gets a PR merged quickly. -->
