# AutoCAD MCP Architecture Review

## Scope

This review covers the Windows startup/COM path and the side effects that
were observed while several MCP server processes shared one AutoCAD session.
It does not inspect or modify licensing, activation, or crack-related files.

## Findings

1. Multiple `autocad_mcp` processes can run at the same time.  The old
   `asyncio.Lock` only serialized calls inside one process, so separate STA
   workers could enter AutoCAD COM concurrently and receive
   `RPC_E_CALL_REJECTED (0x80010001)`.
2. `ensure_ready()` applied Activity Insights variables to an already-open
   user document.  Attaching to CAD therefore changed the user's profile even
   when MCP had not launched the process.
3. The window policy was also applied during attach/draw.  A manually opened
   CAD window could be minimized or activated unexpectedly.
4. CER exception data is primarily in `rawdata-t1.pb`; reading only t2 caused
   startup failures to look like generic connection failures.  Some CER
   directories cannot be enumerated even when a known file can be read.
5. Startup evidence written by test doubles could overwrite the real evidence
   and trip the cooldown circuit breaker.
6. The startup path is intentionally still large (`_autostart_autocad` and
   `ensure_ready`).  A wholesale rewrite while a user-owned CAD session is
   open would increase risk, so the first pass isolates side effects and adds
   contracts before extracting those functions.

## Changes in this pass

- Added a per-user Windows named mutex around each COM/native AutoCAD turn.  A
  competing MCP receives `E_AUTOCAD_COM_BUSY` with a retry recommendation
  instead of entering the same session concurrently.  The mutex state is
  included in `com_sta` status.
- Added ownership-aware window behavior.  A window is only minimized or
  activated automatically when this backend launched it, unless the caller
  explicitly enables `AUTOCAD_MCP_APPLY_WINDOW_POLICY_TO_EXISTING`.
- Added `preserve`/`user` window mode and changed the local MCP configuration to
  `AUTOCAD_MCP_WINDOW_MODE=preserve` with autostart disabled.  Explicit
  `show_window`/`minimize_window` operations remain available.
- Activity Insights writes now require both a backend-owned launch and
  `AUTOCAD_MCP_APPLY_ACTIVITY_POLICY=true`; existing sessions are read-only.
- CER parsing now reads t1 and t2, extracts exception code/address/module/build,
  theme and crash date, and supports a `cer.log` fallback.
- Startup evidence is separated into `real_autocad`, `unit_test`, and
  `simulation` sources.  Only matching evidence can activate the cooldown.
- Profile creation is opt-in and restricted to the AutoCAD HKCU `Profiles`
  branch.  Profile names and registry roots are validated so the helper cannot
  reach licensing keys.

## CAD restoration boundary

- Machine-specific profile backups and recovery records remain under the
  configured local output root; they are not part of the public repository.
- The MCP does not reset, rename, delete, or replace an existing AutoCAD user
  profile by default. Profile creation and registry repair remain explicit,
  opt-in maintenance operations outside normal drawing calls.
- Never swap profile directories while AutoCAD has open handles. Any local
  recovery must first close AutoCAD normally, retain a reversible backup, and
  record every filesystem/registry change outside the source tree.

## Next extraction steps

1. Move startup command construction/evidence persistence into a dedicated
   `startup.py` module with immutable `StartupSpec` and `StartupResult` types.
2. Move registry profile inspection/recovery into `profile_store.py`; keep all
   registry writes behind an explicit `--apply`/environment gate.
3. Make `FileIPCBackend.ensure_ready()` a state machine with bounded phases:
   discover, acquire COM lease, bind document, load dispatcher, verify, and
   release/rollback.
4. Add a subprocess-based integration harness that never targets the user's
   live AutoCAD window.  Live drawing tests must be explicitly opt-in.
