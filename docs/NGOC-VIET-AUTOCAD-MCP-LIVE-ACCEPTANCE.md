# Ngoc Viet CNC - AutoCAD MCP live acceptance

Date: 2026-10-20 (Asia/Bangkok)

Branch: `ngoc-viet-cnc/safe-dxf-laser`

Starting commit: `b37ff2d39a534fa916bfa37374af17afe066d4f3`

Acceptance implementation commit: `c889ff416ce1b3023737b6b0e201ab5b8f17be64`

## Result

| Check | Result | Evidence |
|---|---|---|
| Attach to the existing AutoCAD process | PASS | AutoCAD ActiveX reported `AutoCAD`, version `24.0s (LMS Tech)`; no application launch API exists in the adapter. |
| MCP allowlist surface | PASS | Exactly four tools: `autocad_status`, `autocad_create_test_plate`, `autocad_inspect_test_drawing`, `autocad_modify_hole_revision`. |
| Create new 200 x 200 mm plate | PASS | One closed `LWPOLYLINE` on `CUT_OUTER`; five `CIRCLE` entities on `CUT_INNER`. |
| Original holes | PASS | Center `(100,100)` radius 10 mm; edge centers `(100,20)`, `(100,180)`, `(20,100)`, `(180,100)` radius 5 mm. |
| Read back in AutoCAD | PASS | Six entities read through the ActiveX object model. The fixed adapter reuses the already-open writable staging document. |
| Independent DXF readback | PASS | `ezdxf` recovery reader: AC1027, `$INSUNITS=4`, zero duplicates, zero audit errors/fixes, no unsupported entities. |
| Revision center diameter 20 -> 24 mm | PASS | New file only; center radius changed from 10 to 12 mm. All four edge radii remained 5 mm. |
| Preserve original file | PASS | Separate SHA-256 values and separate filenames; overwrite checks are fail-closed. |
| AutoCAD screenshots before/after | PASS | PNG evidence listed below. |
| Regression tests | PASS | `304 passed, 1 warning in 40.33s`; warning is the upstream `fitz` deprecation notice. |
| Static security scan | PASS | Bandit exited 0 with no findings for the live adapter, live server, and acceptance runner. |
| Dependency audit | PASS WITH EXCEPTIONS | `pip-audit`: no known vulnerabilities found, 12 findings explicitly ignored under the project's documented exception list; local `autocad-mcp` is not on PyPI. |
| DWG output | NOT TESTED | This acceptance deliberately produced DXF only. |
| Production drawing / laser control | NOT TESTED | Intentionally outside scope; no production or laser operation was performed. |
| GitHub draft PR | PASS | `https://github.com/beiming183-cloud/AutoCAD-MCP/pull/2`; opened from the `nguyenvinh1234` fork, not merged. |

## Geometry verified independently

| File | Size | Entities | Layers | Closed contours | Duplicates | Audit errors | Cut length |
|---|---:|---|---|---:|---:|---:|---:|
| `live_square_200_center20_4edge10.dxf` | 200 x 200 mm | 1 polyline + 5 circles | `CUT_OUTER`: 1; `CUT_INNER`: 5 | 6 | 0 | 0 | 988.495559215 mm |
| `live_square_200_center24_4edge10_rev2.dxf` | 200 x 200 mm | 1 polyline + 5 circles | `CUT_OUTER`: 1; `CUT_INNER`: 5 | 6 | 0 | 0 | 1001.061929830 mm |

Both outer contours contain the ordered vertices `(0,0)`, `(200,0)`, `(200,200)`, `(0,200)` and are closed. Both files use millimetres. The revision differs geometrically only at the center circle radius.

## Artifacts and SHA-256

| Artifact | SHA-256 |
|---|---|
| `artifacts/autocad-live/live_square_200_center20_4edge10.dxf` | `96143c16c16b9380c67503998f7686a56031b7a18926d4bcf7b56d09a559820a` |
| `artifacts/autocad-live/live_square_200_center24_4edge10_rev2.dxf` | `47026f28328ee50993358590a41c89daaa8fd5d13789ad04527a0dba0808ed3f` |
| `artifacts/autocad-live/autocad_2021_live_base_center20.png` | `eba86266c85885b501b5395264b3464a8dd10a2af463f55238774aed84298def` |
| `artifacts/autocad-live/autocad_2021_live_revision_center24.png` | `5a0593ba666a8ab7a306b1dad3af74b7a5c2edb0698b87ecead7e8ded43a3cf2` |
| `artifacts/autocad-live/live_autocad_mcp_create.json` | `e7aa4260dfeff977aef1c5793cb496fd5a8b69ebdbe6f79b5dff207b418f4e02` |
| `artifacts/autocad-live/live_autocad_mcp_inspect.json` | `a8173833fbb29640f39277b181fe16237e628cad8a046f0628014813035129ad` |
| `artifacts/autocad-live/live_autocad_mcp_revision.json` | `a22c76e0d32e18bc6058c0d04e08d18e61a2b7224c8173297a6a52caf3ae07ec` |
| `artifacts/autocad-live/logs/live_autocad_mcp.jsonl` | `971ea3b8c241c037aad21b5bac6ad6d0b812962940a8114eb7ae7fad3f443d89` |

## Readback-tab defect and correction

The first live readback opened the just-created DXF a second time as read-only. This produced a blank read-only viewport even though the COM entity read succeeded. The adapter now searches the existing AutoCAD `Documents` collection by canonical full path, prefers the writable instance, activates that instance, and applies `ZoomExtents`. Focused tests cover document reuse and source preservation.

One read-only duplicate created before the correction remains visible in AutoCAD. It was not closed because the acceptance constraints prohibit closing user documents. Subsequent create/inspect/revision calls did not create another read-only tab and did not change the unrelated `square_center_and_4edge_holes.dxf` document.

## Security controls verified

- The approval gate is fail-closed and must equal the exact per-process approval value.
- `GetActiveObject("AutoCAD.Application")` attaches only to an existing AutoCAD instance.
- The MCP server exposes only four fixed tools.
- `SendCommand`, AutoLISP, process launch, shell execution, and arbitrary AutoCAD command input are absent from the live path.
- Output accepts only a basename ending in `.dxf`, is confined to `artifacts/autocad-live`, rejects symlinks, and rejects existing targets.
- The log contains only UTC timestamp, fixed operation, PASS status, staging basenames, source basename, and entity count. It contains no approval value, credential, customer data, or production path.
- No administrator permission, production drawing, laser controller, or manufacturing output was used.

Dependency-audit exceptions used: `PYSEC-2026-1795`, `PYSEC-2026-2875`, `PYSEC-2026-1796`, `PYSEC-2026-2876`, `PYSEC-2026-3721`, and `PYSEC-2026-196`. These remain residual risks to be reviewed independently before production deployment.

## AutoCAD startup observation

AutoCAD 2021 initially displayed `acad.dwt` at 0% during first-run initialization, then completed without registry, profile, template, or configuration changes. The template file existed and the user had access. No destructive reset was performed. A repeatable startup-hang root cause is therefore NOT CONFIRMED.

## Stop gate

The one-session live acceptance is complete. Do not deploy, merge, enable arbitrary commands, operate a laser, or touch production drawings until the user and ChatGPT review these artifacts.
