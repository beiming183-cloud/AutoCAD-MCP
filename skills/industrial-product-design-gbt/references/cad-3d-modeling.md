# Mechanical CAD 3D Modeling

Read this reference for parametric parts, surfaces, assemblies, motion, imported geometry, 3D manufacturing output, and 3D presentation.

## Modeling Brief

Before editing geometry, record a concise internal brief:

- Task: new part, modification, assembly, reconstruction, inspection, motion, or export.
- Required native/source and exchange outputs.
- Units, origin, coordinate frame, primary datums, and projection orientation.
- Supplied dimensions, interfaces, materials, manufacturing process, and design rules.
- Named parameters and expected envelope.
- Part/component graph, mating interfaces, degrees of freedom, and motion limits.
- Assumptions, unresolved requirements, and validation targets.
- CAD application, geometric kernel, plug-in/API schema, generator/library versions, and external dependencies that can change rebuild or export behavior.

Dimensioned requirements outrank visual proportions. Flag contradictions instead of silently selecting one.

Before operating a live CAD session, inspect the active document, units, design type, root/component hierarchy, bodies, configurations, parameters, selections, suppressed/failed features, and unsaved state. Do not assume the visible viewport or previously mentioned document is the actual target.

## Source and Representation

- Prefer editable native features or source-controlled parametric code as the source of truth.
- Prefer B-rep solids for exact geometry, topology, dimensions, sections, booleans, and manufacturing exchange.
- Use meshes for rendering, slicing, scanning, and mesh-native workflows. Do not report mesh measurements as exact; state tessellation error or resolution.
- Preserve imported user geometry as a referenced component. Verify units and one known dimension before designing around it.
- Do not hand-edit derived STEP/STL/3MF/GLB/PDF output when an owning source exists. Change the source and regenerate explicit targets.
- Determine the CAD API's internal units and angle convention before sending values. Convert explicit task units at the API boundary and report the units returned by measurements.
- Create or identify a durable version before cross-document insertion, associative drawing publication, or other workflows that require immutable references.

## Part Modeling

1. Establish units, origin, principal planes, datums, axes, and named parameters.
2. Choose a stable base feature: extrusion, revolution, sweep, loft, sheet, or imported body.
3. Fully constrain design-driving sketches where the tool supports constraints. Check that profiles are closed, non-self-intersecting, and free of accidental duplicate segments.
4. Add one logical feature at a time and recompute after each operation. Treat a null/failed feature result as failure even when the API raises no exception.
5. Use native holes, fillets, chamfers, shells, ribs, drafts, and feature patterns. Create one seed plus a native linear/polar pattern instead of manually copying repeated geometry when practical.
6. Reference stable datum geometry or named interfaces instead of volatile face/edge indices when the CAD system permits it.
7. After feature reorder, suppression, boolean, fillet/chamfer, import healing, or parameter change, verify that downstream references still resolve to the intended semantic faces/edges. A rebuilt model can be geometrically valid while attached to the wrong topology.
8. Check body count, connectivity, validity, bounding box, volume, mass properties when material data exists, and every requirement changed by the feature. Compare requested and actual geometry so an empty cut, no-op update, unintended extra body, or auto-adjusted feature cannot pass on status alone.

For surfaces, additionally check trim boundaries, gaps, self-intersections, normal orientation, and the required positional/tangent/curvature continuity. Sew to a watertight shell before claiming a solid.

## Assembly Modeling

- Build each part in its local coordinate frame. Define mating faces, axes, points, connectors, or coordinate systems as its public interface.
- Plan the component graph and joints before adding detailed shells to a moving mechanism. Prove the kinematic skeleton at rest, mid-travel, both limits, and coupled/mirrored states first.
- Position structural parts with native mates/constraints or explicit named interfaces. Do not use unexplained final translations to make one pose look assembled.
- Give every separate body a physical role: fabricated part, fastener, bearing, seal, cable, purchased component, or documented analysis envelope.
- Verify the intended degrees of freedom, constraint convergence, mating-axis alignment, contact/clearance, fastener stack, service access, and component count.
- Verify every released configuration/variant separately. Confirm suppression state, part revision, quantity, mate set, envelope, mass properties, BOM applicability, and drawing-view linkage for the selected configuration.
- Require zero unintended positive-volume collisions. Distinguish touching contact from penetration and document any intentional bonded, welded, overmolded, or press-fit overlap.
- For motion, check swept or sampled interference at enough positions to catch thin obstacles. State step size and tunneling risk when using discrete samples.

