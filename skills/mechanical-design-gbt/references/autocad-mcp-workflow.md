# AutoCAD MCP Workflow

Read this reference whenever AutoCAD is operated through MCP, file IPC, COM, scripts, or another automation bridge.

Keep this workflow bridge-neutral. Names such as `system.ensure_ready`, transaction operations, and JSON bodies describe required capabilities or preferred contracts; call them only when the active bridge exposes them, otherwise use an available safe equivalent and record the limitation.

## Contents

- Readiness and capability discovery
- Failure contract and bounded recovery
- Strict request and postcondition contract
- Bridge conformance tests
- Mechanical drawing setup
- Transactions and entity ownership
- Geometry and annotation execution
- 2D/3D capability and fallback contract
- Geometry DRC
- Preview, plotting, and scale proof
- Delivery and DXF re-import
- Mechanical primitives and gears
- Standard workflow

## Readiness and Capability Discovery

Treat readiness as an idempotent state transition, not as a side effect of a status query.

1. Discover installed AutoCAD products and executable paths from the environment or bridge. Do not hardcode `AutoCAD LT`, a version, or a user-specific path.
2. Inspect whether AutoCAD is running and identify the actual product/version.
3. Wait for an active document or create one only when the user requested a new drawing.
4. Verify that the dispatcher/bridge is loaded, compatible, and responsive with a version handshake and IPC ping.
5. Record AutoCAD product/build, dispatcher/bridge/backend versions, transport, protocol/API schema, enabled toolset/capability manifest, and relevant parameter/response schemas. After an upgrade or mismatch, run harmless read-only probes before trusting mutable calls.
6. Prefer an exposed `system.ensure_ready`-style operation when available. Otherwise execute the equivalent discover/start/wait/load/handshake/ping steps using only exposed safe tools.
7. Keep `status` observational. Do not hide startup, LISP loading, document creation, or retries inside a call presented as read-only.

Return or record separate state for installation, process, document, dispatcher version, transport, and readiness. Never infer the product edition from a missing window or dispatcher error.

Use bounded recovery:

- Autostart or reload the trusted dispatcher at most once per unchanged failure state.
- Re-ping after the recovery action.
- Stop and report the exact failed state when the retry does not change it. Do not loop on startup or IPC timeouts.
- Do not run mutable CAD calls in parallel against the same document or file-IPC queue.

## Failure Contract and Bounded Recovery

Treat any payload containing an error as failure even when an outer MCP wrapper incorrectly reports `isError: false`. Normalize nested text JSON before deciding whether an operation succeeded.

After every mutable operation, inspect command completion, document state, transaction result, created/modified handles, and any rebuild/constraint warnings before issuing dependent operations. A nominally successful response with an empty trim, failed join, unsolved constraint, partial batch, or stale document is not success.

Compare requested targets/values with actual post-operation entities and a changed-handle/geometry fingerprint. Treat `ok` with no required semantic difference, unexpected auto-adjustment, coordinate/state carry-over, or changes outside the target set as a hard failure. An idempotent ensure-state operation may pass without mutation only when the pre-state already matched the full request and the response explicitly reports `already_satisfied` with readback evidence. Stop dependent mutation on every other no-op until the stage is rolled back and the cause is resolved.

Prefer structured failures containing:

```json
{
  "ok": false,
  "error": {
    "code": "E_DISPATCHER_NOT_LOADED",
    "message": "AutoCAD is running but the dispatcher is unavailable",
    "recoverable": true,
    "recommended_action": "autoload_dispatcher"
  }
}
```

Distinguish at least these states when the bridge exposes enough evidence:

- `E_AUTOCAD_NOT_INSTALLED`
- `E_AUTOCAD_NOT_RUNNING`
- `E_NO_ACTIVE_DOCUMENT`
- `E_DISPATCHER_NOT_LOADED`
- `E_DISPATCHER_VERSION_MISMATCH`
- `E_IPC_TIMEOUT`
- `E_COMMAND_STATE_BLOCKED`
- `E_OUTPUT_PATH_REJECTED`
- `E_POSTCONDITION_MISMATCH`

Do not guess recovery from message fragments when a code or state probe is available. Report bridge defects separately from drawing defects.

## Strict Request and Postcondition Contract

Prefer immutable, strongly typed, self-contained request objects. Every mutable request must carry all required coordinates/parameters explicitly; never inherit omitted `x1`, `y1`, `x2`, `y2`, layer, style, or geometry fields from a global/last-command state. Use `strict: true` or an equivalent schema mode when exposed so missing, mistyped, or unknown extra fields are rejected before mutation.

Assign each operation a unique request/stage ID. Require responses to return:

