---
name: industrial-product-design
description: Define, compare, model, review, and mature industrial products before manufacturing drafting. Use for new products, major redesigns, enclosures, appliances, controls, ports, mechanisms, configurable assemblies, product-quality 3D, CMF, ergonomics, service, motion states, native renders, prototype planning, or any task where the product itself must be designed rather than merely documented.
---

# Industrial Product Design

Make the product credible before making the drawing polished. Integrate user tasks, architecture, form, interfaces, motion, CMF, manufacturing, service, and safety into one revision-bound design.

## Portability Contract

- Keep the workflow independent of Codex, Claude Code, a CAD vendor, and a specific MCP namespace.
- Discover actual tools and capabilities before selecting a modeling route. Never invent a tool call or validation result.
- Keep editable native CAD as the design authority. Treat STEP, GLB, STL, renders, PDF, and DXF as derived evidence.
- Preserve source files and unrelated geometry. Use document IDs, revisions, configurations, and absolute paths to bind every mutation and output.
- Mark missing inputs `TBD`, skipped checks `NOT_EVALUATED`, and assumptions explicitly. Do not turn missing evidence into a pass.

## Skill Boundary

- Use this skill upstream for product intent, concept selection, architecture, proportion, interaction, form language, interfaces, configurations, motion, CMF, and design maturity.
- Use `mechanical-drafting-gbt` downstream for GB/T views, dimensions, fits, GPS/GD&T, BOMs, manufacturing notes, DRC/DFM, plotting, exchange verification, and release packaging.
- Do not duplicate drafting rules here. Do not start release drawings while the product-design gates for the claimed maturity are unresolved.

## Required Workflow

1. **Classify maturity.** Choose `discovery`, `concept`, `design-development`, `engineering-candidate`, or `release-candidate`. State what the requested output can and cannot prove.
2. **Write the brief.** Record users, scenarios, functions, critical actions, interfaces, environment, loads, anthropometry, size, cost, process, service, safety, appearance intent, retained features, prohibited features, and deliverables. Separate supplied, derived, assumed, contradictory, and `TBD` requirements.
3. **Build architecture.** Define functional blocks, energy/signal/material flows, purchased-component envelopes, motion axes, interfaces, assembly direction, cables/hoses, thermal paths, service volumes, and hazard keep-outs.
4. **Generate concepts.** For a new product or major redesign, compare at least three materially different architectures or silhouettes at the same scale. Color, fillet, or trim variants of one idea do not count.
5. **Select and codify.** Record the selected direction and why. Define primary/secondary/tertiary masses, control lines, section strategy, curvature/radius families, seams, openings, interface treatment, detail density, and CMF zones.
6. **Pass the capability gate.** Read [cad-capability-contract.md](references/cad-capability-contract.md). A primitive-only backend may prove packaging but may not claim refined surfaces, parametric assembly, or product rendering.
7. **Create the skeleton.** Model named overall dimensions, datums, component envelopes, support footprint, human contact zones, motion axes, interfaces, cable paths, thermal volumes, service access, and safety keep-outs. Preview immediately from common views.
8. **Develop by subsystem.** Add one logical feature or subsystem at a time. Read back actual feature identity, parameters, hierarchy, material role, bounds, and associations after each mutable stage. Roll back on unexplained mismatch.
9. **Model configurations and motion.** Use component instances and transforms, not duplicated loose geometry. Define fixed components, mates/limits, degrees of freedom, named configurations, motion envelopes, and interference evidence. See [configuration-motion-render.md](references/configuration-motion-render.md).
10. **Review through separate channels.** Judge design intent, ergonomics, architecture, geometry, motion, manufacturing, service, safety, and physical performance separately. A valid B-rep or attractive render is not an overall pass.
11. **Handoff deliberately.** Freeze the selected revision and configuration, list `TBD` and `NOT_EVALUATED` items, then invoke `mechanical-drafting-gbt` for manufacturing definition and release evidence.

## Hard Gates

Read [design-gates.md](references/design-gates.md) for gate inputs and evidence. At minimum enforce:

- `BRIEF_GATE`: critical users, tasks, interfaces, constraints, maturity, and unknowns are recorded.
- `CONCEPT_GATE`: materially different concepts were compared or an authorized supplied concept is identified.
- `ARCHITECTURE_GATE`: component envelopes, flows, interfaces, motion, service, and hazards are coherent.
- `DOCUMENT_IDENTITY_GATE`: every mutation targets the requested document ID and expected revision.
- `BACKEND_CAPABILITY_GATE`: required modeling, selection, assembly, analysis, and render operations are actually available.
- `SKELETON_GATE`: same-scale multi-view review passes before detail.
- `FORM_GATE`: 360-degree massing, transitions, seams, interfaces, and detail hierarchy express one intent.
- `CONFIGURATION_GATE`: named states change component transforms without copying uncontrolled geometry.
- `VERIFICATION_GATE`: geometry, interference, wall/draft/surface, exchange, render, and physical checks are reported separately.
- `HANDOFF_GATE`: authoritative source, revision, configuration, units, dependencies, assumptions, and unresolved items are explicit.

Any failed identity, transaction, topology, or output-integrity gate is a hard stop. Attempt one bounded recovery, then switch to a verified backend or report `blocked`.

## Evidence Rules

- Require `requested`, `actual`, and `diff` for mutable CAD operations. Treat unexplained postcondition differences as failure.
- Treat the semantic registry, native entity registry, and B-rep as one commit unit. When a native handle is erased or replaced, invalidate or replace its registry record in the same transaction; stale feature records block release.
- Prefer stable semantic feature IDs, named datums, and component IDs. Do not rely on volatile face or edge sequence numbers for automation.
- Require atomic transactions for multi-entity or multi-feature stages. A partial result is not a recoverable success.
- Require rollback evidence for entity/feature counts, registry rows, document revision, layers, and temporary outputs. An exception message alone does not prove rollback.
- Verify B-rep validity, body count, bounds, mass properties where meaningful, wall thickness, draft, surface continuity, and interference according to maturity and process.
- Label AABB overlap and sampled motion checks as broad-phase only. Distinguish containment, contact, permitted crossing, intentional motion overlays, and exact B-rep interference.
- Re-import required exchange formats and compare units, body count, bounds, critical dimensions, and configuration identity.
- Use deterministic named cameras and native/offscreen rendering for product review. Window screenshots and `SendKeys` are diagnostic fallbacks, not release evidence.
- Bind every render to document ID, revision, configuration, camera, style, resolution, output path, content hash, clipping state, visible components, and `material_render_verified`. A linework image cannot pass a shaded/material review.
- Prepare revision-bound section planes/cut sets and exploded component transforms before requesting section or exploded evidence. Camera movement alone does not create either state.
- Treat `FIT`/`NTS` and fixed numeric plot scales as mutually exclusive. Verify paper, orientation, PDF MediaBox, viewport scale, and title-block declaration before release.
- Preserve a machine-written `reports/round.json` for each validation round with identity, transaction/rollback, readback diffs, DRC, render truth, plot truth, artifact hashes, cleanup, and lessons.
- Never infer strength, fatigue, thermal, fluid, electrical, safety, or compliance performance without inputs, method, thresholds, and evidence.

## Workspace Contract

Use or map these portable folders under the project root:

```text
specs/       brief, requirements, parameters, component/interface tables
scripts/     reproducible generators and validation entry points
models/      authoritative native CAD and exchange models
drawings/    downstream manufacturing drawings
previews/    revision-bound multi-view and configuration renders
reports/     design reviews, validation, interference, re-import evidence
outputs/     approved delivery package
```

Do not retain disposable CAD/PDF/PNG test artifacts after validation. Preserve compact JSON evidence, logs, hashes, and test summaries.

## Final Response

State the current maturity, selected design decisions, user-confirmed requirements, assumptions, authoritative source/revision/configuration, checks that passed, checks that failed or were not evaluated, produced files, and the highest-value next test.
