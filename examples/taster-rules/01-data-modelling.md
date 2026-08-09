# Domain 01: Designing a Data Model

*This is a taster copy of one domain from a maintained private rules pack (16 domains, 260 rules, each with a cited source and a review cadence), exported as a point-in-time snapshot. The maintained original, its revision history and its proving records live in the private repository; open an issue on this repository to ask about access.*

**Trigger:** you are about to design or change how data is stored: modelling entities or facts, choosing keys, constraints or nullability, mapping a model to tables, normalising, writing DDL or a migration, wiring relationships and cascades in code, or granting database access.

**Load this when:** designing a new schema or data model, adding a table, column or
relationship to an existing one, mapping a class model to persistence, choosing ORM
relationship configuration, or granting database access. Run the group that matches the
moment; run all five for a new schema.

Rules follow the checkpoint shape defined in `METHOD.md`. Each carries a stable rule
identifier so its wording can change without breaking references to it.

---

## A. Before you draw: get the facts right

### 1. Decompose the requirement into elementary facts before drawing or typing anything.

An elementary fact is an atomic assertion that particular objects play particular roles:
"Employee 101 works in Department Sales". Verbalising the domain this way forces the two
questions a rushed schema skips: what are the object types, and how is each one identified
(its reference scheme)? A schema that starts at `CREATE TABLE` inherits whatever shape the
first UI form or API payload suggested, and every downstream rule in this document gets harder
to apply because the facts were never separated. This is CSDP step 1, taught as the most
important step of the whole procedure.

*Source: Object-Role Modeling and its Conceptual Schema Design Procedure (Halpin, *Object-Role Modeling: an overview*, orm.net), CSDP step 1. Rule id: D01-R01. Volatility: durable. Verified: 2026-08-05 (current, primary source).*

### 2. Validate every fact-type split with a population check before trusting it.

When one fact could be modelled as two binary relationships or one ternary (Student-Unit plus
Student-Grade, versus Student-Unit-Grade), populate the candidate tables with sample rows,
join them back on the shared column, and compare against the original examples. If the join
loses or invents rows, the split is wrong and the fact is genuinely ternary. This lossless-join
test costs five minutes with sample data and catches a corruption class that otherwise ships:
the schema that cannot say which grade belongs to which unit.

*Source: Object-Role Modeling and its Conceptual Schema Design Procedure (Halpin, *Object-Role Modeling: an overview*, orm.net), CSDP step 2 (population check). Rule id: D01-R02. Volatility: durable. Verified: 2026-08-05 (current, primary source).*

### 3. Challenge look-alike entities with the three combination tests.

Before keeping two entity types separate, ask: can one instance belong to both (do they share
values)? Could instances be meaningfully compared (same unit or dimension, like wholesale and
retail price both being money)? Is the same kind of information recorded for each (doctor,
dentist and pharmacist all recording gender)? A yes to any test means they are one primitive
entity type playing different roles: Person with MovieStar and Director roles, not two tables.
The failure mode of skipping this is parallel tables that accumulate duplicated columns and
quietly diverging data for what was always one population.

*Source: Object-Role Modeling and its Conceptual Schema Design Procedure (Halpin, *Object-Role Modeling: an overview*, orm.net), CSDP step 3. Rule id: D01-R03. Volatility: durable. Verified: 2026-08-05 (current, primary source).*

### 4. Mark derived data as derived, and store it only as a recorded decision.

Markup is retail minus wholesale; area is height times width; an order total is the sum of its
lines. Fact-based modelling marks derived facts in the schema instead of storing them, because a stored
derivable is a second source of truth that drifts from the first the moment one write path
forgets to update it. Storing for performance is sometimes right, but only with the decision
recorded and the invalidation story written down: what recomputes this value, and when.

*Source: Object-Role Modeling and its Conceptual Schema Design Procedure (Halpin, *Object-Role Modeling: an overview*, orm.net), CSDP step 3 (derivation notation). Rule id: D01-R04. Volatility: durable. Verified: 2026-08-05 (current, primary source).*

---

## B. Constrain the model before it becomes tables

### 5. Give every fact type an explicit uniqueness constraint, and derive keys from them.

A uniqueness constraint states which role combination cannot repeat, and it is the thing that
later becomes the primary key. For a ternary or wider fact type, apply the n-1 rule: the
constraint must span the whole fact type or at least n-1 of its roles, and if no legal
constraint fits, the fact type is too long and must be split. The failure this prevents is the
table whose only key is an autoincrement id: with no natural uniqueness declared, every
duplicate is representable and the schema asserts nothing about what makes a row one thing.

