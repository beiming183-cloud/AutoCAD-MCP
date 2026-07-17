# GB/T Mechanical Drafting Reference

## Standards Baseline

Use the following standards as the core baseline. Their status was checked on the National Public Service Platform for Standards on 2026-07-16. Recheck the official platform before making a time-sensitive or contractual claim that a drawing complies with the current edition.

### Core drawing presentation

- GB/T 4457.4-2002, Mechanical drawings - General principles of presentation - Lines.
- GB/T 4458.4-2003, Mechanical drawings - Dimensioning.
- GB/T 4458.5-2003, Mechanical drawings - Indication of tolerances for size and of fits.
- GB/T 14689-2008, Technical drawings - Sizes and layout of drawing sheets.
- GB/T 14690-1993, Technical drawings - Scales.
- GB/T 14691-1993, Technical drawings - Lettering.
- GB/T 14692-2008, Technical drawings - Projection methods.
- GB/T 17451-1998, Technical drawings - General principles of presentation - Views.
- GB/T 17452-1998, Technical drawings - General principles of presentation - Sections and cuts.
- GB/T 17453-2005, Technical drawings - General principles of presentation - Indicating areas on cuts and sections.
- GB/T 10609.1-2008, Technical drawings - Title blocks.

### Apply when required by the drawing

- GB/T 10609.2-2009, Technical drawings - Item lists.
- GB/T 4458.2-2003, Mechanical drawings - Item references on assembly drawings and their arrangement.
- GB/T 4459.1-1995, Mechanical drawings - Representation of screw threads and threaded fasteners.
- GB/T 4459.2-2003, Mechanical drawings - Representation of gears.
- GB/T 4459.3-2000, Mechanical drawings - Representation of splines.
- GB/T 4459.4-2003, Mechanical drawings - Representation of springs.
- GB/T 4459.5-1999, Mechanical drawings - Representation of centre holes.
- GB/T 4459.7-2017, Mechanical drawings - Representation of rolling bearings.
- GB/T 1182-2018, Geometrical product specifications (GPS) - Geometrical tolerancing - Tolerances of form, orientation, location and run-out.
- GB/T 131-2006, Technical product documentation - Indication of surface texture in technical product documentation.
- GB/T 1800.1-2020 and GB/T 1800.2-2020, ISO code system for tolerances on linear sizes.
- GB/T 1804-2000, General tolerances - Tolerances for linear and angular dimensions without individual tolerance indications.

Official metadata source: National Public Service Platform for Standards, State Administration for Market Regulation (`https://openstd.samr.gov.cn/`). Standard status alone does not establish that every rule in this reference is normative; some values below are practical CAD defaults.

## Requirement Integrity

- Classify each production requirement as supplied, derived from geometry, assumed, or unresolved.
- Derive only mathematically necessary values from trusted geometry. Do not derive design intent, fits, tolerances, materials, heat treatment, surface texture, or manufacturing processes from appearance.
- Preserve the source precision. Do not add decimal places that imply unsupported accuracy.
- Use `TBD` or an explicit assumption for missing production data. Never silently convert a plausible choice into a requirement.
- Resolve contradictions between geometry, dimensions, notes, and title-block data before claiming manufacturability.
- Record the standard number and edition actually applied to each contractual symbol/rule. Do not combine examples or remembered conventions from different editions into an undocumented hybrid.

## Practical Rules

### GB/T anchor rules

- Use first-angle projection as the GB/T workflow default only when the user, approved project template, and source drawing leave the method unspecified. Record that assumption; never mix first-angle view placement with a third-angle symbol or title-block declaration.
- Use the applicable standard sheet/frame and place the title block in the standard lower-right location unless an approved organization template defines another valid arrangement. Verify fields, orientation, and plot margins on the actual sheet.
- Use a Chinese engineering lettering style that satisfies the selected GB/T 14691 edition, including required Chinese-character proportions and plotted height/stroke. AutoCAD font mappings such as `gbenor`/`gbcbig`, SimHei, or FangSong are implementation-specific fallbacks; verify glyph coverage, substitutions, width/proportion, symbols, and PDF output rather than declaring compliance from the filename.

### Line hierarchy

- Visible outlines and visible edges: thick continuous line.
- Dimension, extension, leader, hatch, and projection lines: thin continuous line.
- Axes and lines of symmetry: thin chain line, extending slightly beyond the outline.
- Hidden edges: thin dashed line. Omit them in a section when they reduce clarity and add no necessary information.
- Select widths from the applicable standard series for the sheet and scale. Keep the thick-to-thin ratio visually distinct; approximately 2:1 is a practical target, not a universal fixed width.

