# ADR 0002: Baked-in stack profiles instead of live web lookup

**Status:** accepted  
**Date:** 2026-08-25  
**Framework:** (applies to tooling design, not a domain rule)

## Context

Stack profiles are collections of engineering rules tailored to specific technology stacks (e.g. Python, React, FastAPI, Django). At grill time, the user specifies the project's tech stack, and the tool loads the corresponding stack profiles to add technology-specific rules alongside the generic rules from the main pack.

The question is: where do stack profiles live, and how are they versioned?

One approach is to fetch stack profiles live from the web at grill or audit time. This ensures the user always gets the latest best practice for their stack, and it keeps the tool size small (profiles are not shipped with the tool). However, it has liabilities:

1. The web lookup can fail (network error, URL changes, the service goes down).
2. The profiles can change between audit runs, silently changing which rules apply and what the user is committing to.
3. A user running the tool in an air-gapped environment cannot fetch profiles.
4. The tool's behaviour becomes non-reproducible: two identical audits on identical commits might load different profiles.

The other approach is to bake stack profiles into the rules pack ahead of time. The tool ships with versioned profiles. This means:

1. The audit is deterministic and reproducible.
2. Air-gapped environments can run audits.
3. The user has control over when to upgrade to a newer profile version.
4. Trade-off: the tool is larger, and stack profiles must be explicitly maintained and kept current.

## Decision

Stack profiles are baked into the rules pack and versioned alongside it. The tool does not make web requests to fetch or update profiles at runtime.

Every stack profile carries a `maintained_until` date in the pack metadata. When a profile reaches its review date, the grill tool warns the user ("The Python 3.10 profile was last reviewed 2024-01-01; consider reviewing it for this project").

## Alternatives considered

**Alternative 1: Live web lookup**

The tool fetches stack profiles from a web URL (e.g. a GitHub repository or an API) at grill or audit time. The profiles are always current.

Rejected because:
- Non-determinism: two runs of the same audit might load different profiles.
- Fragility: network errors can break the audit.
- Air-gapped environments cannot use the tool.
- The tool's behaviour becomes opaque to the user (they do not know which version of the profile they are using until it is too late).

**Alternative 2: Optionally baked, optionally live**

The tool ships with baked profiles, but allows the user to override them with live profiles from a web URL if they want to opt in to the latest.

Rejected because: this adds complexity (two code paths for loading profiles) and still suffers from the non-determinism and fragility risks of live lookup, just optionally rather than always. It also means the user must make a deliberate choice every time they run a grill, which adds friction.

**Alternative 3: Semi-baked profiles with versioning**

The tool ships with profiles, but also checks for newer versions at grill time and offers to upgrade before proceeding.

Rejected because: this is still a live lookup (same network/air-gap/determinism issues) and adds friction to the grill workflow by requiring version-management decisions from the user.

## Consequences

**Benefits:**
- Audits are deterministic and reproducible. The same commit audited on the same tool version will always load the same rules.
- Air-gapped environments can run audits without network access.
- The user has explicit control over when to upgrade stack profiles (by upgrading the tool).
- The tool's behaviour is transparent: a user can read the profiles shipped with the version they are using.

**Costs:**
- The rules pack must ship with stack profiles, making it larger.
- Stack profiles must be explicitly maintained and kept current. The tool maintainer must regularly review each profile for changing best practice.
- Users running old tool versions will use old stack profiles, and they may fall out of sync with the organisation's actual standards.

**Risks:**
- A stack profile that falls out of date silently encodes stale best practice. A project that was grill'd in 2025 and re-audited in 2027 might be using an old Python profile.
  - Mitigation: the grill tool warns the user when a profile has not been reviewed recently. The warning encourages the user to upgrade the tool or manually review the profile.
- An organisation might want their own custom stack profiles, not the ones shipped with the tool.
  - Mitigation: the tool allows users to configure a custom rules directory via environment variable or config file, so organisations can ship their own profiles alongside the standard pack.

