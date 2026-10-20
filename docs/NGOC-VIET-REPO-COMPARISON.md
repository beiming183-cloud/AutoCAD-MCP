# Ngoc Viet CNC - repository comparison

Snapshot date: 2026-10-20. Findings below come from the checked-out source and Git history, not README claims alone.

## Pinned inputs

- Repo 1: `beiming183-cloud/AutoCAD-MCP` at `11f7c47e5038796a20451b38b23032e625b5aa26`.
- Repo 2: `Slacker-LLC/autocad-mcp` at `2723b4fae13ac7bc91274fcd502528b92da7a6a5`.

## Comparison

| Criterion | Repo 1 | Repo 2 |
| --- | --- | --- |
| Architecture | Python MCP server; headless `ezdxf`; Windows COM/LISP file IPC; C# named-pipe AutoCAD worker | Small Python stdio MCP server attaching to a running AutoCAD through COM/ActiveX |
| Languages | Python, C#, AutoLISP, PowerShell | Python |
| License | MIT | Apache-2.0 with NOTICE |
| History | 25 commits, one author, 2026-07-16 to 2026-07-19; tags `v3.10.0`, `v3.10.1` while package reports 4.0.0 | 8 commits, one author, all on 2026-08-16; no tag in inspected clone |
| Repository identity | Package links match the GitHub repository | Origin is Slacker-LLC, while package URLs/install text point to `limuzi013/autocad-mcp`; provenance needs clarification |
| Direct dependencies | `mcp`, `ezdxf`, matplotlib, Pillow, PyMuPDF, structlog, Windows-only pywin32; locked by `uv.lock` | `mcp`, pywin32, Pillow; no lockfile |
| Offline tests | Upstream baseline: 293 passed after placing pytest temp under the workspace | 105 passed plus 197 subtests; one non-functional pytest cache ACL warning |
| Create/edit/read | Broad structured 2D, layers, annotations, audit; headless create/edit/read supported | Structured 2D, layers, dimensions and handle-based edits through live AutoCAD only |
| AutoCAD control | Full AutoCAD/LT compatibility paths plus native worker; higher capability and attack surface | Full AutoCAD 2026 COM attach; narrower surface; no AutoCAD LT/headless |
| DXF/DWG | DXF headless; DXF/DWG/PDF through AutoCAD paths | DXF/DWG SaveAs through AutoCAD; no independent parser/audit |
| Headless/Linux | Yes for DXF through `ezdxf` | No; Windows and AutoCAD required |
| MCP transport | stdio | stdio |
| Geometry verification | Structured audit/DRC includes duplicates, zero/short segments and topology checks | Validates inputs and COM calls but has no laser topology/duplicate audit |
| Production overwrite risk | Upstream output workspace helps, but several legacy paths support force/replace and AutoCAD command transport | `save_drawing_as` refuses overwrite by default, but `save_active_drawing` saves the active source in place |
| Main security concern | Large codebase; AutoCAD autostart, subprocesses, COM `SendCommand`, window-message command injection and LISP dispatcher exist internally | Arbitrary read/open path, in-place save/delete tools, desktop screenshot, no approval layer, no remote authentication |

## Selection

Repo 1 is selected because it is the only candidate with a headless DXF backend, independent-of-AutoCAD execution, geometry DRC, a dependency lock, and a substantial test suite. This is a capability decision, not a maturity endorsement: both projects have short, single-author histories and no evidence here of sustained production maintenance.

The Ngoc Viet branch therefore exposes a new narrow server instead of trusting the full upstream surface. Its default command and `python -m autocad_mcp` both route to the DXF-only server. The supervisor entry point is removed, backend detection defaults to `ezdxf`, and legacy AutoCAD command transport requires two explicit break-glass conditions.

No Repo 2 source was copied. If a future change imports Repo 2 code, retain its Apache-2.0 copyright headers, LICENSE and NOTICE, state modified files, and keep the Repo 1 MIT notice. MIT and Apache-2.0 components can be distributed together when both notice obligations are preserved; legal review is still required before commercial redistribution.
