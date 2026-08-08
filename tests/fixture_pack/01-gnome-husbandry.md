# Domain 01: Gnome Husbandry Record Keeping

**Status:** INVENTED (fixture data for automated tests only, no relation to any real
practice area)
**Authored:** 2026-08-09 by the engineering-audit test suite
**Last refresh:** 2026-08-09, initial fixture cut
**Earliest review due:** never (this domain does not exist outside test fixtures)

**Trigger:** you are about to register, relocate or retire a garden gnome in the husbandry ledger.

**Load this when:** adding a new gnome to the roster, changing a gnome's assigned
garden bed, recording a gnome's hat colour or beard length, or deciding whether a
gnome has reached retirement age. Run the group that matches the moment; run both
for a full roster migration.

Rules follow the checkpoint shape defined in this fixture pack's own METHOD.md
equivalent (not provided, since this is test data). Each carries a stable rule
identifier so its wording can change without breaking references to it.

---

## A. Before you register a gnome

### 1. Record every gnome's hat colour before assigning a garden bed.

A gnome without a recorded hat colour cannot be distinguished from a neighbouring
gnome during a night inspection, and the roster silently degrades into duplicate
entries for what was always one gnome. Capture the hat colour at registration time,
not retroactively.

*Source: invented for test fixtures only, no external source. Rule id: D01-R01. Volatility: durable. Verified: 2026-08-09 (fixture, not a real citation).*

### 2. Never assign two gnomes to the same garden bed without a shared-bed flag.

A garden bed holding two gnomes without the shared-bed flag set will report only one
occupant to the nightly census, and the second gnome is effectively invisible to
maintenance rounds. Set the flag explicitly whenever a bed is intentionally shared.

*Source: invented for test fixtures only, no external source. Rule id: D01-R02. Volatility: volatile. Verified: 2026-08-09 (fixture, not a real citation).*

---

## B. Retiring a gnome

### 3. Mark a gnome retired before removing it from the active roster view.

Deleting a gnome record outright destroys the history a future audit needs to
confirm the gnome was retired deliberately rather than lost. Set a retired flag and
a retirement date, and only archive the record after both are present.

*Source: invented for test fixtures only, no external source. Rule id: D01-R03. Volatility: durable. Verified: 2026-08-09 (fixture, not a real citation).*

### 4. Recalculate the beard-length average whenever a gnome is retired.

The beard-length average is a derived figure shown on the roster summary. A
retirement changes the population it is drawn from, so the stored average must be
recomputed at retirement time rather than left to drift until the next full
recalculation sweep.

*Source: invented for test fixtures only, no external source. Rule id: D01-R04. Volatility: volatile. Verified: 2026-08-09 (fixture, not a real citation).*