*Source: Object-Role Modeling and its Conceptual Schema Design Procedure (Halpin, *Object-Role Modeling: an overview*, orm.net), CSDP step 4 (uniqueness constraints and arity). Rule id: D01-R05. Volatility: durable. Verified: 2026-08-05 (current, primary source).*

### 6. Decide optionality per role; never let nullability default.

A mandatory role constraint (every instance must play this role) becomes NOT NULL; an optional
role stays nullable. Both defaults fail. Forcing NOT NULL onto genuinely optional data breeds
sentinel junk: empty strings, zeroes and 1970 dates that later read as facts. Leaving every
column nullable means the schema cannot distinguish "unknown" from "does not apply" and every
consumer re-implements the guess. Optionality is a per-role decision made from the domain, not
whatever the ORM or DDL emitted.

*Source: Object-Role Modeling and its Conceptual Schema Design Procedure (Halpin, *Object-Role Modeling: an overview*, orm.net), CSDP step 5 (mandatory role constraints). Rule id: D01-R06. Volatility: durable. Verified: 2026-08-05 (current, primary source).*

### 7. Bound every domain-constrained attribute with a value constraint.

An INTEGER column will happily store a wholesale price of -1000. Where the domain bounds a
value (price at or above zero, employee number in a range, status in a known set), write the
bound into the schema as a value constraint (CHECK, enum, or the ORM equivalent), not into
application code alone. Application validation guards one write path; the constraint guards
them all, including the admin script written at 11pm.

*Source: Object-Role Modeling and its Conceptual Schema Design Procedure (Halpin, *Object-Role Modeling: an overview*, orm.net), CSDP step 6 (value constraints). Rule id: D01-R07. Volatility: durable. Verified: 2026-08-05 (current, primary source).*

### 8. Keep subtype hierarchies acyclic, each subtype under exactly one supertype.

Two rules govern subtyping: every subtype stems from exactly one primitive entity type, and the
subtyping graph must be a directed acyclic graph. A hierarchy that cycles, or a subtype with
two supertypes, cannot be mapped to tables coherently and usually signals that rule 3's
combination tests were skipped: the "subtypes" are actually roles.

*Source: Object-Role Modeling and its Conceptual Schema Design Procedure (Halpin, *Object-Role Modeling: an overview*, orm.net), CSDP step 6 (subtyping constraints). Rule id: D01-R08. Volatility: durable. Verified: 2026-08-05 (current, primary source).*

---

## C. From model to tables

### 9. Map the conceptual model to tables by procedure, not ad hoc.

The mapping is mechanical once the model is validated: apply the rules in strict order
(compound-key fact types to their own relations first, then entity types with simple keys,
then nested fact types, then resolve 1:1s), with every uniqueness constraint becoming a
primary key and every inter-entity reference a foreign key. The discipline matters more than
the specific rule set: hand-improvised mapping reintroduces exactly the redundancies and
ambiguities the conceptual steps just removed.

*Source: Object-Role Modeling and its Conceptual Schema Design Procedure (Halpin, *Object-Role Modeling: an overview*, orm.net), relational mapping procedure. Rule id: D01-R09. Volatility: durable. Verified: 2026-08-05 (current, primary source).*

### 10. Normalise to 3NF: every non-key column a fact about the key, the whole key, and nothing but the key.

Three checks, one per form. 1NF: no repeating groups or multi-valued columns (two unit-grade
pairs packed into one student row). 2NF: no column depending on part of a composite key
(School depends on Student alone, not on Student plus Unit; it belongs in a Student table).
3NF: no column depending on another non-key column (Location depends on School, itself
non-key; School gets its own table). The tell for a transitive dependency is lockstep change:
edit one non-key column and another must change with it.

*Source: Kent, *A Simple Guide to Five Normal Forms in Relational Database Theory*, Communications of the ACM 26(2), 1983; the formulation quoted is Kent's. Rule id: D01-R10. Volatility: durable. Verified: 2026-08-05 (current, primary source).*

### 11. Anomaly-test the schema with sample data before shipping, and record any deliberate denormalisation.

