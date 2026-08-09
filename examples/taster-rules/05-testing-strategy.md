# Domain 05: Choosing What to Test and How Much

*This is a taster copy of one domain from a maintained private rules pack (16 domains, 260 rules, each with a cited source and a review cadence), exported as a point-in-time snapshot. The maintained original, its revision history and its proving records live in the private repository; open an issue on this repository to ask about access.*

**Trigger:** you are deciding what or how much to test: choosing test levels, techniques or coverage, weighing testing effort against risk, structuring or maintaining a suite, planning load, stress or soak tests and their pass criteria, or judging whether testing is enough to ship.

**Load this when:** planning what to test for a feature or system, deciding how much
testing is enough, triaging where test effort goes, reviewing a test suite's shape, or
justifying testing investment to anyone.

---

## A. The economics: when and how much

### 1. Shift verification left; the cost of a defect multiplies by stage.

A defect costs less to fix the earlier it is caught: a defect found at requirements time
is cheap, and the same defect surviving to production is markedly more expensive, because
every later stage has built on top of it. Any check that can run earlier should: reviewing
a requirement is testing, reviewing a design is testing. Deferring quality work to a later
stage does not save its cost, it multiplies it.

The specific 1:5:10:20:100 cost multipliers often quoted for this effect are not stated in
the ISTQB syllabus, and no primary source for them was confirmed this session. Treat them
as illustrative industry folklore about the direction of the effect, not a measured ratio.

*Source: the ISTQB Foundation Level syllabus v4.0.1 (2024-09-15), Testing Principle 3 "Early testing saves time and money": "Defects that are removed early in the process will not cause subsequent defects in derived work products. The cost of quality will be reduced since fewer failures will occur later in the SDLC (Boehm 1981)." The syllabus states the direction of the effect and cites Boehm (1981) for it, but gives no specific multiplier figures. Boehm's own 1984 condensation, "Software Engineering Economics" (IEEE Transactions on Software Engineering, SE-10(1), January 1984; the maintainer-supplied copy cited at D05-R04), was searched this cycle for per-phase cost-to-fix figures and contains none: its Figure 3 plots software cost estimation accuracy versus phase, the narrowing range of estimation uncertainty, not defect cost-to-fix, and greps for relative-cost ratios and cost-to-fix phrasings returned zero against a fired must-hit control (COCOMO, 27 hits). The multipliers the syllabus attributes to Boehm (1981) therefore remain unverified against any Boehm text this register holds; the 1981 book itself was not available. Rule id: D05-R01. Volatility: medium. Verified: 2026-08-08 (current, primary source, for the directional claim; the specific cost multipliers remain unconfirmed and are flagged in the rule text as illustrative folklore, not sourced; checked Boehm's 1984 TSE paper, maintainer-supplied copy, this cycle, nothing on point).*

### 2. Accept that exhaustive testing is impossible, and design strategically instead.

Howden (1976): no general algorithm can produce a finite test set guaranteeing
correctness for arbitrary programs; only exhaustive testing guarantees absence of defects,
and it is computationally infeasible. This is not a licence to skip testing but the reason
a test suite must be designed around fault types and risk rather than chasing a coverage
number that cannot mean "correct".

*Source: Howden, "Reliability of the Path Analysis Testing Strategy" (IEEE Transactions on Software Engineering, vol. SE-2, no. 3, September 1976, pp. 208-215), Theorem 2: no computable procedure exists that, given an arbitrary program and function, generates a finite reliable test set for it. Also the ISTQB Foundation Level syllabus v4.0.1 (2024-09-15), Testing Principle 2 "Exhaustive testing is impossible": "Testing everything is not feasible except in trivial cases (Manna 1978)." Rule id: D05-R02. Volatility: durable. Verified: 2026-08-06 (current, primary source; both fetched and read this session).*

### 3. Finite suites work because faults couple; target known fault patterns.

The Coupling Effect Hypothesis is why testing survives Howden's proof: tests that catch
simple faults tend to also catch the complex faults coupled to them, because bugs in real
software are structured, not random. Design tests at known fault patterns and high-risk
areas and a manageable suite finds most defects.

