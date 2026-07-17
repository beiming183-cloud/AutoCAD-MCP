# CAD 2D and 3D Workflows

Read this reference when the task includes DXF, DWG, PDF, images, STEP/STP, 2D-to-3D reconstruction, 3D-derived drawing views, or 3D review output.

## Evidence Hierarchy

Use the strongest available evidence and retain provenance for every extracted or inferred requirement:

1. User-approved requirements, dimensions, notes, and revision instructions.
2. Approved product/configuration/revision records, BOM/effectivity, inspection/analysis requirements, and external release decisions.
3. Trusted native parametric geometry and associative drawing/PMI data.
4. Verified neutral CAD geometry such as STEP/STP or structured DXF entities.
5. Vector PDF geometry and embedded text.
6. Rasterized pages, screenshots, and OCR output.
7. Visual proportion estimates.

Never let a weaker source silently override a stronger one. Flag contradictions between two equally authoritative sources. Treat OCR and visual estimates as review evidence, not manufacturing truth.

## Choose a Route

### Native 2D drawing

Use this route for an existing DWG/DXF or a drawing authored directly in CAD.

- Prefer the native CAD application when associative dimensions, blocks, layouts, plot styles, or vendor-specific objects must survive.
- Keep model geometry at 1:1 and control plotted scale through layouts/viewports unless the existing project has a different documented convention.
- Use structured CAD APIs. For DXF automation or audit, prefer a library such as `ezdxf`; do not parse DXF as unstructured text.
- If a live CAD/MCP mutation path repeats a requested-versus-actual postcondition mismatch, stop using it for geometry. Prefer a deterministic parameter generator plus structured DXF library such as `ezdxf` when available, then reopen and independently audit the DXF before using the native application for proven-safe annotation/plot operations.
- Validate entity types and counts by layer, drawing extents, closed profiles, duplicate or overlapping entities, dimensions against geometry, layouts, viewport scales, fonts, symbols, and final plot output.
- Treat a DWG-to-DXF round trip as potentially lossy. Preserve the source file and disclose any unsupported object, font, plot-style, or annotation behavior.

### 3D-backed 2D drawing

Use this route when a trustworthy part or assembly model exists or can be reconstructed without inventing critical design intent.

1. Validate the 3D source first: units, coordinate frame, bounding box, solid/shell count, positive volume where intended, connectivity, feature placement, and assembly interfaces.
2. Generate orthographic views from a shared orientation and projection convention. Prefer associative projected views in native CAD or a drawing workbench such as FreeCAD TechDraw when available.
3. Derive sections and detail views from the same model instead of redrawing their outlines independently.
4. Import model dimensions only when they represent approved design intent. Remove duplicates and organize remaining dimensions by manufacturing function.
5. Recompute/regenerate the drawing after any model change, then detect stale or detached dimensions and compare the plotted views again.
6. Keep the validated model and drawing linked in the delivery report. Do not imply that an attractive 3D model proves the 2D annotations are complete.

### 2D-only spatial reconstruction

Use this route to test whether front, top, side, section, and detail views can describe one real part.

- Map each named view to model axes before comparing features. Do not infer view identity from page position alone.
- Build a feature correspondence table for material boundaries, bores, holes, slots, shoulders, chamfers, fillets, patterns, and axes. Record each feature's projected location, extent, visibility, and source dimension in every applicable view.
- Reconstruct a minimal spatial truth model when the available dimensions constrain one. Keep exact geometry separate from assumed geometry.
- Project the reconstructed geometry back into the drawing orientations and compare visible contours, hidden edges, centerlines, section boundaries, multiplicity, and handedness.
- Use a dimensioned or section view as stronger evidence than an undimensioned silhouette, but report the conflict rather than quietly changing the weaker view.
- When multiple solids satisfy the views, report the ambiguity. Do not claim that a unique 3D shape has been proven.

### PDF, scan, and image intake

- Classify each page as vector, raster, mixed, or low-confidence before extraction.
- Parse native PDF vectors and embedded text first. Use OCR only for content that cannot be read natively, and retain source/page/confidence metadata.
- Never scale undimensioned raster geometry into production dimensions unless a trusted scale reference exists.
- Mark the result `needs_review` when geometry depends on OCR, a page is cropped or blurred, text is garbled, scale is uncertain, critical dimensions or notes are unreadable, or a converted DWG/DXF has not been opened and visually checked.
- Treat automatic PDF/image-to-CAD output as a candidate until geometry, dimensions, annotations, title block, and plot output are independently verified.

## 3D Review Presentation

Use 3D display to explain geometry and reveal semantic errors, not as decoration or as a substitute for measurement.

- Render the actual CAD geometry. Do not use an artistic image generator for a geometry-validation view.
- For a simple part, provide one clear isometric view plus front, top, and side orthographic views when cross-view comparison matters.
- For features on several faces or uncertain handedness, add the opposite isometric view so every outer face is visible at least once.
- For bores, cavities, passages, shells, blind holes, or section-critical details, add a section or clipped view tied to the same cutting location used by the drawing.
- For assemblies, add an exploded, transparent, or hidden-edge view only when it clarifies interfaces, interference, fastener access, or motion. Keep a normal assembled view for context.
- Keep axes, camera orientation, units, part colors, and section directions stable across revisions. Label views by orientation and avoid perspective when comparing them with orthographic drawings.
- Refresh only the views affected by a repair unless the overall shape or orientation changed. Review every regenerated image before handoff.
- Convert each visual concern into a deterministic check: measure a suspicious offset, count a pattern, inspect a section depth, test interference, or verify a mating frame. Visual inspection alone cannot establish pass/fail.

## Tool Routing

