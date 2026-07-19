# Migration to AutoCAD-MCP 4.0

Version 4.0 adds a native transactional path for full AutoCAD 2025/2026 while
retaining the existing COM/LISP path for AutoCAD LT and compatibility.

## Required changes for industrial writes

1. Install a signed `AutoCADMcp.bundle` and keep `SECURELOAD` enabled.
2. Start AutoCAD from the user-owned desktop supervisor, or attach the supervisor
   to a deliberately selected AutoCAD PID.
3. Set `AUTOCAD_MCP_NATIVE_PLUGIN=required` in production. Use `auto` only when a
   compatibility fallback is acceptable.
4. Read `transaction(operation="context")` before every mutation stage.
5. Send `doc_id`, `expected_revision`, and a stable `idempotency_key` with
   `transaction(operation="execute")`.
6. On an ambiguous worker error, set `AUTOCAD_MCP_ACAD_PID`; never guess between
   multiple AutoCAD instances.

## Desktop policy

Use `AUTOCAD_MCP_WINDOW_MODE=preserve` when attaching to a CAD session opened by
the user. The MCP then leaves visibility, minimization, focus, and position
unchanged. For a process launched by MCP, `quiet_minimized` keeps a real,
taskbar-visible window available without stealing focus. `recording` is reserved
for an explicitly requested recording session; it is the one mode allowed to
activate AutoCAD. Existing-user window/activity changes require explicit
`AUTOCAD_MCP_APPLY_WINDOW_POLICY_TO_EXISTING=true` or
`AUTOCAD_MCP_APPLY_ACTIVITY_POLICY=true`.

PDF and PNG operations write files without launching viewers. Test artifacts may
be deleted only with `job(operation="cleanup_test_artifacts", data={...})` and
only inside the managed job root. Cleanup retains hashes, reports, audits, specs,
and logs.

## Output root

The portable public default remains `~/Documents/AutoCAD-MCP`. On this workstation
configure one D-drive root in every MCP client and in the supervisor:

```text
AUTOCAD_MCP_OUTPUT_ROOT=D:/Codex/AutoCAD-MCP
AUTOCAD_MCP_ACTIVITY_INSIGHTS_PATH=D:/Codex/AutoCAD-MCP/activity-insights
```

The protocol and server remain standard MCP stdio. Codex, Claude Code, Claude
Desktop, Cursor, and other MCP clients use the same executable and tool schema.

## Compatibility

- Existing LISP tools remain available when the native bundle is absent.
- `transaction.begin/commit/rollback` remain compatibility undo transactions.
- `transaction.context/create/execute` are the preferred native operations.
- Native operation dictionaries accept public snake_case fields and normalize
  them to the plugin protocol.
- General 3D fillet, chamfer, shell, assemblies, exact motion sweeps, and material
  rendering remain explicit roadmap items rather than simulated capabilities.
