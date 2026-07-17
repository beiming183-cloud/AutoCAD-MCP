# Local installation notes

- Source: https://github.com/puran-water/autocad-mcp
- Source commit: `95476a33a1c246308326eb4709d6379ef2efdbc1`
- Installed: 2026-07-15
- Runtime: Python 3.12 virtual environment in `.venv`

## Local changes

1. Arbitrary AutoLISP execution is disabled unless
   `AUTOCAD_MCP_ALLOW_ARBITRARY_LISP=true` is explicitly set.
2. `ezdxf` is preloaded before the MCP event loop starts to avoid a Windows
   native-module import stall.
3. File IPC reads `AUTOCAD_MCP_IPC_DIR` and defaults to portable `C:/temp`.
4. File IPC can auto-start a configured AutoCAD executable and wait for its
   main window when `AUTOCAD_MCP_AUTOSTART=true`.
5. AutoCAD window detection verifies the owning process is `acad.exe`, so the
   Start page and localized drawing titles are supported.
6. A configured dispatcher can be loaded for the current AutoCAD session with
   `AUTOCAD_MCP_LISP_PATH`, without changing AutoCAD's persistent trust paths.
7. Full AutoCAD uses COM `ActiveDocument.SendCommand` for reliable background
   command delivery; the original window-message path remains the LT fallback.
8. Structured drawing audits report bounded entity geometry, layer/type counts,
   drawing bounds, and added/modified/removed handles.
9. Full AutoCAD produces milestone previews directly through its native PNG
   plot device instead of desktop/window screenshots or temporary PDF files.
10. Existing DXF files can be parsed into normalized, bounded audit JSON.
11. Automatic screenshot attachments are disabled by default; direct window
    capture remains available for UI diagnostics.
12. Standard mechanical layers are created without interactive `-LAYER` or
    linetype prompts, including built-in `CENTER` and `HIDDEN` definitions.
13. Structured entity batches provide a high-throughput alternative to
    arbitrary AutoLISP while preserving the command whitelist.
14. File IPC encodes Chinese annotation text as AutoCAD Unicode escapes and
    exposes explicit hatch angle/scale parameters.
15. `system.recover` cancels stuck commands outside the dispatcher and cleans
    abandoned IPC files; timeouts trigger the same cancellation path.
16. Full AutoCAD uses COM `Save`/`SaveAs` for DWG and the non-switching COM
    export API for DXF.
17. Hidden automation sessions fall back to AutoCAD's COM `HWND`, and DXF
    audits normalize `TEXT` and `MTEXT` heights without cross-type lookups.
18. AutoCAD is visible but initially minimized by default. Drawing never steals
    focus; a user-restored window remains open, and `view.show_window` is the
    explicit foreground action while `view.minimize_window` returns it to the taskbar.
19. Transient COM call rejection while AutoCAD is still loading is retried for
    up to five seconds before falling back to window-message delivery.
20. IPC waits for `CMDACTIVE=0` before and after requests, and entity-producing
    commands verify that `entlast` actually changed before reporting success.
21. Full AutoCAD creates dimensions through native COM methods; prompt-driven
    AutoLISP dimensions remain available as the LT fallback.
22. Geometry-changing operations automatically center and fit drawing extents;
    batch creation suspends intermediate fits and performs one final fit.
23. All generated outputs are organized under `AUTOCAD_MCP_OUTPUT_ROOT`. The
    portable default is `~/Documents/AutoCAD-MCP`; this workstation overrides
    it to `D:/CAD-Automation`. External output paths are contained unless enabled.
24. `drawing.workspace` reports the managed specs, scripts, models, drawings,
    exports, reports, outputs, jobs, templates, incoming, archive, and log folders.
25. `drawing.deliver` creates isolated industrial delivery jobs with source and
    exported-DXF audits, configurable validation gates, DWG/DXF/PDF artifacts,
    atomic manifests, and SHA-256 checksums.
26. `system.ensure_ready` performs AutoCAD discovery/startup, active-document
    creation, dispatcher loading, version handshake, and IPC ping without
    misidentifying full AutoCAD as AutoCAD LT.
27. MCP failures are emitted with `isError=true` and stable structured error
    codes, recoverability, and recommended actions.
28. `drawing.set_variables` safely updates a numeric whitelist; mechanical
    setup applies millimeter, dimension, and linetype-scale defaults.
29. Drawing and DXF audits include units, geometry digests, and line/polyline
    DRC for zero/short segments, duplicate vertices/entities, and self-crossing.
30. Delivery passes paper/orientation/style/scale/centering into native plotting
    and verifies type/layer counts, bounds, units, digest, and exported DRC.
31. Geometry DRC includes an endpoint topology graph plus configurable gap,
    dangling-endpoint, interior-crossing, tangency, equal-radius, and projection checks.
32. Structured batches are atomic by default and use AutoCAD undo transactions
    or tracked-entity rollback when a batch operation fails.
33. File IPC exposes explicit trim, extend, break, join, geometric constraints,
    and mathematically solved tangent arcs without arbitrary AutoLISP.
34. Full AutoCAD exposes native boxes, cylinders, extrusions, revolutions,
    sweeps, and boolean solids through a dedicated standard MCP tool.
