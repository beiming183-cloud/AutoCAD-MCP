# Local installation notes

- Source: https://github.com/puran-water/autocad-mcp
- Source commit: `95476a33a1c246308326eb4709d6379ef2efdbc1`
- Installed: 2026-07-15
- Runtime: bundled Codex Python 3.12 virtual environment in `.venv`

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

## Codex registration

The server is registered as `[mcp_servers.autocad]` in the global Codex
`config.toml`, with backend selection set to `file_ipc` on the local workstation.
