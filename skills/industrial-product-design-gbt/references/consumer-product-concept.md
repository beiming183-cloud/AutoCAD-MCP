# Consumer Product Concept and Safety Gates

Read this reference for a new or substantially redesigned consumer product, enclosure, appliance, powered accessory, desktop product, handheld product, or product whose ports, cables, controls, moving parts, appearance, ergonomics, or stability materially affect the design.

These gates control permission to proceed. Passing geometry DRC proves only that checked geometry satisfies its rules; it does not prove that the product concept is useful, attractive, ergonomic, stable, safe, certifiable, or ready to manufacture.

## Contents

- Scope and gate order
- Brief completeness
- Low-cost concept comparison
- Purchased-component evidence
- Mains and moving-part safety architecture
- Parametric skeleton and early preview
- Product design review
- Detailed modeling authorization
- Failure review

## Scope and Gate Order

Use this order before detailed dimensions or production CAD:

1. Complete the people, scenario, interface, action, cable, and product-intent brief.
2. Compare at least three low-cost outline concepts for new concepts or major form-factor changes.
3. Anchor purchased components to traceable physical or supplier evidence.
4. Establish the applicable mains, moving-part, fire, grounding, and compliance architecture.
5. Build a parameterized envelope skeleton and review an immediate preview.
6. Run the product-design review separately from geometry DRC.
7. Authorize detailed modeling only after all applicable blockers are resolved or explicitly returned to the user.

If the user supplies an approved, already-selected concept, record its identifier/revision and the authority for bypassing concept comparison. A minor local revision may retain that concept baseline; a major silhouette, interface, user-action, cable-route, support-footprint, or safety-architecture change reopens the applicable gates.

## Gate 0: Brief Completeness

Create a decision brief containing at least:

- `people`: target users, relevant anthropometric range, handedness, reach/strength limits, accessibility needs, clothing/PPE, and foreseeable nonexpert use. Unknowns remain `TBD`; do not invent a percentile population.
- `scenarios`: setup, normal use, temporary use, cleaning, storage, transport, maintenance, misuse that is reasonably foreseeable, and the intended indoor/outdoor/environmental conditions.
- `interfaces`: type and count of electrical, data, fluid, mechanical, ventilation, mounting, and service interfaces; mating objects and insertion/removal keep-outs.
- `actions`: ordered user tasks, grip/contact areas, viewing direction, actuation direction, expected force/torque source, feedback, recovery from error, and maintenance actions.
- `cables`: entry/exit direction, connector orientation, bend direction/radius source, strain relief, pull/trip exposure, slack storage, separation, and routing through every relevant product state.
- `product_intent`: desired product character, brand or family cues, environment, cost/size targets, visual priorities, and unacceptable forms.
- `adapter_and_mating_envelopes`: explicit dimensions or TBD assumptions for large plugs, adapters, connectors, hands, tools, and simultaneous use.
- `footprint_and_stability`: occupied desk or installation area, support polygon, mass uncertainty, cable/user forces, moving masses, and tipping test needs.
- `design_dna`: immutable innovation, adjustable variables, prohibited substitutions, authority, and regression views.

`CONSUMER_CONCEPT_GATE` fails while any scope-defining field is absent or contradictory. Before it passes, create only research sketches, block envelopes, and explicitly provisional concept dimensions. Do not create a detailed dimension drawing or describe the result as manufacturing-ready.

## Gate 1: Low-Cost Concept Comparison

For a new concept or major form-factor change, produce at least three materially different low-cost silhouettes before selecting a detailed architecture. Variants must differ in a useful decision dimension such as footprint, massing, orientation, port/cable strategy, control location, support method, service access, or product character; color-only variants do not count.

For each concept, provide the same evidence at comparable scale:

- Front/side/top outline or an equivalent simple 3D massing view.
- Human, mating-product, cable, and support-surface context where relevant.
- Port/control count and directions, principal action arrows, cable exits, and required keep-outs.
- Major benefits, tradeoffs, safety concerns, unresolved inputs, and likely manufacturing implications.
- A common comparison matrix covering scenario fit, action simplicity, appearance/product character, ergonomics, reach/access, cable management, stability, purchased-part fit, safety architecture, service, cost/complexity, and uncertainty.

Do not hide a critical failure behind a weighted average. Mark nonnegotiable constraints as pass/fail gates and score only comparative preferences. Preserve thumbnail/preview identity and decision rationale.

Ask the user or designated product owner to select, reject, combine, or redirect the concepts. When interactive selection is unavailable, stop at the comparison package unless selection authority was explicitly delegated. Record the selected concept and rejected alternatives; do not silently choose a favorite and proceed to detailed CAD.

## Gate 2: Purchased-Component Evidence

