# CAD Design Rule Check and Review

Read this reference for every DRC, DFM, fit, interference, tolerance, conversion, or release review.

## Principle

A deterministic validator owns pass/fail. The agent chooses the correct rules, parameters, evidence, and review depth; it does not replace a missing measurement with visual confidence.

There is no universal `manufacturable` result. Process, material, machine, tooling, supplier, quantity, and inspection capability determine the applicable rule deck and thresholds.

Keep geometry/drawing validity and product-design validity as separate verdicts. A model may pass topology, dimensions, interference, and export checks while still failing concept selection, appearance intent, ergonomics, reach/access, cable management, stability, or safety architecture.

## Validator Contract

Every rule must define:

- Stable rule ID, domain, description, severity, and applicability condition.
- Source of the requirement: user specification, GB/T/other named standard with edition, organization rule, vendor rule, or clearly labeled typical guidance.
- Explicit units, threshold, inclusivity at the boundary, tolerance, datum/direction, and configuration.
- Required representation and preconditions: native model, valid B-rep solid, watertight manifold mesh, structured DXF, drawing view, or assembly.
- Deterministic algorithm and any sampling density/error bound.
- Result schema containing status, measured value, threshold, margin, units, location/entity IDs, parameters used, and evidence artifact.
- Known false-positive and false-negative conditions.

Never bury thresholds in prose or code constants without naming their source. Never report a sampled or mesh-derived result as exhaustive/exact.

## Status and Severity

Use these result states:

- `PASS`: preconditions passed and measured evidence satisfies the applicable rule.
- `FAIL`: measured evidence violates an applicable rule.
- `WARNING`: evidence indicates risk or a nonblocking recommendation, not a disguised failure.
- `NOT_EVALUATED`: required inputs, rule deck, representation, or tool capability are absent.
- `ERROR`: the validator could not complete reliably.

Keep severity separate from confidence. A critical rule with weak evidence is `NOT_EVALUATED`, not a low-confidence pass.

A `WARNING` is not blanket permission to release. Any warning involving unresolved topology, component ownership, view source/semantics, document identity, or plot scale is release-blocking until every instance is individually classified with evidence or formally waived. Never explain a group of handles as "expected" without proving intent for each handle/location.

## Exclusions and Waivers

Never suppress, exclude, downgrade, or waive a finding merely to make a gate pass. Require each waiver to record the exact rule/finding, scope/entities, technical justification, compensating evidence, author/reviewer when available, date/revision, and expiry or revalidation condition. Re-run the applicable checks after any edit and report stale or unverifiable waivers as findings.

## Gate Sequence

### Gate 0: intake and rule applicability

- Identify revision, units, coordinate system, configuration, material, manufacturing process, supplier/machine limits, and intended use.
- Identify CAD/kernel/API/rule-deck versions, source commit/native version, external dependencies, and the exact variant/configuration/effectivity under review.
- Select the applicable rule deck and record unknown inputs.
- Establish which source/model/drawing is authoritative and whether derived files are stale.
- Build a cross-reference matrix when several artifacts define the design: requirement/specification, native model, drawing, BOM, standard/purchased-part data, and release export. Missing or contradictory mappings are findings even when each file is internally valid.

### Gate 1: representation validity

- Native model: recomputes without suppressed/failed unexpected features or dangling references.
- B-rep: valid topology, expected solid/shell count, positive volume where intended, consistent orientation, and no unintended open boundaries.
- Mesh: watertight/manifold when required, oriented normals, no self-intersections, degenerate faces, isolated fragments, or nonfinite coordinates; record tessellation tolerance.
- DXF/DWG: supported entity types, explicit `$INSUNITS`, expected `$ACADVER`, layers/layouts, no corrupt blocks, and no unsupported proxy objects that affect the review.
- PDF/image: classify vector/raster/mixed and record extraction/OCR confidence.
- Dependency closure: required Xrefs, fonts, plot styles, templates, linked data, standard-part libraries, custom objects, and scripts are present/versioned or explicitly external; missing dependencies that change geometry, annotations, or plots fail representation validity.

Do not run downstream exact-geometry rules on a representation that failed its preconditions.
Run the native CAD/EDA application's own geometry checker, rebuild, DRC, or ERC when available. External scripts add targeted evidence; they do not replace a required native-tool verification run.

### Gate 2: universal geometry DRC

