# Research Authority and Source Control

Read this file before importing requirements, dimensions, safety rules, ergonomic values, CAD methods, or tool behavior from the web, a repository, a catalog, or an image set.

## Authority Order

Use the highest available authority for each claim:

1. Controlled user or project requirement, approved drawing, current supplier data, or controlled physical measurement.
2. Applicable official standard or regulation in the correct market, edition, product category, and language.
3. Official tool, kernel, format, or research-agency documentation for the exact version in use.
4. Maintained open-source project documentation and tests, pinned to a release or commit.
5. Peer-reviewed or institutional research with a population and method that match the task.
6. Manufacturer application notes, reputable textbooks, and professional guidance.
7. Blogs, forums, videos, image boards, AI summaries, and search snippets for discovery only.

Lower tiers may suggest a question or method. They do not override a higher-tier requirement.

## Source Record

Record every decision-driving source:

```text
source_id:
title:
publisher_or_owner:
source_type:
url_or_controlled_path:
standard_or_document_id:
edition_revision_or_commit:
publication_or_release_date:
retrieved_date:
license_or_access_limit:
applicable_market_product_population:
claim_supported:
limitations_and_conflicts:
```

Never cite a search-result title as evidence. Open the source, verify identity and date, and record the bounded claim it supports.

## Standards and Licensed Material

- Use official catalog pages to identify the standard title, edition, status, and scope.
- Obtain the authorized full text through the user's organization or licensed source before applying normative requirements not visible on the public page.
- Do not reproduce paywalled tables or invent thresholds from memory.
- Check national adoption, amendments, product-family standards, market, and transition dates.
- Mark a rule `NOT_EVALUATED` when the applicable edition or licensed content is unavailable.

## Dimension Authority Levels

Assign every decision-driving dimension one authority level and preserve the exact source identity:

- `A`: authorized normative text, current supplier-controlled drawing or CAD, or an approved project-controlled drawing. Record edition/revision, applicability, and access boundary.
- `B`: official catalog, official brand page, or public manufacturer parameter. Use for product identity, rating, configuration, and search direction only within the published claim.
- `C`: controlled measurement of an identified physical sample using a stated datum, method, instrument, resolution, repeatability, sample count, and condition.
- `D`: concept assumption, visual estimate, generic library geometry, remembered value, or exploratory envelope.

The letters identify source classes; they are not a simple quality ranking. For manufacturing-critical, regulated, mating, mounting, protective, terminal, or moving electrical-contact geometry, only `A` or applicable `C` may support `CONFIRMED`. If the available evidence is `B` or `D`, set the dimension status to `TBD` and restrict it to provisional concept work.

Standard identity, edition, and applicability do not expose hidden normative dimensions. A public catalog entry can confirm that a standard exists without authorizing remembered or image-derived values. Likewise, a brand page may support a published rating, form factor, or feature direction but not unpublished socket holes, protective shutters, terminals, installation holes, USB cutouts, or internal copper geometry.

When a regulated interface lacks A or C evidence, model a named certified-module reservation envelope with installation data `TBD`; do not hand-draw a plausible interface pattern. Run `scripts/check_dimension_authority.py` on a structured register.

Use this record for each dimension:

```text
dimension_id:
dimension_kind:
category:
value_and_units:
status: CONFIRMED | TBD
authority_level: A | B | C | D
authority_basis:
source_id_and_revision:
datum_and_method:
applicability_and_limitations:
owning_parameter:
```

## Open-Source Tool Use

- Verify the repository owner, archived status, current maintenance, documentation, license, default branch, and release or commit used.
- Pin production workflows to tested versions. A current repository page does not prove compatibility with the installed environment.
- Read API documentation and executable tests before relying on a function.
- Treat project defaults as software behavior, not engineering standards.
- Re-run deterministic fixtures after any kernel, exporter, dependency, or schema change.

## Curated Public Sources

Retrieved 2026-07-17. Recheck identity and currency before future use.

### Human-centered design and safety

- [ISO 9241-210:2019 official catalog](https://www.iso.org/standard/77520.html): identifies the current public catalog entry for human-centred design of interactive systems. Use its process scope; obtain authorized normative content before claiming conformity.
- [ISO 12100:2010 official catalog](https://www.iso.org/standard/51528.html): identifies the public catalog entry for machinery risk assessment and risk reduction. Use it to route safety work, not as a substitute for the full applicable standard set.
- [NASA Human Integration Design Handbook](https://www.nasa.gov/human-integration-design-handbook/): official page for HIDH Revision 1, Human Integration Design Processes, and the 2025 OCHMO anthropometry, biomechanics, and strength handbook. Use as transparent human-systems background and method evidence; spaceflight populations and environments do not automatically generalize to consumer products.

### Design process

- [Design Council Double Diamond](https://www.designcouncil.org.uk/our-resources/the-double-diamond/): official framework separating divergent and convergent discovery and development. Use it as a process model, not as a release or engineering standard.

### Surface analysis and geometry kernels

- [Rhino 8 Zebra](https://docs.mcneel.com/rhino/8/help/en-us/commands/zebra.htm): official description of stripe-map visual analysis for surface smoothness and continuity.
- [Rhino 8 CurvatureAnalysis](https://docs.mcneel.com/rhino/8/help/en-us/commands/curvatureanalysis.htm): official false-color surface-curvature analysis reference.
- [Open CASCADE Technology Modeling Data](https://dev.opencascade.org/doc/overview/html/occt_user_guides__modeling_data.html): official OCCT geometry and topology modeling documentation. Pin behavior to the kernel version in use.

### Camera, color, and exchange validation

- [Blender camera manual source](https://projects.blender.org/blender/blender-manual/src/branch/main/manual/render/cameras.rst): official camera model documentation covering perspective, focal length or field of view, and orthographic behavior.
- [Blender color-management manual](https://docs.blender.org/manual/en/latest/render/color_management/index.html): official color-pipeline entry point. Record the exact Blender, OpenColorIO, display, view transform, look, and output settings used.
- [Khronos glTF Validator](https://github.com/KhronosGroup/glTF-Validator): official Khronos validator producing JSON findings and asset statistics against glTF 2.0. A clean glTF validation report proves format conformance, not CAD or manufacturing equivalence.

### Maintained CAD and geometry projects

- [FreeCAD](https://github.com/FreeCAD/FreeCAD): official open-source parametric 3D modeler using OpenCASCADE with Python access and drawing workflows.
- [CadQuery](https://github.com/CadQuery/cadquery): script-based parametric B-rep CAD framework based on OCCT. Favor reproducible named parameters and pinned dependencies.
- [build123d](https://github.com/gumyr/build123d): Python parametric B-rep framework built on Open Cascade. Verify the tested API and branch or release before use.
- [trimesh](https://github.com/mikedh/trimesh): maintained triangular-mesh loading and analysis library emphasizing watertight surfaces. Use for mesh-native checks, not exact B-rep authority.
- [Open CASCADE Technology](https://github.com/Open-Cascade-SAS/OCCT): official open-source CAD/CAM/CAE geometry platform used by several parametric CAD stacks.

## Claim Boundary

Research can support tool selection, process, terminology, and a bounded method. It cannot by itself prove that the current product is usable, attractive, safe, manufacturable, compliant, or approved. Preserve source identity and run task-specific design, engineering, prototype, and human review.