Use actual purchased-part envelopes and interfaces wherever a supplier part, cable, connector, power module, switch, motor, fan, bearing, foot, fastener, display, PCB, or certification-critical module constrains the design.

For each part, record manufacturer, part number, datasheet/drawing/CAD revision, retrieval date, configuration, units, and source. Separate:

- Nominal body envelope and tolerances.
- Mounting pattern, datum scheme, fastener engagement, and allowed mounting orientation.
- Connector mating/unmating, tool, latch, service, ventilation, and thermal keep-outs.
- Cable/hosing bend, strain-relief, motion, and routing envelopes.
- Mass, center of mass, loads, heat, and electrical/safety attributes only when documented.

`PURCHASED_PART_ENVELOPE` passes only when the dimensions used by detailed CAD are traceable to a current supplier source or a controlled physical measurement. A web thumbnail, approximate marketplace listing, or visually estimated model is not evidence. Keep unverified mounting dimensions as `TBD`; a provisional bounding envelope may support concept comparison but must be labeled and cannot authorize manufacturing geometry.

Apply the A/B/C/D dimension authority levels from `research-authority.md`. Do not draw regulated socket holes, protective shutters, terminals, USB module cutouts, installation holes, or rotating electrical contacts from memory, product photography, a public brand page, or a generic CAD block. Without level A controlled data or level C controlled measurement, reserve a named certified-module envelope and keep installation geometry `TBD`.

## Gate 3: Mains and Moving-Part Safety Architecture

Trigger `MAINS_SAFETY_GATE` when the product connects to mains, contains or switches mains, uses a mains-powered motor/heater, routes power through a moving or rotating interface, exposes hazardous energy, or has moving parts that can pinch, entangle, cut, eject, or destabilize the product.

Before detailed mechanical design, identify at least:

- Intended markets, product category, rated input/output, supply topology, environment, user/service access, and the owner responsible for selecting applicable standards and certification route.
- Protection class and grounding strategy; protective-earth continuity/termination where applicable; or the complete double/reinforced-insulation boundary when applicable.
- Isolation architecture, barriers, accessible conductive parts, insulation materials, pollution/overvoltage/material assumptions, and the source of required creepage/clearance values. Keep numeric distances `TBD` until the applicable rule set is selected.
- Overcurrent, short-circuit, stall, overtemperature, abnormal-operation, leakage/touch-current, ingress, discharge, and single-fault strategy as applicable.
- Fire enclosure, ignition sources, material/flame/temperature requirements, ventilation, hot surfaces, and separation from combustible or user-accessible regions.
- Moving/rotating hazards, guards, openings, interlocks, pinch/entanglement zones, ejected parts, braking/coast-down, service lockout, balance, and foreseeable loose cables/clothing/hair.
- Inlet, plug, switch, fuse, supply, motor/control, connector, cable, strain-relief, bend, pull, torque, and routing architecture, including separation from heat, sharp edges, and moving parts.
- Certification-critical modules and whether their approval scope, ratings, conditions of acceptability, mounting, ventilation, and surrounding enclosure are actually satisfied.
- Preliminary hazard log, verification plan, unresolved decisions, and the evidence needed from a qualified safety/compliance review.

Until this architecture is defined, limit output to an exterior concept and clearly separated hazard/keep-out envelopes. Do not release internal mounting geometry, claim manufacturability, or represent the design as safe, compliant, or certifiable.

### Rotating mains-power interface

When rotation crosses L, N, PE, or another hazardous-energy path, do not treat the joint as an appearance seam. Define and assign evidence owners for:

- conductor topology through every state and protective-earth continuity where applicable;
- contact technology, current path, contact resistance, voltage drop, temperature rise, and abnormal-current behavior;
- bearing and conductor separation, insulation, creepage/clearance source, touch protection, barriers, and debris containment;
- wear, oxidation, fretting, arcing, contamination, dust, lubrication compatibility, life cycles, and end-of-life failure mode;
- detents, stops, torque, misuse, over-rotation, cable twist, pinching, loose parts, service access, and assembly error;
- single-fault behavior, loss of PE, stuck or intermittent contact, conductive wear debris, overheating, and fire containment;
- prototype sequence and qualified electrical, thermal, endurance, abnormal-operation, and compliance review.

Until this architecture and its evidence plan exist, show only exterior form, rotation envelopes, and a clearly labeled hazardous-interface reservation. Do not imply that a visually plausible bearing ring or rotary contact is electrically viable.

Passing this gate means only that the mechanical concept has a traceable preliminary safety architecture. It is not product certification and does not replace applicable laboratory tests, risk assessment, electrical design review, or authorized approval.

## Gate 4: Parametric Skeleton and Early Preview