- Open or self-intersecting profiles, consecutive duplicate vertices, duplicate closing vertices, zero-length or below-tolerance segments, duplicate/reversed entities, sliver faces, zero-thickness regions, unintended overlaps, disconnected material, and accidental extra bodies.
- For every polyline-like profile, evaluate each consecutive vertex pair and the closing pair when closed. Report the entity handle, segment index, measured length, tolerance, units, and location for every degenerate segment; do not let closed-state or entity-count checks mask it.
- Build an endpoint/topology graph for linework that represents pipes, manifolds, belts, boundaries, or other connected systems. Compare actual nodes and edges with the intended connection graph; detect dangling endpoints, endpoints within the near-miss tolerance but not coincident, unintended branches, non-noded crossings, overlaps, and protruding segments.
- Check required tangency at line/arc and arc/arc transitions using positional and angular tolerances. Check concentric/equal-radius groups against shared parameters rather than visual similarity.
- Bounding box, mass/volume where justified, symmetry, concentricity, tangency, parallel/perpendicular intent, pattern count/pitch, and critical dimensions.
- Minimum distance, thickness, radius, angle, and feature size only against supplied/applicable thresholds.
- Imported geometry unit/orientation sanity and comparison against at least one known dimension.

Instantiate these semantic topology rules when applicable:

- `DANGLING_ENDPOINT`: every open endpoint records handle/subentity, coordinates, `component_id`, `line_class`, nearest intended node, and `intentional_open_end`. It passes only when the design contract explicitly permits that exact open end.
- `NEAR_MISS_CONNECTION`: endpoints within the connection search tolerance but outside the coincidence tolerance require measured gap and intended-node comparison.
- `INTERIOR_CROSSING`: every non-noded crossing records both entity owners/roles and whether it is a permitted visual crossing, an occlusion requiring trim/hide, or a defect.
- `UNOWNED_LINE`: every geometry entity must belong to a component/subsystem or an allowed annotation/construction/table role; release requires zero unexplained entities.
- `UNCLOSED_MATERIAL_BOUNDARY`: each intended material boundary must form the specified closed loops after occlusion/view semantics are applied.
- `PROTRUDING_OR_OCCLUDED_GEOMETRY`: detect fragments beyond the owning envelope and foreground/background lines that were not trimmed/hidden as intended.

Concept and schematic drawings must still have zero unexplained findings for these rules. Reduced detail is not reduced topology integrity.

### Semantic intent and graphical exceptions

Geometry DRC does not automatically understand design semantics. Before classifying open ends or crossings, create an intent manifest that assigns each relevant entity a component, view, configuration or motion state, line class, graphical role, and owning region. Include explicit flags for arrowheads, motion ghosts, break lines, leaders, hatches, construction geometry, title blocks, tables, and permitted visual crossings.

Use these per-finding dispositions:

- `DEFECT`: unintended geometry or presentation error that must be repaired at the owning source.
- `INTENTIONAL_GRAPHIC`: an individually identified graphical element whose open end or crossing is required by the declared drawing semantics.
- `VALIDATOR_LIMITATION`: the checker cannot resolve the declared view, state, clipping, or annotation semantics; preserve the raw finding and require bounded human review.
- `WAIVED`: an authorized, revision-bound exception with justification and revalidation condition.
- `UNRESOLVED`: insufficient evidence; release-blocking.

Do not delete or globally suppress raw findings to obtain a clean count. A motion arrow, ghosted state, or title-block crossing may be intentional only when its exact handles/entities, role, view, and location match the manifest. If the checker cannot distinguish them, report the raw count, the individually reviewed dispositions, and the residual unresolved count; do not claim that geometry DRC is fully clean.

### Gate 3: drawing DRC

- Projection method, view identity/alignment, 2D/3D and cross-view consistency, section truth, hidden/center lines, and feature multiplicity.
- Apply the GB/T anchor and simplified-representation rules from `gbt-drafting.md` when applicable, including projection method/symbol/title-block agreement, Chinese text rendering, and thread/gear/spline/spring/rolling-bearing representation rule IDs.
- For separately constructed views, project every shared datum, axis, component envelope, repeated feature, and connection node into the other views and compare at a declared tolerance. Matching page appearance is not evidence that the views describe the same assembly.
- Classify every plotted line as visible outline, hidden line, centerline, hatch, dimension/leader, or justified annotation. Flag unclassified geometry, centerlines extending into unrelated components, construction geometry on plotting layers, and hidden/detail lines that create unsupported visual noise.
- Dimensions agree with geometry; no duplicate, contradictory, stale, detached, closed-chain, or visually ambiguous requirements.
- Fits, tolerances, datum references, feature-control frames, surface texture, notes, material, scale, revision, and title-block data are complete only when justified.
- When GPS/GD&T or formal inspection is in scope, apply `gps-inspection.md`; verify semantic feature associations, datum precedence, tolerance-zone meaning, inspection feasibility, and model/drawing/PMI agreement rather than symbol appearance alone.
- Text, symbols, fonts, lineweights, hatches, viewports, and plot scale survive final export.
- Compare stable-view before/after renders for revisions. Confirm that the visual difference covers the intended components/annotations only; investigate an empty delta, unrelated changed regions, or camera/layer/scale drift before accepting it as evidence.
- Manufacturing and inspection information is sufficient for the intended release scope; unresolved data is explicit.

