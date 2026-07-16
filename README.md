# AutoCAD MCP Server

MCP server for full AutoCAD automation, AutoCAD LT automation, and headless DXF generation.

Version 3.8 treats every structured entity creation as a checked transaction: request fields are strict and immutable, the created handle is read back, geometry and layer are compared, and a mismatch is deleted with `E_POSTCONDITION_MISMATCH`. Atomic batches roll back when any semantic postcondition fails.

This edition is based on [puran-water/autocad-mcp](https://github.com/puran-water/autocad-mcp) and keeps its MIT license. It adds full AutoCAD COM command delivery, process-based window detection, optional AutoCAD startup, and session-scoped dispatcher loading.

Two backends, one API:

| Backend | Runtime | Requires AutoCAD? | Validation feedback |
|---------|---------|-------------------|------------|
| **File IPC** | Windows Python | Yes - full AutoCAD or AutoCAD LT 2024+ | Topology audit + native PDF and direct PNG |
| **ezdxf** | Any platform | No (headless) | Structured audit + deterministic PNG |

The server exposes **9 consolidated tools** (`drawing`, `entity`, `solid`, `layer`, `block`, `annotation`, `pid`, `view`, `system`) over the standard MCP stdio transport. It is client-independent and works with Codex, Claude Code, Claude Desktop, Cursor, and other MCP-compatible clients.

## Prerequisites (File IPC backend)

- **Windows 10/11** (the File IPC backend uses Win32 APIs for focus-free window messaging)
- **Full AutoCAD or AutoCAD LT 2024+ on Windows** - AutoLISP support was added to LT in 2024; full AutoCAD also uses the native COM path.
- **Python 3.10+** (Windows native — not WSL Python)
- **uv** package manager ([install guide](https://docs.astral.sh/uv/getting-started/installation/))

> The ezdxf headless backend works on any platform (Linux, macOS, WSL) for offline DXF generation without AutoCAD installed.

## Quick Start

### 1. Clone and install

```powershell
git clone https://github.com/beiming183-cloud/AutoCAD-MCP.git
cd AutoCAD-MCP
uv sync
```

### 2. Load the LISP dispatcher in AutoCAD

Open AutoCAD or AutoCAD LT and load `mcp_dispatch.lsp` using **APPLOAD**:

1. Type `APPLOAD` in the AutoCAD command line
2. Browse to `<repo>/lisp-code/mcp_dispatch.lsp`
3. Click **Load**
4. You should see: `=== MCP Dispatch v3.8.0 loaded ===` and `Ready for commands via (c:mcp-dispatch)`

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

The same server command can be used in Claude Code project-level `.mcp.json`:

```json
{
  "mcpServers": {
    "autocad": {
      "type": "stdio",
      "command": "C:\\path\\to\\autocad-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "autocad_mcp"],
      "env": {
        "PYTHONPATH": "C:\\path\\to\\autocad-mcp\\src",
        "AUTOCAD_MCP_BACKEND": "file_ipc",
        "AUTOCAD_MCP_AUTOSTART": "true",
        "AUTOCAD_MCP_VISIBLE": "true",
        "AUTOCAD_MCP_WINDOW_MODE": "minimized",
        "AUTOCAD_MCP_ACTIVATE_ON_DRAW": "false"
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
| `save_as_dxf` | Export as DXF without switching the active DWG | Yes | Yes |
| `plot_pdf` | Plot to PDF | Yes | No |
| `render_preview` | True PNG with DPI, force-overwrite, dimensions, and SHA-256 | Yes | Yes |
| `workspace` | Create and report the managed output workspace | Yes | Yes |
| `deliver` | Validated DWG/DXF/PDF package with manifest and checksums | Yes | No |
| `audit` | Compact structured entity audit with change tracking | Yes | Yes |
| `audit_geometry` | Geometry/topology DRC including gaps, dangling endpoints, crossings, tangency, equal radii, and projection checks | Yes | Yes |
| `audit_dxf` | Parse an existing DXF into normalized JSON | Yes | Yes |
| `setup_mechanical` | Configure mm units, dimensions, sheet metadata, and seven GB/T layers | Yes | Yes |
| `purge` | Purge unused objects | Yes | Yes |
| `get_variables` | Get system variables by name | Yes | Yes |
| `set_variables` | Set a bounded whitelist of units/dimension/linetype variables | Yes | Yes |
| `undo` | Undo last operation | Yes | No |
| `redo` | Redo last undone operation | Yes | No |

### `entity` — Entity CRUD + modification

**Create:** `create_line`, `create_circle`, `create_polyline`, `create_rectangle`, `create_arc`, `create_tangent_arc`, `create_ellipse`, `create_mtext`, `create_hatch`, `create_batch`

`create_batch` accepts up to 500 structured entities in one MCP call. It supports line, circle, polyline, rectangle, arc, ellipse, text, mtext, and hatch records. A hatch can use `entity_id: "$last"` to reference the preceding entity. Batches are atomic by default: a failed operation rolls back the AutoCAD undo group or the tracked headless entities. This is the preferred high-throughput path; it does not enable arbitrary AutoLISP.

For `ANSI31`, pass `angle: 0` to retain the pattern's native 45-degree section angle. `scale` and hatch `layer` are also explicit parameters.

**Read:** `list`, `count`, `get`

**Modify:** `copy`, `move`, `rotate`, `scale`, `mirror`, `offset`\*, `array`, `fillet`\*, `chamfer`\*, `trim`\*, `extend`\*, `break`\*, `join`\*, `constrain`\*, `erase`

> \* These native editing operations are File IPC only. `trim` and `extend` require explicit entity IDs and pick points so AutoCAD never guesses which side to keep.

`constrain` reports whether the native command was accepted, but currently returns `verified: false` because the portable ActiveX API does not expose AutoCAD's associative constraint collection. Treat the constraint as review-required rather than release evidence.

### `solid` - Native AutoCAD 3D solids

`create_box`, `create_cylinder`, `extrude`, `revolve`, `sweep`, `boolean`

The `solid` tool is available on full AutoCAD through the native ActiveX object model. Box placement uses its center; cylinder placement uses the center of its base. Extrude, revolve, and sweep consume a closed profile handle; boolean accepts `union`, `intersection`, or `subtract`. AutoCAD LT and the ezdxf backend report these operations as unsupported. Edge fillets/chamfers and projected drawing views are not advertised yet because their prompt-driven workflows have not reached the same deterministic standard.

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

### `view` — Viewport and diagnostic capture

| Operation | Description |
|-----------|-------------|
| `zoom_extents` | Zoom to show all entities |
| `zoom_window` | Zoom to a specified window |
| `get_screenshot` | Diagnostic-only AutoCAD window capture |

Normal validation is data-first: use `drawing.audit` after edits and `drawing.render_preview` at milestones. `get_screenshot` remains available only for diagnosing AutoCAD UI state.

### Data-first validation

```text
edit entities -> drawing.audit -> native render_preview -> audit_dxf for final delivery
```

`drawing.audit` returns entity counts by type and layer, drawing bounds, units, a handle-independent geometry digest, geometry DRC, an endpoint topology graph, limited normalized entity geometry, and added/modified/removed handles since the previous audit. In addition to degenerate geometry, rules can check connection gaps, dangling endpoints, interior crossings, explicit tangent pairs, equal-radius groups, and cross-view projection alignment. `limit` is clamped to 500 so large drawings do not flood model context.

`drawing.render_preview` always returns a real PNG. Full AutoCAD plots a temporary PDF with its native renderer and rasterizes it with PyMuPDF, preserving plot styles without capturing the desktop. The result includes DPI, pixel size, SHA-256, and the source geometry digest. `drawing.plot_pdf` remains the release-quality vector output.

### Industrial delivery jobs

`drawing.deliver` turns the active drawing into a traceable job rather than treating a successful script as a finished drawing. It creates an isolated folder under `jobs`, records the request under `specs`, audits the source, applies geometry gates, saves DWG/DXF/PDF, audits the exported DXF, compares type/layer counts, bounds, units and geometry digests, writes `reports/validation.json`, and records artifact sizes and SHA-256 hashes.

```json
{
  "operation": "deliver",
  "data": {
    "name": "gearbox-output-shaft",
    "metadata": {"drawing_number": "GB-OS-001", "revision": "A"},
    "plot": {
      "paper": "A3",
      "orientation": "landscape",
      "plot_style": "monochrome.ctb",
      "scale_mode": "fixed",
      "scale": "1:1",
      "center": true
    },
    "validation": {
      "min_entities": 20,
      "required_layers": ["OUTLINE", "CENTER", "DIM"],
      "required_types": ["LINE", "CIRCLE", "DIMENSION"],
      "require_geometry_clean": true,
      "geometry_tolerance": 0.000001
    }
  }
}
```

The job contains `specs/request.json`, `manifest.json`, editable DWG and DXF files, a native PDF, `audits/drawing-audit.json`, and `reports/validation.json`. Failed validation or export leaves a failed manifest with step-level diagnostics.

### Industrial automation roadmap

- **v3.6 reliability (complete):** self-healing startup, dispatcher version handshake, machine-readable MCP failures, controlled variables, geometry DRC, DXF units/digests, and enforced plot configuration.
- **v3.7 geometry control (complete):** topology DRC, atomic batches, safe trim/extend/break/join/constraints, native 3D solids, non-switching DXF export, and verified PNG previews.
- **v3.8 specifications:** versioned JSON drawing specifications, template/title-block registry, assembly/component trees, drawing-number/revision rules, and spec-to-audit comparison.
- **v3.9 orchestration:** idempotency keys, resumable job states, bounded retries, structured logs, and a local job queue.
- **v4.0 hybrid CAD:** FreeCAD CLI/MCP as the parameterized 3D and STEP executor, AutoCAD as the visible DWG/2D drafting and release executor, with shared specs and acceptance reports.

### `system` — Server management

`status`, `ensure_ready`, `health`, `get_backend`, `runtime`, `init`, `recover`, `execute_lisp`

`status` is observational and does not start AutoCAD. `ensure_ready` performs the full discovery/start/document/dispatcher/version/ping sequence and reports the detected AutoCAD product without assuming AutoCAD LT.

Tool failures use MCP `isError=true` and a stable structure containing `code`, `message`, `recoverable`, and `recommended_action`.

`recover` cancels stale AutoCAD command-line state and removes abandoned IPC files without calling the potentially blocked dispatcher. Arbitrary AutoLISP remains disabled unless `AUTOCAD_MCP_ALLOW_ARBITRARY_LISP=true` is explicitly configured.

> `execute_lisp` is an explicit opt-in escape hatch for trusted local use. Normal automation should use the structured tools and `create_batch`.

## Architecture

```
MCP Client (Codex / Claude Code / Claude Desktop / Cursor)
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
| `AUTOCAD_MCP_DOCUMENT_TIMEOUT` | `30.0` | Seconds to wait for COM registration and an active document after the window appears (5-120) |
| `AUTOCAD_MCP_ONLY_TEXT` | `true` | Disable automatic screenshot attachments; direct diagnostic capture remains available |
| `AUTOCAD_MCP_AUTOSTART` | `false` | Start AutoCAD automatically when File IPC is requested and no AutoCAD window exists |
| `AUTOCAD_MCP_VISIBLE` | `true` | Keep AutoCAD as a visible desktop application rather than a hidden automation session |
| `AUTOCAD_MCP_WINDOW_MODE` | `minimized` | Initial policy: `minimized` (taskbar, no activation), `visible` (show without activation), or `foreground` |
| `AUTOCAD_MCP_ACTIVATE_ON_DRAW` | `false` | Allow per-command activation only when `AUTOCAD_MCP_WINDOW_MODE=foreground` |
| `AUTOCAD_MCP_AUTO_FIT` | `true` | Automatically center and fit drawing extents after geometry changes |
| `AUTOCAD_MCP_OUTPUT_ROOT` | `~/Documents/AutoCAD-MCP` | Unified root for specs, scripts, models, drawings, reports, outputs, jobs, templates, logs, and archives |
| `AUTOCAD_MCP_ALLOW_EXTERNAL_OUTPUTS` | `false` | Permit writes outside the managed output root; disabled by default |
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

## What's New in v3.7

- **Topology-aware DRC** - audits expose endpoint connectivity and configurable checks for near misses, dangling endpoints, non-endpoint crossings, tangency, equal-radius groups, and projection alignment.
- **Atomic batches** - `create_batch` opens an AutoCAD undo transaction by default and rolls back the whole batch on failure.
- **Controlled 2D repair** - explicit `trim`, `extend`, `break`, `join`, geometric constraints, and mathematically solved tangent arcs replace blind coordinate patching.
- **Native 3D solids** - full AutoCAD supports boxes, cylinders, extrusions, revolutions, sweeps, and boolean operations through a dedicated safe tool.
- **True PNG previews** - native AutoCAD plotting is rasterized to a force-overwritable white-background PNG with DPI, dimensions, hashes, and no desktop capture.
- **Non-switching DXF export** - `save_as_dxf` uses AutoCAD's export API and verifies that the active DWG remains unchanged.
- **Richer entity data** - arc endpoints, polyline bulges, MText width/attachment, block attributes, bounds, length, area, and object ownership are returned when available.

### v3.6

- **Self-healing readiness** - `system.ensure_ready` discovers or starts AutoCAD, ensures an active document, loads the configured or bundled dispatcher, verifies its version, and pings IPC.
- **Structured MCP errors** - failures are marked `isError=true` and use stable error codes such as `E_DISPATCHER_NOT_LOADED`, `E_IPC_TIMEOUT`, and `E_OUTPUT_PATH_REJECTED`.
- **Safe variable updates** - `drawing.set_variables` exposes a validated whitelist; `setup_mechanical` now applies millimeter and dimension defaults in addition to layers.
- **Geometry DRC** - source and exported DXF audits detect zero/short segments, duplicate vertices/endpoints/entities, and polyline self-intersections.
- **Stronger DXF evidence** - audits report `$INSUNITS` and a handle-independent geometry digest; delivery compares types, layers, bounds, units, digest, and DRC.
- **Enforced plotting** - `plot_pdf` and `deliver` apply and record paper, orientation, plot style, fixed/fit scale, centering, device, media, and paper units.

### v3.5

- **Unified managed workspace** - output paths default to the portable `~/Documents/AutoCAD-MCP`; each client can override it with `AUTOCAD_MCP_OUTPUT_ROOT` (for example `D:/CAD-Automation`).
- **Output containment** - save/export paths outside the managed root are redirected unless external outputs are explicitly enabled.
- **Validated delivery jobs** - `drawing.deliver` produces DWG, DXF, PDF, request/audit JSON, a step manifest, validation results, file sizes, and SHA-256 checksums.
- **Quality gates** - delivery can require minimum/maximum entity counts plus required layers and entity types, and rejects DXF exports whose entity count differs from the source.

### v3.4

- **Prompt-free layer management** - common center and hidden linetypes are created or loaded without opening an interactive linetype prompt.
- **Mechanical drafting profile** - `drawing.setup_mechanical` creates `OUTLINE`, `THIN`, `CENTER`, `HIDDEN`, `HATCH`, `DIM`, and `TEXT` with monochrome GB/T lineweights.
- **Structured batch creation** - up to 500 whitelisted entities can be submitted in one MCP call without enabling arbitrary AutoLISP.
- **Portable Chinese text** - File IPC converts non-ASCII annotation text to AutoCAD `\\U+XXXX` escapes.
- **Reliable hatching** - pattern angle and scale are explicit; `ANSI31` defaults to an added angle of zero.
- **Out-of-band recovery** - `system.recover` cancels stuck commands and cleans IPC state without waiting for the dispatcher.
- **Native save path** - full AutoCAD saves DWG/DXF through COM first and uses the AutoLISP command path only as a fallback.
- **Automation-session discovery** - hidden AutoCAD instances can be found through their COM window handle when no visible main window is available.
- **TEXT audit fix** - final DXF audits read `TEXT` and `MTEXT` heights through their correct entity-specific attributes.
- **Live visible drawing** - AutoCAD is restored before drawing by default; optional foreground activation and `view.show_window` make automation observable in real time.
- **Startup busy retry** - transient COM call rejection during AutoCAD startup is retried briefly before the Win32 fallback is used.
- **Idle-state synchronization** - each IPC request waits for AutoCAD to finish unwinding the previous dispatcher before sending the next command.
- **Verified entity creation** - rectangles, hatches, and dimensions report an error when no new CAD entity was actually created.
- **Native COM dimensions** - full AutoCAD creates linear, aligned, angular, and radial dimensions directly through ActiveX, with AutoLISP retained as the LT fallback.
- **Automatic centered view** - geometry changes automatically fit all extents into the viewport; structured batches fit once after completion, and `view.fit_drawing` can trigger it manually.

### v3.3

- **Structured drawing audits** - compact counts, layers, bounds, normalized geometry, and change fingerprints.
- **Native preview rendering** - full AutoCAD plots PDF through `PlotToFile`; ezdxf writes deterministic PNG.
- **DXF mathematical audit** - parses existing DXF files into bounded normalized JSON instead of returning raw DXF text.
- **Data-first defaults** - automatic screenshot feedback is disabled by default; window capture is diagnostic only.
- **Richer AutoLISP entity details** - arcs, polylines, text, blocks, and dimensions report useful geometry.

### v3.2

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
