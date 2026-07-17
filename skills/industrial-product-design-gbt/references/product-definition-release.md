# Product Definition and Release

Read this reference for configurations/variants, BOMs, item balloons, revisions, model-based definition (MBD/PMI), external dependencies, interoperability, or formal release packages.

## Contents

- Authoritative baseline
- Stable identity and dependency graph
- Configurations, BOMs, and item balloons
- Change scope and semantic difference
- Exchange and round-trip verification
- Reproducible release package
- Release verdict

## Authoritative Baseline

Record one release baseline before editing or exporting:

- Product/document ID, revision, lifecycle state, configuration/variant, and source commit/version/microversion.
- Native CAD application, geometric kernel, automation/API schema, plug-in and generator/library versions.
- Units, coordinate frame, projection convention, model/drawing scale convention, and active sheet/layout.
- Authoritative native model/drawing plus every derived exchange, plot, render, analysis, and inspection artifact.
- Absolute working-session locations for the preserved original/source, editable working copy, staging/candidate outputs, and final delivered copies; keep public/package manifests relative and free of private paths.
- Applicable standards/rule-deck editions, supplier/process profile, waivers, approvals, and unresolved requirements.

Do not release from an ambiguous active document, unsaved state, stale drawing, or unspecified configuration. Preserve the previous released baseline and create the candidate in a revisioned staging location.

## Stable Identity and Dependency Graph

Use stable product, component, feature, requirement, drawing-view, annotation, and BOM item IDs. Prefer named datums, coordinate systems, mate connectors, parameters, features, and semantic selectors over volatile face/edge indices or display-order positions.

Build a dependency graph covering:

- Source parameters, sketches/features, parts, assemblies/configurations, drawings/views, BOMs/balloons, PMI, analysis models, and exports.
- External references/Xrefs, linked spreadsheets, templates, title blocks, blocks, images, fonts, SHX files, plot styles, material/appearance libraries, standard-part catalogs, macros/scripts, custom objects, and proxy-object providers.
- Tool/runtime versions and environment settings that affect rebuild, tessellation, plotting, fonts, units, or output ordering.

After feature reorder, suppression, healing, replacement, or parameter changes, verify that each downstream reference still resolves to the intended semantic object. A successful rebuild does not prove that a dimension, mate, drawing view, PMI callout, or operation remained attached to the correct face/edge.

Package portable relative references when possible. Report missing, absolute/user-specific, network-only, licensed, or unsupported dependencies; never hide credentials or private paths in a public release.

## Configurations, BOMs, and Item Balloons

Treat each released configuration/variant as a distinct evaluated product state.

- Record suppression/substitution state, component revision, transform/mates, quantity, material/mass source, and configuration effectivity.
- Rebuild and validate geometry, interference, motion, drawing views, BOM, and exports for every released state; one passing default configuration does not cover the family.
- Map each BOM row to stable component IDs and applicable item balloons. Include item/reference number, part/document ID, revision, description, quantity/unit, make/buy or standard-part status when required, and configuration applicability.
- Check missing components, duplicate item numbers, inconsistent quantities, phantom/reference components, flexible subassemblies, mirrored/derived parts, weldments, consumables, and excluded construction/analysis geometry.
- Require every plotted item balloon to resolve to exactly one applicable BOM row and every required BOM row to have the intended drawing reference.
- Source bearings, fasteners, seals, motors, and other purchased/standard parts from an identified catalog/specification. Do not infer designation or performance from visual resemblance.

Do not let a manually edited BOM or balloon override the authoritative assembly silently. Record intentional manual fields and verify them after regeneration.

## Change Scope and Semantic Difference

Translate each change request into an impact set: requirements, parameters/features, components, configurations, mates, views, dimensions/PMI, BOM rows, analyses, inspection characteristics, and release artifacts.

Compare before and after using semantic evidence, not timestamps or file size alone:

- Native feature/parameter changes, suppression state, stable-ID mapping, component transforms, and constraint/mate state.
- Body/topology counts, bounding boxes, mass properties where justified, critical dimensions, interfaces, and boolean added/removed regions.
- Drawing view geometry, annotations, BOM/balloons, title/revision fields, and fixed-view visual differences.
- Exchange-format geometry fingerprints, hierarchy, names, colors, PMI semantics, units, axes, and dependency manifests.

Detect no-op updates and auto-adjustments by comparing requested values/targets with actual post-rebuild state. Investigate changes outside the impact set and expected changes that produce no semantic difference. Aggregate similarity scores are diagnostic only.

## Exchange and Round-Trip Verification

Record the actual format/schema/version and export options. For STEP, state the schema selected (for example AP242 when supported) rather than calling every `.step` file equivalent. Distinguish semantic PMI associated with geometry from graphical annotation that only looks correct.

For each critical exchange artifact:

1. Export from the authoritative baseline into staging without switching or overwriting the source document unexpectedly.
2. Reopen/re-import with the receiving application or an independent structured parser/kernel.
3. Compare units, coordinate handedness/orientation, assembly hierarchy/transforms, names, colors/material identifiers, analytic versus spline geometry where relevant, body/shell count, tolerances/healing, and critical measurements.
4. Verify PMI/dimensions, datum references, item/BOM semantics, and configuration identity when the format and receiving tool claim to preserve them.
5. Record unsupported entities, proxies, approximations, healing operations, missing fonts, and semantic downgrades.

Do not claim round-trip equivalence from successful parsing, equal total entity count, or a visually similar render.

## Reproducible Release Package

Create a revisioned package atomically from one validated baseline. Include only the smallest complete artifact set justified by the target, such as:

- Editable native source or source-controlled generator and required reusable libraries.
- Required DWG/DXF/STEP/3MF or other exchange files.
- Plotted PDF and selected PNG/3D review evidence.
- BOM/item list and configuration/variant declaration when applicable.
- DRC/DFM, GPS/inspection, analysis, round-trip, and visual-review evidence that actually ran.
- Waiver/`NOT_EVALUATED` register and approval status.
- Machine-readable manifest containing artifact role, relative path, byte size, content hash, source revision/configuration, producing tool/version, units/schema, and gate status.

Use `scripts/build_release_manifest.py` when Python is available, or an equivalent structured implementation. The script inventories and hashes artifacts deterministically; it does not run release gates or grant approval.

Regenerate a representative package from the same inputs when release risk justifies it. Compare content hashes for deterministic artifacts and semantic fingerprints for exporters that embed timestamps/order nondeterministically. Reject missing, zero-byte, stale, duplicate-role, or unmanifested critical artifacts.

Do not overwrite a prior released package. Do not include credentials, temporary locks, autosaves, caches, private paths, unrelated source files, or unlicensed dependencies.

Define a cleanup strategy before handoff:

- Preserve the authoritative source and previous released baseline.
- List working/staging/candidate/final locations and identify which copy the user should continue editing.
- Delete only temporary or failed artifacts created and tracked by the current workflow, after proving they are not referenced and deletion is authorized.
- Retain failed prototypes only when they are useful test evidence; label them non-deliverable and keep them outside the final package.
- Report every retained temporary/external dependency and every cleanup action that was deferred.

## Release Verdict

Use one explicit verdict:

- `blocked`: a release-critical rule failed or evidence is unreliable.
- `draft package`: useful artifacts exist but required gates or inputs are incomplete.
- `candidate after human review`: applicable automated gates passed, with limitations and waivers disclosed.
- `approved externally`: an identified authorized process/person approved the exact revision/configuration outside the agent workflow.

An agent does not self-certify a manufacturing release. Report the exact baseline, artifact inventory, failed/unevaluated gates, waivers, dependency risks, round-trip results, and required human actions.

Also report the authoritative source location, preserved original/backup, editable working copy, final copies, and cleanup result. Do not leave the user guessing which similarly named file is current.
