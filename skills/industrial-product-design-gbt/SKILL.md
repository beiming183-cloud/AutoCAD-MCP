---
name: industrial-product-design-gbt
description: Create and review industrial design and physical product design from brief through concept selection, product architecture, proportion, form language, ergonomics, human factors, controls, interfaces, CMF, parametric CAD, refined native 3D, surface quality, product rendering, motion states, engineering validation, DFM, and revision-bound GB/T handoff. Use for consumer products, consumer electronics, appliances, smart-home hardware, enclosures, tools, equipment, rotating mechanisms, reference-image redesign, aesthetic direction, prototype planning, 3D product-quality review, or any task where Codex must design or improve a physical product before mechanical drafting and manufacturing release.
---

# Industrial Product Design with GB/T Handoff

Treat product design as the primary task and mechanical drafting as the engineering expression of a sufficiently resolved design. Decide who the product serves, what it must do, how it is used, why it has its form, and how it will be made and verified before detailed CAD and drawings.

## Core Principles

- Converge function, architecture, interaction, form, CMF, manufacturing, service, and safety together. Do not apply styling as a late shell over an unresolved package.
- Separate observation, inference, assumption, and confirmed requirement. Mark unknown inputs `TBD` and checks that did not run `NOT_EVALUATED`.
- Create meaningful alternatives. Color or fillet variants of the first idea do not count as concept exploration.
- Express aesthetics as testable form language: proportion, silhouette, mass hierarchy, geometry vocabulary, line and surface relationships, transitions, boundaries, detail density, CMF, and 360-degree coherence.
- Keep editable parametric native CAD as engineering truth. Renders, meshes, images, plots, and exchange files are derived evidence.
- Never infer manufacturing-critical dimensions, material performance, tolerances, safety distances, or compliance from appearance alone.
- Label every output by maturity. A concept is not a manufacturable design, and automated checks do not grant release approval.
- Never treat body count, entity count, dimension audit, geometry DRC, or save success as proof that proportion, form language, interface integration, motion logic, surface quality, or product realism is acceptable.

## Portability Contract

- Discover the available CAD application, automation bridge, renderers, parsers, and file formats before execution. Map capability-level steps to tools that actually exist.
- Never invent a tool call or verification result. After mutable CAD operations, read back actual entities, parameters, associations, hierarchy, and geometry.
- Preserve the user's source and unrelated geometry. Use a traceable working copy for major revisions; do not overwrite, remove, or replace user files without authority.
- Keep deliverables and evidence in standard CAD, exchange, image, and report artifacts that another tool or reviewer can reopen.

## Reference Routing

1. Before using web, standards, supplier, research, repository, or catalog evidence, read `references/research-authority.md`.
2. For every new design or substantial redesign, read `references/design-brief-and-gates.md`.
3. When a person uses, carries, installs, cleans, services, approaches, or works around the product, read `references/human-factors-and-usability.md`.
4. For form, product character, proportion, detail, or CMF, read `references/form-language-and-aesthetics.md`.
5. When the user supplies reference images, competitor images, sketches, or aesthetic feedback, read `references/reference-image-learning.md` and `references/aesthetic-profile.md`.
6. Before selecting CAD, surface, mesh, exchange, or rendering tools, read `references/backend-routing.md`.
7. For refined industrial-design 3D, ports, controls, rotating products, motion states, or presentation renders, read `references/industrial-design-3d-quality.md`.
8. For a large local CAD/JPG corpus, read `references/local-cad-reference-library.md`. Generate a private manifest with `scripts/catalog_reference_library.py`; use `references/local-reference-library.example.json` as the portable schema and do not commit personal absolute paths.
9. For concept selection, stage review, prototype review, or design audit, read `references/integrated-design-review.md`.
10. For a new consumer product, enclosure, appliance, controls, ports, cables, moving parts, or mains-powered product, also read `references/consumer-product-concept.md`.
11. For parametric parts, surfaces, assemblies, motion, or 3D delivery, read `references/cad-3d-modeling.md`; for evidence tiers and 2D/3D workflows, read `references/cad-workflows.md`.
12. For complex mechanisms or multi-system assemblies, read `references/complex-assembly-drafting.md`; for physical-performance claims, read `references/engineering-analysis.md`.
13. For DRC, DFM, fit, interference, tolerance, or release review, read `references/drc-review.md`; for GPS, GD&T, or inspection, read `references/gps-inspection.md`.
14. Before GB/T engineering drawings, read `references/gbt-drafting.md`; for AutoCAD or AutoCAD MCP execution, also read `references/autocad-mcp-workflow.md`.
15. For configurations, BOMs, revisions, MBD/PMI, dependencies, or formal packages, read `references/product-definition-release.md`.

Load only the references required for the current task.

For Chinese-language drafting work, prefer the matching file under `references/zh-CN/` when one exists; do not load both language versions unless comparing translations.