Apply these drawing-semantic rules:

- `VIEW_SOURCE_CONSISTENCY`: every principal/section/end/detail view maps to the same authoritative 3D model or shared parameter skeleton and declared transform; separately eyeballed proportions fail.
- `VIEW_SEMANTICS_CONSISTENCY`: labels such as section, cutaway, detail, schematic, or principle diagram agree with actual cutting-plane, hatch, visibility, and abstraction behavior.
- `DELIVERABLE_SCOPE_IDENTITY`: the title and advertised object are supported by the component/functional coverage matrix. A crank-slider outline with valve/port/ignition context removed cannot be presented as a complete four-stroke engine assembly drawing.
- `PLOT_SCALE_CONSISTENCY`: title-block scale agrees with actual layout/viewport/PDF transform. Fit-to-page output cannot carry `1:1` or another numeric engineering scale; use an approved `NTS`/non-scale declaration and record plot mode `FIT`.
- `FINAL_VISUAL_INTEGRITY`: stable final views contain no unexplained isolated lines, protruding fragments, unowned thin lines, open material boundaries, or foreground/background occlusion errors.

### Consumer-product design review overlay

For a new or substantially redesigned consumer product, enclosure, appliance, powered accessory, or product materially shaped by ports, cables, controls, moving parts, appearance, ergonomics, or stability, also apply `consumer-product-concept.md` before Gate 4 and before detailed/manufacturing release:

- `CONSUMER_CONCEPT_GATE`: people, scenarios, interface/port count, user actions, cable directions, and product intent are complete; at least three materially different low-cost concepts were compared for a new concept/major form change, unless an approved selected concept and bypass authority are recorded.
- `PURCHASED_PART_ENVELOPE`: every detail-driving purchased-part body, mounting interface, connector, service/thermal keep-out, and cable envelope is traceable to a current supplier source or controlled physical measurement; unverified installation data remains `TBD`.
- `MAINS_SAFETY_GATE`: when mains, hazardous energy, or moving/rotating hazards apply, a preliminary supply/isolation/grounding/fire/motion-protection/compliance architecture exists with an owner, hazard log, evidence plan, and unresolved certification work. Failure limits the artifact to exterior concept and hazard/keep-out envelopes.
- `EARLY_SKELETON_PREVIEW`: a fresh preview immediately after the parameterized skeleton proves plausible proportion/layout, interface count/directions, people/actions, cable routes, support footprint, purchased-part fit, and safety/service keep-outs before detail and annotation.
- `PRODUCT_DESIGN_REVIEW`: appearance/product character, ergonomics/actions, reach/access/service, cable management, and stability have separate evidence and dispositions. Do not average a blocker into an overall score or infer this pass from geometry DRC or render quality.

Treat a product-design gate failure as release-blocking for the advertised maturity, even when all universal geometry rules pass. Keep subjective appearance decisions traceable to the approved brief and authorized reviewer; mark missing anthropometric, user-test, mass, friction, force, or safety evidence `NOT_EVALUATED` rather than favorable.

### Gate 4: assembly and motion DRC

- Expected component/body count and intended connectivity graph.
- Mate/constraint convergence, remaining degrees of freedom, stable mating datums, axis alignment, and interface orientation.
- Broad-phase candidate detection followed by exact/narrow-phase interference; distinguish contact from positive-volume penetration.
- Minimum clearances, tolerance stack, press/transition/clearance fit intent, fastener access, insertion/removal path, and service envelope.
- Motion at rest, midpoint, limits, and coupled/mirrored states. For discrete collision checks, record step size and tunneling risk.
- Every cable, tube, fastener, bearing, seal, and purchased component has a plausible receiving/retention interface.

### Gate 5: process-specific DFM

Load only rules applicable to the selected process.

#### CNC machining

- Tool access and orientation, internal corner radius versus actual tool radius, pocket/slot depth-to-width or tool reach, minimum web/wall under cutting load, drill depth and breakout, thread depth/relief, undercuts/special tooling, setup/fixturing access, deburring, and inspectability.

#### Sheet metal and 2D cutting

- Constant thickness, valid bend radius and direction, minimum flange, bend relief, hole-to-edge/bend distances, bend collision, flat-pattern validity, grain direction when relevant, closed cut profiles, no duplicates/overlaps, kerf/process layers, minimum feature/web, and part nesting separation.

#### Additive manufacturing

- Build volume/orientation, minimum wall and feature, hole compensation/clearance, overhang/support threshold, bridge span, support removal, trapped resin/powder volumes and drains, enclosed voids, bed contact, mesh manifoldness, and slicer/profile compatibility.