### Text and symbols

- Use one technical lettering family and one text-height system.
- Choose plotted text heights from the applicable lettering series. A 3.5 mm dimension/note height is a common CAD default, not a universal requirement.
- Use the diameter symbol before cylindrical sizes and `R` before radii.
- Write fits with the size as one requirement, for example diameter 50 with tolerance zone `h6`; use the CAD application's proper diameter symbol rather than relying on a literal control code in exported output.
- Use `x` or the configured multiplication symbol consistently. Avoid `deg` when the CAD font can display a degree symbol.

### Dimensioning

- Give each required size once. Do not repeat the same size in another view.
- Place the shortest dimension nearest the object and larger overall dimensions outside it.
- Avoid dimension-line crossings and extension-line crossings. Do not use object outlines as dimension lines.
- Dimension from functional or datum surfaces. Avoid a fully closed chain unless one dimension is explicitly reference-only.
- Place shaft diameters in the longitudinal view with the diameter symbol. Place axial lengths below or above the view in aligned tiers.
- Use extension lines perpendicular to the measured feature and dimension lines parallel to it.
- Arrowheads, text, offsets, and spacing must be uniform throughout the sheet.

### Views and sections

- Keep views aligned according to the selected projection method. Do not mix first-angle and third-angle placement, and show the projection symbol when required by the drawing or organization.
- Draw the keyway correctly in longitudinal and end views. The end-view circle must be interrupted at the keyway opening.
- A section view requires a cutting plane and matching identifiers unless its location and convention make the meaning unambiguous.
- Use view names that match the actual representation. A view labeled as a section/cutaway must have a valid cutting relationship, visibility treatment, and hatch/section semantics; otherwise label it as an end view, schematic, principle diagram, or concept view as applicable.
- Hatch only cut material. Keep one angle and spacing for the same part, usually 45 degrees with even spacing.
- Do not hatch holes, slots, keyways, or space outside the material boundary.

### Standard simplified representations

- Keep the detailed manufacturing model and the standardized 2D representation as separate evidence. A routine drawing view should not project helical thread edges, every gear tooth, every spline tooth, every spring turn, or every rolling element merely because the 3D model contains them.
- For screw threads under GB/T 4459.1, use the applicable simplified external/internal-thread convention and a complete thread callout. For a visible external thread, keep the major-diameter boundary thick and the minor-diameter boundary thin. In an end view, draw the applicable thin diameter as an approximately three-quarter circle with the required opening rather than a full visually estimated circle. Verify thread termination, runout/length, section hatching, engagement, and designation as applicable.
- For gears under GB/T 4459.2, use the prescribed addendum, pitch, root, axis, mesh, and section conventions for the selected view/representation level. Do not substitute a decorative repeated-tooth polyline for either a valid manufacturing tooth form or a standard simplified drawing.
- Apply GB/T 4459.3, GB/T 4459.4, GB/T 4459.5, and GB/T 4459.7 when splines, springs, centre holes, or rolling bearings appear. Select the permitted simplified/schematic/detailed representation for the drawing purpose and keep the associated designation, quantity, section, and BOM data consistent.

Instantiate at least these release DRC rules when applicable:

- `GBT_PROJECTION_METHOD`: method, view placement, symbol, and title-block declaration agree.
- `GBT_TITLE_BLOCK_LAYOUT`: sheet/frame/title-block placement, fields, margins, and orientation match the approved template/standard.
- `GBT_CHINESE_TEXT_RENDER`: required glyphs, technical lettering style, plotted size/proportion, font substitution, and PDF output pass.
- `GBT_THREAD_REPRESENTATION`: external/internal boundaries, end view, termination, section behavior, and callout match the selected standard edition.
- `GBT_GEAR_REPRESENTATION`, `GBT_SPLINE_REPRESENTATION`, `GBT_SPRING_REPRESENTATION`, `GBT_CENTRE_HOLE_REPRESENTATION`, and `GBT_BEARING_REPRESENTATION`: the selected representation level and related annotations/BOM agree with geometry and purpose.
- `VIEW_SEMANTICS_CONSISTENCY`: view name, cutting plane, hatching, visibility, abstraction, and component coverage agree.
- `PLOT_SCALE_CONSISTENCY`: title-block numeric scale agrees with actual plotted transform; fit-to-page output uses an approved non-scale declaration such as `NTS` and records plot mode `FIT`.

Each result must record the standard edition, drawing/view/entity IDs, representation level, evidence, and any unsupported semantic object. A visually familiar symbol without traceable applicability is not a pass.

### Shaft-specific checklist

