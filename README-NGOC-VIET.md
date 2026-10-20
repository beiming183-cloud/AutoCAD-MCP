# Ngoc Viet CNC safe DXF MCP

This branch is a staging-only, headless DXF profile based on Repo 1. It does not open AutoCAD and must not be connected to laser hardware.

## Run locally

```powershell
uv sync --frozen --all-groups
$env:NGOC_VIET_DXF_ROOT = (Resolve-Path .\staging\ngoc-viet-cnc-dxf).Path
uv run ngoc-viet-autocad-mcp
```

Configure an MCP desktop client with the `ngoc-viet-autocad-mcp` command over stdio. Available tools are `create_laser_plate`, `resize_laser_plate`, `inspect_dxf`, `validate_laser_plate`, and `list_test_dxfs`.

The first four tools accept only simple `.dxf` basenames. Existing files are refused. To change a plate size, provide a different `output_name` so the source stays immutable.

## Reproduce acceptance artifact

Run in a clean output directory:

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
uv run python .\tools\generate_laser_test.py --output-dir .\artifacts-recheck
```

The committed `artifacts/laser_test_300x200.dxf` is checked by `ezdxf` and independently reopened by `dxfgrabber`. See `artifacts/laser_test_300x200.validation.json` for exact geometry, layers, units, cut length and SHA-256.

## Tests

```powershell
New-Item -ItemType Directory -Force .test-tmp | Out-Null
$env:TEMP = (Resolve-Path .test-tmp).Path
$env:TMP = $env:TEMP
uv run pytest -q
```

Read `docs/NGOC-VIET-SECURITY.md` before any expansion toward AutoCAD or production files.