#### Injection molding and casting

- Pull/parting direction, core/cavity side, draft, undercuts and side actions, wall thickness/uniformity, thick transitions and sink/warp risk, ribs/bosses, shutoffs, radii, gate/ejection/tool access where data exists, and supplied shrink/machining allowances.

#### Fabrication and welding

- Joint access, fit-up gaps, weld tool/torch access, distortion-sensitive geometry, edge preparation, standard stock availability, bend/roll feasibility, fixture access, and post-weld machining/inspection allowances.

Geometry alone can flag process risk; it does not predict mold flow, distortion, residual stress, print quality, cutting force, or tool life without appropriate analysis.

### Gate 6: export and release

- Generate explicit deliverables from the authoritative source.
- Reopen/re-import each critical exchange artifact and compare units, envelope, body/component count, hierarchy, orientation, and critical measurements.
- For DXF, compare entity counts by type and layer, canonical geometry fingerprints at a declared coordinate tolerance, polyline vertex/closed/bulge data, circles and arcs, Unicode text, dimension-object preservation, `$INSUNITS`, `$ACADVER`, extents, and critical measurements. Equal total entity counts alone are insufficient evidence.
- Review plotted PDF/images and 3D review views for clipping, missing symbols/fonts, stale geometry, wrong visibility, and presentation errors.
- Confirm the original/source, derived artifacts, revision identifiers, DRC report, and `NOT_EVALUATED` items are traceable.
- Apply `product-definition-release.md` for configurations, BOM/balloon mapping, dependency closure, semantic change review, manifest hashes, release verdict, and external approval status.
- Block release while any semantic/topology warning lacks a per-finding disposition and evidence, even when all purely geometric rules report no `FAIL`.
- Prefer a structured conversion/status report as the automation contract. Read detailed logs only when the status report is incomplete or failed; a zero exit code without the expected output and evidence is not success.

## Incremental and Full DRC

- After a local edit, run the cheapest checks that cover the changed feature and its dependents: sketch/profile validity, affected dimensions, neighboring clearances, cross-view projection, and local plot/render.
- A Tier 1 nonrelease edit does not require regeneration or re-import of unchanged PDF/DXF/STEP artifacts. Record deferred release gates and invalidate their cached evidence only when an input/dependency changed.
- Run expensive full topology, all-body interference, all-view regeneration, final plot, exchange re-import, and release-package checks once the geometry stabilizes for release or whenever units, global parameters/datums/coordinates, projection, configuration, topology mapping, dependencies, or format schema changes.
- Cache evidence by input content hash, configuration, rule-deck version, and validator version. Invalidate only affected results.
- Stop when all applicable release-blocking rules pass and every remaining warning or `NOT_EVALUATED` item is disclosed. Repeating an unchanged passed check adds no confidence.

## Independent Verification

Use a second route for critical requirements:

- Sum independent segments and compare with the overall dimension.
- Project the 3D model into a drawing view and compare with native 2D geometry.
- Recompute clearance from mating surfaces rather than rereading a dimension object.
- Compare source and re-imported exchange geometry.
- For 3D revisions, align by authoritative datums and compare component transforms, topology/body counts, bounding boxes, volume/mass properties where justified, critical surfaces/features, and boolean added/removed regions. Treat IoU/Dice or another aggregate similarity score as diagnostic only, never as proof that functional details are correct.
- Test rule boundaries with just-below, exactly-at, and just-above threshold fixtures when creating or changing a validator.
- Include an invalid-geometry fixture to prove precondition gating.

Independence means a different calculation path or representation, not running the same command twice.

When a physical-performance claim is in scope, apply `engineering-analysis.md`. A valid CAD model and passing geometry DRC are preconditions, not evidence of strength, fatigue, thermal, fluid, vibration, wear, or safety performance.

## DRC Report

Use a machine-readable table or JSON plus a concise human summary. Each finding must include:

```text
rule_id:
scope/configuration:
verification_tier: Tier 1 | Tier 2 | Tier 3
consumer_product_gate_status:
product_design_review_status:
component_id_and_line_class:
intentional_open_end_or_crossing_basis:
finding_disposition: DEFECT | INTENTIONAL_GRAPHIC | VALIDATOR_LIMITATION | WAIVED | UNRESOLVED
status: PASS | FAIL | WARNING | NOT_EVALUATED | ERROR
severity:
requirement_source:
measured:
threshold_and_inclusivity:
margin:
units:
location_or_entities:
method_and_tolerance:
evidence:
limitations:
warning_disposition_or_waiver:
deferred_gates_and_escalation_triggers:
recommended_action:
```

Lead with release blockers, then warnings and unevaluated scope. Never replace measurements with a numeric score, and never claim certification unless an authorized process and reviewer actually provided it.