- `requested`: normalized request values after explicit unit conversion.
- `actual`: values read back from AutoCAD after creation/update.
- `diff`: field-level numeric/semantic differences with tolerance and units.
- All created/modified handles, document/path identity, transaction state, and warning/error state.

Immediately query every created/modified handle. Compare entity type, layer, `component_id`, `line_class`, coordinates/endpoints/vertices, radii/angles, text content/bounds, closed state, associations, and other operation-owned parameters. Prefer native semantic metadata; if the entity cannot store it safely, keep the same fields in the operation manifest/sidecar evidence.

Any unexplained mismatch is `E_POSTCONDITION_MISMATCH`, not `WARNING`:

1. Stop all dependent drawing calls.
2. Roll back the atomic batch; if transactions are unavailable, delete only the tracked new entities after authorization and prove cleanup.
3. Reset/reload the bridge once and rerun a minimal conformance probe.
4. If the same failure recurs, switch to a verified structured backend or mark the work `blocked`. Do not continue repairing or visually decorating entities whose semantic coordinates are untrusted.

For document and plot operations, postconditions include the actual active filename/path, media, printable area, plot mode, computed scale, and output identity. A requested filename that leaves the active document as `Drawing1.dwg`, or a requested fixed scale that produces fit-to-page, is a postcondition failure.

## Bridge Conformance Tests

Run bridge conformance tests after a bridge/backend/schema update and whenever coordinate carry-over or a postcondition mismatch is suspected. Do not run the full stress suite during every normal drawing task.

- Interleave `LINE`, `RECTANGLE`, `POLYLINE`, and `MTEXT` creation with unique sentinel coordinates/content for at least 100 deterministic cycles; read every handle back and prove that no field leaks across requests or batches.
- Vary omitted/extra/wrong-type fields under strict mode and prove that invalid requests are rejected without creating entities.
- Inject one batch failure and prove atomic rollback leaves entity count, handles, and geometry fingerprint unchanged.
- Exercise repeated drawing creation/naming, save/export, and plot calls; compare requested and actual document/output identities.
- Record bridge/backend/schema versions, seed/input fixture, per-operation requested/actual/diff, failure index, cleanup result, and final drawing fingerprint.

A failing conformance fixture blocks all further mutable use of that backend until the fixture passes after repair or a verified backend is selected.

## Mechanical Drawing Setup

Create or verify one explicit setup contract before geometry:

```json
{
  "standard": "GB/T",
  "units": "mm",
  "sheet": "A3",
  "orientation": "landscape",
  "projection": "first-angle",
  "scale": "1:1"
}
```

When the drawing is governed by GB/T and the user, source, and approved template do not specify a projection method, use first-angle as a documented default. Do not infer projection from AutoCAD locale; verify view placement, projection symbol, and title-block field together.

Apply the contract to actual CAD state, not only delivery metadata:

- Drawing units and insertion units.
- Layer names, linetypes, and lineweights.
- Text, dimension, leader, and table styles.
- Paper-space layout, page setup, plot device, paper size, printable area, orientation, plot style, plot area, and scale mode.
- Frame/title-block attributes, including the same sheet, projection, units, and scale values used by plotting.
- External references, block definitions/attributes, fonts/SHX, plot styles, page setups, images, data links, custom/proxy objects, and support paths required for faithful plotting and editing.

For Chinese text, verify the selected style against the applicable GB/T lettering requirements and the plotted artifact. Common AutoCAD mappings such as `gbenor` with `gbcbig`, or installed TrueType alternatives, are valid only after checking Chinese glyph coverage, technical-lettering proportions, missing/substituted symbols, text bounds, and PDF output.

Prefer a safe whitelisted variable-setting operation. Restrict variable names, types, and ranges; read values back after setting. Do not bypass the bridge with arbitrary LISP merely to change units or dimension variables.

Treat values such as dimension text height as configuration selected for the sheet/style, not universal constants.

## Transactions and Entity Ownership

Prefer atomic batch creation or explicit `begin` / `commit` / `rollback` semantics.

- `continue_on_error: false` stops later calls but does not prove rollback.
- Require `atomic: true` or `rollback_on_error: true` when the bridge supports it.
- If transactions are unavailable, create new work on a unique staging layer/block, record every returned handle, validate the batch, then promote it. On failure, remove only the tracked new entities when deletion is authorized.
- Never use a broad erase or layer purge as an improvised rollback in a user drawing.
- Preserve the original drawing and save to a revision/copy when the blast radius is uncertain.

Maintain an operation manifest containing source operation IDs, entity handles, layer, role, and grouping. This manifest supports targeted repair and evidence reporting.

## Geometry and Annotation Execution