After concept selection, build only the first parameterized skeleton: overall envelope, support footprint, major masses, user/contact zones, ports, controls, cable routes, purchased-part envelopes, service volumes, safety barriers/keep-outs, principal axes, and view origins.

Immediately generate a fresh preview before detailed features, dimensions, rounds, vents, ribs, fasteners, or dense annotation. Use stable view/camera/scale and show enough orthographic and perspective context to check:

- Overall proportion, silhouette, massing, visual balance, and layout.
- Human/product scale, action sequence, reach, grip, viewing, and access zones.
- Correct interface count, positions, orientations, mating direction, and removal path.
- Cable exits, bend/strain-relief volumes, routing, slack, snag/trip exposure, and moving-state conflicts.
- Support footprint, approximate mass placement, expected center-of-mass uncertainty, and obvious tipping/sliding risk.
- Purchased-part fit, safety keep-outs, ventilation, service access, and conflicts between subsystems.

`EARLY_SKELETON_PREVIEW` fails on stale/cached output, missing context, implausible scale, contradictory layout, or unresolved critical interference. Return to the brief/concept rather than hiding structural problems under detail. Record preview identity and reviewer disposition.

## Gate 5: Product Design Review

Run this review as its own evidence channel. Geometry DRC, drawing DRC, a valid solid, and a polished render do not imply `PRODUCT_DESIGN_REVIEW: PASS`.

### Appearance and product character

- Compare proportion, silhouette, mass distribution, visual balance, line/edge continuity, control/port integration, seam strategy, material/color/finish intent, family/brand fit, and consistency from all intended viewing angles.
- Use the approved brief and selected concept as criteria. Appearance has no universal deterministic threshold; record alternatives, reviewer/user preference, and unresolved disagreements instead of inventing objective beauty scores.

### Ergonomics and actions

- Trace every setup, use, adjustment, cleaning, transport, and service action through reach, posture, clearance, grip, force/torque, visibility, feedback, error recovery, and handedness.
- Use applicable anthropometric or test evidence. Do not infer comfortable force, reach, or clearance from CAD appearance alone. Mark absent user trials, mockups, or data as `NOT_EVALUATED`.

### Access, service, and cables

- Check ports, controls, indicators, labels, consumables, fasteners, doors/covers, cleaning areas, and replacement items for direct access and tool/hand clearance.
- Evaluate mating/unmating sequence, cable bend/strain relief, pull direction, routing, separation, storage, snag/trip risk, connector confusion, service loops, and all relevant motion/configuration states.

### Stability and physical use

- Check support polygon, feet/contact conditions, center-of-mass range, cable/user forces, control actuation, moving masses, incline, vibration, surface friction assumptions, transport state, and foreseeable edge/corner loading.
- Use calculations or physical tests for release claims. A resting CAD model on a flat plane does not prove stability; unknown mass/friction/force inputs remain `TBD` or `NOT_EVALUATED`.

Report each category separately with evidence, assumptions, critical failures, and reviewer. Do not average a safety, reach, access, cable, or stability failure into an overall favorable score.

## Detailed Modeling Authorization

Authorize detailed dimensions and production CAD only when all applicable conditions are true:

- `CONSUMER_CONCEPT_GATE`: complete and selected by an authorized user/owner.
- `PURCHASED_PART_ENVELOPE`: all detail-driving supplier/measurement data verified; remaining `TBD` items are outside the detailed scope.
- `MAINS_SAFETY_GATE`: passed when triggered, with unresolved certification work explicitly separated from mechanical concept authorization.
- `EARLY_SKELETON_PREVIEW`: current preview reviewed and dispositioned.
- `PRODUCT_DESIGN_REVIEW`: no unresolved blocker in appearance intent, ergonomics, access, cable management, or stability for the intended maturity level.

Record gate status, evidence links/identities, reviewer/decision authority, date/revision, unresolved items, and reopened triggers. If a blocker remains, deliver the concept/decision package rather than a falsely precise manufacturing drawing.

## Failure Review

Use this structure after a gate escape, failed prototype, unsafe or unusable concept, incorrect purchased-part fit, misleading drawing, repeated tool failure, or release-blocking review finding:

```text
problem_and_scope:
evidence_and_detection_point:
impact_and_escaped_gate:
root_cause:
contributing_conditions:
temporary_containment:
permanent_corrective_action:
prevention_and_gate_change:
verification_fixture_or_test:
owner_and_due_state:
affected_artifacts_and_revisions:
```

`FAILURE_REVIEW_COMPLETENESS` requires evidence-backed root cause rather than restating the symptom, a bounded temporary containment, a permanent source/process correction, and a concrete recurrence-prevention control such as a rule, checklist item, fixture, test, schema constraint, or reviewer gate. Re-run affected checks and preserve the failed artifact as evidence when appropriate; do not cosmetically relabel it as a deliverable.