## Upstream and Downstream Contract

- Use this skill upstream to select product intent, architecture, proportion, interaction, form language, interfaces, motion states, CMF, and an approved 3D design revision.
- Use `mechanical-drafting-gbt` downstream to complete manufacturing definition, GB/T drawings, dimensions, fits, GPS/GD&T, BOMs, DRC/DFM, exchange verification, and release evidence.
- Do not enter manufacturing drawing work while the industrial-design gates for the claimed maturity remain failed or unevaluated.
- Do not duplicate the complete GB/T rule set here; never weaken or bypass it at handoff.

## Research and Tool Contract

- Record title, publisher, edition or commit, date, retrieval, license or access limit, applicability, and bounded claim for decision-driving sources.
- Treat official catalog pages as identity and scope evidence only when normative content is licensed or unavailable publicly.
- Pin open-source CAD, geometry, exporter, and validator versions; rerun conformance fixtures after upgrades.
- Route B-rep, surface, mesh, rendering, and drafting work to tools that can verify the required representation. Do not force one backend to impersonate missing capabilities.
- Keep human factors, product-design review, engineering analysis, geometry DRC, format validation, and external approval as separate evidence channels.

## Required Design Workflow

### 1. Classify the task and maturity

Classify the work as a new design, major redesign, local improvement, reconstruction, form study, structural design, mechanism design, engineering validation, or drafting handoff. Declare the target maturity:

- `discovery`: user, scenario, need, and constraints are still being defined.
- `concept`: product architecture, silhouette, and interaction directions are being compared.
- `design development`: a selected direction is integrating structure, form, CMF, and process.
- `engineering candidate`: critical interfaces, materials, processes, dimensions, and validation have evidence.
- `release candidate`: applicable checks ran and unresolved items and external approval status are explicit.

### 2. Build the design brief

Record users, scenarios, task flow, functions, interfaces, loads and environment, anthropometric constraints, size, cost, process goals, service, safety, product character, family cues, retained features, prohibited features, and deliverables. Classify each requirement as:

- supplied with authority;
- derived from evidence;
- assumed for exploration;
- `TBD`;
- contradictory and requiring a decision.

When inputs are missing, complete low-cost work that does not depend on them and constrain the maturity. Do not fill gaps with false precision.

### 3. Analyze references and existing product language

Use `reference-image-learning.md`. Record visible facts before interpreting intent. Add only user-confirmed preferences to the confirmed section of `aesthetic-profile.md`; keep agent inference labeled with evidence and confidence.

### 4. Establish system architecture and envelopes

Define functional blocks, energy, motion, signal and material flows, major components, purchased parts, interfaces, assembly direction, service paths, cables or hoses, thermal paths, motion sweeps, and hazard keep-outs. Create a named-parameter envelope skeleton before surface detail.

### 5. Generate and compare concepts

For a new design or major form change, create at least three materially different directions. Vary architecture, massing, center of gravity, posture, support, controls and interfaces, access, cables, or process strategy. Present each at comparable scale with:

- one-sentence design intent;
- user flow and critical actions;
- primary masses, proportion, silhouette, and visual center;
- internal architecture and critical envelopes;
- benefits, tradeoffs, risks, unknowns, and likely processes;
- links to the brief and aesthetic profile.

Treat hard constraints as pass or fail. Never hide a critical failure inside a weighted average.

### 6. Select a direction and define form grammar

Record selected, rejected, or combined concepts and rationale. If the user has not selected, deliver a comparison package and a recommendation. Continue along the recommendation only when selection authority is delegated or the task clearly permits provisional exploration; label the direction provisional.

Translate the direction into executable rules: primary, secondary, and tertiary masses; control lines; section strategy; curvature and radius families; seams and splits; openings and textures; interface integration; visual weight; CMF zones; and detail density. Check front, rear, sides, top, bottom, and expected use views.

### 7. Prove backend capability and document identity

Before refined 3D, route the task with `backend-routing.md` and pass `BACKEND_CAPABILITY_GATE` and `DOCUMENT_IDENTITY_GATE` from `industrial-design-3d-quality.md`. Require the active backend to support the operations and evidence needed by the claimed maturity. A primitive-only backend may create massing but may not claim refined product surfaces or product rendering.

After every open, create, switch, save, export, or render request, read back the active document ID, absolute path, revision, configuration, and output identity. If requested and actual identity differ, stop immediately, attempt one bounded recovery, and then switch to a verified backend or report `blocked`. Never audit or decorate the wrong document.

Run `scripts/check_document_identity.py` on structured expected and actual identities whenever the backend can export them. A script pass does not replace live readback; it proves only that the supplied identities agree.

### 8. Build a parametric skeleton and immediate preview