- Use native AutoCAD entities and associative dimensions wherever supported.
- Prefer blocks, arrays, constraints, or parameterized repeated features over copied loose geometry.
- Require creation responses to return all relevant handles. A leader-plus-text operation should identify the leader, text, and association/group rather than only the top-level entity type.
- Group annotation changes so they can be moved, deleted, or rolled back together.
- Use native annotation styles, explicit text height, and collision/clearance checks. Re-audit after automatic placement; auto-avoidance is evidence only after visual review.
- When an API lacks a native or associative construct, document the fallback and its editability/export limitations.

## 2D/3D Capability and Fallback Contract

Discover capabilities before selecting a construction method. Record whether the active bridge safely exposes:

- 2D editing: endpoint/object snap, `trim`, `extend`, `break`, `join`/polyline edit, fillet, tangent construction, blocks, arrays, and associative dimensions.
- Constraints: coincident, tangent, concentric, equal radius, horizontal/vertical, symmetry, and dimensional constraints.
- 3D modeling: primitive solids, extrude, revolve, sweep, loft where applicable, union/subtract/intersect, fillet, chamfer, pattern, assembly transforms, sectioning, and projected drawing views.
- Reliability: atomic transactions/rollback, complete entity queries and associations, deterministic save/export, and uncached preview generation with overwrite or content-hash evidence.

Choose the highest representation that the available tools and evidence can support. If safe 3D operations or projected views are unavailable, use a shared parametric 2D skeleton and disclose that the result is a 2D concept/teaching representation. Do not simulate 3D authority by drawing front and side views from separate visual estimates.

If the AutoCAD bridge repeats `E_POSTCONDITION_MISMATCH`, prefer a deterministic structured DXF generator/parser such as `ezdxf` when available: generate parameterized geometry outside the faulty mutation path, then use AutoCAD/MCP only to open, read back, audit, annotate where proven safe, and plot. If no verified backend exists, stop as `blocked`.

Limit each mutable batch to one coherent subsystem or reviewable stage. After the skeleton and after every subsystem, audit handles, topology, shared parameters, affected views, and a fresh preview before continuing. Batch success proves only that calls returned; it does not prove trimming, connectivity, tangency, occlusion, or assembly correctness.

Prefer dry-run/preview modes for cleanup, relayering, topology repair, and other broad mutations when exposed. Compare the proposed handle set with the subsystem manifest before applying changes.

## Geometry DRC

Run changed-scope geometry DRC before presentation review. For Tier 3 release/export work, run it again on each applicable exported/re-imported artifact; do not regenerate an unaffected exchange file for Tier 1 merely to repeat the check.

For lines, arcs, circles, and polylines, check:

- Consecutive duplicate vertices and repeated closing vertices.
- Zero-length and below-tolerance segments.
- Zero/invalid radius, nonfinite coordinates, and degenerate arcs.
- Open profiles that must be closed.
- Self-intersections, local reversals, spikes, and inconsistent winding where orientation matters.
- Duplicate or reversed duplicate entities.
- Overlapping collinear segments and unintended coincident geometry.
- Dangling endpoints, near-touching endpoints that should coincide, unintended crossings, untrimmed occluded geometry, protruding segments, and broken required tangencies.
- Equal-radius/concentric groups, repeated-feature pitch, and projected locations against their shared parameters.
- Unexpected entity islands, layer/type mismatches, and stray construction geometry that will plot.

Use an explicit model-space tolerance with units. Each failure must identify the entity handle, segment/subentity index, location, measured value, and threshold. Entity count, closed flags, save success, and DXF readability do not prove clean geometry.

Audit by semantic role as well as geometry. Distinguish component outlines, material boundaries, center/hidden/construction lines, dimensions/leaders, hatches, table/title-block lines, and intentional open ports; do not let table or annotation geometry dilute component-topology findings.

Prefer repairing the source generator and regenerating. Automated cleanup may remove consecutive duplicate vertices or zero-length segments only on newly generated/tracked geometry and only when topology and intent remain unchanged. Re-run the full affected audit after cleanup.

## Preview, Plotting, and Scale Proof

Keep preview and release plotting semantically distinct:

- A PNG preview operation must produce PNG at an explicit resolution/DPI or return image content/thumbnail data.
- A PDF plot operation must produce a plotted PDF with explicit page setup.
- If only PDF is available, rasterize it with an available structured PDF renderer and disclose the fallback; do not pretend the PDF path is a PNG preview.
- Require a unique output name or explicit overwrite and verify a content hash or modification identity so a cached preview cannot be mistaken for the current drawing.
- For iterative work, keep camera/view, layer visibility, background, resolution, and scale stable enough to produce a meaningful before/after visual difference. An empty difference may indicate a stale preview; an unrelated difference indicates unintended mutation or unstable rendering state.
- Check image dimensions and nonblank/nonuniform pixel content before visual review. Request metadata/thumbnails and only the risk-specific full-resolution views needed for evidence; do not flood the agent context with redundant unchanged images.

