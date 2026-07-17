# Backend Routing and Representation Strategy

Read this file before selecting a CAD, surface, mesh, rendering, or exchange tool for a product-design task.

## Route by Required Evidence

Do not select a backend merely because it is available. Select the weakest combination of tools that can produce and verify the required claim without representation fraud.

| Need | Preferred representation and route | Required evidence | Invalid shortcut |
|---|---|---|---|
| Early proportion and packaging | Named-parameter massing in native CAD or scripted B-rep | Same-scale orthographic plus controlled perspective views | Separate eyeballed 2D views |
| Exact parts and interfaces | Native parametric B-rep, OCCT-based CAD, CadQuery, build123d, FreeCAD, or equivalent | Rebuild, topology, units, datums, measurements, STEP re-import | Mesh-only dimensional authority |
| Refined appearance surfaces | NURBS or capable surface CAD such as Rhino or equivalent | Edge continuity, zebra, curvature, sections, highlights, trim and sew checks | Shaded screenshot only |
| Assemblies and mechanisms | Native component hierarchy, joints, configurations, or reproducible transforms | DOF, states, limits, collisions, cable state, component identity | Loose duplicate bodies in model space |
| Mesh QA and visualization | trimesh or equivalent mesh-native validator | Manifoldness, watertightness, normals, bounds, tessellation disclosure | Calling a mesh exact CAD |
| glTF or GLB review delivery | Source export plus Khronos glTF Validator | JSON format report, hierarchy, transforms, animation and material checks | Successful viewer load only |
| Product rendering | Native CAD render or Blender-equivalent scene tied to source revision | Camera, lens, color pipeline, model hash, configuration, nonblank image | Pillow pseudo-3D or painted occlusion |
| GB/T manufacturing drawing | Mechanical CAD and `mechanical-drafting-gbt` | Projection, dimensions, line hierarchy, DRC, plot and exchange evidence | Marketing render with annotations |

## AutoCAD Boundary

Use AutoCAD for proven 2D drafting, block workflows, annotation, plotting, and limited solid massing when the exposed bridge supports them. Do not claim refined industrial-design 3D when the bridge lacks native solid fillets, shelling, surface continuity tools, component configurations, stable cameras, or native product rendering.

Always pass document-identity conformance before accepting any AutoCAD result. A file-open acknowledgement without active-path readback is failure.

## B-rep and Mesh Boundary

- Use B-rep for exact analytic geometry, topology, booleans, dimensions, sections, manufacturing interfaces, and STEP exchange.
- Use meshes for rendering, scanning, slicing, web viewing, and mesh-native analysis.
- Record tessellation parameters whenever B-rep becomes mesh.
- Keep source and derivative IDs, transforms, units, and hashes linked.
- Do not repair a derived mesh when an authoritative B-rep source exists; repair the source and regenerate.

## Multi-Tool Workflow

When one application cannot cover all evidence:

1. Freeze a revisioned authoritative B-rep source.
2. Export an explicit schema with units and transforms.
3. Re-import or independently parse the exchange artifact.
4. Bind surface, mesh, scene, and render derivatives to the source hash.
5. Keep cameras and configurations named and reproducible.
6. Return every accepted design change to the authoritative source.
7. Regenerate all affected derivatives and rerun only dependent checks.

Do not let the rendering scene become an untracked alternate design source.

## Capability Manifest

Record actual support and readback reliability, not marketing feature lists:

```text
backend_and_version:
kernel_and_version:
source_representation:
units_and_angle_convention:
parametric_history:
fillet_chamfer_shell:
loft_sweep_surface_trim_sew:
surface_continuity_analysis:
component_hierarchy_and_configurations:
joints_motion_collision:
camera_material_light_render:
import_export_schemas:
document_identity_readback:
atomic_rollback:
known_limitations:
conformance_fixture:
```

Re-run the relevant conformance fixture after upgrades. Mark missing or unreliable capabilities `NOT_EVALUATED` and cap maturity accordingly.
