# Integrated Mechanical Product Design Review

Read this file for concept comparison, direction freeze, skeleton review, detailed design review, prototype review, or release-candidate review.

## Review Baseline

Fix product or document ID, revision, configuration, maturity, brief revision, selected concept, authoritative CAD, reviewers, date, and current decision question. Use stable cameras, common scale, and current outputs.

Before using any returned geometry, audit, or render as evidence, prove that the active document ID and absolute path match the requested source. A successful open acknowledgement with stale active-document readback fails the review baseline.

## Separate Review Channels

Use `PASS`, `FAIL`, `WARNING`, `NOT_EVALUATED`, or `ERROR` for every channel with evidence. Never average away a critical failure.

### A. Design Intent and Form

- Do silhouette, posture, proportion, and visual center express the intended role?
- Are primary, secondary, and tertiary masses clear and coherent in 360 degrees?
- Do geometry, control lines, boundaries, splits, ports, patterns, and details share one grammar?
- Does CMF support function, touch, durability, and material honesty?
- Are matches and deliberate departures from `aesthetic-profile.md` explained?
- Does the product read as a resolved object rather than stacked boxes, trays, exposed slabs, or pasted interface plates?

### B. User, Task, and Ergonomics

- Are setup, use, adjustment, cleaning, transport, and service actions complete?
- Do reach, grip, posture, visibility, force, feedback, error recovery, and handedness have evidence?
- Were critical actions checked with a 1:1 mockup, anthropometric data, or user test? If not, mark them `NOT_EVALUATED`.

### C. Function and Architecture

- Are functions, flows, purchased parts, interfaces, motion, control, and states closed and coherent?
- Does every component have a role, connection, input and output, and service path?
- Do critical envelopes, motion sweeps, routing, thermal paths, and safety spaces avoid conflict?
- Are named motion states generated from one component hierarchy and transform source rather than duplicated loose geometry?

### D. Structure, Assembly, and Service

- Are load paths, locating, joints, fastening, sealing, stiffness strategy, and tolerance absorption plausible?
- Are assembly direction, order, tools, error-proofing, rework, disassembly, and consumable replacement feasible?
- Do seams and steps account for both appearance intent and production variation?

### E. Manufacturing and Supply

- Do material and process choices have volume, cost, surface, precision, and supply rationale?
- Are parting, draft, wall strategy, tool, bend, weld, support, datum, and inspection requirements applicable?
- Are purchased-part revision, envelope, mounting, rating conditions, and alternatives traceable?

### F. Safety, Compliance, and Environment

- Do mechanical, motion, electrical, thermal, fire, pinch, tip, edge, and foreseeable misuse risks have architecture and ownership?
- Are regulation, restricted materials, noise, energy, weather, cleaning, and end-of-life goals defined?
- A preliminary safety architecture is not certification. Do not claim safety or compliance without the authorized process.

### G. Engineering Performance and Evidence

- Were geometry, interference, clearance, tolerance, motion, and configurations checked under the applicable DRC?
- Do strength, stiffness, fatigue, thermal, fluid, vibration, life, and stability claims have inputs, models, convergence or sensitivity, and validation evidence?
- Renders, animations, and one solver run are not engineering proof.
- Entity count, body count, dimension audit, or geometry DRC is not industrial-design proof.

### H. Native 3D and Render Integrity

- Does the backend support the fillet, shell, surface, component, camera, and native-render evidence required by the claimed maturity?
- Do 2D callouts, 3D radii, openings, interface centers, and configurations share one parameter source?
- Are wall thickness, surface continuity, split lines, base, rear, underside, and service surfaces resolved or explicitly unevaluated?
- Do renders identify the current native document, revision, and configuration with stable cameras and correct depth and occlusion?
- Is any pseudo-3D or image composite labeled a diagram rather than a product render?

## Finding Schema

```text
finding_id:
channel:
status:
severity:
design_intent_or_requirement:
observed_or_measured:
evidence:
impact:
recommended_source_change:
owner:
recheck:
```

## Maturity Verdict

- `remain at current stage`: a blocker or major evidence gap remains.
- `advance with named risks`: blockers are cleared and risks have bounded validation plans.
- `candidate after human review`: applicable automated and agent reviews passed with limitations disclosed.
- `approved externally`: use only after an authorized external process approves the exact revision.

## Failure Review

After prototype failure, user rejection, assembly conflict, or gate escape, record evidence, effect, escaped gate, root cause, temporary containment, permanent source-design correction, prevention rule, recheck, and affected versions. Do not repair only a render, drawing, or symptom.