For every release plot, explicitly set and verify:

- Plot device and media/paper name.
- Orientation and printable area.
- Layout/window/extents plot area.
- Monochrome or required plot style.
- Fixed versus fit-to-paper scale.
- Center/offset and lineweight behavior.

When the title block says `1:1`, require fixed 1:1 plotting. Record the calculated plot scale and reject a fit-to-paper result mislabeled as 1:1. Reopen the PDF and inspect full-sheet plus readable-detail views for blank output, clipping, overlaps, missing fonts/symbols, incorrect lineweights, and scale/title-block disagreement.

Report `PLOT_SCALE_CONSISTENCY` with requested mode/scale, actual media/plot area/transform/computed scale, title-block declaration, and PDF evidence. If fit-to-page is used, do not show a numeric engineering scale such as `1:1`; use an approved non-scale designation such as `NTS` and record the plot mode as `FIT`.

## Delivery and DXF Re-import

Deliver from the verified authoritative drawing and then re-import or independently parse the exchange artifact.

Treat DXF as an export unless the user explicitly requests it as the active source. After `save_as_dxf`-style operations, verify that the active document and authoritative DWG/native source did not silently switch to the DXF.

For DXF, record and compare:

- `$INSUNITS`, `$ACADVER`, coordinate precision, and extents.
- Entity counts grouped by type and layer.
- Canonical geometry fingerprints at a declared tolerance.
- Polyline vertex count, closed flag, bulge/arc data, and segment lengths.
- Circle/arc centers, radii, and angles.
- Unicode text and attributes.
- Native dimension/leader preservation versus exploded lines, text, or proxy objects.
- Critical dimensions and reference coordinates.

Compare the source drawing, DXF re-import, plotted PDF, and manifest as different evidence channels. A matching total entity count is only a coarse inventory check.

The final report must state actual files, paper, plot scale, units, DRC results, re-import comparison, assumptions, unsupported objects, and every `NOT_EVALUATED` item.

For formal release, apply `product-definition-release.md`: package required Xrefs/fonts/plot styles/templates or report them as external dependencies, and bind every artifact to the exact drawing revision/configuration and producing AutoCAD/bridge versions.

Require entity queries to return the parameters needed for repair and proof, including handles, endpoints, arc angles/endpoints, polyline vertices/bulges, MText width/bounds, block membership, groups, constraints, and associative relationships. Mark affected checks `NOT_EVALUATED` when the bridge omits required fields.

## Mechanical Primitives and Gears

Use domain-aware primitives when exposed. Do not advertise a generic polyline as a validated mechanical gear.

Classify gear output explicitly:

- `symbolic`: diagrammatic symbol only.
- `simplified`: approximate teeth for layout/communication; mark `NOT FOR MANUFACTURING`.
- `manufacturing`: true validated involute geometry with complete design inputs.

For spur, internal, and planetary gears, validate as applicable:

- Module, pressure angle, tooth counts, profile shifts, addendum/dedendum, and center distance.
- Root-cut/undercut risk; for an unshifted standard full-depth external gear, compare tooth count with the applicable minimum such as `2 / sin(alpha)^2` before claiming a standard tooth form.
- Internal/external mesh compatibility, interference, contact ratio, backlash, and tooth thickness.
- Planetary relation `Zr = Zs + 2*Zp` for the standard concentric arrangement.
- Equal-planet assembly phase, including divisibility of `(Zs + Zr) / Np` for the applicable arrangement.
- Planet-to-planet clearance, carrier pin positions, tooth phase, and motion/interference through a full cycle.

Manufacturing output requires true involute geometry and process/tolerance data. If the bridge lacks these primitives, perform the calculations with a proven mechanical library or keep the drawing symbolic/simplified and disclose the limitation.

## Standard Workflow

Select the verification tier in `cad-workflows.md` before running this sequence. A Tier 1 local edit may stop after targeted mutation, recompute, changed-handle/geometry proof, dependent checks, and one affected-view inspection; do not perform unchanged plot/export/re-import steps merely to complete the list. Use the full sequence for Tier 3 release work, skipping only genuinely inapplicable artifacts.

```text
discover capabilities and installation
-> ensure AutoCAD/dispatcher/document readiness
-> create or verify mechanical setup
-> begin atomic/staged transaction
-> create structured geometry and annotations
-> audit geometry
-> audit presentation
-> plot and inspect preview
-> commit/promote the transaction
-> deliver DWG/DXF/PDF
-> re-import and compare DXF geometry
-> publish artifacts and evidence
```

Do not collapse `audit geometry`, `audit presentation`, `plot review`, and `exchange re-import` into one generic `validated` flag.