- Use the existing native CAD tool and project templates first when they can preserve associative behavior and GB/T styles.
- Use AutoCAD-compatible native entities for DWG/DXF drafting; use `ezdxf` for deterministic DXF structure checks when available.
- Use SolidWorks drawing APIs for model-backed standard views, aligned first-angle views, sections, details, model dimensions, balloons/BOMs, and PDF export when SolidWorks is the source of truth.
- Use FreeCAD Part/Sketcher for parametric geometry and TechDraw for projected drawing views when an open, scriptable workflow is needed.
- Use a B-rep kernel such as OpenCascade/build123d for neutral STEP geometry, topology, measurements, sections, and projection support when it is already available.
- Do not add a large toolchain merely to improve presentation. Scale the method to the task and disclose unavailable capabilities.
- Treat any unauthenticated CAD automation server as local/trusted-network only; never expose it directly to the public internet.

## CAD Domain Routing

- **Mechanical CAD (MCAD):** use this skill for parametric parts/assemblies, engineering drawings, tolerances, DRC/DFM, manufacturing exchange, and 3D review.
- **Electronic CAD (ECAD/PCB):** route to a KiCad/EDA-specific workflow for schematic ERC, PCB DRC, net/pin/footprint/BOM/datasheet cross-reference, stack-up, signal/power integrity, Gerber/drill review, and fabrication outputs. Mechanical GB/T drawing rules do not replace ECAD rules. Require the native EDA DRC for release.
- **Building/plant CAD and BIM:** route to domain standards and IFC/Revit/plant-specific checks for levels, spaces, systems, clashes, code compliance, and permit documentation. Do not reuse mechanical-part thresholds.
- **Mesh/sculpting/visualization:** use Blender or another mesh/render tool after validated CAD export when the goal is presentation, animation, retopology, USD/glTF, or game assets. Preserve the B-rep/native source and treat the mesh scene as derived.
- **Simulation/physical AI:** convert to the solver or scene format only through a documented pipeline, preserve hierarchy/units/material provenance, and validate the converted artifact before using it. Visualization or simulation conversion does not prove the source design passed DRC.

## Risk-Scaled Verification

Choose the tier from change blast radius, semantic dependencies, requested deliverable, and release intent. Record the tier, why it applies, checks run, cached evidence reused, and deferred gates.

### Tier 1: local fast path

Use only for a narrow nonrelease edit with known stable identifiers and no change to units, coordinate frame, projection, configuration, global parameters, topology ownership, BOM/effectivity, external dependencies, or export schema. Examples include one supplied tolerance value, thread designation/size, isolated chamfer parameter, or local note.

- Capture the before-state of target parameters, handles/features, affected dimensions, and local geometry fingerprint.
- Apply the smallest source edit, recompute, and prove the requested post-state on the same semantic targets.
- Check changed geometry/profile validity plus direct dependents, neighboring clearance/topology, and affected drawing view/annotation.
- Inspect the active local view or one stable cropped preview when visible geometry changed. Do not force PDF regeneration, DXF/STEP re-import, full-sheet rendering, or all-body interference when those artifacts/dependencies are unaffected and no release/export is requested.
- Report that release/export gates were deferred; Tier 1 is not a manufacturing-release verdict.

### Tier 2: affected-system verification

Use for new multi-view parts, sections, patterns, internal features, or format conversion:

- Run whole-drawing checks plus independent cross-view reconstruction on critical features.
- Regenerate only affected views/derivatives and produce one fresh risk-specific preview set; add a section when internal geometry matters.
- Recheck every changed dimension, section, and conversion-sensitive annotation.
- Re-import an exchange artifact only when the edit changed that artifact, its schema-sensitive content, or the user requested a candidate export.

### Tier 3: full release verification

Use for assemblies, fit-critical interfaces, safety-related parts, conflicting inputs, global changes, or manufacturing/release handoff:

- Verify every specified critical dimension and interface with an independent calculation or measurement route.
- Check mating alignment, clearances/interference, motion envelopes when defined, material continuity, and section truth.
- Run full applicable geometry/drawing/assembly/DFM/GPS gates, regenerate and inspect the final plotted sheet, re-import critical exchange formats, and build the revision-bound release evidence manifest.
- Require explicit resolution or a documented `needs_review` finding for every conflict. Add engineering analysis only when loads, materials, boundary conditions, and acceptance criteria are available.

Escalate immediately to Tier 2 or Tier 3 when an edit changes units, global coordinates/datums, projection method, model orientation, global parameters, configurations, topology/stable-ID mapping, assembly constraints, cross-view authority, title block/revision, BOM, external dependencies, file schema, or a release-critical requirement; also escalate when unexpected entities change or the local proof is inconclusive. A repeated mutable postcondition mismatch does not merely escalate verification: it blocks that backend until conformance is restored or a verified backend is selected.

Stop when all applicable deterministic checks pass, visual review finds no unexplained issue, and high-risk conflicts are resolved or explicitly reported. Do not repeat unchanged low-risk checks without a new reason.

## Delivery and Report

Preserve the original source. Return only the formats justified by the task, typically:

- Editable native CAD or source-controlled generator.
- Exchange output such as DXF/DWG and a plotted PDF.
- A validated 3D neutral model when one was created or supplied as the drawing source.
- A small 3D review packet with labeled views when requested or useful.
- A verification summary listing each check, evidence or measurement, result, assumptions, `needs_review` items, and unsupported claims.
- The authoritative source, preserved original/backup, editable working copy, final artifact locations, and cleanup/deletion/retention strategy.

Report only checks that actually ran. State whether physical review covered geometry/assembly plausibility only or included a real engineering calculation/simulation.

For configurations, BOMs, MBD/PMI, inspection, analysis, or manufacturing release, load the corresponding progressive references from `SKILL.md` and produce a revision-bound evidence manifest rather than an unversioned file collection.
