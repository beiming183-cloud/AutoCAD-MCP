# GPS, GD&T, Tolerance, and Inspection

Read this reference for geometrical product specifications (GPS), GD&T-style feature-control requirements, datum systems, fits, tolerance stacks, surface requirements, inspection planning, or measurement evidence.

## Contents

- Standards and semantic integrity
- Requirement model
- Datum systems and tolerance zones
- Size, fits, and tolerance stacks
- Inspection planning
- Measurement uncertainty and decision rules
- Model, drawing, and PMI agreement
- Release gates

## Standards and Semantic Integrity

Use the governing GB/T/GPS standard number and edition named by the project or verified from an official source. Do not mix ISO/GPS, ASME GD&T, legacy drawing conventions, organization practices, or software defaults without documenting the governing semantics.

Do not infer a geometric tolerance, datum modifier, material-condition modifier, general tolerance, surface texture, or inspection acceptance rule from appearance. Keep supplied requirements separate from derived geometry and engineering recommendations.

## Requirement Model

Represent every production characteristic with a stable ID and structured fields:

- Owning part/configuration/revision and controlled feature(s).
- Characteristic type: size, form, orientation, location, run-out, profile, surface texture, edge, fit, or another governed requirement.
- Nominal/basic value, tolerance/zone shape, units, modifiers, datum-reference sequence, extent/scope, and standard edition.
- Requirement source and status (`supplied`, `derived`, `assumed`, or `TBD`).
- Associated model geometry, drawing/PMI object, manufacturing process relevance, and inspection method/evidence.

Check that every symbol applies to geometrically valid features and that its tolerance zone, extent, and datum references are unambiguous. Reject detached annotations, contradictory duplicate requirements, undefined symbols, impossible zones, and requirements that constrain the wrong feature.

## Datum Systems and Tolerance Zones

- Identify physical datum features and the ideal datums/simulators they establish; do not treat a convenient model origin as a production datum without justification.
- Verify primary/secondary/tertiary precedence, remaining degrees of freedom, accessibility, stability, size/extent, and compatibility with assembly function and inspection setup.
- Check that each datum reference resolves to the intended feature in the released configuration and survives model/drawing/PMI export.
- Validate the tolerance-zone geometry and orientation against the controlled feature and datum reference frame. Distinguish form, orientation, and location controls rather than treating them as generic numeric tolerances.
- Apply material-condition, projected-zone, free-state, tangent-plane, common-zone, all-around, or similar modifiers only when supplied and supported by the governing standard/toolchain.

Use an independent coordinate/datum reconstruction for critical characteristics. A feature-control frame that renders correctly is not proof that the semantic association or datum sequence is correct.

## Size, Fits, and Tolerance Stacks

- Keep nominal size, upper/lower limits, tolerance zone, fit designation, envelope/independence rules, and general-tolerance applicability explicit.
- Confirm that general tolerances do not silently duplicate or override individually toleranced characteristics.
- Evaluate mating limits, allowance/interference, assembly sequence, thermal/finish/process effects, and functional clearance using the actual selected standards and revisions.
- Build each tolerance stack from a signed dimension/path graph with common datums and direction. Detect missing links, double-counting, closed chains, incompatible configurations, and correlated dimensions.
- Use worst-case, RSS, Monte Carlo, or another statistical method only when its assumptions are stated. Do not turn RSS/Monte Carlo into a production pass without justified distributions, correlations, process capability, sample basis, and acceptance criteria.
- Report contributors, sensitivity, margin, and unresolved inputs; do not provide only a final stack number.

## Inspection Planning

For each release-critical characteristic, define:

- Characteristic ID, nominal/tolerance, units, datum setup/alignment, measurement locations/extent, and configuration.
- Method/instrument or functional gauge concept, required range/resolution, accessibility/fixturing, environmental or surface-condition needs, and sample plan when supplied.
- Data-reduction algorithm, filtering/fitting convention, outlier policy, acceptance boundary/inclusivity, and required record format.
- Calibration/traceability status, expected measurement uncertainty, and responsible human/process when formal acceptance is required.

Check measurability early. Flag inaccessible datums, hidden surfaces, flexible/deformable setups, burr/coating ambiguity, insufficient instrument resolution, unstable fixturing, and requirements that cannot be evaluated by the proposed method.

Design verification and product inspection are different evidence. A CAD nominal check does not prove a manufactured part conforms; a scan overlay without an agreed alignment and uncertainty does not prove dimensional acceptance.

## Measurement Uncertainty and Decision Rules

Record the uncertainty source/model appropriate to the measurement: instrument, calibration, setup/alignment, environment, repeatability/reproducibility, sampling, fitting/filtering, surface condition, and operator/process effects.

- Compare uncertainty with the tolerance and risk, but do not enforce a universal ratio such as 10:1 without a governing requirement.
- State the acceptance/guard-band decision rule and treatment of results near the specification boundary.
- Use `NOT_EVALUATED` when uncertainty or traceability is unavailable for a formal conformity claim.
- Keep measured value, expanded/combined uncertainty as applicable, coverage assumptions, limit, margin, and verdict separately visible.

## Model, Drawing, and PMI Agreement

- Cross-reference each characteristic ID across native model/PMI, 2D drawing, BOM/configuration, inspection plan, and report.
- Verify displayed and semantic PMI separately. Confirm association to the correct geometry, datum order, modifiers, units, annotation plane/view, and configuration.
- After STEP/AP242 or another MBD exchange, re-import and compare semantic PMI counts/types/associations as well as graphical appearance. Report downgraded graphical-only annotations.
- Regenerate drawings after model changes and detect stale dimensions, annotations attached to replaced topology, and conflicting dual authority between 2D and 3D definitions.

## Release Gates

Do not release a characteristic set until:

1. Governing standards/editions and requirement sources are recorded.
2. Every required characteristic has valid geometry, semantics, datum references, scope, and configuration mapping.
3. Fits and tolerance stacks use traceable contributors and justified methods.
4. Critical requirements have a feasible inspection method and decision rule.
5. Measurement uncertainty/traceability limitations are disclosed and formal claims are gated accordingly.
6. Native model, drawing, PMI exchange, inspection plan, and report agree or conflicts are explicit.

Report `blocked`, `draft`, or `candidate after human review`; never claim metrology certification or production acceptance without the authorized process and actual measurement evidence.
