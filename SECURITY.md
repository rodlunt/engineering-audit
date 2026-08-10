# Security Policy

engineering-audit is a solo-maintained, local-first tool: the MCP server runs on your own
machine, and the only network calls it makes are the ones you explicitly opt into (GitHub issue
filing, feedback submission, and the update check against this repository's own tags). This
policy is scaled to that: a lightweight private reporting path, not a formal disclosure
programme.

## Supported versions

Only the latest tagged release receives security fixes. This project is pre-1.0 and moves
quickly; there is no long-term support branch.

| Version | Supported |
|---|---|
| latest tag (currently v0.5.1) | yes |
| anything older | no |

If you are running an older tag or an untagged checkout of `main`, update to the latest release
before reporting: the issue may already be fixed. See [README.md](README.md#how-to-use) for how to
pin an install to a specific tag, and how to find the current one.

## Reporting a vulnerability

Please do not open a public GitHub issue for a security problem: that discloses it before a fix
exists.

Instead, report it privately through one of:

- **GitHub Security Advisories** ("Report a vulnerability" under this repository's Security tab),
  which lets you submit a private report the maintainer can see and respond to without it being
  public.
- **Email**: rodneylunt79+audit-feedback@gmail.com (the same address the in-tool feedback channel
  uses). Put "security" in the subject line.

Please include what you have: the affected version or commit, the class of issue (for example,
command injection via the `gh` CLI wrapper, path traversal in report or config-page file
handling, or a credential-handling problem), and a reproduction if you have one.

## What to expect

This is a one-person project, so response times are best-effort, not contractual:

- **Acknowledgement**: within 5 business days of a report arriving.
- **Initial assessment** (severity and a rough plan): within 14 days of acknowledgement.
- **Fix or mitigation**: timeline depends on severity and complexity; you will be told what to
  expect once the report has been triaged.

You will be credited in the fix's release notes if you want to be, and left out if you would
rather not be.
