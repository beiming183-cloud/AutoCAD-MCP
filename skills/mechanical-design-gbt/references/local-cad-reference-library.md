# Local Home-Interior CAD Reference Library

Read this file only when a task needs a local CAD/JPG corpus or a comparable large block library. Generate `local-reference-library.json` privately for the current inventory and hashes; use `local-reference-library.example.json` as the portable schema.

## Corpus Status

- Indexed date: 2026-07-17.
- Inventory: 21 files, including 11 DWG and 10 JPG files.
- Approximate size: 0.418 GB.
- Manifest bytes: 448,477,063.
- DWG header families: 8 `AC1018` (2004-2006), 2 `AC1021` (2007-2009), and 1 `AC1032` (2018+).
- Latest source-file modified timestamp in the current snapshot: 2025-01-23T09:59:10Z.
- Visible publication dates on reviewed boards: 2023 to 2024.
- Scope: furniture, sofas, tables, chairs, beds, cabinets, doors, stairs, people, landscape, kitchen and bathroom fixtures, appliances, lighting, controls, home accessories, vehicles, and layout templates.
- Rights: several boards state rights reserved by their publisher. Keep this corpus local and do not redistribute or bundle source DWG/JPG files into the skill.

The generated manifest stores paths in JSON with Unicode escaping where necessary. Do not commit a private absolute root or personal corpus manifest. Accept the root from the user or current task and verify hashes before use.

## Useful Design Signals

Extract these transferable practices:

- show plan, front, side, top, and three-view variants when an object's use or installation depends on them;
- compare a family at common scale to expose silhouette and proportion differences;
- show people, rooms, counters, walls, cables, or mating products to establish use and installation context;
- organize broad product categories before selecting detailed references;
- represent modular families and state changes explicitly;
- keep clear low-detail silhouettes for early packaging and space-planning review;
- pair a product view with installation, access, opening, swing, or service context;
- use named dynamic behaviors and predictable grips for reusable CAD blocks.

Observed dynamic-behavior vocabulary on the boards includes:

- horizontal and vertical stretch;
- horizontal and vertical mirror;
- rotation or direction switching;
- visibility-state switching among variants;
- alignment;
- distribution or array-like movement;
- layer or annotation visibility toggles.

Use these as inspiration for configuration management and review states. A CAD dynamic-block action is not proof of a physical mechanism, joint, load path, clearance, or safe motion.

## Board-Level Observations

### Broad catalog boards

The general boards are strong at category coverage, compact silhouettes, modular layouts, and human scale. They are weak evidence for dimensions, construction, materials, current purchased-part identity, and mechanical standards.

### Appliance and kitchen or bathroom boards

These boards provide useful front, side, top, installation, opening, and product-family views. Some include brand names or approximate dimensions. Treat them as search leads and packaging references only. Verify every supplier model, revision, mounting pattern, connector, service zone, and safety requirement independently.

### Furniture and custom-cabinet boards

These boards demonstrate modular composition, stretchable envelopes, visibility variants, room context, and dimensional layout patterns. They are useful for massing, reach, opening, storage, and installation studies, but they are not mechanical-detail or tolerance authority.

### Layout template

The A3 layout board demonstrates room indexing, a title area, human figures, material arrows, and a declared drawing scale. It is an interior-design presentation template, not a GB/T mechanical drawing template. Do not transfer its line, title-block, scale, projection, or annotation conventions into mechanical release drawings without a standards review.

## Engineering Authority Boundary

Never use the corpus alone to authorize:

- manufacturing dimensions or tolerances;
- materials, wall thickness, heat treatment, or surface finish;
- supplier identity, current model, ratings, mounting, or certification;
- electrical clearances, grounding, fire enclosure, or safety compliance;
- load, strength, stiffness, fatigue, thermal, acoustic, or life claims;
- mechanism joints, limits, bearings, locks, cable motion, or collision clearance;
- GB/T drawing compliance.

Mark such uses `TBD` or `NOT_EVALUATED` until controlled evidence is available.

## DWG Reliability Finding

During the 2026-07-17 read-only probe, the AutoCAD bridge twice acknowledged opening the appliance-library DWG, once from its original Unicode path and once from an ASCII staging path. Immediate readback still reported the previous USB rotary-product document, its path, 87 entities, and its mechanical layers. Therefore:

- `DOCUMENT_IDENTITY_GATE`: `FAIL` for that backend and session;
- all returned DWG entity counts, layers, units, and DRC results from the probe are invalid as library evidence;
- DWG block structure, dynamic properties, units, and internal quality remain `NOT_EVALUATED`;
- use the JPG boards and filesystem manifest until a verified backend proves the requested active document identity;
- after a future bridge repair, rerun identity conformance before structural sampling.

Do not reinterpret this failure as a defect in the source DWG. It is a bridge/document-selection evidence failure.

## Reference Workflow

1. Run `scripts/catalog_reference_library.py` on the current root and compare the manifest with the prior one.
2. Select references by product category, view need, interaction state, and context rather than by visual novelty alone.
3. Record image or file IDs and separate observed facts from likely intent and engineering unknowns.
4. Extract only reusable proportion, silhouette, view, configuration, context, and interface-layout principles.
5. Verify purchased-part and engineering data from controlled sources before detail CAD.
6. Create at least three target-product concepts rather than tracing one library item.
7. Apply `industrial-design-3d-quality.md` and the full engineering and GB/T gates before handoff.
8. Update the aesthetic profile only when the user confirms a preference or repeated evidence supports a labeled inference.