Un-normalised tables fail three ways, each demonstrable with worked sample data:
modification anomalies (rename a school, update it in every student row, miss some, the
database now asserts two names), insertion anomalies (cannot add a school until a student
enrols, because the key requires one), deletion anomalies (delete the last student at a school
and the school's existence goes with them). Each one is silent data loss wearing a working
schema. Run all three tests against sample rows for every table. Denormalising is sometimes
right, but only as a recorded decision with the anomaly risk named, never as the accident of
skipping the step.

*Source: Kent, *A Simple Guide to Five Normal Forms in Relational Database Theory*, Communications of the ACM 26(2), 1983 (update, insertion and deletion anomalies). Rule id: D01-R11. Volatility: durable. Verified: 2026-08-05 (current, primary source).*

---

## D. Relationships and integrity in code

### 12. Take multiplicity from requirements, then place keys by rule: FK on the many side, association table for many-to-many.

Multiplicity comes from elicitation (one order can contain any number of items because the
stakeholders said so), never from the modeller's assumption; an unspecified UML attribute
multiplicity defaults to one, and an unspecified association end to many ('*'), which is rarely
what was meant either way. Once known, placement is mechanical: the one side's primary key goes
into the many side as a foreign key (City into Tour), and a many-to-many relationship gets a
dedicated association table carrying both primary keys as foreign keys (OrderDetails between
Order and Tour). Modelling an M:N directly on either side is the classic shortcut that later
forces a rewrite.

*Source: Microsoft Support, "Database design basics" (support.microsoft.com/en-us/access/database-design-basics, fetched and read this cycle), which states the foreign-key-placement convention directly: "To represent a one-to-many relationship in your database design, take the primary key on the 'one' side of the relationship and add it as an additional column or columns to the table on the 'many' side of the relationship," and the association-table convention in the same order-line-item shape this rule illustrates: "create a third table, often called a junction table, that breaks down the many-to-many relationship into two one-to-many relationships. You insert the primary key from each of the two tables into the third table," made concrete as "The Order Details table's primary key consists of two fields, the foreign keys from the Orders and the Products tables" (the source joins the last two clauses with a dash, rendered here as a comma per this file's punctuation convention). Corroborated by the companion Microsoft Support page "Guide to table relationships" (fetched and read this cycle). The multiplicity-from-elicitation half is now sourced to Halpin and Morgan, *Information Modeling and Relational Databases*, 2nd edition (Morgan Kaufmann, 2008; maintainer-supplied electronic copy, read and string-matched this cycle), whose method derives and checks constraints from real domain data rather than modeller assumption: "For each fact type, a fact table may be added with a sample population to help validate the constraints"; "For a binary fact type, three rows are enough for a significant population, so long as you pick the data carefully"; sample populations are printed "along with automatic verbalizations of the constraints for the domain expert to validate"; and "In any case, the uniqueness constraints should be at least as strong as those that apply in the real world." The same book qualifies this rule's defaults sentence: "If the multiplicity is not declared explicitly, it is assumed to be 1 (exactly one)" holds for UML attributes, but "If no multiplicity is supplied for an association role, '*' is assumed by default (unlike attributes, where 1 is the default multiplicity)", so unspecified-defaults-to-one is true of attributes and not of association ends; recorded in the revision log as an amendment candidate on 2026-08-07 and applied to this rule's text on 2026-08-08, both quotes having been re-string-matched against the maintainer-supplied copy that cycle. The 2026-08-06 checks (OMG UML 2.5.1 specification; Halpin's ORM overview, whose "an exhaustive treatment of the mapping procedure is beyond the scope of this paper" is answered by the full book) remain on record. Rule id: D01-R12. Volatility: durable. Verified: 2026-08-07 (current, vendor documentation, for foreign-key placement and the association table; current, published text, maintainer-supplied copy, for constraints-from-the-domain and the default-multiplicity qualification); 2026-08-08 (current, published text, maintainer-supplied copy, for the attributes-only qualifier as now written into the rule text: both multiplicity quotes re-string-matched against the held copy after whitespace normalisation, with a must-miss control returning zero hits).*

### 13. Match cascade behaviour to the relationship's semantics, never to the ORM's default.

Composition (the part dies with the whole: order lines with their order) is the only case
where cascade delete is correct. Aggregation and plain association (the contained object
outlives the container: a tour outlives any one order) must not cascade, or a delete on one
row silently destroys shared data. Classify the relationship first, then configure the ORM to
match. ORM tooling exposes this directly (delete-orphan style cascades for true compositions), but
configuration syntax moves between major versions, so the principle is durable while the
literal syntax must be checked against the current ORM's documentation.

*Source: OMG Unified Modeling Language, Version 2.5.1 (omg.org/spec/UML/2.5.1/PDF), clause 9.9.1 (AggregationKind) read together with clause 9.9.17: "Composite aggregation is a strong form of aggregation that requires a part object be included in at most one composite object at a time. If a composite object is deleted, all of its part instances that are objects are deleted with it." The specification genuinely supports both halves of this rule: the composition-versus-aggregation modelling distinction, and the cascade-on-delete claim. It also states the caveat this rule already carries, in its own words: "The precise lifecycle semantics of composite aggregation is intentionally not specified." Rule id: D01-R13. Volatility: principle durable, ORM configuration syntax fast. Verified: 2026-08-06 (current, primary source).*

### 14. Wrap every multi-step write in an explicit transaction with a real failure path.

A write that touches more than one table (the order and its order lines) is one logical action
and must commit or roll back as one, or a crash mid-sequence leaves the database asserting a
state that never existed. ACID is the grounding, and the working shape is:
mutate, commit inside a try/except whose exception path reports failure rather than flashing
success, and end every mutating POST handler with a redirect so a browser refresh cannot
replay the write. The anti-pattern is autocommit-per-statement code that works until the first
mid-sequence failure.

*Source: Haerder and Reuter, *Principles of Transaction-Oriented Database Recovery*, ACM Computing Surveys 15(4), 1983 (origin of the ACID acronym). Rule id: D01-R14. Volatility: durable. Verified: 2026-08-05 (current, primary source).*

---

## E. Access

### 15. Grant access by role, at the minimum scope the task needs.

Define named roles and grant to them, never privilege-by-privilege to individual users:
full CRUD on the tables a role owns, SELECT only on tables it merely references, and
column-scoped grants for sensitive columns (UPDATE on salary alone, not the whole staff
table). Use views to hide base tables from consumers who need a slice. When revoking, choose
RESTRICT or CASCADE explicitly rather than discovering the default by breaking a dependent
view. Caveat: SQLite implements none of GRANT/REVOKE, so on SQLite the
least-privilege boundary must live at the file-permission and application layer instead, and
pretending otherwise is a stated-nowhere gap.

*Source: Saltzer and Schroeder, "The Protection of Information in Computer Systems," Proceedings of the IEEE, vol. 63, no. 9, 1975 (web.mit.edu/Saltzer/www/publications/protection/), which states the least-privilege principle directly: "Every program and every user of the system should operate using the least set of privileges necessary to complete the job." This is the canonical origin of least privilege; grouping those privileges into named roles is standard practice built on the principle rather than something the 1975 paper itself specifies. Rule id: D01-R15. Volatility: medium (vendor SQL semantics). Verified: 2026-08-06 (current, primary source).*

---

## Known gaps

Stated per METHOD.md: a stated gap is a risk, an unstated one is a surprise. These are
subjects this rule set does not currently cover, not claims that they do not matter.

- **Indexing and query performance.** No coverage of index design, query optimisation
  or scaling.
- **Concurrency control and isolation levels.** ACID isolation is covered conceptually; locking,
  isolation levels and their trade-offs are absent.
- **Migrations and schema evolution.** Nothing on altering a live schema safely: no migration
  tooling, no expand-contract, no rollback discipline. Basis: observed silence.
- **Schema-less and non-relational design.** No NoSQL, time-series or graph coverage. Basis:
- **Backup and recovery practice.** Named as a countermeasure with no worked scenario.
- **ORM currency.** Durable principles are captured in rules 13 and 14; ORM configuration
  syntax moves between major versions and must be checked against current documentation.
- **Cross-store referential integrity and atomicity.** Real systems keep judgement in
  config files and facts in a database; this rule set does not yet address shared keys, write
  atomicity or drift across that seam. Basis: proving-run observation (findings F1, F6, F8
  all live on it).
- **Durability classification of user-authored data.** Whether a table holds source facts,
  derived data or user-authored corrections determines its backup and rebuild story, and this
  rule set does not yet ask the question. Basis: proving-run observation (finding F1 is the cost).
- **Exact numeric types for quantities.** Nothing here covers numeric type
  selection for money-adjacent arithmetic; the audited system runs FIFO subtraction on
  floating-point quantities. Basis: proving-run observation.
