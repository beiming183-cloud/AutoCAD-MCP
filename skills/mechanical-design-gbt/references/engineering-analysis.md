# Engineering Analysis Evidence

Read this reference whenever a task asks whether a design is physically correct, strong enough, stiff enough, stable, durable, thermally adequate, fluidically adequate, dynamically safe, or otherwise validated beyond geometry/kinematics.

## Contents

- Claim levels
- Analysis input contract
- Mathematical and physical sanity checks
- Kinematics and mechanisms
- Structural analysis
- Thermal, fluid, fatigue, and dynamic analysis
- Verification, validation, and sensitivity
- Evidence and reporting

## Claim Levels

Classify the evidence before calculating:

- `geometric plausibility`: shape, continuity, assembly, contact/clearance, and defined motion only.
- `screening estimate`: transparent hand calculation or conservative bound for a stated load case.
- `simulation candidate`: solver result with documented model/inputs and internal checks, pending required verification/validation.
- `engineering conclusion`: applicable requirements, verified model, convergence/sensitivity evidence, validation basis, and authorized review support the stated scope.

Do not upgrade a claim because a contour plot or animation looks convincing. Use `NOT_EVALUATED` outside the available evidence.

## Analysis Input Contract

Record before analysis:

- Exact revision/configuration and analysis geometry, including simplifications, defeaturing, symmetry, contacts, joints, and omitted components.
- Load cases/combinations, magnitudes, directions, locations/distributions, time/history, duty cycle, environment, and source.
- Boundary conditions, supports, constraints, initial conditions, contact/friction, preload, and assembly state.
- Material model/properties with units, temperature/rate/direction dependence, source, lot/condition, and allowable basis.
- Governing equations/model type, solver/version, element/discretization type, mesh/time step, nonlinearities, tolerances, and stopping criteria.
- Acceptance criteria, safety/reliability factors, applicable standard, and required human review.

Do not invent a load, material property, restraint, coefficient, allowable, fatigue curve, convection coefficient, turbulence model, or safety factor. A plausible default may support an explicitly labeled sensitivity study, not a release pass.

## Mathematical and Physical Sanity Checks

Before trusting a solver or CAD-derived calculation:

- Perform dimensional/unit analysis at every interface; reject equations or data paths with inconsistent dimensions.
- Check sign conventions, coordinate frames, angle units, reference temperatures/pressures, and mass versus force.
- Estimate order of magnitude and limiting cases. Compare with simple closed-form or conservative bounds where applicable.
- Check symmetry/antisymmetry, monotonic trends, continuity, positivity, and invariants expected from the model.
- Verify conservation/equilibrium: force and moment reactions, mass/flow balance, energy/work/heat balance, and charge only when the domain applies.
- Check that mesh/body mass, center of gravity, inertia, area/volume, and section properties agree with trusted CAD values within declared tolerances.

When an independent estimate materially disagrees with the numerical result, investigate the model rather than averaging the answers.

## Kinematics and Mechanisms

- Count degrees of freedom independently and compare with solver/mate state.
- Verify joint axes, limits, phase/timing, loop closure, singular positions, mechanical advantage, velocity/acceleration direction, and intended grounded reference.
- Test rest, representative mid-states, both limits, reversals, and coupled/mirrored configurations.
- Check continuous or sufficiently sampled swept interference/clearance. Record sampling step and tunneling risk.
- Separate prescribed motion from force-driven dynamics. A kinematic animation does not prove loads, torque, energy, stability, or durability.

## Structural Analysis

- Verify element formulation and dimensional idealization (solid, shell, beam, connector) against geometry and expected behavior.
- Check support realism, rigid-body modes, overconstraint, contact state, preload, bolt/joint representation, and load application area.
- Inspect deformed shape and reactions before stress. Confirm load/reaction equilibrium and plausible displacement direction/magnitude.
- Distinguish membrane/bending, nominal/local/peak stress, contact pressure, buckling modes, and singularities at point loads, sharp re-entrant corners, fixed edges, or idealized contacts.
- Demonstrate mesh convergence for the response quantity used in the decision. Do not use a singular peak that diverges with refinement as a material stress verdict.
- For buckling, nonlinear, plasticity, large deformation, or contact problems, document imperfections/initial state, increment strategy, convergence history, and stability of the solution path.

## Thermal, Fluid, Fatigue, and Dynamic Analysis

Apply only the relevant domain rules:

- Thermal: verify heat inputs, conduction paths, contacts, convection/radiation assumptions, reference/ambient conditions, steady versus transient choice, and energy balance.
- Fluid: verify domain closure, fluid properties/state, inlet/outlet/wall conditions, mass conservation, pressure reference, mesh near walls/features, regime/turbulence assumptions, and convergence beyond residuals alone.
- Fatigue: require load spectrum/cycles, mean-stress treatment, material fatigue data/condition, surface/size/notch/environment effects, and damage model; static yield margin alone is not fatigue life.
- Dynamics/vibration: require mass/stiffness/damping basis, constraints, excitation spectrum/time history, modal participation/range, time/frequency resolution, and resonance/nonlinear-contact treatment.
- Wear, lubrication, creep, fracture, thermal shock, and multiphysics claims require their own validated models and inputs; do not infer them from a neighboring analysis.

## Verification, Validation, and Sensitivity

- Verify equations/implementation with a benchmark, manufactured solution, hand calculation, or known limiting case when feasible.
- Demonstrate mesh, time-step, and solver-tolerance independence for decision-driving outputs; record at least the compared discretizations and observed change.
- Run sensitivity studies for uncertain/high-leverage inputs and modeling choices. Report ranges and threshold crossings, not only a nominal result.
- Compare with test, field, supplier, or trusted reference data when making a validation claim. Separate calibration data from independent validation data.
- Track solver warnings, nonconvergence, distorted elements, contact chatter, negative volumes, energy imbalance, mass scaling, stabilization, or auto-adjustments as findings.

Passing one solver run is not independent verification. Re-running the same setup with more output images adds no confidence.

## Evidence and Reporting

Report:

- Claim level and exact revision/configuration/load case.
- Inputs/sources, assumptions, simplifications, equations/model, solver/version, and acceptance criteria.
- Mesh/time-step/tolerance data, convergence and conservation/equilibrium results, independent checks, sensitivity, and validation basis.
- Decision-driving measured/calculated values, margins, units, locations, and uncertainty/limitations.
- Failed cases, warnings, unavailable checks, and required design/test/human actions.

Never call a design safe, certified, code-compliant, or production-approved solely from agent-generated analysis. State the bounded conclusion the evidence supports and identify every unevaluated failure mode relevant to the requested claim.
