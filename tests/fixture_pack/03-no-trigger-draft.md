# Domain 03: Unfinished Draft Domain

**Status:** DRAFT (fixture data for automated tests only; deliberately has no Trigger
line so the loader's skip-reporting path is exercised)
**Authored:** 2026-08-09 by the engineering-audit test suite

This fixture file intentionally omits the `**Trigger:**` line. A rules pack loader
must skip files without one (matching the private generator's behaviour) rather
than fail the whole pack load, but the skip itself must show up in the pack's
skip report rather than passing silently.

---

## A. Nothing to see here

### 1. This rule should never be loaded because the file has no trigger.

If this rule id ever appears in a loaded Domain, the loader's skip-on-no-trigger
behaviour is broken.

*Source: invented for test fixtures only, no external source. Rule id: D03-R01. Volatility: durable. Verified: 2026-08-09 (fixture, not a real citation).*
