# Domain 02: Teacup Logistics Handling

**Status:** INVENTED (fixture data for automated tests only, no relation to any real
practice area)
**Authored:** 2026-08-09 by the engineering-audit test suite
**Last refresh:** 2026-08-09, initial fixture cut
**Earliest review due:** never (this domain does not exist outside test fixtures)

**Trigger:** you are about to schedule, pack or reroute a teacup shipment.

**Load this when:** planning a new teacup delivery route, choosing packing material
for a fragile order, or deciding whether a delayed shipment needs a customer
notice.

---

## A. Packing a shipment

### 1. Never pack a bone china teacup without a declared fragility tier.

A shipment without a declared fragility tier is routed through the standard
conveyor, which bone china does not survive. Declare the tier before the parcel
leaves the packing bench, not after a breakage report arrives.

*Source: invented for test fixtures only, no external source. Rule id: D02-R01. Volatility: durable. Verified: 2026-08-09 (fixture, not a real citation).*

### 2. Record the packer's initials against every fragility-tier override.

An override that is not attributed to a specific packer cannot be reviewed when a
breakage pattern emerges, because nobody can tell whether one packer is
overriding the tier repeatedly or the pattern is spread evenly across the team.

*Source: invented for test fixtures only, no external source. Rule id: D02-R02. Volatility: volatile. Verified: 2026-08-09 (fixture, not a real citation).*

---

## B. Rerouting a delayed shipment

### 3. Notify the customer before rerouting a shipment more than one day late.

A silent reroute leaves the customer's own tracking page showing a route that no
longer matches reality, and the first they hear of the delay is a missed delivery
window. Send the notice before the reroute takes effect, not after.

*Source: invented for test fixtures only, no external source. Rule id: D02-R03. Volatility: durable. Verified: 2026-08-09 (fixture, not a real citation).*
