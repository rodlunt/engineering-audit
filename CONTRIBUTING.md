# Contributing

Thanks for wanting to improve engineering-audit. This is a solo-maintained project, so
the process is deliberately light; the few rules below exist so contributions land
smoothly rather than stalling in back and forth.

## Where things go

- **Bugs**: [open an issue](https://github.com/rodlunt/engineering-audit/issues/new/choose)
  using the bug report form. Tool version, assistant, and reproduction steps are the
  three things that get a bug fixed fast.
- **Questions**: [Q&A Discussions](https://github.com/rodlunt/engineering-audit/discussions/categories/q-a).
- **Ideas and feature requests**: [Ideas Discussions](https://github.com/rodlunt/engineering-audit/discussions/categories/ideas).
  Raising the idea before writing the code is strongly recommended; it protects you from
  building something that will not merge.
- **Security problems**: never publicly. See [SECURITY.md](SECURITY.md).

## Development setup

The project runs on [uv](https://docs.astral.sh/uv/) with Python 3.10+:

```sh
git clone https://github.com/rodlunt/engineering-audit
cd engineering-audit
uv sync
uv run pytest -q
```

CI gates every pull request on four checks, so run them locally before pushing:

```sh
uv run --with ruff==0.15.16 ruff check .
uv run --with ruff==0.15.16 ruff format --check .
uv run --with mypy==2.3.0 mypy src
uv run pytest -q
```

The report page's JavaScript has its own executable tests, run through pytest via node;
with node absent they skip visibly rather than passing silently.

Set `git blame` to skip the one-off mechanical reformat that adopted `ruff format`
(issue #106), since git does not read `.git-blame-ignore-revs` on its own:

```sh
git config blame.ignoreRevsFile .git-blame-ignore-revs
```

Without this, `git blame` on a file touched by that commit shows it as the last change
to nearly every line, hiding whoever actually wrote them.

## Expectations for a pull request

- **Branch from `main`**, named descriptively (`fix/...`, `feat/...`, `docs/...`).
- **Conventional commit messages** (`fix:`, `feat:`, `docs:`, `refactor:`, `test:`,
  `ci:`, `chore:`), imperative subject, body explaining why rather than what.
- **Link the tracking issue** with a `Closes #N` line in the PR body, when the PR has one.
  Release PRs and housekeeping PRs with no tracking issue behind them are the exception:
  there is nothing to close, so no keyword is expected.
- **Tests for behaviour changes.** This project's hardening rules apply: a check whose
  failure can be read as a pass does not count as a check, and validation rejections
  need a failing-case test asserting the error message is actionable.
- **Prose in Australian English**, and no em or en dashes anywhere (commas, colons,
  parentheses or hyphens instead); this matches the rest of the repository.

The merge gate is deliberately CI-only: every change lands via a pull request that must
pass the `check` status, and there is no human review requirement configured. That means
CI green is necessary but not sufficient; the maintainer still reads every diff before
merging.

## Releasing

The version lives in `pyproject.toml` and is derived everywhere else, not hand-edited in
roughly ten places. To cut a release:

```sh
uv run python scripts/bump-version.py X.Y.Z
```

This writes `pyproject.toml`'s version, rewrites every other version pin to match (see
`scripts/version_pins.py` for the exact list: install command refs, integration docs, a
few known prose mentions, and the top-level `"version"` field of any tracked JSON
manifest, of which this repository currently ships none), and runs
`scripts/check-version-pins.py` itself as a self-check before reporting success. Review the
diff, commit as `chore(release): X.Y.Z`, and push it through the normal pull request flow.
Once merged, tag `vX.Y.Z` on `main`. `tag-version-guard.yml` fails the tag push if the
tagged tree disagrees with `pyproject.toml`'s version, and generates the release SBOM.

## Rules pack content

Rule content does not live in this repository. The tooling here reads any rules
directory in the documented format, and the maintained pack has its own home; rule
suggestions belong in Ideas Discussions, not PRs here.

## Licence

By contributing you agree your contributions are licensed under this repository's
[Apache-2.0 licence](LICENSE).
