This file does not start with a "# Domain NN: Title" heading, which is required. It
does have a Trigger line below, so a loader must attempt a full parse and fail
loudly rather than skip it the way a missing-trigger file is skipped.

**Trigger:** this file exists only to break the loader's header parsing on purpose.

## A. Broken

### 1. This rule can never be reached because the header above is malformed.

*Source: invented for test fixtures only, no external source. Rule id: D99-R01. Volatility: durable. Verified: 2026-08-09 (fixture, not a real citation).*
