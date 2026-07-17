# Industrial Product Design Brief and Gates

Read this file for a new physical product, major redesign, or any task that must develop an ambiguous goal into a defensible industrial-design direction.

## Design Brief

Create a concise, revision-controlled decision brief:

- `product_and_problem`: what the product is, what problem it solves, and why change is needed.
- `people`: primary and secondary users, capability differences, PPE, reach, strength, vision, and foreseeable misuse.
- `scenarios`: setup, normal use, adjustment, cleaning, transport, storage, service, and abnormal states.
- `task_flow`: action order, inputs, feedback, error recovery, tools, and time-sensitive steps.
- `functions`: core and supporting functions, targets, and dependencies.
- `interfaces`: human, electrical, data, fluid, mechanical, mounting, environmental, and service interfaces.
- `constraints`: envelope, mass, cost, schedule, regulation, process, supply, and compatibility.
- `engineering_inputs`: loads, materials, life, environment, precision, noise, thermal, and reliability sources.
- `design_intent`: product character, brand or family cues, visual priorities, and unwanted expressions.
- `deliverables`: sketches, comparison boards, CAD, renders, prototypes, drawings, BOM, analysis, or release package.
- `authority`: who may confirm requirements, select concepts, accept risk, and approve release.

Mark each item `supplied`, `derived`, `assumed-for-exploration`, `TBD`, or `conflict`, with its source.

## PRE_CAD_DESIGN_GATE

Before detailed CAD, output a compact, reviewable record containing all of these fields:

- `user_scenarios`: who uses the product, where, for what tasks, and in which normal, abnormal, cleaning, transport, storage, and service states.
- `interface_inventory`: every interface type, count, orientation, access direction, and simultaneous-use assumption.
- `adapter_and_mating_envelopes`: plug, adapter, hand, tool, cable, connector, accessory, or mating-product dimensions; label every unverified value as an assumption or `TBD`.
- `cable_directions`: entry, exit, bend, strain-relief, pull, storage, snag, and motion-state routing.
- `motion_actions`: user action, axis, range, intermediate states, stops, locks, feedback, pinch zones, and state-dependent access. Use a justified `NOT_APPLICABLE` record for fixed products.
- `footprint_and_context`: occupied desk, floor, wall, appliance, or installation envelope, including surrounding use and service space.
- `stability_and_tip_risks`: support polygon, mass uncertainty, cable and user forces, moving masses, friction assumptions, edge loading, and the test still required.
- `product_character`: intended personality, brand or family relationship, visual priorities, unwanted expressions, and expected level of product realism.
- `design_dna`: immutable features, adjustable variables, prohibited substitutions, rationale, authority, and regression views.

Run `scripts/check_pre_cad_brief.py` when these fields are serialized. Presence is only the first gate: contradictory, unsupported, or unusable content still fails human review. Until this gate passes, permit only research, sketches, provisional envelopes, and concept massing; do not create detailed openings, fillets, production dimensions, or manufacturing claims.

## Immutable Design DNA

Record the defining innovation before optimization. Examples include equal-section column geometry, identical upper and lower footprints, non-tower posture, layered rotation, required interface families, or vertical use. Treat each item as pass/fail in concept comparison, massing review, and every later regression view.

Change an immutable item only when the recorded decision authority explicitly approves the change and the brief revision explains why. Do not trade away design DNA merely because a conventional architecture is easier to package, render, dimension, or manufacture.

## Conflict Priority

Use this order to expose conflicts, not to excuse weak design:

1. Safety, regulation, and irreversible risk.
2. Authorized hard requirements and controlled interfaces.
3. Core function and critical performance.
4. Ergonomics, assembly, service, and process constraints.
5. Cost, schedule, and supply goals.
6. Form, CMF, and preference goals.

Seek a new architecture when goals conflict. When no architecture resolves them, show the decision and its cost explicitly.

## Stage Gates

### G0 - Brief Ready

The brief passes `PRE_CAD_DESIGN_GATE`, can distinguish success from failure, protects immutable design DNA, and exposes major conflicts and unknowns. Before this gate, limit work to research, sketches, provisional envelopes, and comparison massing.

### G1 - Architecture Plausible

Functional blocks, flows, purchased parts, interfaces, mass and support, motion, routing, thermal paths, service, and hazard keep-outs form a coherent system. Do not build refined appearance surfaces before this gate.

### G2 - Concepts Comparable

At least three materially different concepts use common views and criteria. Critical failures and unknowns remain separate from preference scores.

### G3 - Direction Selected

The selected direction, rejected alternatives, rationale, decision authority, and provisional items are traceable.

### G4 - Skeleton Reviewed

The parameter skeleton demonstrates plausible proportion, human and product scale, interfaces, actions, motion, support, component fit, assembly, service, routing, thermal space, and safety envelopes.

### G5 - Design Integrated

Form, structure, CMF, splits, joints, process direction, and service strategy agree. Key sections and details preserve the overall form grammar.

### G6 - Engineering Evidence Adequate

Critical interfaces, dimensions, materials, processes, and applicable physical claims have sources and verification. Remaining `TBD` and `NOT_EVALUATED` scope is compatible with the advertised maturity.

### G7 - Prototype and Test Dispositioned

Applicable appearance, ergonomic, functional, or engineering prototypes answered predeclared questions. Failures changed the owning source design and were retested.

### G8 - Handoff Ready

Authoritative CAD, GB/T drawings, BOM, configurations, exchange files, evidence, and approval state agree. Agent output defaults to `candidate after human review` at most.

## Concept Comparison Dimensions

- scenario and task-flow fit;
- functional and architectural clarity;
- silhouette, recognition, and design intent;
- ergonomics, controls, and feedback;
- packaging efficiency, mass, and stability;
- interfaces, cables or hoses, and motion;
- assembly, service, and cleaning;
- process, cost, and supply risk;
- safety and compliance architecture;
- evidence gaps and iteration cost.

Use pass or fail for non-negotiable constraints. Weight only preferences, and record who set the weights.

## Prototype Strategy

Choose the cheapest prototype that answers the risk:

- proportion and silhouette: scale foam, card, fast render, or digital massing;
- grip, reach, and actions: 1:1 ergonomic model and task walkthrough;
- packaging and assembly: interface blocks, transparent shell, or exploded mockup;
- mechanisms and interference: kinematic skeleton or local functional rig;
- stiffness, thermal, noise, or life: controlled engineering prototype and measurement plan;
- CMF: real material and process samples under controlled light.

Before building, state the hypothesis, measurements, acceptance criteria, and how failure will change the design.