- End chamfers are represented geometrically and dimensioned once, for example `2x45 degrees` at each applicable end.
- Shoulder transitions are unambiguous. Add fillet radii or undercuts when manufacturing requires them.
- Bearing and fit seats use functionally plausible tolerance zones. Do not invent a fit without noting that parameters are assumed.
- Keyway width, depth, and length are all specified; a `30x5` note alone is ambiguous unless the convention is explicitly defined.
- Surface texture is attached to the relevant surface or declared as a valid general requirement, not floating decoratively.
- Overall length and segment lengths do not create an over-constrained closed chain.

## Staged Verification

Use risk-based verification instead of repeating every check blindly. Escalate any unexplained conflict to the next pass and do not claim completion while a material conflict remains.

### Pass 1: whole-drawing consistency

- Check all geometry once for impossible or degenerate conditions: open boundaries that should be closed, zero-thickness material, unintended overlaps, duplicate entities, broken tangency, off-axis concentric features, and disconnected solids.
- Recalculate simple mathematical relationships: chained and overall lengths, radii versus diameters, symmetry offsets, angles, pitch/count relationships, and section locations. Compare calculated values with displayed dimensions and source requirements.
- Trace each material boundary, hole, slot, keyway, shoulder, chamfer, fillet, and pattern across every view where it should appear. Projection coordinates, feature extents, visibility, and centerlines must describe one consistent part.
- For an assembly, check that mating geometry, supplied fits/clearances, motion envelopes, and part interfaces do not create obvious interference or disconnected load paths.

### Pass 2: independent cross-view reconstruction

Apply this pass to changed geometry, manufacturing-critical features, dense intersections, sections, and anything that failed or looked ambiguous in Pass 1.

- Reconstruct the feature independently from two views or from a view plus dimensions; predict its location and visible/hidden boundaries in the remaining view, then compare with the drawing.
- Verify every section against its cutting plane. Material and void regions before and behind the plane must produce the shown outline and hatch boundary.
- Recompute critical dimensions from geometry using a different route from the original construction when possible. For example, compare an overall value with the sum of independent segments rather than re-reading the same dimension object.
- For repeated or symmetric features, compare count, pitch, angular spacing, handedness, and mirrored orientation rather than checking only one instance.

### Pass 3: targeted release check

- Recheck all corrected conflicts, high-risk features, functional datums, mating interfaces, and critical dimensions after annotation and layout changes.
- Inspect the final plot/export for lost fonts or symbols, clipped content, incorrect scale, changed lineweights, stale dimensions, and view misalignment.
- Stop after all high-risk checks pass and no unexplained cross-view or numerical conflict remains. Do not spend extra passes on unchanged low-risk entities that passed deterministic checks.

### Physical plausibility boundary

- Check what the available data can support: geometric continuity, assembly/motion feasibility, obvious interference, minimum material continuity, and consistency of specified interfaces.
- Treat strength, stiffness, stability, fatigue, wear, thermal behavior, fluid performance, and safety as unverified unless the required loads, material properties, constraints, model, and acceptance criteria are available and an appropriate calculation or simulation is performed.
- Report the exact scope of physical validation. Use `not evaluated` instead of a favorable assumption when evidence is missing.

## Audit Checklist

- Trusted dimensions agree numerically with the geometry; coaxial, symmetric, tangent, parallel, and perpendicular relationships are geometrically valid where intended.
- Profiles that must be closed are closed, duplicate entities are removed, and no stray construction geometry will plot.
- Sheet/frame/title block follows the chosen standard and scale.
- Projection method and view alignment are consistent.
- Projection symbol and title-block declaration agree with the actual first-/third-angle view placement.
- View names and drawing title agree with the actual cutting/abstraction behavior and represented component scope.
- Visible, hidden, center, dimension, and hatch lines are visually distinct.
- All dimensions use one style, text height, arrow size, precision, and unit convention.
- No dimension crosses the part unnecessarily; no dimension is shown diagonally for a horizontal axial length.
- Diameter/radius symbols, fits, tolerances, and surface texture are attached to the correct features.
- Section hatching is bounded, evenly spaced, and excludes voids.
- Notes do not overlap geometry or dimensions.
- Viewports, annotation scales, fonts, symbols, and lineweights survive the final PDF/plot/export.
- Chinese text has no missing/substituted glyphs, and applicable thread/gear/spline/spring/bearing representations pass their GB/T rule IDs.
- Numeric title-block scale agrees with actual PDF scale; fit-to-page output is not mislabeled as `1:1`.
- Assumed and unresolved production requirements are disclosed; no invented value is presented as user-approved design intent.
- The drawing contains enough information to manufacture and inspect the part without duplicate or contradictory requirements.
