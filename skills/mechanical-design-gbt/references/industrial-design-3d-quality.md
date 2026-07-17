# Industrial Design 3D Quality Gates

Read this file before refined product 3D, presentation rendering, integrated controls or ports, rotating or articulated products, or any handoff that claims product-quality form rather than rough massing.

## Purpose

Prevent a technically countable model from being mistaken for a resolved product. Correct body count, dimensions, topology, or geometry DRC may be necessary, but they do not prove proportion, form language, interface integration, motion logic, surface quality, or product realism.

Use the gates in order. A failed gate returns the work to the owning prior stage. Do not hide failure by adding dimensions, labels, colors, materials, or more bodies.

## Claim Levels

- `diagram`: a 2D or 3D explanation with no form or manufacturing authority.
- `massing`: proportion, silhouette, packaging, and major motion envelopes only.
- `industrial-design candidate`: selected form language, integrated interfaces, named configurations, coherent surfaces, and review evidence.
- `engineering candidate`: industrial design plus controlled interfaces, structure, materials, process intent, and applicable engineering evidence.
- `release candidate`: verified native source, drawings, configurations, exports, and external human approval status.

Never upgrade the claim because a render looks polished.

## Gate 0 - Innovation and Constraint Lock

Record:

- product innovation or differentiator that must survive refinement;
- immutable envelope, section, posture, or architecture constraints;
- adjustable variables and target ranges;
- prohibited forms, interactions, or visual shortcuts;
- decision authority and current revision.

Treat a declared equal-section column, fixed axis, required silhouette, or other defining constraint as a named hard requirement from the first concept. Do not discover it after detailing.

## Gate 1 - BACKEND_CAPABILITY_GATE

Read `backend-routing.md`. Discover and record the real backend, versions, source representation, and operations. Match capability to claim level.

For `massing`, require at least:

- primitive or profile-based solids;
- boolean union and subtraction;
- explicit 3D transforms;
- stable orthographic and perspective views;
- deterministic save and readback.

For an `industrial-design candidate`, additionally require as applicable:

- native edge fillet and chamfer with readback;
- shell or controlled wall-thickness workflow;
- loft, sweep, surface construction, trim, sew, and continuity inspection;
- component hierarchy and named configurations;
- explicit joint or component transforms for motion states;
- sectioning, measurement, interference, and surface diagnostics;
- stable camera, focal length, material, lighting, and native depth-aware rendering.

If the backend exposes only boxes, cylinders, extrude, and basic boolean, limit output to `massing`. Do not simulate refined authority with 2D polygons, Pillow pseudo-3D, manual perspective linework, or painted highlights.

Record missing operations as blockers or `NOT_EVALUATED`. Switch to a capable backend when refined 3D is required.

## Gate 2 - DOCUMENT_IDENTITY_GATE

Before and after every open, create, switch, save, export, or render operation, read back:

- active document ID and absolute path;
- requested versus actual file name;
- source revision, configuration, and units;
- application, kernel, bridge, and schema versions;
- unsaved state and owning source;
- output path, type, timestamp or hash, and source identity.

An acknowledgement such as `opened: target.dwg` is not evidence. The active document variables and subsequent queries must identify the same target.

On mismatch:

1. stop every dependent operation;
2. do not use entity counts, audits, screenshots, or renders returned from the suspect session;
3. perform one bounded recovery and one harmless identity probe;
4. if the mismatch repeats, mark the backend `blocked` and switch to an independently verified route;
5. preserve the user's original active document and never save suspect output over it.

When structured identities are available, run `scripts/check_document_identity.py`. Preserve its JSON report with live readback evidence.

## Gate 3 - Purchased Parts and Interface Source

Before detail, create one parameter source for sockets, USB modules, switches, breakers, displays, connectors, bearings, cables, internal supplies, and other purchased parts. Record supplier or measurement evidence, revision, body envelope, mounting, service and mating space, cable bend, keep-outs, and TBDs.

Do not infer current supplier geometry from a branded catalog sheet or thumbnail. Use the library as a search and category reference only until verified data is obtained.

## Gate 4 - MASSING_GATE

Create only primary and secondary masses, major openings, support, interface zones, motion axes, and use envelopes. Generate same-revision, same-scale views:

- front and rear;
- left and right side;
- top and bottom;
- one controlled three-quarter view;
- aligned and key motion states when applicable;
- human, desk, wall, appliance, cable, or mating-product context where relevant.

Review:

- hard constraints and innovation lock;
- overall ratio families and silhouette;
- posture, support, visual and physical center of gravity;
- major negative spaces and shadow gaps;
- packaging plausibility and action direction;
- interface count, zones, and cable exits;
- whether the object reads as a real product rather than a stack of primitives.

Require user or delegated design-authority disposition before refinement. Stop immediately when the first massing view fails; do not add tertiary detail.

## Gate 5 - FORM_LANGUAGE_GATE

Define and parameterize:

- primary, secondary, and tertiary mass hierarchy;
- section and taper strategy;
- one controlled radius and chamfer family;
- tangent or curvature transition intent;
- seam, reveal, step, and shadow-gap system;
- panel flush, recessed, or proud relationships;
- split, base, rear, underside, and service-door treatment;
- repeated opening, grille, rib, and texture rules;
- visual weight, CMF zones, and technology-reveal strategy.

Verify that every view follows the same grammar. A 2D callout such as a radius does not pass when the 3D body remains sharp. Exposed intermediate top slabs, tray-like layers, arbitrary cover plates, and unrelated fillet sizes fail unless the design intent explicitly requires them.

## Gate 6 - INTERFACE_GATE

