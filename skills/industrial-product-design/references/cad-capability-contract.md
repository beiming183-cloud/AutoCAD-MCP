# CAD Capability Contract

Interrogate the active backend before refined modeling. Record each required capability as `SUPPORTED`, `UNSUPPORTED`, or `UNVERIFIED`; never treat an operation name as proof.

## P0 Trust

Require document create/open/activate responses to include `doc_id`, `requested_path`, `active_doc_id`, `active_path`, and monotonic `revision`. Require every mutation to carry `doc_id` and `expected_revision`.

Require real begin/commit/rollback transactions. Any failed batch item must restore the pre-batch model. Missing layers or features must fail before mutation. Convert CAD/COM/system failures into structured operation, parameter, path, system-call, error-code, and recovery data.

Require created entities or features to return stable identity, owner document/component, role/layer, bounds, mass properties where meaningful, and readback differences. Delivery copies and exports must not change the active source document.

## Refined Product Modeling

Verify native support and deterministic selection for fillet/chamfer, shell, offset face, loft, sweep, draft, mirror, pattern, 3D transform, and atomic boolean operations before using them. Require semantic or persistent feature selection; volatile edge/face indices are unacceptable for unattended edits.

Verify surface continuity and analysis capabilities separately. If G0/G1/G2, wall thickness, draft angle, curvature, zebra, or B-rep validity cannot be measured, mark them `NOT_EVALUATED` and constrain maturity.

## Assembly And Configuration

Verify component definitions/instances, mates, degrees of freedom, configurations, transforms, motion limits, swept interference, and exploded views. When unavailable, build only a labeled static packaging study; do not call duplicated solids a parametric assembly.

## Camera And Render

Prefer a native/offscreen render interface accepting document identity, configuration, eye, target, up vector, projection, lens/focal length, resolution, visual style/materials, shadow/AO state, clipping/transparency, and output path.

Require actual camera parameters, image dimensions, content hash, non-empty pixel ratio, and visible-component list in the response. Do not depend on GUI focus, `SendKeys`, screenshots, or frontmost-window state for product render evidence.

## Honest Degradation

- Primitive solids only: allow packaging and massing.
- No stable feature selection: stop before unattended edge/face refinement.
- No assembly/configuration model: allow labeled static states only.
- No native/offscreen 3D render: allow viewport evidence but do not claim product-quality rendering.
- No required validator: report `NOT_EVALUATED`; never substitute entity count or save success.

