# Configuration, Motion, And Render Evidence

## Component Model

Create one definition per part or subassembly and instantiate it. Assign stable `component_id`, parent, source revision, local datum, transform, material/appearance intent, and interface IDs. Fix the base component explicitly.

Represent a rotating multi-layer product as a component tree with named axes and interfaces. Each configuration changes transforms or parameters; it must not copy an uncontrolled second set of bodies.

## Mates And Motion

For every joint record type, axis/datum references, limits, home position, remaining degrees of freedom, expected user force/torque if known, stops, cable/hose implications, and collision exemptions.

Validate discrete named configurations and continuous sweeps. Report first collision time/angle, colliding component IDs, penetration/clearance, configured limits, and sampled step or continuous method. A clean endpoint check does not prove a clean motion path.

## Named Cameras

Use stable cameras for comparison. Record eye, target, up vector, projection, lens/focal length, clipping, configuration, visual style, resolution, and background. Keep a common camera set for revisions and configurations.

At minimum create front, rear, left, right, top, bottom, isometric, and one use-context view when relevant. Use contact sheets for comparison, not as the only full-resolution evidence.

## Render Verification

For each render verify file existence, dimensions, hash, non-empty pixel ratio, expected visible components, actual camera parameters, orientation, clipping, background, and revision/configuration binding. Reject blank, stale, cached, wrong-document, wrong-configuration, or unexpectedly cropped output.

Keep render publication atomic: write and validate a temporary file, then rename. On a locked destination return a structured lock error and leave no half-written artifact.

