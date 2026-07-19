# Execution Hang Review: 2026-07-18

## Finding

The Codex turn did not show evidence of a GPT model crash. A pytest command was
started through a nested execution cell, returned a running-cell handle, and
then waited without a completion event. The next user message replaced that
waiting turn. The process-manager record had no usable OS PID, so the command
was not a bounded foreground process. Large diagnostic output and repeated
sandbox permission refreshes increased the appearance of a stopped model, but
there was no `task_failed` or model error in the captured session.

AutoCAD had a separate failure. CER records show `0xE0434352` during
`Autodesk.Windows.Themes.OverridePaletteTheme.get_Dark` /
`AdUiMgdPaletteTheme` initialization. That is a damaged or incompatible
AutoCAD profile/UI startup path, not a drawing-coordinate failure. The MCP now
reports it as `E_AUTOCAD_CRASHED` and retains the CER evidence.

## Changes Made

- `tests/run_bounded.py` has a hard process timeout, process-group launch, tree
  termination attempt, automatic `src`/`--basetemp` setup, and a durable JSON
  report. A host that denies process termination is reported instead of being
  hidden.
- `tests/run_autostart_rounds.py` applies a timeout to every operation and to
  the entire campaign. Startup failure stops later rounds, timeouts return
  structured records, and cancellation cleans only the managed test artifacts
  while retaining evidence.
- `ComStaExecutor` quarantines itself after a timed-out callback, cancels
  queued work, rejects new calls, and ignores late writes to cancelled Futures.
- `FileIPCBackend._wait_for_autocad_idle` has a finite executor boundary rather
  than an unbounded COM wait.

## Verification

- AST parsing passes for all 53 source and test Python files.
- A local async timeout returns `E_TEST_OPERATION_TIMEOUT`.
- A campaign timeout returns `E_CAMPAIGN_TIMEOUT`.
- A COM timeout returns `E_COM_STA_TIMEOUT`; a subsequent call returns
  `E_COM_STA_UNAVAILABLE` without entering the worker.
- `tests/test_com_sta.py`: 3 passed.
- `tests/test_journal.py tests/test_session.py tests/test_supervisor.py`: 8
  passed.
- The native-pipe slice remains environment-blocked because the sandbox denies
  access to the virtualenv's `ezdxf` files; the complete suite and live CAD
  campaign are not claimed.

## Safe Continuation Gate

Do not start a multi-round live AutoCAD drawing campaign until AutoCAD starts
with a clean or explicitly exported `.arg` profile and `system(operation="preflight")`
passes. Use the desktop supervisor or an already-visible user-owned AutoCAD
process. Keep `quiet_minimized` as the default window policy so the taskbar
entry remains available without stealing focus.

## Follow-up: 2026-07-19 Context and Tool Flood

The next apparent stop was also not evidence of a model crash. The captured
rollout contained 454 MCP tool-call completions, 35 AutoCAD-side errors, 18
window screenshots (about 1.74 MB of image payload), a 3.29 MB input image, and
about 20 context compactions. Several Python/Codex workers from older turns
were still resident. That combination can exhaust the desktop app-server's
memory and transcript budget even when every individual call has a timeout.

### Containment now required

- AutoCAD transport calls are admitted through one FIFO lane. A bounded queue
  and sliding-window budget return a structured retryable error instead of
  allowing parallel mutation or screenshot storms.
- `view.get_screenshot` and optional screenshot feedback write a managed PNG
  under the output root and return path, dimensions, byte count, and SHA-256.
  Raw image content requires both `data.include_image=true` and
  `AUTOCAD_MCP_ALLOW_INLINE_IMAGES=true`, and is capped by
  `AUTOCAD_MCP_MAX_INLINE_IMAGE_BYTES`.
- JSON responses have a hard `AUTOCAD_MCP_MAX_RESPONSE_BYTES` ceiling. Large
  entity/audit lists become a hash/count evidence envelope; callers should
  narrow `limit` or read the saved artifact.
- The MCP never kills arbitrary Python workers. Only a wrapper that owns a
  process may terminate its tree; stale workers must be inspected and closed
  by their owning client or supervisor.

These controls address transcript/memory pressure and are independent of the
separate AutoCAD `0xE0434352` profile/UI startup failure.