*Source: Howden, "Reliability of the Path Analysis Testing Strategy" (IEEE Transactions on Software Engineering, vol. SE-2, no. 3, September 1976), fetched and read this session (see D05-R02), for the impossibility framing. The Coupling Effect hypothesis originates in DeMillo, Lipton and Sayward, "Hints on Test Data Selection: Help for the Practicing Programmer" (IEEE Computer, vol. 11, no. 4, April 1978, pp. 34-41); the 1978 primary text itself was fetched and read in full on 2026-08-07 from a public university mirror (inf.pucrs.br, direct fetch, HTTP 200; the publisher's copy remains paywalled), whose boxed statement of the hypothesis reads: "The coupling effect: Test data that distinguishes all programs differing from a correct one by only simple errors is so sensitive that it also implicitly distinguishes more complex errors", followed by "complex errors are coupled to simple errors. There is, of course, no hope of 'proving' the coupling effect; it is an empirical principle." Rule id: D05-R03. Volatility: durable. Verified: 2026-08-06 (impossibility framing: current, primary source); 2026-08-07 (coupling effect: current, primary source, public mirror of the 1978 text read in full).*

### 4. Find the bottom of the U-curve; under-testing defers cost, not spends less.

Testing cost rises with thoroughness while defect-correction cost falls; their sum has a
minimum. Do not assume that minimum sits short of perfection: the classical U-curve puts it
at an interior point, but Juran's handbook revises that reading and places the total-cost
minimum at 100 percent conformance, on the grounds that the interior optimum rested on
fallible-human limits and underestimated failure costs, while conceding that perfection is
not necessarily the most economic goal in the short run or in every situation. Either way,
cutting testing below the minimum increases total cost through production escapes,
emergency response and reputational damage. When cutting test effort is proposed, the
question is where current effort sits on the curve, not whether testing is expensive.

*Source: *Juran's Quality Handbook: The Complete Guide to Performance Excellence*, 7th edition (McGraw-Hill, ISBN 9781259643613), section 25.8, "Optimum Cost of Quality", whose model figure is reproduced from the 4th edition (JQH4, p. 4.19, the 1988 Juran and Gryna edition). It states the three-curve mechanism this rule applies to testing: "The failure costs. These costs equal zero when the product is 100 percent good and rise to infinity when the product is 100 percent defective"; "The costs of appraisal plus prevention. These costs are zero at 100 percent defective and rise as perfection is approached"; and the total-quality-cost curve as "The sum of curves 1 and 2". The handbook then revises where the minimum sits: "Figure 25.7 suggests that the minimum level of total quality costs occurs when the quality of conformance is 100 percent, that is, perfection", explaining that the earlier interior-optimum reading rested on fallible-human limits and underestimated failure costs, while conceding that "perfection is not necessarily the most economic goal for the short run or for every situation". That revision pulled against this rule's genuine-interior-minimum framing and was recorded in the revision log as an amendment candidate; the candidate was applied on 2026-08-09, and the rule text now carries the revised optimum in the handbook's own terms rather than asserting an interior minimum. Provenance: the section text was supplied by the maintainer from the institutional-subscription AccessEngineering copy and is quoted as supplied, not independently machine-fetched. Corroborated by Keller and Pyzdek, *The Handbook for Quality Management*, 2nd edition, excerpted at the authors' own site (qualityamerica.com, fetched and read 2026-08-07, machine-verified), which presents the same classical model and its limits. That test-effort sizing is an economic question is stated for software specifically by Boehm, "Software Engineering Economics" (IEEE Transactions on Software Engineering, SE-10(1), January 1984; maintainer-supplied copy of the paper, read and string-matched this cycle), which lists among the field's per-phase economic decision problems: "Integration and Test Phase: How much testing and formal verification should we perform on a product before releasing it to users?" and offers statistical decision theory as the technique for resolving how much information-buying is enough; it states no cost-of-quality curve. The defers-cost mechanism gains practitioner support from Construx, "Software Development's Defect Cost Increase Curve" (construx.com, fetched and read this cycle, after an earlier truncated tool fetch was recorded as a limitation): "By focusing on correcting defects earlier rather than later in the development of each feature, you can cut development costs and schedules by factors of two or more", with the curve stated to apply "whether the project is highly sequential (doing 100 percent of requirements and design up front) or highly iterative (doing only a small percentage of requirements and design at a time)"; that is the cost-deferral-and-amplification mechanism behind this rule's defers-cost-not-spends-less framing, stated in vendor training material, not a standard. The named consequences (production escapes, emergency response, reputational damage) remain common practice (checked ISO/IEC/IEEE 29119-1:2022, 2026-08-06, and the ISTQB CTFL syllabus v4.0.1 read in full plus ASQ's cost-of-quality guidance, retrieved via a text-reader proxy after direct fetches returned HTTP 403, 2026-08-07, and Boehm's 1984 paper on 2026-08-08, nothing on point for an optimum-effort curve in testing terms; re-checked 2026-08-09 against fresh downloads of the ISTQB CTFL syllabus v4.0.1 PDF (istqb.org) and the ISTQB CT-PT syllabus v1.0, and against ASQ's cost-of-quality page, again readable only through a text-reader proxy after a direct fetch returned HTTP 403, all three still nothing on point). The 2026-08-09 negative is machine-checked, not impressionistic: across the CTFL syllabus the strings "optimum", "optimal", "diminishing return", "how much testing", "appraisal cost", "prevention cost", "failure cost", "conformance" and "economics" all return zero, against fired must-hit controls ("cost of quality", 2 hits, both at Principle 3 and in a metrics list, and "Early testing saves time and money", 1 hit); the ASQ page returns zero for "optimum", "minimum", "curve" and "testing" against fired must-hit controls ("prevention costs", "appraisal costs" and "cost of poor quality", 3 hits each); the CT-PT syllabus returns zero for "cost of quality". A single must-miss control string was fired against every document in this cycle and returned zero everywhere. Rule id: D05-R04. Volatility: durable. Verified: 2026-08-07 (current, primary source, maintainer-supplied section text, quoted as supplied, for the cost-of-quality model and its revised optimum; common practice, checked as listed, nothing on point, for the testing-specific application and consequences); 2026-08-09 (rule text amended this cycle to carry the handbook's revised optimum, which this source line already quoted, so the amendment is a re-alignment of rule to existing evidence rather than a new claim; the testing-specific application of the cost-of-quality curve remains common practice, checked as listed, nothing on point).*

### 5. Never spread effort uniformly; weight by risk, complexity and defect clustering.

Complex code carries more faults per unit of testing; critical high-traffic code makes
each escaped fault dearer; and roughly 80% of defects cluster in 20% of the code (high
complexity, frequent modification, many contributors, external integrations, prior defect
history). Uniform effort across all code guarantees a suboptimal outcome. Risk analysis is
the input that makes test allocation rational, with the caveat that a repeatedly-tested
hotspot eventually yields diminishing returns and attention must rotate to unexamined
areas.

*Source: the ISTQB Foundation Level syllabus v4.0.1 (2024-09-15), Testing Principle 4 "Defects cluster together": "A small number of system components usually contain most of the defects discovered or are responsible for most of the operational failures (Enders 1975). This phenomenon is an illustration of the Pareto principle." Rule id: D05-R05. Volatility: medium (syllabus revises). Verified: 2026-08-06 (current, primary source).*

---

## B. What to test with

### 6. Combine black-box and white-box; neither is sufficient alone.

Black-box tests derive from the specification and verify functional correctness from the
user's perspective with no knowledge of internals; white-box tests exercise internal
paths, branches and conditions with full knowledge of the implementation. Each is blind to
the other's failure class: a mature strategy runs both.

*Source: ISTQB Certified Tester Foundation Level syllabus v4.0.1 (istqb.org, dated 15 September 2024, PDF downloaded and read in full this session), section 4.3.3: "A fundamental strength that all white-box test techniques share is that the entire software implementation is taken into account during testing, which facilitates defect detection even when the software specification is vague, outdated or incomplete. A corresponding weakness is that if the software does not implement one or more requirements, white-box testing may not detect the resulting defects of omission", and "Performing only black-box testing does not provide a measure of actual code coverage. White-box coverage measures provide an objective measurement of coverage and the necessary information to allow additional tests to be generated to increase this coverage". Each family's stated weakness is the other's strength, which is this rule's combine-both claim in the syllabus's own words. Rule id: D05-R06. Volatility: durable. Verified: 2026-08-07 (current, publisher documentation, syllabus read in full via agent dispatch).*

### 7. Classify every found defect, and let its class choose the next tests.

Three fault classes: computation (wrong calculation: bad formula, misplaced operator,
float precision), domain (wrong control-flow path: off-by-one, boundary comparison such as
a discount firing above $100 but not at exactly $100), and subcase (logic absent entirely
for a scenario, usually born from incomplete requirements, and hardest to catch because
tests are only written for specified behaviour). The class points at the technique that
catches its siblings: boundary value analysis for domain faults, requirements and risk
review for subcase faults.

*Source: Chillarege et al., "Orthogonal Defect Classification - A Concept for In-Process Measurements" (IEEE Transactions on Software Engineering, vol. 18, no. 11, November 1992; full text fetched and read this session at chillarege.com/docs/Papers/concept, quotes re-verified against the live page in a browser session): ODC "enables in-process feedback to developers by extracting signatures on the development process from defects"; "Orthogonal Defect Classification (ODC) essentially means that we categorize a defect into classes that collectively point to the part of the process which needs attention"; and, on class choosing the next move, "Had such in-process measurements on defect type been available, developers could compensate for problems by altering test strategy." Rule id: D05-R07. Volatility: durable. Verified: 2026-08-07 (current, primary source, read at the first author's own site).*

### 8. Layer the suite: unit for feedback speed, integration for interaction, end-to-end for journeys.

No single testing type guarantees quality and no single tool covers the need. The layered
shape is unit tests for fast developer feedback, integration tests for component
interaction, end-to-end tests for user journeys, each backed by tooling fit for that
layer. A suite that is all end-to-end is slow and undiagnostic; all unit, and integration
seams go unwatched.

*Source: Mike Cohn, "The Forgotten Layer of the Test Automation Pyramid," Mountain Goat Software (originally published 2009; page last updated 2023-02-07): "At the base of the test automation pyramid is unit testing... Automated user interface testing is placed at the top of the test automation pyramid because we want to do as little of it as possible," with a service/integration layer in between. This blog post is the commonly credited origin of the "test pyramid" shape this rule describes. Rule id: D05-R08. Volatility: durable. Verified: 2026-08-06 (current, primary source).*

### 9. Pair at least one requirements-side prevention technique with one code-side one.

The pesticide paradox: no single technique prevents all bugs, so combine. Requirements
side: test-first development, specification by concrete examples, formal test models that
expose ambiguity before code exists. Code side: reviews matched to risk (heavyweight
inspection for critical components, lightweight peer review for routine change), static
analysis, checklists encoding past defects, refactoring. Prevention and detection are
complementary budgets, not alternatives.

*Source: ISTQB Certified Tester Foundation Level syllabus v4.0.1 (as cited at rule 6, read in full this session), sections 3.1.2 and 3.1.3: "Static testing can detect defects in the earliest phases of the SDLC, fulfilling the principle of early testing. It can also identify defects which cannot be detected by dynamic testing"; "Static testing and dynamic testing practices complement each other"; and the syllabus's list of what static testing catches spans exactly this rule's pairing, naming both "Defects in requirements (e.g., inconsistencies, ambiguities, contradictions, omissions, inaccuracies, duplications)" and "Certain types of coding defects (e.g., variables with undefined values, undeclared variables, unreachable or duplicated code, excessive code complexity)". Rule id: D05-R09. Volatility: durable. Verified: 2026-08-07 (current, publisher documentation, syllabus read in full via agent dispatch).*

### 10. Remember absence of error is a fallacy; validate against user needs.

A system passing every test can still fail by not meeting the need the tests never
encoded. Requirements validation with actual users is part of testing, not a separate
discipline; a green suite proves conformance to the spec as written, nothing more.

*Source: the ISTQB Foundation Level syllabus v4.0.1 (2024-09-15), Testing Principle 7 "Absence-of-defects fallacy": "Thoroughly testing all the specified requirements and fixing all the defects found could still produce a system that does not fulfill the users' needs and expectations... In addition to verification, validation should also be carried out (Boehm 1981)." Rule id: D05-R10. Volatility: medium. Verified: 2026-08-06 (current, primary source).*

---

## C. Running it as a practice

### 11. Keep testing independent of authorship, then tailor it to context.

Developers testing their own code test what they expect to work; independence buys the
fresh perspective that challenges assumptions. But independence is not uniformity: the
right depth for a safety-critical device is wrong for a low-stakes feature, and the
context (risk profile, regulatory ground) sets the bar.

*Source: the ISTQB Foundation Level syllabus v4.0.1 (2024-09-15): Testing Principle 6 "Testing is context dependent" (section 1.3), and section 1.5.3 "Independence of Testing": "A certain degree of independence makes the tester more effective at finding defects due to differences between the author's and the tester's cognitive biases (cf. Salman 1995)... it is usually best to carry out testing with multiple levels of independence." Rule id: D05-R11. Volatility: medium. Verified: 2026-08-06 (current, primary source).*

### 12. Maintain the suite as the system evolves; tests are lifecycle artefacts.

Continuous development requires continuous testing: new tests arrive with new features,
obsolete tests are removed, regression suites are updated. A suite frozen at some past
version of the system gives green results about software that no longer exists.

*Source: ISTQB Certified Tester Foundation Level syllabus v4.0.1 (as cited at rule 6, read in full this session): testing principle 5, "Tests wear out. If the same tests are repeated many times, they become increasingly ineffective in detecting new defects (Beizer 1990). To overcome this effect, existing tests and test data may need to be modified, and new tests may need to be written", with the regression-suite counterpoint stated in the same breath ("in some cases, repeating the same tests can have a beneficial outcome, e.g., in automated regression testing"); and section 2.3, Maintenance Testing, which makes upkeep a lifecycle obligation across "Modifications, such as planned enhancements", "Upgrades or migrations of the operational environment" and "Retirement", requiring both "evaluating the success of the implementation of the change and the checking for possible regressions in parts of the system that remain unchanged". Rule id: D05-R12. Volatility: durable. Verified: 2026-08-07 (current, publisher documentation, syllabus read in full via agent dispatch).*

### 13. Run testing as a life cycle, and let analysis quality drive design quality.

Planning (scope, approach, risks), analysis (what to test, from requirements and design),
design (cases and data), implementation (environment, scripts), execution (run, log,
report), closure (coverage evaluation, artefact archival). The taught principle: time
spent understanding requirements and identifying test conditions pays back many times in
suite effectiveness; test design can never be better than the analysis feeding it.

*Source: the ISTQB Foundation Level syllabus v4.0.1 (2024-09-15), section 1.4.1 "Test Activities and Tasks," which describes this process as test planning, test monitoring and test control, test analysis, test design, test implementation, test execution and test completion, and which itself names "the ISO/IEC/IEEE 29119-2 standard" as providing further information about test processes. ISO/IEC/IEEE 29119-2:2021, "Software and systems engineering, Software testing, Part 2: Test processes" (second edition, 2021-10, joint ISO/IEC/IEEE standard): title page and structure fetched and read this session; the Scope clause itself is paywalled and not read, so it is cited catalogue-level only. Rule id: D05-R13. Volatility: durable. Verified: 2026-08-06 (ISTQB portion: current, primary source; 29119-2: catalogue-level, full text not read).*

### 14. When requirements churn, generate tests from a model instead of hand-maintaining them.

Model-based testing makes a formal model the single source of truth: requirements change,
the model updates, the cases regenerate. Then choose offline or online generation. Offline
generation produces the complete suite first and executes it later, which decouples
generation from execution and leaves a stand-alone suite that can be inspected, re-run for
regression and handed to a test management system; prefer it when stakeholders must review
behaviour before implementation exists, because the artefact they review is the thing that
runs. Online generation executes each test as it is generated and couples the tool tightly
to the system under test, which is what you want for nondeterministic systems and
long-running random traversals, but it leaves no suite to review. Building the model is
itself requirements validation: it forces ambiguities out before code. The online/offline
vocabulary and the requirements-validation framing are Utting and Legeard's;
ISO/IEC/IEEE 29119-1:2022 states neither, and nor does the ISTQB CT-MBT syllabus, checked
in an earlier sourcing cycle.

*Source: ISO/IEC/IEEE 29119-1:2022, clause 4.4.2: "Model-based testing (MBT) uses models to generate test cases systematically and automatically." The clause also states "test cases can quickly be generated from the model and automatically executed," which supports the model-as-source-of-truth premise (a formal or semi-formal model drives systematic, automatic test case generation). It does not describe a one-phase/two-phase MBT distinction and does not frame model-building as requirements validation. The requirements-validation half is now sourced: Utting and Legeard, Practical Model-Based Testing: A Tools Approach, Morgan Kaufmann, 2007, held in the source library and string-matched this cycle, section 2.7.4 (Requirements Defect Detection, pp. 51-52): "A sometimes unexpected benefit of model-based testing is that writing the model exposes issues in the informal requirements... So the modeling phase typically exposes numerous requirements issues." The one-phase/two-phase framing remained unsourced as worded: the book's own distinction is online versus offline MBT (pp. 29-30, "In online model-based testing tools, steps 2 through 4 are usually merged into one step, whereas in offline model-based testing, they are usually separate") plus a two-layer abstract-to-concrete test split, which is adjacent vocabulary but not that rule wording; it was recorded as a wording candidate for the September review (adopt the book's online/offline terms). That candidate was applied on 2026-08-09: the rule now uses the book's vocabulary, and the definitions it turns on are the book's own. Glossary, p. 406: "Online testing Model-based testing wherein the tests are executed and generated at the same time" and "Offline testing Model-based testing wherein the complete test suite is generated first, then executed later. It decouples the test generation and test execution environments and tools". The trade-off the rule now states is section 11.2, p. 376: "The last dimension is whether to do online or offline testing. Online testing is where tests are executed as they are generated, so the model-based testing tool is tightly coupled to the SUT. Offline testing decouples the generation and execution phases, so the test execution can be completely independent of the model-based test generation process", and, on which to pick, "Online testing is particularly good for testing nondeterministic SUTs and for long-running test sessions, such as overnight testing based on random traversal of a model. Offline testing has the advantage that it can perform a deeper analysis of the model to generate a small but powerful test suite, and the generated test suite is a stand-alone product that can be inspected, used repeatedly for regression purposes, put into a test management system, and so on". The reviewable-artefact half of the rule is that last clause, and the process-step version of the same distinction is at p. 30: "With online model-based testing, the tests will be executed as they are produced, so the model-based testing tool will manage the test execution process and record the results. With offline model-based testing, we have just generated a set of concrete test scripts in some existing language, so we can continue to use our existing test execution tools and practices". One page-number correction from the earlier note above: the steps-2-through-4 sentence sits on p. 28, not in the pp. 29-30 span recorded then, which covers the surrounding process discussion. Rule id: D05-R14. Volatility: durable. Verified: 2026-08-06 (current, primary source, PDF read directly via institutional subscription, for the model-driven test generation premise); 2026-08-08 (current, primary source, held copy, string-matched, for the requirements-validation framing; near-miss recorded, wording candidate, for the one-phase/two-phase distinction); 2026-08-09 (current, primary source, maintainer-supplied copy, text extracted with pdftotext and every quoted span string-matched under whitespace normalisation, for the online/offline vocabulary the rule now uses; three of the spans matched only after a de-hyphenation pass, the extraction having broken "test-ing", "gen-eration" and "inde-pendent" across lines, and both the raw and de-hyphenated results are reported rather than the successful one alone; must-miss control fired at zero, must-hit controls "model-based testing" at 386 hits and "finite state machine" at 39).*

### 15. Match model statefulness to the behaviour: stateless by default, stateful when history changes outcomes.

Start stateless for independent transactions (forms, search, login): same action, same
response. Escalate to a finite state machine (or extended FSM when data values drive
behaviour) only where history changes the outcome, as in the worked car-rental discount
model where deleting the same item produces different results depending on prior actions.
Use a mixed action-state model when both kinds of expressiveness are needed at scale, and
watch state explosion, the cost that makes stateful modelling a deliberate choice. The
finite-state-machine/extended-FSM terminology, the mixed action-state model, state
explosion and the car-rental worked example above are not stated in ISO/IEC/IEEE
29119-1:2022; treat them as common practice, not standards-sourced.

*Source: ISO/IEC/IEEE 29119-1:2022, clause 4.4.1: "The test coverage items are these valid transitions and so a test model needs to be chosen that clearly shows these transitions." The clause frames state models (state transition diagram or state table) as a notation chosen specifically when the required test coverage concerns transitions between states, supporting the premise that a state model is a deliberate choice tied to state-dependent behaviour rather than a default. It does not use finite state machine or extended finite state machine terminology, does not discuss a mixed action-state model or state explosion, and does not include the car-rental worked example. The FSM/EFSM terminology and state explosion are now sourced: Utting and Legeard, Practical Model-Based Testing: A Tools Approach, Morgan Kaufmann, 2007, held in the source library and string-matched this cycle. Glossary, p. 406: "Finite state machine (FSM) A model that has a finite number of states and a finite number of transitions between those states" and "Extended finite state machine (EFSM) A finite state machine plus some state variables. The transitions of the finite state machine are labeled with guards and actions". State explosion is the book's own drink-vending-machine motivation, section 3.3.1 (pp. 69-73): extending the plain FSM to all coins "would become rather large (41 nodes and more than 200 transitions)!", with "the use of data variables solves the problem of having too many states in the state machine" as the EFSM rationale. The mixed action-state phrasing and the car-rental worked example remain this rule set's own presentation (the EFSM definition, a state machine plus guarded actions, is the closest primary support for the mixed model). Rule id: D05-R15. Volatility: durable. Verified: 2026-08-06 (current, primary source, PDF read directly via institutional subscription, for the state-model-when-state-dependent premise); 2026-08-08 (current, primary source, held copy, string-matched, for FSM/EFSM terminology and state explosion; the mixed-model phrasing and worked example remain this rule set's own).*

## D. Performance testing

### 16. Pick the performance test type from the question you are asking, not from the phrase "load test".

Performance testing is not one activity. An average-load test answers "does the system
meet its targets on a normal day"; a stress test answers "what happens when the load is
heavier than usual"; a breakpoint test answers "where does it actually fail"; a soak test
answers "does it degrade or leak over days rather than minutes"; a spike test answers
"does it survive a sudden rush". Running a single generic run at expected volume and
calling the system performance-tested leaves the other four questions unanswered, which is
how a service that passes every pre-launch run still falls over on a launch spike or dies
quietly on day six from a memory leak. Name the question first, then choose the profile
that can answer it.

*Source: Grafana k6 documentation, testing guides, test types, all five pages fetched and read this session. Average-load testing, grafana.com/docs/k6/latest/testing-guides/test-types/load-testing/: "An average-load test assesses how the system performs under typical load. Typical load might be a regular day in production or an average moment." Stress testing, grafana.com/docs/k6/latest/testing-guides/test-types/stress-testing/: "Stress testing assesses how the system performs when loads are heavier than usual." and "Stress tests verify the stability and reliability of the system under conditions of heavy use." Breakpoint testing, grafana.com/docs/k6/latest/testing-guides/test-types/breakpoint-testing/: "Breakpoint testing aims to find system limits", and "A breakpoint ramps to unrealistically high numbers. This test commonly has to be stopped manually or automatically as thresholds start to fail." Soak testing, grafana.com/docs/k6/latest/testing-guides/test-types/soak-testing/: "This test type checks for common performance defects that show only after extended use. Those problems include response time degradation, memory or other resource leaks, data saturation, and storage depletion." Spike testing, grafana.com/docs/k6/latest/testing-guides/test-types/spike-testing/: "A spike test verifies whether the system survives and performs under sudden and massive rushes of utilization." The five type definitions are sourced; the choose-by-the-question framing and the named failure (one generic run answering none of the other questions) were recorded as common practice with no single canonical source. The framing half is sourced as of 2026-08-09, the named failure is not. Also the ISTQB Certified Tester Foundation Level Specialist Syllabus, Performance Testing (CT-PT), Version 2018 (9 December 2018, per its own revision history), fetched from istqb.org this cycle as a PDF and string-matched, which states the choose-by-the-question framing directly. Section 1.2, introducing its type list: "Different types of performance testing can be defined. Each of these may be applicable to a given project, depending on the objectives of the test." Section 4.1.1, on what a technical performance objective is: "Technical objectives, on the other hand, focus on operational aspects and providing answers to questions regarding a system's ability to scale, or under what conditions degraded performance may become apparent." The syllabus is also the independent taxonomy this domain's known gaps asked for. Its list is longer than k6's five and cut differently (performance, load, stress, scalability, spike, endurance, concurrency and capacity testing, section 1.2), with endurance testing occupying the position k6 calls soak: "Endurance testing focuses on the stability of the system over a time frame specific to the system's operational context. This type of testing verifies that there are no resource capacity problems (e.g., memory leaks, database connections, thread pools) that may eventually degrade performance and/or cause failures at breaking points." The two taxonomies do not agree on names or count, which is the point: what they agree on is that the type follows the objective, and that is the claim this rule makes. Rule id: D05-R16. Volatility: durable. Verified: 2026-08-07 (current, primary source, for the test-type definitions, five pages fetched and read this session; the choose-by-the-question framing is common practice; checked the k6 test-types index page and the Apache JMeter user manual best-practices page, 2026-08-07, nothing on point); 2026-08-09 (current, primary source, ISTQB CT-PT syllabus fetched and string-matched this cycle, for the choose-by-the-question framing, which is no longer common practice, and as a second-source cross-check on the test-type taxonomy; the named failure remains common practice, checked the k6 test-types index page and the Apache JMeter user manual best-practices page, 2026-08-07, and the ISTQB CT-PT syllabus, 2026-08-09, nothing on point).*

### 17. Set percentile-based pass/fail targets from user-facing objectives before the run, never from averages.

A run with no pass/fail criteria produces a chart and a judgement call, and the judgement
is always made after the numbers are known. Worse is a run judged on mean latency: an
average hides the tail, so a system where the typical request returns in 50 ms and one
request in twenty takes a second reports as healthy while a slice of users is having an
unusable time. Express the criteria as percentiles plus an error rate and a throughput
figure (for example p95 under 200 ms, p99 under 400 ms, errors under 1%), and derive the
numbers from what users need rather than from what the system currently does, before the
test runs. Copying today's measured performance into the target locks in whatever the
system happens to do now, including the parts of it that are already too slow.

*Source: Google, Site Reliability Engineering (the SRE Book), Chapter 4, "Service Level Objectives," sre.google/sre-book/service-level-objectives/ (fetched and read this session): "Monitoring and alerting based only on the average latency would show no change in behavior over the course of the day, when there are in fact significant changes in the tail latency"; "a high-order percentile, such as the 99th or 99.9th, shows you a plausible worst-case value, while using the 50th percentile (also known as the median) emphasizes the typical case"; "The higher the variance in response times, the more the typical user experience is affected by long-tail behavior, an effect exacerbated at high load by queuing effects."; "Start by thinking about (or finding out!) what your users care about, not what you can measure."; and, under the heading "Don't pick a target based on current performance": "While understanding the merits and limits of a system is essential, adopting values without reflection may lock you into supporting a system that requires heroic efforts to meet its targets, and that cannot be improved without significant redesign." Also Google, The Site Reliability Workbook, "Implementing SLOs," sre.google/workbook/implementing-slos/ (fetched and read this session): "In order to capture both the typical user experience and the long tail, we also recommend using multiple grades of SLOs for some types of SLIs." and "For example, if 90% of users' requests return within 100 ms, but the remaining 10% take 10 seconds, many users will be unhappy." Also Grafana k6 documentation, "Thresholds," grafana.com/docs/k6/latest/using-k6/thresholds/ (fetched and read this session): "Thresholds are the pass/fail criteria that you define for your test metrics."; "Often, testers use thresholds to codify their SLOs."; worked expectations on that page include "95% of requests have a response time below 200ms", "99% of requests have a response time below 400ms" and "Less than 1% of requests return an error", expressed as "p(95)<200" and "rate<0.01", and on failure "k6 would exit with a non-zero exit code". Rule id: D05-R17. Volatility: durable. Verified: 2026-08-07 (current, primary source; three sources fetched and read this session).*

### 18. Test performance on infrastructure that resembles production, and automate it where the pipeline can carry it.

A run is a measurement of the environment it ran in. A developer machine or a
cost-reduced QA stack differs from production in instance sizes, data volumes, caching and
autoscaling policy, so a capacity number taken from it and multiplied up is arithmetic, not
evidence. Use the closest environment available, and where it is not production-like, read
the results as a regression signal against a baseline rather than as an absolute capacity
figure. On placement: small automated runs belong in the pipeline for performance-critical
paths, but a run that takes tens of minutes does not belong in a pipeline that deploys
automatically, so schedule the long profiles instead. On gates, start one rung below
warning: verify the gate is actually invoked. A gate that is configured, thresholded and
even defended by its own tests, but that no pipeline or schedule runs, is not a
warning-level gate, it is decoration, and it reads as coverage while providing none. The
first question about any gate is which scheduled thing runs it, and only once that is
answered do the other two rungs apply: introduce pass/fail gates as warnings that trigger
investigation, then tighten them into blocking criteria as they earn trust. A gate nobody
believes gets bypassed, a gate that fires on noise gets deleted, and a gate nobody runs is
never believed or bypassed at all, only forgotten.

*Source: Grafana k6 documentation, "Automated performance testing," grafana.com/docs/k6/latest/testing-guides/automated-performance-testing/ (fetched and read this session): "Given the infrastructure does not closely match the production environment, this type of QA environment is unsuitable for assessing the performance and scalability of the application." (on environment representativeness, of a QA environment); of pre-production environments generally: "Typically, the previous testing environments do not perfectly mirror the production environment, with differences in test data, infrastructure resources, and scalability policies."; and "Testing in production provides real-world insights that cannot be achieved in other environments." On pipeline placement: "Moreover, note that the load test duration often takes between 3 to 15 minutes or more; thus, introducing performance testing into CI/CD significantly increases the time of the release process." and "This is another reason we advise not to run larger tests in pipelines meant for automatic deployment." On gating: "However, setting up reliable quality gates is challenging when testing thousands or millions of interactions."; "Unless your verification process is mature, do not rely entirely on Pass/Fail results to guarantee the reliability of releases."; and "If unsure, start utilizing Pass/Fail results to warn about possible issues for deeper investigation, and continuously tweak the criteria until becoming confident." The baseline-and-compare reading of non-production results, and the observation that an unbelieved gate gets bypassed, were both recorded as common practice with no single canonical source; the first of the two is sourced as of 2026-08-09, the second is not. Also the ISTQB Certified Tester Foundation Level Specialist Syllabus, Performance Testing (CT-PT), Version 2018 (9 December 2018, per its own revision history), fetched from istqb.org this cycle as a PDF and string-matched, which cross-checks this rule from the certification-body side rather than the tool vendor's. On environment representativeness, section 4.2.8: "It is important to ensure the test environment is as close to the production environment as possible." and, giving the reason this rule calls a scaled-up capacity number arithmetic rather than evidence, "It is important to remember that performance is a non-linear function of the environment, so the further the environment is from production standard, the more difficult it becomes to make accurate projections for production performance. The lack of reliability of the projections and the increased risk level grow as the test system looks less like production." Section 4.1.2 concedes the scaled-down case this rule addresses: "The test environment is often a separate environment that mimics production, but at a smaller scale." The same section then expects the test plan to state "how the results from the performance testing will be extrapolated to apply to the larger production environment", which sits in tension with this rule's arithmetic-not-evidence stance; the syllabus asks for the extrapolation to be planned and disclosed while its own section 4.2.8 warns the projection grows unreliable as the environment diverges, and this rule takes the stricter of the syllabus's two positions rather than the more permissive one. On the baseline-and-compare reading, section 4.1.2 defines the comparison this rule asks for, "A baseline is a set of metrics used to compare current and previously achieved performance measurements. This enables particular performance improvements to be demonstrated and/or the achievement of test acceptance criteria to be confirmed."; section 4.4 makes it a reporting item, "Results of a baseline test that serves as 'snapshot' of system performance at a given time and forms the basis of comparison with subsequent tests." (the syllabus sets the word snapshot in its own quotation marks, rendered here as single quotes inside the quoted span); and section 3.4 states the regression reading of a pipeline run outright, "The main objective of performance tests as part of CI is to ensure a change does not negatively impact performance." The uninvoked-gate rung added to the rule text on 2026-08-09 is not from any published source and is not claimed as one: it is the late-rules proving run's own observation, recorded before the amendment (proving-runs/2026-08-07-late-rules-05-11-14.md, finding F3 and amendment candidate A2). The run found, and string-matched here, "The Lighthouse configuration asserts four numeric thresholds across four routes, a shell script stands up the app and runs the tool against them, and a test module locks the thresholds against silent relaxation. VERIFIED: no workflow invokes it.", against a control of seven workflow files swept; its reading is "a gate nobody runs cannot be believed or bypassed, only forgotten. It reads as coverage in the repository and provides none."; and candidate A2 proposed the wording the rule now carries, "a configured but uninvoked gate is not a warning-level gate, it is decoration, and that the first question about any gate is which scheduled thing runs it." Rule id: D05-R18. Volatility: medium (vendor guidance and CI platform practice revise). Verified: 2026-08-07 (current, primary source, for the environment-representativeness, pipeline-placement and gate-maturity claims; the baseline-and-compare reading and the bypassed-gate observation are common practice; checked the Apache JMeter user manual best-practices page, 2026-08-07, nothing on point); 2026-08-09 (current, primary source, ISTQB CT-PT syllabus fetched and string-matched this cycle, for environment representativeness and the baseline-and-regression reading, which is no longer common practice; proving-run evidence, this register's own run, for the uninvoked-gate rung, which is an observation rather than a published claim; the unbelieved-gate observation remains common practice, checked the Apache JMeter user manual best-practices page, 2026-08-07, and the ISTQB CT-PT syllabus, 2026-08-09, which has no quality-gate treatment at all, the string "quality gate" returning zero and "pass/fail" zero against fired must-hit controls "performance testing" at 103 hits and "load generation" at 15, nothing on point).*

---

## Known gaps

- **Narrow origin.** These rules were distilled from a single body of teaching material, so
  they lack the cross-checking that multiple independent sources would give. The re-sourcing
  cycle partly addresses this, but the shape of the domain still reflects its origin.

- **Equivalence partitioning and boundary value analysis are named, not worked.** The
  techniques appear as concepts; their step-by-step procedures are not covered here and should
  be taken from a testing text.

- **No coverage-metric practice.** Coverage tools are named in a tool list; nothing
  teaches choosing or interpreting a coverage threshold.
- **No test-data management, mocking/stubbing discipline, or flaky-test handling.**
  Absent from the captured material.

- **Performance testing is three rules deep, not a treatment.** Section D covers choosing
  the test type, setting percentile pass/fail targets, and where a run may live. It does
  not cover workload modelling (deriving realistic scenarios, think time, data volumes and
  arrival patterns from production traffic), capacity planning, load-generator saturation
  (the common false result where the client, not the system under test, is the bottleneck),
  correlating a failed run to a cause (that is domain 14), client-side and front-end
  performance, or the observability a run needs in place before it starts. Take those from
  a performance engineering text.

- **Section D leans on one vendor's documentation.** The test-type taxonomy and the
  pipeline guidance behind D05-R16 and D05-R18 come from Grafana k6's testing guides, a
  tool vendor writing about its own tool, cross-checked only against the Google SRE
  material for the percentile half. The Apache JMeter user manual was checked and covers
  none of it. Partly closed on 2026-08-09: the ISTQB CT-PT syllabus v1.0 now cross-checks
  the type-follows-the-objective framing in D05-R16, and environment representativeness and
  the baseline reading in D05-R18, from a certification body rather than a tool vendor, with
  its own longer and differently cut type taxonomy. The k6 pages remain the only source for
  the five specific type definitions D05-R16 lists and for the pipeline-placement advice, so
  a formal standards-side cross-check (the 29119 series or an equivalent) is still
  outstanding.
