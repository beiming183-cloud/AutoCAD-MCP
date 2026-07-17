# Windows AutoCAD Recovery

Use this runbook when AutoCAD is alive in Task Manager but MCP cannot obtain a
usable window or COM document.

## 1. Check Before Starting AutoCAD

Call the read-only MCP operation:

```text
system(operation="preflight")
```

It does not start AutoCAD. It reports. The recommended default is to start
AutoCAD manually from the normal desktop and let MCP attach to it; automatic
startup is opt-in and is blocked when the profile preflight fails.

- the exact Python executable used by the MCP;
- `pywintypes`, `pythoncom`, `win32api`, `win32gui`, `win32process`, and
  `win32com.client` import health;
- `acad.exe` processes found without relying on pywin32;
- whether the Activity Insights directory can be written.

## 2. Repair pywin32 in the MCP's Python

Use the same interpreter printed by `system(operation="preflight")`. Do not
repair a different system Python:

```powershell
& "C:\path\to\AutoCAD-MCP\.venv\Scripts\python.exe" -m pip uninstall -y pywin32
& "C:\path\to\AutoCAD-MCP\.venv\Scripts\python.exe" -m pip install --no-cache-dir --force-reinstall pywin32
& "C:\path\to\AutoCAD-MCP\.venv\Scripts\python.exe" -c "import pywintypes, pythoncom, win32api, win32com.client; print('pywin32 OK')"
```

If the last import still fails, delete and recreate the project environment
with `uv sync` rather than mixing packages from another Python installation.

## 3. Remove the Ghost AutoCAD Process

When `preflight` reports an `acad.exe` process but no usable main window, do not
start another AutoCAD instance. AutoCAD's single-instance behavior can forward
the new launch to the broken process.

After confirming that no unsaved drawing is open, close the stale process from
Task Manager. A command-line fallback is:

```powershell
Get-Process acad -ErrorAction SilentlyContinue | Stop-Process
```

This command is intentionally not executed by MCP automatically. Only a user
can decide whether an AutoCAD process contains unsaved work.

## 4. Fix Activity Insights Before Auto-Start

AutoCAD records Activity Insights under the user profile. Configure a writable
D: location in the MCP client environment:

```text
AUTOCAD_MCP_ACTIVITY_INSIGHTS_PATH=D:/CAD-Automation/activity-insights
```

Or explicitly disable the feature for an automation-only workstation:

```text
AUTOCAD_MCP_DISABLE_ACTIVITY_INSIGHTS=true
```

The MCP will verify the configured directory before automatic startup and will
return `E_AUTOCAD_PROFILE_UNWRITABLE` instead of launching a process that is
likely to crash. On a healthy COM session it also persists the requested
AutoCAD settings for the next restart.

Autodesk documents `ACTIVITYINSIGHTSSUPPORT=0` as disabling Activity Insights
and `ACTIVITYINSIGHTSPATH` as changing its log location. Both settings are
stored by AutoCAD and should be applied before the next clean restart.

## 5. Reconnect in Order

1. Close the ghost process or start AutoCAD manually from the normal desktop.
2. Run `system(operation="preflight")` again.
3. Confirm `runtime.ok=true`, no unexplained `acad.exe` process remains, and the
   Activity Insights check is green or intentionally disabled.
4. Call `system(operation="ensure_ready")`.
5. Only after that create or modify drawings.

The MCP will now return distinct errors instead of collapsing these states into
`E_NO_ACTIVE_DOCUMENT`:

- `E_PYWIN32_BROKEN` - the Python COM runtime is damaged;
- `E_AUTOCAD_GHOST_PROCESS` - `acad.exe` is alive without a usable main window;
- `E_AUTOCAD_PROFILE_UNWRITABLE` - the startup profile path cannot be written;
- `E_AUTOCAD_CRASHED` - AutoCAD exited or showed a fatal-error dialog.
