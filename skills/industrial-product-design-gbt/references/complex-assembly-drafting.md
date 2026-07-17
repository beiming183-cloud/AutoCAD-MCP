# Complex Assembly Drafting

Read this reference for engines, gearboxes, pumps, machines, mechanisms, piping-rich layouts, or any drawing containing several interacting subsystems.

## Contents

- Delivery classification
- Pre-geometry contract
- Capability-based representation
- Parameterized skeleton and views
- Staged subsystem construction
- Connection and occlusion topology
- Line organization and visual review
- Acceptance gates and reporting

## Delivery Classification

Classify the requested result before drawing:

- `concept`: communicates architecture, envelopes, and major interfaces; not manufacturing authority.
- `teaching`: prioritizes explanatory visibility and may intentionally simplify geometry.
- `design-review`: supports measured layout, interfaces, motion/clearance, and traceable open issues.
- `manufacturing-assembly`: requires authoritative part geometry, assembly relations, item balloons/BOM, overall and installation dimensions, justified fits/tolerances, technical requirements, sections/details, and release evidence.

Do not allow visual detail to upgrade the classification. Label a 2D approximation `CONCEPT ONLY` or `NOT FOR MANUFACTURING` when authoritative 3D geometry, component definitions, or production requirements are absent.

Classification changes required evidence, not geometric truth:

- A `concept` or `teaching` drawing may omit production dimensions/tolerances, full BOM, process details, and engineering analysis, but must still pass basic topology, component ownership, shared-view-source, scale-truth, and visual-integrity gates.
- A `design-review` or `manufacturing-assembly` drawing additionally requires the applicable interface, BOM, GPS/inspection, DFM, configuration, release, and approval evidence.

Maintain a functional/component coverage matrix that supports the drawing title. For an object advertised as a four-stroke engine, identify the required cylinder/piston/crank train plus the intended abstraction or presence of valve timing, intake/exhaust flow path, ignition/injection, compression/combustion context, lubrication/cooling, and other scope-defining systems. If the supplied scope only supports the crank train, title it as a crank-slider mechanism or partial concept rather than a complete engine assembly.

## Pre-Geometry Contract

Create these artifacts before detailed entities:

1. Parameter table with names, values, units, sources, and status (`supplied`, `derived`, `assumed`, or `TBD`). Include overall envelopes, pitches, axes, repeated diameters/radii, interface locations, and view origins.
2. Component tree with stable IDs, subsystem ownership, representation level, expected entity/block/body count where meaningful, and parent/child relationships.
3. Common datum scheme with global origin, principal axes, center planes, mounting planes, shaft/cylinder axes, and view projection transforms.
4. Intended connection graph listing ports/nodes, mating or continuation pairs, allowed open endpoints, required tangency/continuity, and expected crossings or occlusions.
5. View mapping table stating projection method, authoritative model/skeleton revision, source feature/component IDs, view transform/direction, section plane, shared scale, and required cross-view correspondences.
6. Line/layer plan separating visible outlines, hidden lines, centers, hatches, dimensions, text, construction geometry, and non-plot references.
7. Acceptance table with rule IDs, applicability, tolerances, units, evidence method, and severity. Treat values such as `0.05 mm` as a project-specific candidate, not a universal threshold; use it only when scale, source accuracy, and user requirements justify it.

Resolve contradictions in these artifacts before creating detail. Unknown production data remains `TBD`; do not estimate it from appearance.

## Capability-Based Representation

Inventory safe tool capabilities before committing to 2D or 3D.

- Prefer a native parametric 3D assembly when solids, features, booleans, fillets/chamfers, patterns, constraints/mates, sections, and projected views can be created and verified.
- Use a shared parametric 2D skeleton when 3D is unavailable but exact coordinates, blocks/arrays, trimming/joining, constraints, and deterministic queries are available.
- Use a simplified 2D concept only when editing/constraint capabilities are limited. Reduce scope and disclose unavailable topology, projection, motion, and manufacturing checks.
- Use coordinate-first construction only for isolated simple primitives whose positions are fully derived. Use parameter/constraint-first construction for repeated geometry, interfaces, connected routes, and cross-view features; verify that the solver is neither under- nor over-constrained.

Never claim 2D and 3D equivalence merely because both artifacts exist. Never call separately estimated orthographic views a projected drawing.

## Parameterized Skeleton and Views

1. Establish the global envelope, principal centerlines, mounting planes, and subsystem envelopes.
2. Place repeated axes, bores, cylinders, pulleys, gears, and ports from named parameters or arrays. Do not set nominally equal radii independently by eye.
3. Define shared connection nodes before routing pipes, belts, manifolds, wiring, or linkages.
4. Generate every view from the same 3D model or transform the same parameter/skeleton dataset into each view.
5. For a 2D-only workflow, reconstruct critical features independently across views and compare projected coordinates, multiplicity, visibility, and section truth.

Reject the skeleton before detail when envelopes overlap impossibly, component axes disagree, required connections cannot be routed, or another view contradicts the shared parameters.

## Staged Subsystem Construction

Build in reviewable stages:

1. Global skeleton and main envelopes.
2. Primary housing/body outlines.
3. One coherent subsystem at a time, such as timing drive, intake, exhaust, flywheel, starter, lubrication, or mounting hardware.
4. Cross-system interfaces and occlusion trimming.
5. Centers, hidden lines, sections, dimensions, leaders, BOM/balloons, and notes as applicable.
6. Plot cleanup and release artifacts.

Use blocks, arrays, or native components for repetition. Keep each batch within one subsystem and transaction/staging boundary. After every stage:

- Query and record created handles/components.
- Read back every created/modified handle and compare the normalized request with actual type, role/layer, coordinates/parameters, ownership tags, and closure. Reject and roll back the entire stage on any unexplained postcondition mismatch; do not continue visual cleanup.
- Inspect structured command, rebuild, constraint, and transaction status. Stop on unresolved errors or warnings that imply missing cuts, failed joins, unsolved constraints, partial creation, or stale geometry; do not build later stages on top of them.
- Run changed-scope geometry and topology DRC.
- Compare affected shared parameters and views.
- Generate a fresh preview with unique identity or verified overwrite.
- Inspect both full assembly and local detail before continuing. Use measurements/queries to catch arithmetic, count, and relationship errors; use renders to catch orientation, occlusion, topology, and readability errors. Neither evidence channel is sufficient alone.
- When a stable before-state exists, render the same view/camera before and after the stage and inspect the visual difference. Require the changed region to match the intended subsystem and investigate missing or unrelated deltas.
- At the skeleton, major-subsystem, and pre-release milestones, show the preview and parameter/change summary to the user or designated reviewer when interactive review is available.

Do not create a large multi-system coordinate batch and postpone all inspection until the end. Repair the parameter generator or subsystem source instead of layering patches over incoherent geometry.

## Connection and Occlusion Topology

Build an actual endpoint graph and compare it with the intended connection graph.

- Snap intended joints exactly or within the declared connection tolerance; report near misses separately from true connections.
- Classify every open endpoint as intended port/termination, construction state, or defect.
- Require positional and tangent continuity where a pipe, manifold, belt path, or smooth housing transition demands it.
- Node real junctions explicitly. A visual crossing without a node is not a connection; an unintended crossing is a defect.
- Trim or hide occluded geometry according to view semantics. Do not leave full circles, pulley outlines, flywheels, starters, pipes, or internal detail visibly passing through foreground components.
- Detect protruding arc/line fragments beyond their owning component envelope, untrimmed overlaps, duplicate boundary ownership, and impossible component interpenetration.
- Verify equal-radius/concentric groups, repeated pitch/count, belt/gear alignment, and interface locations against named parameters.

For a concept drawing, require zero unexplained near-miss connections and zero unintended crossings before release. For manufacturing or design review, use the applicable interface and tolerance requirements rather than concept-only defaults.

Treat `DANGLING_ENDPOINT`, `INTERIOR_CROSSING`, `UNOWNED_LINE`, and `UNCLOSED_MATERIAL_BOUNDARY` per entity. A subsystem-level sentence such as "expected opening" is insufficient without the exact handle/location and design-contract node/role.

## Line Organization and Visual Review

- Assign every plotted entity an owning component/subsystem and semantic line class.
- Keep construction geometry on non-plot layers or remove it after dependent geometry is stable.
- Extend centerlines only by the applicable drawing convention and local scale. Stop them from traversing unrelated components.
- Show hidden lines only when they clarify the assembly; prefer sections when hidden detail creates noise.
- Control outline hierarchy so foreground ownership and occlusion remain legible in monochrome output.
- Remove unclassified fragments, redundant ribs/detail, stale alternatives, excessive hidden geometry, and unsupported helper lines before release.

Perform visual review after deterministic checks, not instead of them. Inspect full sheet, each subsystem, dense intersections, every view pair, and the final PDF/PNG. Convert every concern into a measurement, topology query, ownership check, or explicit unresolved finding.

## Acceptance Gates and Reporting

Do not release until all applicable gates pass:

1. Classification and required evidence match the advertised deliverable.
2. Parameter, component, datum, connection, view, and line-class artifacts are complete and traceable.
3. Geometry DRC passes for degeneracy, duplication, self-intersection, radii, tangency, envelopes, and repeated features.
4. Topology DRC passes for intended connections, dangling/near-miss endpoints, junctions, crossings, occlusions, and protrusions.
5. Every geometry entity has a component/semantic role, and every material boundary required by the representation is closed.
6. Every orthographic/section/end/detail view agrees with the authoritative model or shared parameter skeleton and its mapping table.
7. Drawing title, view labels, cutting/hatching behavior, and component/functional coverage describe the same deliverable class.
8. Actual PDF plot scale and title-block scale/declaration agree.
9. Stage previews and final plotted artifacts are current, uncached, legible, and free of unexplained visual noise.
10. Before/after geometry and visual differences are limited to intended components and relationships.
11. All missing capabilities and `NOT_EVALUATED` rules are disclosed; none is silently converted to `PASS`.

Report parameter assumptions, component coverage, connection and view results, preview identities, release blockers, and the exact limits of any 2D fallback. Treat a failed prototype as test evidence, not as a deliverable to be cosmetically relabeled.
