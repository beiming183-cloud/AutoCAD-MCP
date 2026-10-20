# Ngoc Viet CNC - security report

## Implemented controls

- Default runtime is headless DXF-only. It does not import COM, native-pipe, LISP, supervisor or AutoCAD backends.
- The normal `autocad-mcp` entry point and `python -m autocad_mcp` both start `safe_server`.
- AutoCAD backend detection is forced to `ezdxf` while `NGOC_VIET_DXF_SAFE_MODE` is enabled (default).
- AutoCAD autostart is blocked by default.
- Legacy COM `SendCommand` and window-message command transport are blocked by default. Break-glass requires safe mode off plus the exact risk-acceptance token; this path is not used by the shipped safe server.
- MCP exposes five structured tools only: create, resize-to-new-file, inspect, validate and list managed DXFs. There is no shell, script, LISP or arbitrary command tool.
- File inputs are simple `.dxf` basenames. Absolute paths, directories, traversal, symlinks and non-DXF suffixes are rejected.
- Outputs remain under `NGOC_VIET_DXF_ROOT`; existing files are never overwritten. Resize validates the source and writes a distinct revision.
- Laser geometry is restricted to one closed outer `LWPOLYLINE` and four `CIRCLE` entities on two named layers.
- No network listener is started; local transport is stdio.

## Verification evidence

- Full suite: `300 passed, 1 warning` (warning is upstream deprecated `fitz` import).
- MCP integration test starts the stdio server, lists its exact five tools, creates a DXF, reads it, resizes it to a new file, and validates the revision.
- Bandit 1.9.4 scanned 372 lines in the Ngoc Viet runtime and generator: zero low/medium/high findings, zero skipped lines.
- OSV/pip-audit found no vulnerabilities in installed packages after excluding six unique advisories reported only against the test environment's `pip 25.0.1` installer. The scan displayed 12 rows because OSV aliases were duplicated. The staging image upgrades to `pip 26.2.1` and installs application dependencies exactly from `uv.lock`.
- `dxfgrabber 1.0.1` is old and emits a Python syntax warning in an unrelated text parser. It is used only as the independent acceptance reader, not in the MCP runtime.
- Docker image build: `PASS`, image ID `sha256:c8a0de59f32e718b96780e6c0512591b767ac5d59705e28b669e11dfb537c67a`. The base image is pinned by digest and the build context is restricted by `.dockerignore`.
- Docker smoke test: `PASS` as UID/GID `10001:10001`, no network, read-only root filesystem, all capabilities dropped and `no-new-privileges`. The result reported safe mode `true`, AutoCAD command transport `false`, and a newly created DXF passed every geometry check.
- `docker compose --file compose.safe.yaml config --quiet`: `PASS`.

## Residual risks and blockers

- The upstream legacy source still exists for comparison and regression tests. Do not deploy `autocad_mcp.server`, `supervisor`, or `file_ipc` on a production workstation from this branch.
- No live AutoCAD test was performed. COM, native worker, DWG, rendering and AutoCAD-specific editing are `NOT TESTED`.
- The container smoke test used a temporary in-container DXF filesystem. Persistence and host-directory permissions for the Compose bind mount have not been acceptance-tested on Fedora.
- No production export approval service is implemented. The current scope is a test-only directory and deliberately cannot publish to a production share.
- No remote HTTP/OAuth gateway is implemented or exposed. Fedora gateway deployment remains a design proposal.
- No code-signing, SBOM signature, restore drill, external penetration test or independent human code review has been completed.
- The generated DXF has geometric acceptance evidence, but machine-specific kerf, lead-in/lead-out, material, nesting and controller post-processing remain outside scope.

## Production gate

Before any production use: independent human drawing review; approved output manifest and hash; immutable source backup; machine/controller-specific CAM validation; explicit production-file approval; authenticated gateway; log retention; least-privilege Windows service account; and a tested rollback/restore procedure. Never map the staging directory directly to a laser controller watch folder.
