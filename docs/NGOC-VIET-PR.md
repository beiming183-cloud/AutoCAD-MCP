# Pull request: Ngoc Viet CNC safe DXF staging profile

## Summary

- make the default CLI a headless, DXF-only MCP server;
- fail closed on AutoCAD `SendCommand`, window-message command injection and autostart;
- restrict every input/output to non-overwriting `.dxf` basenames under one staging root;
- add contract-driven plate creation, revision-by-new-file, inspection and validation tools;
- add the 300 x 200 mm, four-hole acceptance artifact and independent reopen report;
- add a locked, non-root, network-disabled Docker staging profile and security/cloud reports.

## Verified

- `300 passed, 1 warning` in the full offline suite;
- real stdio MCP lifecycle: initialize/list/create/read/resize/validate;
- independent `dxfgrabber` reopen of `laser_test_300x200.dxf`;
- Bandit: zero findings in the safe runtime and generator;
- Docker build, Compose validation and isolated container smoke test: PASS.

## Deliberately out of scope

- no production AutoCAD control;
- no DWG workflow;
- no laser/CAM/controller integration;
- no unauthenticated Internet endpoint;
- no production-file overwrite or release approval service.

## Reviewer focus

Review the fail-closed policy in `safety.py`, pathname and atomic no-overwrite controls in `safe_dxf.py`, and the exact five-tool surface in `safe_server.py`. The legacy upstream modules remain present only for compatibility analysis and regression tests; they are not reachable from installed console entry points.