Drive 2D views, 3D cutouts, bezels, clearances, and renders from the same named interface parameters. Check:

- type, count, center, orientation, spacing, and symmetry;
- actual opening or recess versus a decorative surface plate;
- flushness, reveal, bezel, retention, and material boundary;
- hand, finger, tool, plug, adapter, and connector keep-outs;
- insertion and removal direction;
- cable bend radius, strain relief, routing, and adjacent-port conflicts;
- visibility, labeling, feedback, and misuse;
- all relevant configurations and moving states.

Any unsynchronized 2D and 3D interface fails. Repair the owning parameter source and regenerate both.

Run `scripts/check_interface_alignment.py` when structured interface tables exist. Missing required interfaces, mismatched parameter sources, and out-of-tolerance center, size, or orientation differences fail the gate.

## Gate 7 - MOTION_GATE

Represent motion as component hierarchy and named states, not as loose bodies placed together. Define:

- grounded component and axis;
- joints, degrees of freedom, limits, stops, detents, locks, bearings, and load path;
- named states such as `0deg`, `90deg`, `180deg`, `270deg`, and required intermediate states;
- transforms derived from one parameter source;
- contact, clearance, collision, swept volume, pinch zones, and cable state;
- state-dependent interface access, support, stability, and visual continuity.

Keep each state as a configuration or reproducible transform set. Do not show aligned and rotated variants as unexplained duplicate geometry in one model space.

Run `scripts/validate_motion_states.py` when structured joint and state data exists. The script checks schema, required states, limits, and recorded dispositions; geometric collision and cable evidence must still come from a capable CAD or simulation route.

## Gate 8 - SURFACE_GATE

For the claimed maturity, verify:

- valid body and expected shell or solid count;
- no unintended gaps, self-intersections, non-manifold regions, or zero thickness;
- positional, tangent, or curvature continuity at intended transitions;
- radius family and highlight flow;
- wall thickness and internal clearance;
- draft, parting, shutoff, tool access, or other process risk classification;
- seam and split consistency through corners and motion states;
- face and feature references still resolve after rebuild;
- underside, rear, and service surfaces are designed, not leftover geometry.

Do not claim surface quality from a single shaded view. Use zebra, curvature, reflection-line, section, thickness, draft, or equivalent evidence when the backend supports it; otherwise mark those checks `NOT_EVALUATED`.

Use official surface-analysis behavior for the exact tool version. Zebra continuity and false-color curvature analysis are diagnostic evidence, not automatic aesthetic approval.

## Gate 9 - RENDER_GATE

Render the real current native model with a stable review setup:

- fixed camera IDs, focal lengths, target, up direction, crop, and resolution;
- fixed lighting, background, material library, and color-management assumptions;
- aligned, rotated, use-context, underside or rear, and interface close-up views as applicable;
- current document, revision, configuration, and content-hash identity;
- correct depth buffering, occlusion, intersections, normals, and transparency;
- nonblank image and meaningful before-and-after difference after a model change.

Separate validation renders from marketing images. A Pillow polygon composite, hand-painted pseudo-perspective, or render disconnected from the authoritative model is a diagram, not product-render evidence.

Run `scripts/compare_render_viewset.py` for revision-bound review sets. Preserve camera type, focal length or orthographic scale, transform, crop, resolution, color pipeline, and configuration. The script verifies image and metadata integrity, not lighting taste or product appeal.

Use `assets/camera-presets.json` as a normalized starting convention when the active backend has no approved project preset. Map directions into the product coordinate frame and record the actual native cameras.

## Gate 10 - DESIGN_REVIEW_GATE

Give separate dispositions for:

- proportion, silhouette, and product character;
- form-language and 360-degree coherence;
- interface integration and user action;
- motion states, cables, and stability;
- CMF, touch, cleanability, and aging;
- assembly, service, and manufacturing risk;
- safety architecture and unevaluated engineering performance.

Geometry DRC and render quality are separate evidence channels. Require explicit user appearance disposition before manufacturing CAD or GB/T drawings.

## Gate 11 - HANDOFF_GATE

Bind one exact revision and configuration set across:

- native 3D source and named parameters;
- product architecture and innovation lock;
- interface and purchased-part table;
- motion configuration table;
- same-camera review render set;
- surface, interference, and design-review evidence;
- unresolved TBD and NOT_EVALUATED register;
- GB/T drafting handoff package.

The 3D model, tables, renders, and drawings must identify the same source revision. Any stale or cross-document artifact blocks handoff.

Run `scripts/check_model_manifest.py` before handoff. Require the role set selected for the maturity and preserve the structured report.

## Recurrent Failure Patterns

Treat these as immediate design-review failures:

- a specified radius exists only in 2D while the 3D body stays sharp;
- ports are proud rectangular stickers rather than real openings or integrated modules;
- rotating layers are stacked slabs without an axis, bearing, reveal, lock, or load path;
- intermediate top faces remain exposed and make the product read as trays or drawers;
- shells, seams, base, rear, underside, and central support are absent;
- multiple motion states occupy one space without component identity or configurations;
- a 2D correction does not regenerate 3D from the same parameter source;
- pseudo-3D has no depth buffer or correct occlusion;
- a geometry audit passes and is incorrectly reported as an appearance or product-design pass;
- the first failed massing preview is ignored and error accumulates into delivery.

## Gate Report

Record for every gate:

```text
gate_id:
status: PASS | FAIL | WARNING | NOT_EVALUATED | ERROR
revision_and_configuration:
requirements_and_source:
requested_identity:
actual_identity:
evidence:
limitations:
blocking_findings:
return_stage:
next_action:
reviewer_or_authority:
```
