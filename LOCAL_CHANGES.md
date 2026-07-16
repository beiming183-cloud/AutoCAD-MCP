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
3. File IPC uses the local `ipc` directory instead of `C:/temp`.
4. File IPC can auto-start a configured AutoCAD executable and wait for its
   main window when `AUTOCAD_MCP_AUTOSTART=true`.
5. AutoCAD window detection verifies the owning process is `acad.exe`, so the
   Start page and localized drawing titles are supported.
6. A configured dispatcher can be loaded for the current AutoCAD session with
   `AUTOCAD_MCP_LISP_PATH`, without changing AutoCAD's persistent trust paths.
7. Full AutoCAD uses COM `ActiveDocument.SendCommand` for reliable background
   command delivery; the original window-message path remains the LT fallback.

## Codex registration

The server is registered as `[mcp_servers.autocad]` in the global Codex
`config.toml`, with backend selection set to `auto`.