Model only overall dimensions, support footprint, primary and secondary masses, human contact zones, component envelopes, motion axes, interfaces, cables, thermal volumes, service space, and safety keep-outs. Immediately produce same-scale front, rear, side, top, bottom, and three-quarter views. Review proportion, silhouette, scale, posture, actions, interface count and direction, cable routing, center of mass versus support, assembly, and service.

Stop after the first massing preview. If the product still reads as stacked boxes, trays, exposed slabs, or unrelated primitives, return to architecture and form exploration. Do not continue by adding dimensions, labels, colors, or tertiary detail.

### 9. Co-develop structure, form, and process

Add one logical feature or subsystem at a time. Integrate wall strategy, stiffness, locating, fastening, sealing, clearances, motion, assembly sequence, tool access, service, routing, heat, and manufacturing direction. Add only enough detail to answer the current risk.

Keep appearance surfaces, structural surfaces, interfaces, and manufacturing datums traceable. Surface continuity, parting, draft, fasteners, seams, ports, and vents must support one design intent. Model ports and controls as real openings, recesses, flush panels, or justified proud features with purchased-part and use envelopes; a plate pasted onto a body is not interface integration.

### 10. Verify through separate evidence channels

Report separate dispositions for:

- design intent and aesthetic coherence;
- user flow, ergonomics, and access;
- function, architecture, motion, and interfaces;
- geometry, assembly, interference, and tolerance;
- manufacturing, assembly, service, and cost risk;
- safety and compliance architecture;
- strength, stiffness, thermal, fluid, fatigue, or dynamic performance.

Use `industrial-design-3d-quality.md`, `integrated-design-review.md`, and the applicable engineering references. Without loads, materials, boundary conditions, rule thresholds, and verification methods, limit the conclusion to geometric plausibility or a labeled screening estimate.

### 11. Draft, deliver, and iterate

Create GB/T drawings only after the design reaches the maturity the drawing claims. Organize sources, exchange files, BOMs, evidence, and unresolved items with `product-definition-release.md`.

After feedback, update the brief, selection rationale, parameters, risks, aesthetic profile, and affected validation. Repair the smallest responsible source operation; never hide a source problem in a render or drawing.

## Output Contract by Maturity

- `discovery package`: brief, contradictions, TBDs, user flow, constraints, and opportunities.
- `concept package`: at least three directions, common views, comparison, recommendation, and decision request.
- `design development package`: selected direction, form grammar, parameter skeleton, key sections, interface, motion, assembly and service strategy, CMF intent, and risks.
- `industrial-design 3D package`: revision-bound native model, parameter table, interface table, named motion configurations, same-camera review views, surface and rendering evidence, and user appearance disposition.
- `engineering candidate package`: editable CAD, critical interfaces and dimensions, material and process basis, draft BOM, applicable analysis, and DRC or DFM results.
- `release candidate package`: GB/T drawings, exchange files, configuration and BOM, verification evidence, TBDs, NOT_EVALUATED items, waivers, and external approval status.

In the final response, state the design decisions, their evidence, what the user confirmed, remaining assumptions, failed or unevaluated scope, current maturity, authoritative editable source, and the highest-value next test.

## Deterministic Checks

Use these scripts when their input contract is available:

- `scripts/check_document_identity.py`: compare requested and actual document, revision, configuration, units, and source identity.
- `scripts/check_interface_alignment.py`: compare 2D and 3D interface types, centers, sizes, orientations, and shared parameter source.
- `scripts/validate_motion_states.py`: verify joint definitions, required named states, limits, collision, cable, and interface dispositions.
- `scripts/compare_render_viewset.py`: verify source identity, camera invariance, image dimensions, nonblank output, and expected visual change.
- `scripts/check_model_manifest.py`: bind required native, table, render, evidence, drawing, and exchange roles to one revision and source hash.
- `scripts/catalog_reference_library.py`: create a stable read-only inventory for large local CAD and image libraries.

Treat every script as a narrow validator. A script pass never proves beauty, usability, physical performance, safety, manufacturability, or approval outside its declared fields.

## Aesthetic Learning Contract

- Treat user reference images as an evidence set, not as answers to copy.
- Treat CAD block libraries as configuration, view, silhouette, and context references only. Their geometry, dimensions, brands, materials, dynamic actions, and annotations are not engineering authority until independently verified.
- Extract repeated design logic across images and preserve counterexamples, context, and contradictions. One image does not create a permanent preference by itself.
- Mark only explicit user likes and dislikes `user-confirmed`. Mark agent conclusions `inferred` with confidence.
- Prefer transferring proportion, massing, line and surface logic, boundary strategy, detail rhythm, and CMF logic. Do not copy brand marks or distinctive trade identity unless the user explicitly requests it and has the right to use it.
- After material aesthetic feedback, update `references/aesthetic-profile.md` and its revision log. Do not claim persistent learning when no durable profile update occurred.