## Manufacturing Intent

Select the intended process before applying process-specific features:

- Machining: stock, setups, tool access, internal radii, pockets, drills/threads, fixturing, and inspectability.
- Sheet metal/cutting: constant thickness, bend model, reliefs, flat pattern, closed cut contours, and process layers.
- Additive: build orientation, wall/feature size, clearances, overhang/support access, trapped volumes, mesh validity, and build envelope.
- Molding/casting: pull/parting direction, draft, wall transitions, ribs/bosses, undercuts, radii, shrink/allowance inputs, and tooling access.
- Hybrid assembly: purchased components, real interfaces, fastening/retention, cable routing, maintenance, and BOM.

Load `drc-review.md`; thresholds must come from the real process/material/machine/vendor rule set or remain `NOT_EVALUATED`.

## Exchange and Delivery Formats

- Native format: preserve feature history, associative drawings, materials, configurations, and assembly constraints when required.
- STEP: preferred neutral B-rep exchange for machined parts and assemblies. Select an application-compatible schema; preserve hierarchy, colors, and PMI only when the export/import chain supports them.
- IGES: use mainly as a compatibility fallback for curves/surfaces; verify healing and body closure after import.
- DXF/DWG: use for 2D drawings, profiles, and flat patterns; keep model geometry 1:1 and verify units/layers/entities.
- STL: mesh-only output for slicing or simple geometry transfer; it loses units, feature history, and assembly semantics unless the workflow adds them externally.
- 3MF: prefer over STL when the consumer supports units, multiple objects, metadata, materials, or printer project data.
- GLB/glTF: use for efficient 3D viewing, hierarchy, and materials; it is a visualization derivative, not manufacturing truth.
- PDF/SVG/PNG: use for plotted drawings and review evidence. Reopen every final export and inspect it.

Where practical, re-import the release exchange file and compare body count, envelope, units, hierarchy, and critical measurements with the source.

## Build and Review Loop

1. Build the smallest meaningful skeleton or base feature.
2. Run the model/recompute command and check structured errors.
3. Run incremental DRC on the new or changed feature.
4. Measure the requirements that feature owns.
5. Render risk-specific views and review them.
6. Convert visual concerns into deterministic measurements or topology checks.
7. Repair the smallest responsible source operation and repeat the failed checks.
8. After completion, run full DRC, generate outputs, reopen them, and compare with the source.

When scripting a live GUI session, restore temporary state after evidence capture: re-enable hidden components, disable temporary section analyses, clear temporary selections, and leave a useful review camera. Never close, overwrite, or discard an unsaved user document without explicit approval.

Do not rerender unchanged views or rerun expensive whole-model checks after an annotation-only change. Do rerun any downstream check whose source geometry or rule parameters changed.

## 3D Presentation

- Use the real validated model and a stable camera/axis convention.
- Provide one context isometric view and only the orthographic, opposite, underside, section, transparent, exploded, or close-up views needed to expose likely failure modes.
- For assemblies, keep an assembled context view even when an exploded view is included.
- Use material-driven colors to distinguish parts, not to hide boundaries or imply unsupported material specifications.
- Keep validation renders orthographic or low-perspective and edge-readable. Marketing-style rendering is a separate derivative and must not replace engineering review views.
- Name every view checked and refresh visible output after geometry changes.

## Validation Boundary

Geometry checks can establish shape validity, dimensions, topology, assembly plausibility, collision/clearance, and kinematic behavior under defined motion. They cannot establish strength, fatigue, vibration, thermal, fluid, wear, cost, or safety without the corresponding model, inputs, solver, and acceptance criteria.
