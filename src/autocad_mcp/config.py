"""Backend detection and environment configuration."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

import structlog

log = structlog.get_logger()

# Paths
LISP_DIR = Path(__file__).resolve().parent.parent.parent / "lisp-code"
IPC_DIR = Path(os.environ.get("AUTOCAD_MCP_IPC_DIR", "C:/temp"))

# Backend selection
BACKEND_DEFAULT = "auto"  # auto | file_ipc | ezdxf

# IPC timeout (seconds), clamped to [1, 300]
IPC_TIMEOUT = max(1.0, min(300.0, float(os.environ.get("AUTOCAD_MCP_IPC_TIMEOUT", "10.0"))))

# Screenshot
ONLY_TEXT_FEEDBACK = os.environ.get("AUTOCAD_MCP_ONLY_TEXT", "").lower() in ("1", "true", "yes")

# Win32 availability
WIN32_AVAILABLE = sys.platform == "win32"


def _env_flag(name: str, default: bool = False) -> bool:
    """Read a conventional boolean environment variable."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _autostart_autocad(find_window: Callable[[], int | None]) -> int | None:
    """Start AutoCAD when explicitly configured and wait for its main window."""
    if not WIN32_AVAILABLE or not _env_flag("AUTOCAD_MCP_AUTOSTART"):
        return None

    executable = os.environ.get("AUTOCAD_MCP_ACAD_EXE", "").strip()
    if not executable:
        raise RuntimeError(
            "AUTOCAD_MCP_AUTOSTART is enabled but AUTOCAD_MCP_ACAD_EXE is not set."
        )

    executable_path = Path(executable).expanduser()
    if not executable_path.is_file():
        raise RuntimeError(f"Configured AutoCAD executable was not found: {executable_path}")

    startup_script = os.environ.get("AUTOCAD_MCP_ACAD_SCRIPT", "").strip()
    command = [str(executable_path), "/nologo"]
    if startup_script:
        script_path = Path(startup_script).expanduser()
        if not script_path.is_file():
            raise RuntimeError(f"Configured AutoCAD startup script was not found: {script_path}")
        command.extend(["/b", str(script_path)])

    log.info("autocad_autostart", executable=str(executable_path))
    subprocess.Popen(command, cwd=str(executable_path.parent))

    timeout = max(
        5.0,
        min(180.0, float(os.environ.get("AUTOCAD_MCP_ACAD_STARTUP_TIMEOUT", "75"))),
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hwnd = find_window()
        if hwnd:
            log.info("autocad_autostart_ready", hwnd=hwnd)
            return hwnd
        time.sleep(0.5)

    raise RuntimeError(f"AutoCAD did not expose a usable main window within {timeout:g} seconds.")


def _current_backend_env() -> str:
    """Read backend selection from env with normalization."""
    return os.environ.get("AUTOCAD_MCP_BACKEND", BACKEND_DEFAULT).strip().lower()


def _is_wsl() -> bool:
    """Detect WSL Linux runtime."""
    if os.environ.get("WSL_INTEROP"):
        return True
    try:
        return "microsoft" in os.uname().release.lower()
    except AttributeError:
        return False


def _write_debug_snapshot(backend_env: str):
    """Optionally write backend detection debug information.

    Set AUTOCAD_MCP_DEBUG_DETECT_FILE to enable.
    """
    debug_file = os.environ.get("AUTOCAD_MCP_DEBUG_DETECT_FILE", "").strip()
    if not debug_file:
        return

    try:
        debug_path = Path(debug_file)
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        with debug_path.open("w", encoding="utf-8") as f:
            f.write(f"sys.platform={sys.platform}\n")
            f.write(f"WIN32_AVAILABLE={WIN32_AVAILABLE}\n")
            f.write(f"BACKEND_ENV={backend_env}\n")
            f.write(f"python={sys.executable}\n")
    except Exception:
        # Best-effort only; never fail backend detection due debug writes.
        pass


def detect_backend() -> str:
    """Return the backend name to use: 'file_ipc' or 'ezdxf'.

    Raises RuntimeError with actionable message if explicit backend fails.
    """
    backend_env = _current_backend_env()
    _write_debug_snapshot(backend_env)

    if backend_env == "ezdxf":
        return "ezdxf"

    if backend_env in ("auto", "file_ipc"):
        if WIN32_AVAILABLE:
            try:
                from autocad_mcp.backends.file_ipc import find_autocad_window

                hwnd = find_autocad_window()
                if not hwnd:
                    hwnd = _autostart_autocad(find_autocad_window)
                if hwnd:
                    log.info("autocad_window_found", hwnd=hwnd)
                    return "file_ipc"
                elif backend_env == "file_ipc":
                    raise RuntimeError(
                        "AUTOCAD_MCP_BACKEND=file_ipc but no AutoCAD window found. "
                        "Start AutoCAD LT and open a .dwg file."
                    )
            except ImportError:
                if backend_env == "file_ipc":
                    raise RuntimeError(
                        "AUTOCAD_MCP_BACKEND=file_ipc requires pywin32. "
                        "Install with: pip install pywin32"
                    )
                log.info("win32_deps_missing_fallback_ezdxf")
        elif backend_env == "file_ipc":
            raise RuntimeError(
                "AUTOCAD_MCP_BACKEND=file_ipc requires Windows. "
                "Use AUTOCAD_MCP_BACKEND=ezdxf for headless mode."
            )
        elif _is_wsl():
            log.info(
                "wsl_linux_python_fallback_ezdxf",
                platform=sys.platform,
                python=sys.executable,
                hint="Launch MCP with Windows python.exe for File IPC backend.",
            )

    log.info("using_ezdxf_backend")
    return "ezdxf"