35. Entity queries include arc endpoints, polyline bulges, MText width and
    attachment, block attributes, ownership, bounds, length, and area when available.
36. `drawing.render_preview` writes a real white-background native-plot PNG with
    requested/actual DPI, pixel dimensions, force-overwrite behavior, SHA-256,
    and geometry digest, without launching or retaining a PDF.
37. Active-document readiness has a separate bounded timeout so a newly started
    AutoCAD window can finish COM registration before dispatcher loading begins.
38. The manual visible-AutoCAD smoke test closes and deletes generated test CAD
    artifacts by default while retaining a timestamped JSON evidence record.
39. Structured entity creation uses frozen strict contracts, reads every handle
    back, and returns `requested`, `actual`, and `diff`; mismatches are erased with
    `E_POSTCONDITION_MISMATCH` and fail the surrounding atomic transaction.
40. Entity semantics (`component_id`, `line_class`, `intentional_open_end`) feed
    topology audits. Unclassified dangling endpoints and interior crossings now
    fail by default, while explicit intentional open ends can be accepted.
41. Plot delivery rejects title-block scale declarations that conflict with the
    actual fit/fixed plotting mode, and PDF generation never launches a viewer.
42. `drawing.create` reports and verifies its requested and actual managed file
    name instead of silently returning `Drawing1.dwg`.
43. AutoCAD process exit and fatal-error dialogs are classified as
    `E_AUTOCAD_CRASHED`; they no longer fall through to `E_NO_ACTIVE_DOCUMENT`.
44. First-document startup uses `Documents.Add` before reading `ActiveDocument`,
    and dispatcher execution remains isolated in the external Python/File IPC process.
45. `drawing.audit_dxf` bypasses backend initialization and parses DXF files fully
    offline, including when AutoCAD is stopped or has no document.
46. File and system-call errors carry operation, parameter fields, system call,
    path, exception type, errno/winerror, system message, and recovery action.
47. PDF plotting defaults to A3 landscape FIT and verifies the generated PDF
    mediabox, detected paper, orientation, scale mode, document path, and output path.
48. Runtime logs are mirrored to a BOM-prefixed UTF-8 file so Chinese Windows
    PowerShell and text editors display diagnostic messages correctly.
49. Active document identity is explicit and revision guarded. Mutating MCP calls
    require `doc_id` plus `expected_revision` and reject wrong or stale contexts.
50. Public `transaction.begin`, `transaction.commit`, and `transaction.rollback`
    wrap AutoCAD undo marks; atomic batches retain all-or-nothing behavior.
51. Missing layers fail before entity creation with `E_LAYER_NOT_FOUND` instead
    of being auto-created or falling back to layer `0`.
52. Validated delivery uses a read-only `Wblock` copy and verifies that document
    ID and active path remain unchanged through DWG/DXF/PDF packaging.
53. PDF and PNG outputs publish by same-directory temporary file plus atomic
    rename. Locked targets return `E_OUTPUT_LOCKED` and leave no partial output.
54. Native PNG output corrects device-specific portrait/landscape rotation and
    reports the actual pixel orientation and whether correction occurred.
55. `system.status` publishes an honest industrial capability matrix that keeps
    unverified edge/face selection, shelling, assemblies, motion, analysis, and
    offscreen material rendering out of the supported feature set.
56. Regression coverage alternates two documents 100 times, checks independent
    revisions, transaction state, batch rollback, missing-layer invariance,
    delivery document isolation, locked output cleanup, and PNG orientation.
57. Cold startup requires three consecutive reads of the same active document,
    and dispatcher load/ping uses a bounded retry to absorb COM registration delay.
58. PDF plotting guards the exact generated temporary filename, hides and closes
    only its external viewer window, restores the last non-AutoCAD foreground
    window, and records viewer suppression and focus-recovery evidence.
59. When AutoCAD holds a plot source lock, publication copies to a second verified
    temporary file and atomically renames that copy; final outputs remain complete
    and locked destinations still return `E_OUTPUT_LOCKED`.
60. Industrial-product entities add component, design-role, view, crossing, and
    source-authority semantics; motion overlays and prepared review geometry no longer
    contaminate ordinary topology failures.
61. Full AutoCAD creates analytic native-B-rep rounded boxes and controlled module,
    annular-gap, detent, rotary-layer, recess, and authoritative USB-cutout features.
62. General edge fillet/chamfer operations reject volatile edge indices with
    `E_STABLE_FEATURE_SELECTION_UNAVAILABLE`; registered analytic radii remain queryable.
63. Fixed product views return camera and PNG composition evidence. Section and
    exploded views require caller-prepared geometry and are never silently fabricated.
64. Motion uses explicit axes, angles, and limits with broad-phase static and sampled
    rotated-AABB screening clearly labeled as non-exact B-rep interference.
65. Appearance, ergonomics, adapter clearance, cable management, stability, and
    mains/rotation safety reviews use independent PASS/FAIL/NOT_EVALUATED verdicts.

## MCP client registration

The server uses standard MCP stdio and can be registered in Codex, Claude Code,
Claude Desktop, Cursor, or any compatible client. This workstation uses the
`file_ipc` backend and a local D-drive output override.
