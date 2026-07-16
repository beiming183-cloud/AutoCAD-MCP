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
9. Full AutoCAD produces milestone previews through native PDF plotting instead
   of desktop/window screenshots.
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
16. Full AutoCAD uses COM `Save`/`SaveAs` for DWG and DXF before falling back
    to command-driven saving.
17. Hidden automation sessions fall back to AutoCAD's COM `HWND`, and DXF
    audits normalize `TEXT` and `MTEXT` heights without cross-type lookups.
18. AutoCAD is visible by default, can be brought to the foreground before
    every drawing command, and exposes `view.show_window` for manual restore.
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

## MCP client registration

The server uses standard MCP stdio and can be registered in Codex, Claude Code,
Claude Desktop, Cursor, or any compatible client. This workstation uses the
`file_ipc` backend and a local D-drive output override.
