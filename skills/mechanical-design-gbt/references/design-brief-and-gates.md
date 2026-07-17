# Mechanical Product Design Brief and Gates

Read this file for a new mechanical product, major redesign, or any task that must develop an ambiguous goal into a defensible design.

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

The brief can distinguish success from failure, and major conflicts and unknowns are visible. Before this gate, limit work to research, sketches, and provisional envelopes.

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
