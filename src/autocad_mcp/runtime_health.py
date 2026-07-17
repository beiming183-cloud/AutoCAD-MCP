"""Preflight checks for the Windows AutoCAD execution environment.

These checks deliberately do not start AutoCAD or connect to COM.  They make
startup failures diagnosable before the drawing backend is allowed to mutate a
document.
"""

from __future__ import annotations

import csv
import importlib
import importlib.metadata
import io
import os
import subprocess
import sys
import uuid
from pathlib import Path


class RuntimeHealthError(RuntimeError):
    """An environment problem with a stable MCP error code."""

    def __init__(self, message: str, *, error_code: str, details: dict, recommended_action: str):
        super().__init__(message)
        self.error_code = error_code
        self.details = details
        self.recommended_action = recommended_action


def win32_runtime_health() -> dict:
    """Check the pywin32 modules required by the File IPC backend."""
    result = {
        "platform": sys.platform,
        "python": sys.executable,
        "python_version": sys.version.split()[0],
        "pywin32_distribution": None,
        "ok": False,
        "checks": {},
    }
    if sys.platform != "win32":
        result["checks"]["platform"] = {"ok": False, "message": "Windows is required"}
        return result

    try:
        result["pywin32_distribution"] = importlib.metadata.version("pywin32")
    except importlib.metadata.PackageNotFoundError:
        result["pywin32_distribution"] = None

    required = ("pywintypes", "pythoncom", "win32api", "win32gui", "win32process", "win32com.client")
    for module_name in required:
        try:
            module = importlib.import_module(module_name)
            result["checks"][module_name] = {
                "ok": True,
                "file": str(getattr(module, "__file__", "")),
            }
        except Exception as exc:
            result["checks"][module_name] = {
                "ok": False,
                "exception_type": type(exc).__name__,
                "message": str(exc) or type(exc).__name__,
            }
    result["ok"] = all(item.get("ok") for item in result["checks"].values())
    return result


def list_autocad_processes() -> list[dict]:
    """List acad.exe processes without requiring pywin32 or COM."""
    if sys.platform != "win32":
        return []
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq acad.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="mbcs",
            errors="replace",
            timeout=3,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        rows = []
        for row in csv.reader(io.StringIO(completed.stdout)):
            if len(row) < 2 or row[0].strip().lower() != "acad.exe":
                continue
            try:
                process_id = int(row[1])
            except (TypeError, ValueError):
                continue
            rows.append(
                {
                    "image": row[0],
                    "process_id": process_id,
                    "session_name": row[2] if len(row) > 2 else None,
                    "session_number": row[3] if len(row) > 3 else None,
                    "memory": row[4] if len(row) > 4 else None,
                }
            )
        return rows
    except (OSError, subprocess.SubprocessError):
        return []


def activity_insights_path() -> Path:
    configured = os.environ.get("AUTOCAD_MCP_ACTIVITY_INSIGHTS_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
    if local_appdata:
        return (Path(local_appdata) / "Autodesk" / "ActivityInsights" / "CSV").resolve()
    return (Path.home() / "AppData" / "Local" / "Autodesk" / "ActivityInsights" / "CSV").resolve()


def activity_insights_write_preflight() -> dict:
    """Verify the directory AutoCAD's Activity Insights component can write."""
    path = activity_insights_path()
    if os.environ.get("AUTOCAD_MCP_DISABLE_ACTIVITY_INSIGHTS", "").strip().lower() in (
        "1", "true", "yes", "on"
    ):
        return {
            "ok": True,
            "checked": False,
            "disabled_by_config": True,
            "path": str(path),
        }

    probe = path / f".autocad-mcp-write-{os.getpid()}-{uuid.uuid4().hex}"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe.write_text("preflight\n", encoding="ascii")
        probe.unlink(missing_ok=True)
        return {"ok": True, "checked": True, "path": str(path)}
    except Exception as exc:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
        return {
            "ok": False,
            "checked": True,
            "path": str(path),
            "exception_type": type(exc).__name__,
            "message": str(exc) or type(exc).__name__,
        }


def environment_preflight() -> dict:
    """Return a non-mutating snapshot suitable for ``system.preflight``."""
    runtime = win32_runtime_health()
    activity = activity_insights_write_preflight()
    processes = list_autocad_processes()
    return {
        "ok": bool(runtime["ok"] and activity["ok"]),
        "runtime": runtime,
        "autocad_processes": processes,
        "activity_insights": activity,
        "autostart_enabled": os.environ.get("AUTOCAD_MCP_AUTOSTART", "false").lower()
        in ("1", "true", "yes", "on"),
        "recommended_action": (
            "repair_pywin32_and_restart_mcp"
            if not runtime["ok"]
            else "fix_activity_insights_path_permissions_or_disable_activity_insights"
            if not activity["ok"]
            else "start_autocad_manually_then_call_system.ensure_ready"
            if processes
            else "environment_ready"
        ),
    }


def runtime_health_error(result: dict) -> RuntimeHealthError | None:
    if not result["ok"]:
        return RuntimeHealthError(
            "The Windows AutoCAD runtime preflight failed",
            error_code="E_PYWIN32_BROKEN"
            if not result["runtime"]["ok"]
            else "E_AUTOCAD_PROFILE_UNWRITABLE",
            details=result,
            recommended_action=result["recommended_action"],
        )
    return None
