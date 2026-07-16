# AutoCAD MCP Server - Codex Edition

MCP server for full AutoCAD automation, AutoCAD LT automation, and headless DXF generation.

This edition is based on [puran-water/autocad-mcp](https://github.com/puran-water/autocad-mcp) and keeps its MIT license. It adds full AutoCAD COM command delivery, process-based window detection, optional AutoCAD startup, and session-scoped dispatcher loading.

Two backends, one API:

| Backend | Runtime | Requires AutoCAD? | Screenshot |
|---------|---------|-------------------|------------|
| **File IPC** | Windows Python | Yes - full AutoCAD or AutoCAD LT 2024+ | Win32 PrintWindow |
| **ezdxf** | Any platform | No (headless) | matplotlib render |

The server exposes **8 consolidated tools** (`drawing`, `entity`, `layer`, `block`, `annotation`, `pid`, `view`, `system`) over the MCP stdio transport. An MCP client (Claude Desktop, Claude Code, etc.) connects and drives AutoCAD through natural-language requests.

## Prerequisites (File IPC backend)

- **Windows 10/11** (the File IPC backend uses Win32 APIs for focus-free window messaging)
- **AutoCAD LT 2024 or newer** — AutoLISP support was added in LT 2024 for Windows. AutoCAD LT for Mac exists but does **not** support AutoLISP.
- **Python 3.10+** (Windows native — not WSL Python)
- **uv** package manager ([install guide](https://docs.astral.sh/uv/getting-started/installation/))

> The ezdxf headless backend works on any platform (Linux, macOS, WSL) for offline DXF generation without AutoCAD installed.

## Quick Start

### 1. Clone and install

```powershell
git clone https://github.com/beiming183-cloud/autocad-mcp-codex.git
cd autocad-mcp-codex
uv sync
```

### 2. Load the LISP dispatcher in AutoCAD

Open AutoCAD LT and load `mcp_dispatch.lsp` using **APPLOAD**:

1. Type `APPLOAD` in the AutoCAD command line
2. Browse to `<repo>/lisp-code/mcp_dispatch.lsp`
3. Click **Load**
4. You should see: `=== MCP Dispatch v3.1 loaded ===` and `Ready for commands via (c:mcp-dispatch)`

> **Tip:** Add the file to your AutoCAD Startup Suite (in the APPLOAD dialog) so it loads automatically with every drawing.

### 3. Configure your MCP client

Add to your MCP client configuration (e.g. Claude Desktop `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "autocad-mcp": {
      "command": "C:\\path\\to\\autocad-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "autocad_mcp"],
      "env": {
        "AUTOCAD_MCP_BACKEND": "file_ipc",
        "AUTOCAD_MCP_LISP_PATH": "C:\\path\\to\\autocad-mcp\\lisp-code\\mcp_dispatch.lsp"
      }
    }
  }
}
```

**Key points:**

- The `command` must point to the **Windows Python** inside the project venv (not WSL python).
- `AUTOCAD_MCP_BACKEND` can be `auto` (tries File IPC, then falls back to ezdxf), `file_ipc` (recommended for engineering drawings), or `ezdxf` (headless only).
- Full AutoCAD uses COM `SendCommand`; AutoCAD LT uses the original window-message transport.
- Set `AUTOCAD_MCP_AUTOSTART=true` and `AUTOCAD_MCP_ACAD_EXE` to let the MCP start AutoCAD when needed.

#### Running from WSL

If your MCP client runs in WSL (e.g. Claude Code), launch the server through `cmd.exe` so it runs as a native Windows process:

```json
{
  "mcpServers": {
    "autocad-mcp": {
      "type": "stdio",
      "command": "cmd.exe",
      "args": ["/d", "/s", "/c", "cd /d C:\\path\\to\\autocad-mcp && .venv\\Scripts\\python.exe -m autocad_mcp"],
      "env": { "AUTOCAD_MCP_BACKEND": "auto" }
    }
  }
}
```

### 4. Verify

From your MCP client, call:

```
system(operation="status")
```

You should see `backend: "file_ipc"` if AutoCAD is running, or `backend: "ezdxf"` for headless mode.

## Tools

### `drawing` — File/drawing management

| Operation | Description | File IPC | ezdxf |
|-----------|-------------|----------|-------|
| `create` | Reset to clean drawing (erase all + purge) | Yes | Yes |
| `open` | Open an existing drawing | Yes | Yes (DXF) |
| `info` | Get entity count and layers | Yes | Yes |
| `save` | Save current drawing (to path if given) | Yes | Yes |
| `save_as_dxf` | Export as DXF | Yes | Yes |
| `plot_pdf` | Plot to PDF | Yes | No |
| `purge` | Purge unused objects | Yes | Yes |
| `get_variables` | Get system variables by name | Yes | Yes |
| `undo` | Undo last operation | Yes | No |
| `redo` | Redo last undone operation | Yes | No |

### `entity` — Entity CRUD + modification

**Create:** `create_line`, `create_circle`, `create_polyline`, `create_rectangle`, `create_arc`, `create_ellipse`, `create_mtext`, `create_hatch`

**Read:** `list`, `count`, `get`

**Modify:** `copy`, `move`, `rotate`, `scale`, `mirror`, `offset`\*, `array`, `fillet`\*, `chamfer`\*, `erase`

> \* `offset`, `fillet`, `chamfer` are File IPC only (not supported in ezdxf headless backend).

### `layer` — Layer management

`list`, `create`, `set_current`, `set_properties`, `freeze`, `thaw`, `lock`, `unlock`

### `block` — Block operations

| Operation | File IPC | ezdxf |
|-----------|----------|-------|
| `list` | Yes | Yes |
| `insert` | Yes | Yes |
| `insert_with_attributes` | Yes | Yes |
| `get_attributes` | Yes | Yes |
| `update_attribute` | Yes | Yes |
| `define` | No | Yes |

### `annotation` — Text, dimensions, leaders

`create_text`, `create_dimension_linear`, `create_dimension_aligned`, `create_dimension_angular`, `create_dimension_radius`, `create_leader`

### `pid` — P&ID operations (CTO symbol library)

`setup_layers`, `insert_symbol`, `list_symbols`, `draw_process_line`, `connect_equipment`, `add_flow_arrow`, `add_equipment_tag`, `add_line_number`, `insert_valve`, `insert_instrument`, `insert_pump`, `insert_tank`

> P&ID symbol insertion requires the [CAD Tools Online](https://www.cadtoolsonline.com/) (CTO) P&ID Symbol Library installed at `C:\PIDv4-CTO\`. The ezdxf backend has built-in CTO library support. For the File IPC backend, some P&ID operations require additional LISP helpers — see the P&ID section in the wiki for setup details.

### `view` — Viewport and screenshot

| Operation | Description |
|-----------|-------------|
| `zoom_extents` | Zoom to show all entities |
| `zoom_window` | Zoom to a specified window |
| `get_screenshot` | Capture current AutoCAD view as PNG |

Screenshots use `PrintWindow` (Win32) for the File IPC backend — works even when AutoCAD is minimized or in the background. The ezdxf backend renders via matplotlib.

### `system` — Server management

`status`, `health`, `get_backend`, `runtime`, `init`, `execute_lisp`

> `execute_lisp` runs arbitrary AutoLISP code (File IPC only). Pass `data: {code: "(+ 1 2)"}`. This turns the server into an extensible automation platform — any valid AutoLISP expression can be executed.

## Architecture

```
MCP Client (Claude)
    │  stdio (JSON-RPC)
    ▼
Python MCP Server (autocad_mcp)
    │
    ├── File IPC Backend ──► C:/temp/*.json ──► mcp_dispatch.lsp
    │   ├── Full AutoCAD: COM ActiveDocument.SendCommand
    │   └── AutoCAD LT: PostMessageW(WM_CHAR) to MDIClient
    │
    └── ezdxf Backend ──► in-memory DXF (headless, no AutoCAD needed)
```

The File IPC backend sends `(c:mcp-dispatch)` to the active drawing. Full AutoCAD uses COM `ActiveDocument.SendCommand`; AutoCAD LT falls back to `PostMessageW(WM_CHAR)`. Both paths avoid taking over normal mouse input.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTOCAD_MCP_BACKEND` | `auto` | Backend selection: `auto`, `file_ipc`, `ezdxf` |
| `AUTOCAD_MCP_IPC_DIR` | `C:/temp` | Directory for IPC command/result JSON files (must match on both Python and LISP sides) |
| `AUTOCAD_MCP_IPC_TIMEOUT` | `10.0` | IPC command timeout in seconds (1-300) |
| `AUTOCAD_MCP_ONLY_TEXT` | `false` | Disable screenshot capture (text feedback only) |
| `AUTOCAD_MCP_AUTOSTART` | `false` | Start AutoCAD automatically when File IPC is requested and no AutoCAD window exists |
| `AUTOCAD_MCP_ACAD_EXE` | empty | Full path to `acad.exe` used by automatic startup |
| `AUTOCAD_MCP_ACAD_SCRIPT` | empty | Optional AutoCAD `.scr` file passed with `/b` during startup |
| `AUTOCAD_MCP_ACAD_STARTUP_TIMEOUT` | `75` | Seconds to wait for the AutoCAD main window, clamped to 5-180 |
| `AUTOCAD_MCP_LISP_PATH` | empty | Dispatcher path loaded for the current AutoCAD session only |

> `mcp_dispatch.lsp` reads `AUTOCAD_MCP_IPC_DIR` from the AutoCAD process environment and falls back to `C:/temp`.

## Development

```powershell
uv sync
uv run pytest tests/ -v
```

## AutoCAD LT AutoLISP Compatibility

AutoLISP was added to AutoCAD LT in the **2024 release (Windows only)**. AutoCAD LT for Mac does not support AutoLISP.

| Supported (LT 2024+ Windows) | Not Supported |
|-------------------------------|---------------|
| `.lsp` / `.fas` / `.vlx` / `.dcl` | VLIDE (Visual LISP IDE) |
| All `vl-*` utility functions | `vlax-*` (ActiveX/COM) |
| File I/O (`open`, `read-line`, etc.) | Express Tools |
| Entity access (`entget`, `entmod`, etc.) | 3D operations |
| Selection sets | AutoLISP on Mac |

The `mcp_dispatch.lsp` dispatcher is fully compatible with LT 2024+.

## What's New in v3.2

- **Full AutoCAD COM transport** - sends command expressions through `ActiveDocument.SendCommand`.
- **Process-based window detection** - recognizes localized titles and the AutoCAD Start page by verifying `acad.exe`.
- **Optional AutoCAD startup** - launches a configured `acad.exe` and waits for its window.
- **Session-scoped dispatcher loading** - loads a configured LISP dispatcher without changing persistent trust paths.

### v3.1

- **`execute_lisp`** — Run arbitrary AutoLISP code via temp file pattern. Turns the server from a fixed command set into an extensible automation platform.
- **Undo / Redo** — Single-step undo and redo via `drawing` tool.
- **Drawing open** — Open existing `.dwg` files programmatically (FILEDIA suppressed).
- **Drawing create** — Now resets current drawing (erase all + purge) instead of `_.NEW`, preserving the LISP dispatcher namespace.
- **Drawing save with path** — `save` with a `path` parameter uses SAVEAS; without path uses QSAVE.
- **`get_variables` fix** — Respects the `names` parameter; returns requested variables with proper type handling.
- **Polyline/leader fix** — Point arrays properly encoded via semicolon-delimited format.
- **ESC prefix** — Sends 2x ESC before each dispatch to cancel stale pending commands from prior timeouts.
- **UTF-8/cp1252 fallback** — Handles non-ASCII characters in LISP result files (AutoCAD writes Windows-1252).
- **Configurable IPC timeout** — `AUTOCAD_MCP_IPC_TIMEOUT` env var (1–300 seconds, default 10).
- **Thread-safe backend init** — `asyncio.Lock` prevents parallel initialization races.

## License

MIT
