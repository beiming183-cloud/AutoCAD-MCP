"""File-based IPC backend for AutoCAD LT.

Protocol:
1. Python writes JSON command to C:/temp/autocad_mcp_cmd_{request_id}.json
2. Python types the fixed string "(c:mcp-dispatch)" + Enter
3. LISP reads cmd, dispatches via command map, writes result to
   C:/temp/autocad_mcp_result_{request_id}.json
4. Python polls for result file (100ms intervals, 10s timeout)
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import sys
import time
import uuid
from pathlib import Path

import structlog

from autocad_mcp import __version__
from autocad_mcp.audit import INSUNITS_NAMES, audit_dxf_file, build_audit
from autocad_mcp.backends.base import AutoCADBackend, BackendCapabilities, CommandResult
from autocad_mcp.config import IPC_DIR, IPC_TIMEOUT, LISP_DIR, _autostart_autocad
from autocad_mcp.drafting import encode_autocad_text
from autocad_mcp.variables import mechanical_variable_updates, validate_variable_updates

log = structlog.get_logger()

# IPC settings
POLL_INTERVAL = 0.1  # seconds
TIMEOUT = IPC_TIMEOUT  # seconds (configurable via AUTOCAD_MCP_IPC_TIMEOUT)
STALE_THRESHOLD = 60.0  # clean up files older than this


def find_autocad_window() -> int | None:
    """Find the AutoCAD main window, including hidden automation sessions."""
    if sys.platform != "win32":
        return None
    try:
        import win32api
        import win32con
        import win32gui
        import win32process

        windows: list[int] = []

        def callback(hwnd, result):
            if win32gui.IsWindowVisible(hwnd):
                is_autocad = False
                process_handle = None
                try:
                    _, process_id = win32process.GetWindowThreadProcessId(hwnd)
                    process_handle = win32api.OpenProcess(
                        win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
                        False,
                        process_id,
                    )
                    executable = win32process.GetModuleFileNameEx(process_handle, 0)
                    is_autocad = Path(executable).name.lower() == "acad.exe"
                except Exception:
                    # A title fallback supports restricted process-query environments.
                    title = win32gui.GetWindowText(hwnd).lower()
                    is_autocad = "autodesk autocad" in title
                finally:
                    if process_handle is not None:
                        try:
                            win32api.CloseHandle(process_handle)
                        except Exception:
                            pass

                if is_autocad:
                    result.append(hwnd)
            return True

        win32gui.EnumWindows(callback, windows)
        if windows:
            return windows[0]

        try:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            application = win32com.client.GetActiveObject("AutoCAD.Application")
            hwnd = int(application.HWND)
            return hwnd if hwnd else None
        except Exception:
            return None
    except ImportError:
        return None


def _com_value(obj, name: str, default=None):
    try:
        value = getattr(obj, name)
        return value if value is not None else default
    except Exception:
        return default


def _com_point(value) -> list[float] | None:
    try:
        return [round(float(item), 6) for item in list(value)[:3]]
    except (TypeError, ValueError):
        return None


def _com_entity_to_dict(entity) -> dict:
    """Normalize a full AutoCAD COM entity for structured auditing."""
    object_name = str(_com_value(entity, "ObjectName", "UNKNOWN"))
    short_name = object_name.removeprefix("AcDb")
    if "Dimension" in short_name:
        entity_type = "DIMENSION"
    else:
        entity_type = {
            "Polyline": "LWPOLYLINE",
            "2dPolyline": "POLYLINE",
            "3dPolyline": "POLYLINE3D",
            "BlockReference": "INSERT",
            "MText": "MTEXT",
            "Text": "TEXT",
        }.get(short_name, short_name.upper())

    result = {
        "type": entity_type,
        "handle": str(_com_value(entity, "Handle", "")),
        "layer": str(_com_value(entity, "Layer", "0")),
    }

    if entity_type == "LINE":
        result.update(
            start=_com_point(_com_value(entity, "StartPoint")),
            end=_com_point(_com_value(entity, "EndPoint")),
        )
    elif entity_type in ("CIRCLE", "ARC"):
        result.update(
            center=_com_point(_com_value(entity, "Center")),
            radius=_com_value(entity, "Radius"),
        )
        if entity_type == "ARC":
            result.update(
                start_angle=math.degrees(float(_com_value(entity, "StartAngle", 0))),
                end_angle=math.degrees(float(_com_value(entity, "EndAngle", 0))),
            )
    elif entity_type in ("LWPOLYLINE", "POLYLINE", "POLYLINE3D"):
        coordinates = list(_com_value(entity, "Coordinates", []) or [])
        stride = 2 if entity_type == "LWPOLYLINE" else 3
        result["points"] = [
            [round(float(value), 6) for value in coordinates[index : index + stride]]
            for index in range(0, len(coordinates), stride)
        ]
        result["closed"] = bool(_com_value(entity, "Closed", False))
    elif entity_type in ("TEXT", "MTEXT"):
        result.update(
            insert=_com_point(
                _com_value(entity, "InsertionPoint", _com_value(entity, "TextAlignmentPoint"))
            ),
            text=str(_com_value(entity, "TextString", "")),
            height=_com_value(entity, "Height", _com_value(entity, "TextHeight")),
            rotation=math.degrees(float(_com_value(entity, "Rotation", 0))),
        )
    elif entity_type == "INSERT":
        result.update(
            name=str(_com_value(entity, "EffectiveName", _com_value(entity, "Name", ""))),
            insert=_com_point(_com_value(entity, "InsertionPoint")),
            rotation=math.degrees(float(_com_value(entity, "Rotation", 0))),
            xscale=_com_value(entity, "XScaleFactor", 1),
            yscale=_com_value(entity, "YScaleFactor", 1),
        )
    elif entity_type == "DIMENSION":
        result.update(
            measurement=_com_value(entity, "Measurement"),
            text=str(_com_value(entity, "TextOverride", "")),
            text_position=_com_point(_com_value(entity, "TextPosition")),
        )
    elif entity_type == "HATCH":
        result.update(
            pattern=str(_com_value(entity, "PatternName", "")),
            area=_com_value(entity, "Area"),
        )

    try:
        minimum, maximum = entity.GetBoundingBox()
        result["bounds"] = {"min": _com_point(minimum), "max": _com_point(maximum)}
    except Exception:
        pass
    return {key: value for key, value in result.items() if value is not None}


class FileIPCBackend(AutoCADBackend):
    """File IPC for full AutoCAD and AutoCAD LT via mcp_dispatch.lsp."""

    def __init__(self):
        self._hwnd: int | None = None
        self._command_hwnd: int | None = None
        self._ipc_dir = Path(IPC_DIR)
        self._screenshot_provider = None
        self._lock = asyncio.Lock()  # Single in-flight command
        self._audit_revision = 0
        self._audit_fingerprints: dict[str, str] | None = None
        self._suspend_auto_fit = 0
        self._dispatcher_version: str | None = None
        self._product_info: dict = {}

    @property
    def name(self) -> str:
        return "file_ipc"

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            can_read_drawing=True,
            can_modify_entities=True,
            can_create_entities=True,
            can_screenshot=True,
            can_save=True,
            can_plot_pdf=True,
            can_zoom=True,
            can_query_entities=True,
            can_file_operations=True,
            can_undo=True,
        )

    async def initialize(self) -> CommandResult:
        """Make AutoCAD and its versioned dispatcher ready for commands."""
        return await self.ensure_ready()

    async def ensure_ready(self) -> CommandResult:
        """Discover, start, connect, load, handshake, and ping AutoCAD."""
        self._hwnd = find_autocad_window()
        if not self._hwnd:
            try:
                self._hwnd = _autostart_autocad(find_autocad_window)
            except Exception as exc:
                return CommandResult(ok=False, error=str(exc))
        if not self._hwnd:
            return CommandResult(
                ok=False,
                error="AutoCAD is not running and automatic startup is unavailable",
                error_code="E_AUTOCAD_NOT_RUNNING",
            )

        visibility = self._ensure_autocad_visible()
        log.info("autocad_visibility", **visibility)

        document = self._ensure_active_document()
        if not document["ready"]:
            return CommandResult(
                ok=False,
                error=document["error"],
                error_code="E_NO_ACTIVE_DOCUMENT",
            )
        self._product_info = self._discover_product()

        # Set up screenshot provider
        try:
            from autocad_mcp.screenshot import Win32ScreenshotProvider

            self._screenshot_provider = Win32ScreenshotProvider(self._hwnd)
        except Exception:
            pass

        # Find command-line child edit control for focus-free dispatch
        self._command_hwnd = self._find_command_line_hwnd()
        log.info("command_line_hwnd", hwnd=self._command_hwnd)

        # Ensure IPC directory exists
        self._ipc_dir.mkdir(parents=True, exist_ok=True)

        # Clean up stale IPC files
        self._cleanup_stale_files()

        candidates = []
        configured = os.environ.get("AUTOCAD_MCP_LISP_PATH", "").strip()
        if configured:
            candidates.append(Path(configured).expanduser())
        bundled = LISP_DIR / "mcp_dispatch.lsp"
        if bundled not in candidates:
            candidates.append(bundled)

        last_result = None
        loaded_path = None
        self._dispatcher_version = None
        for candidate in candidates:
            if not candidate.is_file():
                continue
            normalized_path = str(candidate.resolve()).replace("\\", "/")
            self._type_command(f'(load "{normalized_path}")')
            await asyncio.sleep(0.75)
            self._wait_for_autocad_idle(timeout=10.0)
            last_result = await self._dispatch("ping", {})
            version = (
                last_result.payload.get("dispatcher_version")
                if last_result.ok and isinstance(last_result.payload, dict)
                else None
            )
            if version == __version__:
                self._dispatcher_version = version
                loaded_path = str(candidate.resolve())
                break

        if not last_result or not last_result.ok:
            return CommandResult(
                ok=False,
                error="AutoCAD is running but the MCP dispatcher could not be loaded or pinged",
                error_code="E_DISPATCHER_NOT_LOADED",
            )
        if self._dispatcher_version != __version__:
            actual = (
                last_result.payload.get("dispatcher_version")
                if isinstance(last_result.payload, dict)
                else "unknown"
            )
            return CommandResult(
                ok=False,
                error=f"Dispatcher version mismatch: expected {__version__}, received {actual}",
                error_code="E_DISPATCHER_VERSION_MISMATCH",
            )

        return CommandResult(
            ok=True,
            payload={
                "autocad": {
                    **self._product_info,
                    "running": True,
                    "hwnd": self._hwnd,
                    "active_document": document["name"],
                },
                "dispatcher": {
                    "loaded": True,
                    "version": self._dispatcher_version,
                    "path": loaded_path,
                },
                "transport": "file_ipc",
                "ready": True,
                "visibility": visibility,
            },
        )

    async def status(self) -> CommandResult:
        info = {
            "backend": "file_ipc",
            "hwnd": self._hwnd,
            "ipc_dir": str(self._ipc_dir),
            "capabilities": {k: v for k, v in self.capabilities.__dict__.items()},
            "visibility": self._window_visibility_status(),
            "auto_fit": os.environ.get("AUTOCAD_MCP_AUTO_FIT", "true").lower()
            in ("1", "true", "yes", "on"),
            "autocad": self._product_info or self._discover_product(),
            "dispatcher": {
                "loaded": self._dispatcher_version is not None,
                "version": self._dispatcher_version,
                "expected_version": __version__,
            },
            "ready": bool(self._hwnd and self._dispatcher_version == __version__),
        }
        return CommandResult(ok=True, payload=info)

    def _discover_product(self) -> dict:
        executable = os.environ.get("AUTOCAD_MCP_ACAD_EXE", "").strip()
        product = None
        version = None
        try:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            application = win32com.client.GetActiveObject("AutoCAD.Application")
            product = str(getattr(application, "Name", "AutoCAD"))
            version = str(getattr(application, "Version", ""))
        except Exception:
            pass
        if executable:
            match = re.search(r"AutoCAD\s+(\d{4})", executable, re.IGNORECASE)
            if match:
                product = f"AutoCAD {match.group(1)}"
        installed = bool(executable and Path(executable).is_file()) or bool(self._hwnd)
        return {
            "installed": installed,
            "product": product or "AutoCAD",
            "version": version,
            "exe": executable or None,
        }

    def _ensure_active_document(self) -> dict:
        deadline = time.monotonic() + 10.0
        last_error = None
        while time.monotonic() < deadline:
            try:
                import pythoncom
                import win32com.client

                pythoncom.CoInitialize()
                application = win32com.client.GetActiveObject("AutoCAD.Application")
                if int(application.Documents.Count) == 0:
                    application.Documents.Add()
                document = application.ActiveDocument
                document.Activate()
                return {"ready": True, "name": str(document.Name)}
            except Exception as exc:
                last_error = exc
                time.sleep(0.25)
        return {"ready": False, "error": f"No active document after retry: {last_error}"}

    # --- IPC dispatch ---

    async def _dispatch(self, command: str, params: dict) -> CommandResult:
        """Send a command via file IPC and wait for result."""
        async with self._lock:
            return await self._dispatch_unlocked(command, params)

    async def _dispatch_unlocked(self, command: str, params: dict) -> CommandResult:
        """Core IPC logic (must be called under _lock)."""
        request_id = uuid.uuid4().hex[:12]
        cmd_file = self._ipc_dir / f"autocad_mcp_cmd_{request_id}.json"
        result_file = self._ipc_dir / f"autocad_mcp_result_{request_id}.json"
        tmp_file = cmd_file.with_suffix(".tmp")

        try:
            # Strip None values — the simple LISP JSON parser can't handle null
            clean_params = {k: v for k, v in params.items() if v is not None}
            # Atomic write: write to .tmp, then rename
            payload = {
                "request_id": request_id,
                "command": command,
                "params": clean_params,
                "ts": time.time(),
            }
            tmp_file.write_text(json.dumps(payload), encoding="utf-8")
            tmp_file.rename(cmd_file)

            # Type the fixed dispatch trigger only after AutoCAD reaches idle.
            if not self._type_dispatch_trigger():
                return CommandResult(
                    ok=False,
                    error="AutoCAD command state remained blocked after cancellation",
                    error_code="E_COMMAND_STATE_BLOCKED",
                )

            # Poll for result
            deadline = time.time() + TIMEOUT
            while time.time() < deadline:
                if result_file.exists():
                    try:
                        # AutoCAD LISP writes files in Windows-1252 encoding;
                        # try UTF-8 first (covers ASCII), fall back to cp1252
                        try:
                            text = result_file.read_text(encoding="utf-8")
                        except UnicodeDecodeError:
                            text = result_file.read_text(encoding="cp1252")
                        data = json.loads(text)
                        # Verify request_id matches
                        if data.get("request_id") == request_id:
                            self._wait_for_autocad_idle(timeout=2.0)
                            result = CommandResult(
                                ok=data.get("ok", False),
                                payload=data.get("payload"),
                                error=data.get("error"),
                            )
                            if result.ok and self._should_auto_fit(command):
                                self._auto_fit_view()
                            return result
                    except (json.JSONDecodeError, OSError):
                        pass  # File may be partially written, retry
                await asyncio.sleep(POLL_INTERVAL)

            self._cancel_active_command()
            return CommandResult(
                ok=False,
                error=(
                    f"Timeout waiting for result (request_id={request_id}); "
                    "AutoCAD command state was cancelled and IPC files were cleaned"
                ),
                error_code="E_IPC_TIMEOUT",
            )

        finally:
            # Cleanup
            for f in (cmd_file, result_file, tmp_file):
                try:
                    f.unlink(missing_ok=True)
                except OSError:
                    pass

    def _find_command_line_hwnd(self) -> int | None:
        """Find AutoCAD's MDIClient child window for command routing."""
        if sys.platform != "win32" or not self._hwnd:
            return None
        try:
            import win32gui

            mdi_client: list[int] = []

            def cb(child_hwnd, _):
                if win32gui.GetClassName(child_hwnd) == "MDIClient":
                    mdi_client.append(child_hwnd)
                    return False  # stop enumeration
                return True

            win32gui.EnumChildWindows(self._hwnd, cb, None)
            return mdi_client[0] if mdi_client else None
        except Exception:
            return None

    def _type_dispatch_trigger(self) -> bool:
        """Post '(c:mcp-dispatch)' + Enter via WM_CHAR to MDIClient — no focus steal.

        Sends ESC keystrokes first to cancel any stale pending command
        (e.g. from a previous timeout leaving AutoCAD in a command prompt).
        """
        self._ensure_autocad_visible(
            activate=os.environ.get("AUTOCAD_MCP_ACTIVATE_ON_DRAW", "false").lower()
            in ("1", "true", "yes", "on")
        )
        if not self._wait_for_autocad_idle(timeout=5.0):
            self._cancel_active_command()
            if not self._wait_for_autocad_idle(timeout=2.0):
                return False
        self._type_command("(c:mcp-dispatch)")
        return True

    def _wait_for_autocad_idle(self, timeout: float = 2.0) -> bool:
        """Wait for AutoCAD to finish unwinding the previous dispatched command."""
        if sys.platform != "win32":
            return True
        deadline = time.time() + max(0.0, timeout)
        while True:
            try:
                import pythoncom
                import win32com.client

                pythoncom.CoInitialize()
                application = win32com.client.GetActiveObject("AutoCAD.Application")
                document = application.ActiveDocument
                if int(document.GetVariable("CMDACTIVE")) == 0:
                    return True
            except Exception as exc:
                hresult = getattr(exc, "hresult", exc.args[0] if exc.args else None)
                if hresult != -2147418111:
                    return False
            if time.time() >= deadline:
                return False
            time.sleep(0.05)

    def _window_visibility_status(self) -> dict:
        configured = os.environ.get("AUTOCAD_MCP_VISIBLE", "true").lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        status = {"configured_visible": configured, "hwnd": self._hwnd}
        if sys.platform == "win32" and self._hwnd:
            try:
                import win32gui

                status.update(
                    visible=bool(win32gui.IsWindowVisible(self._hwnd)),
                    minimized=bool(win32gui.IsIconic(self._hwnd)),
                )
            except Exception:
                pass
        return status

    @staticmethod
    def _should_auto_fit(command: str) -> bool:
        if command.startswith("create-"):
            return True
        return command in {
            "entity-copy",
            "entity-move",
            "entity-rotate",
            "entity-scale",
            "entity-mirror",
            "entity-offset",
            "entity-array",
            "entity-fillet",
            "entity-chamfer",
            "entity-erase",
            "block-insert",
            "block-insert-with-attribs",
            "pid-insert-symbol",
            "pid-draw-process-line",
            "pid-connect-equipment",
            "pid-add-flow-arrow",
            "pid-add-equipment-tag",
            "pid-add-line-number",
            "pid-insert-valve",
            "pid-insert-instrument",
            "pid-insert-pump",
            "pid-insert-tank",
        }

    def _auto_fit_view(self, force: bool = False) -> dict:
        """Center all drawing extents in the viewport after geometry changes."""
        configured = os.environ.get("AUTOCAD_MCP_AUTO_FIT", "true").lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        if not force and (not configured or self._suspend_auto_fit > 0):
            return {
                "configured": configured,
                "fitted": False,
                "suspended": self._suspend_auto_fit > 0,
            }

        if not self._wait_for_autocad_idle(timeout=2.0):
            return {"configured": configured, "fitted": False, "reason": "autocad-busy"}
        try:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            application = win32com.client.GetActiveObject("AutoCAD.Application")
            application.ZoomExtents()
            application.Update()
            return {"configured": configured, "fitted": True, "renderer": "autocad-com"}
        except Exception:
            self._type_command("_.ZOOM _E")
            self._wait_for_autocad_idle(timeout=2.0)
            return {"configured": configured, "fitted": True, "renderer": "autocad-command"}

    def _ensure_autocad_visible(self, activate: bool = False) -> dict:
        """Show and restore AutoCAD so drawing actions can be watched live."""
        configured = os.environ.get("AUTOCAD_MCP_VISIBLE", "true").lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        if not configured:
            return {"configured_visible": False, "shown": False}

        transport = "win32"
        try:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            application = win32com.client.GetActiveObject("AutoCAD.Application")
            application.Visible = True
            self._hwnd = int(application.HWND) or self._hwnd
            application.Update()
            transport = "autocad-com"
        except Exception:
            pass

        if sys.platform == "win32" and self._hwnd:
            try:
                import win32con
                import win32gui

                win32gui.ShowWindow(self._hwnd, win32con.SW_RESTORE)
                if activate:
                    try:
                        win32gui.SetForegroundWindow(self._hwnd)
                    except Exception:
                        pass
            except Exception:
                pass
        return {
            "configured_visible": True,
            "shown": bool(self._hwnd),
            "activated": bool(activate),
            "transport": transport,
        }

    def _cancel_active_command(self) -> str:
        """Cancel command-line state without requiring the IPC dispatcher."""
        if sys.platform != "win32":
            return "not-windows"
        try:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            application = win32com.client.GetActiveObject("AutoCAD.Application")
            document = application.ActiveDocument
            active = int(document.GetVariable("CMDACTIVE"))
            if active:
                document.SendCommand("\x1b\x1b")
                time.sleep(0.1)
            return "autocad-com"
        except Exception:
            pass

        try:
            import ctypes

            target = self._command_hwnd or self._hwnd
            if not target:
                return "no-window"
            post = ctypes.windll.user32.PostMessageW
            for _ in range(2):
                post(target, 0x0100, 0x1B, 0)
                post(target, 0x0101, 0x1B, 0)
            time.sleep(0.1)
            return "win32-message"
        except Exception:
            return "failed"

    def _type_command(self, command: str):
        """Post a command-line expression to the active AutoCAD session."""
        if self._send_command_via_com(command):
            return

        try:
            import ctypes

            WM_CHAR = 0x0102
            WM_KEYDOWN = 0x0100
            WM_KEYUP = 0x0101
            VK_ESCAPE = 0x1B
            target = self._command_hwnd or self._hwnd
            post = ctypes.windll.user32.PostMessageW

            # Cancel any pending command (2x ESC for nested commands)
            for _ in range(2):
                post(target, WM_KEYDOWN, VK_ESCAPE, 0)
                post(target, WM_KEYUP, VK_ESCAPE, 0)
            time.sleep(0.05)

            for ch in command:
                post(target, WM_CHAR, ord(ch), 0)
            # Enter = carriage return
            post(target, WM_CHAR, 0x0D, 0)
            time.sleep(0.05)
        except Exception as e:
            log.error("command_trigger_failed", error=str(e))

    def _send_command_via_com(self, command: str) -> bool:
        """Use full AutoCAD's COM API before falling back to window messages."""
        if sys.platform != "win32":
            return False
        deadline = time.time() + 5.0
        while True:
            try:
                import pythoncom
                import win32com.client

                pythoncom.CoInitialize()
                application = win32com.client.GetActiveObject("AutoCAD.Application")
                document = application.ActiveDocument
                document.SendCommand(command + "\n")
                log.debug("command_sent_via_com")
                return True
            except Exception as exc:
                hresult = getattr(exc, "hresult", exc.args[0] if exc.args else None)
                if hresult == -2147418111 and time.time() < deadline:
                    time.sleep(0.25)
                    continue
                log.debug("com_command_unavailable", error=str(exc))
                return False

    def _cleanup_stale_files(self):
        """Remove stale IPC files from previous sessions."""
        try:
            now = time.time()
            for pattern in ("autocad_mcp_*.json", "autocad_mcp_*.tmp", "autocad_mcp_lisp_*.lsp"):
                for f in self._ipc_dir.glob(pattern):
                    if now - f.stat().st_mtime > STALE_THRESHOLD:
                        f.unlink(missing_ok=True)
        except OSError:
            pass

    async def recover(self) -> CommandResult:
        cancel_transport = self._cancel_active_command()
        removed = 0
        for pattern in ("autocad_mcp_cmd_*", "autocad_mcp_result_*", "*.tmp"):
            for path in self._ipc_dir.glob(pattern):
                try:
                    path.unlink(missing_ok=True)
                    removed += 1
                except OSError:
                    pass
        return CommandResult(
            ok=True,
            payload={
                "recovered": True,
                "cancel_transport": cancel_transport,
                "removed_ipc_files": removed,
            },
        )

    # --- Drawing management ---

    async def drawing_info(self) -> CommandResult:
        return await self._dispatch("drawing-info", {})

    async def drawing_save(self, path: str | None = None) -> CommandResult:
        try:
            return CommandResult(ok=True, payload=self._save_via_com(path))
        except Exception as com_error:
            result = await self._dispatch("drawing-save", {"path": path})
            if result.ok and isinstance(result.payload, dict):
                result.payload["warning"] = f"COM save unavailable: {com_error}"
            return result

    async def drawing_save_as_dxf(self, path: str) -> CommandResult:
        try:
            return CommandResult(ok=True, payload=self._save_via_com(path, file_type=61))
        except Exception as com_error:
            result = await self._dispatch("drawing-save-as-dxf", {"path": path})
            if result.ok and isinstance(result.payload, dict):
                result.payload["warning"] = f"COM DXF save unavailable: {com_error}"
            return result

    async def drawing_create(self, name: str | None = None) -> CommandResult:
        result = await self._dispatch("drawing-create", {"name": name})
        if result.ok:
            self._audit_revision = 0
            self._audit_fingerprints = None
        return result

    async def drawing_purge(self) -> CommandResult:
        return await self._dispatch("drawing-purge", {})

    async def drawing_setup_mechanical(self, config: dict | None = None) -> CommandResult:
        layers = await self._dispatch("drawing-setup-mechanical", {})
        if not layers.ok:
            return layers
        try:
            variables = await self.drawing_set_variables(mechanical_variable_updates(config))
        except ValueError as exc:
            return CommandResult(ok=False, error=str(exc), error_code="E_VARIABLE_REJECTED")
        if not variables.ok:
            return variables
        options = dict(config or {})
        return CommandResult(
            ok=True,
            payload={
                "profile": "mechanical-gbt",
                "standard": options.get("standard", "GB/T"),
                "units": options.get("units", "mm"),
                "sheet": options.get("sheet", "A3"),
                "orientation": options.get("orientation", "landscape"),
                "projection": options.get("projection", "first-angle"),
                "scale": options.get("scale", "1:1"),
                "layers": layers.payload,
                "variables": variables.payload,
            },
        )

    async def drawing_plot_pdf(
        self,
        path: str,
        paper: str = "A4",
        orientation: str = "auto",
        plot_style: str = "monochrome.ctb",
        scale_mode: str = "fit",
        scale: str = "1:1",
        center: bool = True,
    ) -> CommandResult:
        try:
            payload = self._plot_preview_via_com(
                path, paper, orientation, plot_style, scale_mode, scale, center
            )
            return CommandResult(ok=True, payload=payload)
        except Exception as com_error:
            result = await self._dispatch("drawing-plot-pdf", {"path": path})
            output = Path(path).expanduser().resolve()
            if result.ok and output.is_file() and output.stat().st_size > 0:
                payload = result.payload if isinstance(result.payload, dict) else {"path": str(output)}
                payload.update(renderer="autolisp-plot", warning=f"COM plot unavailable: {com_error}")
                return CommandResult(ok=True, payload=payload)
            fallback_error = result.error or "AutoLISP reported success but no non-empty PDF was created"
            return CommandResult(
                ok=False,
                error=f"COM plot failed: {com_error}; AutoLISP plot failed: {fallback_error}",
            )

    async def drawing_audit(
        self,
        limit=50,
        include_entities=True,
        changed_only=False,
        layer=None,
        space="model",
    ) -> CommandResult:
        try:
            entities = self._collect_entities_via_com(layer=layer, space=space)
            source = "autocad-com"
        except Exception as com_error:
            listing = await self.entity_list(layer)
            if not listing.ok:
                return CommandResult(ok=False, error=f"COM audit failed: {com_error}; {listing.error}")
            entities = list(listing.payload.get("entities", []))
            if include_entities:
                detail_limit = max(0, min(int(limit), 500))
                details = {}
                for entity in entities[:detail_limit]:
                    detail = await self.entity_get(entity.get("handle"))
                    if detail.ok:
                        details[entity.get("handle")] = detail.payload
                entities = [details.get(entity.get("handle"), entity) for entity in entities]
            source = "autolisp-fallback"

        self._audit_revision += 1
        payload, fingerprints = build_audit(
            entities,
            limit=limit,
            include_entities=include_entities,
            changed_only=changed_only,
            previous_fingerprints=self._audit_fingerprints,
            revision=self._audit_revision,
            space=space.lower(),
        )
        self._audit_fingerprints = fingerprints
        payload["source"] = source
        try:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            document = win32com.client.GetActiveObject("AutoCAD.Application").ActiveDocument
            units_code = int(document.GetVariable("INSUNITS"))
            payload["units"] = {
                "code": units_code,
                "name": INSUNITS_NAMES.get(units_code, "unknown"),
            }
            payload["drawing_variables"] = {
                name: document.GetVariable(name)
                for name in ("INSUNITS", "LUNITS", "LUPREC", "DIMTXT", "DIMASZ")
            }
        except Exception as metadata_error:
            payload.setdefault("warnings", []).append(
                f"Drawing unit metadata unavailable: {metadata_error}"
            )
        if source == "autolisp-fallback":
            payload.setdefault("warnings", []).append(
                "AutoLISP fallback has limited geometry fields; full AutoCAD COM provides deeper auditing."
            )
        return CommandResult(ok=True, payload=payload)

    async def drawing_audit_dxf(self, path, limit=50, include_entities=True) -> CommandResult:
        try:
            return CommandResult(
                ok=True,
                payload=audit_dxf_file(path, limit=limit, include_entities=include_entities),
            )
        except Exception as exc:
            return CommandResult(ok=False, error=str(exc))

    async def drawing_render_preview(
        self, path, paper="A4", orientation="auto", plot_style="monochrome.ctb"
    ) -> CommandResult:
        try:
            payload = self._plot_preview_via_com(
                path, paper, orientation, plot_style, "fit", "1:1", True
            )
            return CommandResult(ok=True, payload=payload)
        except Exception as com_error:
            fallback = await self.drawing_plot_pdf(path)
            if fallback.ok:
                payload = fallback.payload if isinstance(fallback.payload, dict) else {"path": path}
                payload.update(renderer="autolisp-plot", warning=f"COM plot unavailable: {com_error}")
                return CommandResult(ok=True, payload=payload)
            return CommandResult(
                ok=False,
                error=f"COM preview failed: {com_error}; AutoLISP plot failed: {fallback.error}",
            )

    async def drawing_get_variables(self, names: list[str] | None = None) -> CommandResult:
        if names:
            # Strip $ prefix for AutoCAD compatibility (ezdxf uses $ACADVER, AutoCAD uses ACADVER)
            clean_names = [n.lstrip("$") for n in names]
            names_str = ";".join(clean_names)
        else:
            names_str = ""
        return await self._dispatch("drawing-get-variables", {"names_str": names_str})

    async def drawing_set_variables(self, values: dict) -> CommandResult:
        try:
            updates = validate_variable_updates(values)
        except ValueError as exc:
            return CommandResult(ok=False, error=str(exc), error_code="E_VARIABLE_REJECTED")
        document = None
        previous = {}
        try:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            document = win32com.client.GetActiveObject("AutoCAD.Application").ActiveDocument
            previous = {name: document.GetVariable(name) for name in updates}
            for name, value in updates.items():
                document.SetVariable(name, value)
            current = {name: document.GetVariable(name) for name in updates}
            return CommandResult(
                ok=True,
                payload={"updated": current, "previous": previous, "verified": True},
            )
        except Exception as exc:
            rolled_back = []
            if document is not None:
                for name, value in previous.items():
                    try:
                        document.SetVariable(name, value)
                        rolled_back.append(name)
                    except Exception:
                        pass
            return CommandResult(
                ok=False,
                error=f"Failed to set system variables: {exc}",
                payload={"rolled_back": rolled_back},
            )

    async def drawing_open(self, path: str) -> CommandResult:
        result = await self._dispatch("drawing-open", {"path": path})
        if result.ok:
            self._audit_revision = 0
            self._audit_fingerprints = None
        return result

    def _save_via_com(self, path: str | None, file_type: int | None = None) -> dict:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        application = win32com.client.GetActiveObject("AutoCAD.Application")
        document = application.ActiveDocument
        if not path:
            document.Save()
            return {"path": str(document.FullName), "renderer": "autocad-com-save"}

        output = Path(path).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        if file_type is None:
            inferred_type = 64 if output.suffix.lower() == ".dwg" else None
            if inferred_type is None:
                document.SaveAs(str(output))
            else:
                document.SaveAs(str(output), inferred_type)
        else:
            document.SaveAs(str(output), file_type)
        if not output.exists():
            raise RuntimeError(f"AutoCAD SaveAs completed but file is missing: {output}")
        active_path = Path(str(document.FullName)).expanduser().resolve()
        if os.path.normcase(str(active_path)) != os.path.normcase(str(output)):
            raise RuntimeError(
                f"AutoCAD SaveAs did not activate the requested output: {active_path} != {output}"
            )
        return {
            "path": str(output),
            "active_document": str(active_path),
            "format": output.suffix.lower().lstrip("."),
            "renderer": "autocad-com-saveas",
        }

    def _collect_entities_via_com(self, layer=None, space="model") -> list[dict]:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        application = win32com.client.GetActiveObject("AutoCAD.Application")
        document = application.ActiveDocument
        collection = document.PaperSpace if space.lower() == "paper" else document.ModelSpace
        entities = []
        for index in range(int(collection.Count)):
            entity = collection.Item(index)
            if layer and str(_com_value(entity, "Layer", "0")) != layer:
                continue
            entities.append(_com_entity_to_dict(entity))
        return entities

    def _plot_preview_via_com(
        self, path, paper, orientation, plot_style, scale_mode="fit", scale="1:1", center=True
    ) -> dict:
        import pythoncom
        import win32com.client

        output = Path(path).expanduser().resolve()
        if output.suffix.lower() != ".pdf":
            raise ValueError("Full AutoCAD preview output must use a .pdf extension")
        output.parent.mkdir(parents=True, exist_ok=True)

        pythoncom.CoInitialize()
        application = win32com.client.GetActiveObject("AutoCAD.Application")
        document = application.ActiveDocument
        layout = document.ActiveLayout
        saved = {}
        for name in (
            "ConfigName",
            "CanonicalMediaName",
            "PaperUnits",
            "PlotType",
            "UseStandardScale",
            "StandardScale",
            "CustomScale",
            "CenterPlot",
            "PlotRotation",
            "StyleSheet",
            "PlotWithPlotStyles",
        ):
            try:
                saved[name] = getattr(layout, name)
            except Exception:
                pass
        try:
            old_background_plot = document.GetVariable("BACKGROUNDPLOT")
        except Exception:
            old_background_plot = None

        try:
            document.SetVariable("BACKGROUNDPLOT", 0)
            layout.ConfigName = "DWG To PDF.pc3"
            layout.RefreshPlotDeviceInfo()
            media_names = list(layout.GetCanonicalMediaNames())
            paper_token = paper.upper().replace(" ", "_")
            media = next(
                (name for name in media_names if paper_token in str(name).upper()),
                media_names[0] if media_names else None,
            )
            if media:
                layout.CanonicalMediaName = media
            plot_paper_units = "millimeters" if str(paper).upper().startswith("A") else "inches"
            layout.PaperUnits = 1 if plot_paper_units == "millimeters" else 0
            layout.PlotType = 1  # acExtents
            selected_scale_mode = str(scale_mode).lower()
            if selected_scale_mode == "fit":
                layout.UseStandardScale = True
                layout.StandardScale = 0  # acScaleToFit
                actual_scale = "fit"
            elif selected_scale_mode == "fixed":
                try:
                    scale_paper_units, scale_drawing_units = [
                        float(item) for item in str(scale).split(":", 1)
                    ]
                except (TypeError, ValueError, ZeroDivisionError) as exc:
                    raise ValueError("Fixed plot scale must use paper:drawing form, e.g. 1:1") from exc
                if scale_paper_units <= 0 or scale_drawing_units <= 0:
                    raise ValueError("Fixed plot scale values must be positive")
                layout.UseStandardScale = False
                layout.SetCustomScale(scale_paper_units, scale_drawing_units)
                actual_scale = f"{scale_paper_units:g}:{scale_drawing_units:g}"
            else:
                raise ValueError("scale_mode must be fit or fixed")
            layout.CenterPlot = bool(center)

            selected_orientation = orientation.lower()
            if selected_orientation == "auto":
                ext_min = list(document.GetVariable("EXTMIN"))
                ext_max = list(document.GetVariable("EXTMAX"))
                selected_orientation = (
                    "landscape"
                    if abs(ext_max[0] - ext_min[0]) >= abs(ext_max[1] - ext_min[1])
                    else "portrait"
                )
            layout.PlotRotation = 1 if selected_orientation == "landscape" else 0
            if plot_style:
                try:
                    layout.StyleSheet = plot_style
                    layout.PlotWithPlotStyles = True
                except Exception:
                    pass
            plotted = bool(document.Plot.PlotToFile(str(output), "DWG To PDF.pc3"))
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if output.is_file() and output.stat().st_size > 0:
                    break
                time.sleep(0.1)
            if not output.is_file() or output.stat().st_size <= 0:
                raise RuntimeError(
                    f"AutoCAD PlotToFile returned {plotted} but no non-empty PDF was created"
                )
            return {
                "path": str(output),
                "format": "pdf",
                "renderer": "autocad-plot",
                "paper": paper,
                "media": str(media) if media else None,
                "paper_units": plot_paper_units,
                "orientation": selected_orientation,
                "plot_style": plot_style,
                "scale_mode": selected_scale_mode,
                "scale": actual_scale,
                "center": bool(center),
                "plot_type": "extents",
                "device": "DWG To PDF.pc3",
            }
        finally:
            for name, value in saved.items():
                try:
                    setattr(layout, name, value)
                except Exception:
                    pass
            if old_background_plot is not None:
                try:
                    document.SetVariable("BACKGROUNDPLOT", old_background_plot)
                except Exception:
                    pass

    # --- Undo / Redo ---

    async def undo(self) -> CommandResult:
        return await self._dispatch("undo", {})

    async def redo(self) -> CommandResult:
        return await self._dispatch("redo", {})

    # --- Freehand LISP execution ---

    async def execute_lisp(self, code: str) -> CommandResult:
        """Execute arbitrary AutoLISP code via temp file.

        File persists for session; cleaned up by _cleanup_stale_files().
        """
        request_id = uuid.uuid4().hex[:12]
        code_file = self._ipc_dir / f"autocad_mcp_lisp_{request_id}.lsp"
        code_file.write_text(code, encoding="utf-8")
        return await self._dispatch("execute-lisp", {
            "code_file": str(code_file).replace("\\", "/")
        })

    # --- Entity operations ---

    async def create_line(self, x1, y1, x2, y2, layer=None) -> CommandResult:
        return await self._dispatch("create-line", {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "layer": layer})

    async def create_circle(self, cx, cy, radius, layer=None) -> CommandResult:
        return await self._dispatch("create-circle", {"cx": cx, "cy": cy, "radius": radius, "layer": layer})

    async def create_polyline(self, points, closed=False, layer=None) -> CommandResult:
        pts_str = ";".join(f"{p[0]},{p[1]}" for p in points)
        return await self._dispatch("create-polyline", {
            "points_str": pts_str, "closed": "1" if closed else "0", "layer": layer
        })

    async def create_rectangle(self, x1, y1, x2, y2, layer=None) -> CommandResult:
        return await self._dispatch("create-rectangle", {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "layer": layer})

    async def create_arc(self, cx, cy, radius, start_angle, end_angle, layer=None) -> CommandResult:
        return await self._dispatch("create-arc", {"cx": cx, "cy": cy, "radius": radius, "start_angle": start_angle, "end_angle": end_angle, "layer": layer})

    async def create_ellipse(self, cx, cy, major_x, major_y, ratio, layer=None) -> CommandResult:
        return await self._dispatch("create-ellipse", {"cx": cx, "cy": cy, "major_x": major_x, "major_y": major_y, "ratio": ratio, "layer": layer})

    async def create_mtext(self, x, y, width, text, height=2.5, layer=None) -> CommandResult:
        return await self._dispatch("create-mtext", {"x": x, "y": y, "width": width, "text": encode_autocad_text(text), "height": height, "layer": layer})

    async def create_batch(
        self, entities: list[dict], continue_on_error: bool = False
    ) -> CommandResult:
        self._suspend_auto_fit += 1
        try:
            result = await super().create_batch(entities, continue_on_error)
        finally:
            self._suspend_auto_fit = max(0, self._suspend_auto_fit - 1)
        if result.ok:
            fit = self._auto_fit_view()
            if isinstance(result.payload, dict):
                result.payload["view"] = fit
        return result

    async def create_hatch(
        self, entity_id, pattern="ANSI31", angle=0.0, scale=1.0, layer=None
    ) -> CommandResult:
        return await self._dispatch(
            "create-hatch",
            {
                "entity_id": entity_id,
                "pattern": pattern,
                "angle": angle,
                "scale": scale,
                "layer": layer,
            },
        )

    async def entity_list(self, layer=None) -> CommandResult:
        return await self._dispatch("entity-list", {"layer": layer})

    async def entity_count(self, layer=None) -> CommandResult:
        return await self._dispatch("entity-count", {"layer": layer})

    async def entity_get(self, entity_id) -> CommandResult:
        return await self._dispatch("entity-get", {"entity_id": entity_id})

    async def entity_erase(self, entity_id) -> CommandResult:
        return await self._dispatch("entity-erase", {"entity_id": entity_id})

    async def entity_copy(self, entity_id, dx, dy) -> CommandResult:
        return await self._dispatch("entity-copy", {"entity_id": entity_id, "dx": dx, "dy": dy})

    async def entity_move(self, entity_id, dx, dy) -> CommandResult:
        return await self._dispatch("entity-move", {"entity_id": entity_id, "dx": dx, "dy": dy})

    async def entity_rotate(self, entity_id, cx, cy, angle) -> CommandResult:
        return await self._dispatch("entity-rotate", {"entity_id": entity_id, "cx": cx, "cy": cy, "angle": angle})

    async def entity_scale(self, entity_id, cx, cy, factor) -> CommandResult:
        return await self._dispatch("entity-scale", {"entity_id": entity_id, "cx": cx, "cy": cy, "factor": factor})

    async def entity_mirror(self, entity_id, x1, y1, x2, y2) -> CommandResult:
        return await self._dispatch("entity-mirror", {"entity_id": entity_id, "x1": x1, "y1": y1, "x2": x2, "y2": y2})

    async def entity_offset(self, entity_id, distance) -> CommandResult:
        return await self._dispatch("entity-offset", {"entity_id": entity_id, "distance": distance})

    async def entity_array(self, entity_id, rows, cols, row_dist, col_dist) -> CommandResult:
        return await self._dispatch("entity-array", {"entity_id": entity_id, "rows": rows, "cols": cols, "row_dist": row_dist, "col_dist": col_dist})

    async def entity_fillet(self, entity_id1, entity_id2, radius) -> CommandResult:
        return await self._dispatch("entity-fillet", {"id1": entity_id1, "id2": entity_id2, "radius": radius})

    async def entity_chamfer(self, entity_id1, entity_id2, dist1, dist2) -> CommandResult:
        return await self._dispatch("entity-chamfer", {"id1": entity_id1, "id2": entity_id2, "dist1": dist1, "dist2": dist2})

    # --- Layer operations ---

    async def layer_list(self) -> CommandResult:
        return await self._dispatch("layer-list", {})

    async def layer_create(
        self, name, color="white", linetype="CONTINUOUS", lineweight=None
    ) -> CommandResult:
        return await self._dispatch(
            "layer-create",
            {
                "name": name,
                "color": str(color),
                "linetype": linetype,
                "lineweight": str(lineweight) if lineweight is not None else None,
            },
        )

    async def layer_set_current(self, name) -> CommandResult:
        return await self._dispatch("layer-set-current", {"name": name})

    async def layer_set_properties(self, name, color=None, linetype=None, lineweight=None) -> CommandResult:
        return await self._dispatch(
            "layer-set-properties",
            {
                "name": name,
                "color": str(color) if color is not None else None,
                "linetype": linetype,
                "lineweight": str(lineweight) if lineweight is not None else None,
            },
        )

    async def layer_freeze(self, name) -> CommandResult:
        return await self._dispatch("layer-freeze", {"name": name})

    async def layer_thaw(self, name) -> CommandResult:
        return await self._dispatch("layer-thaw", {"name": name})

    async def layer_lock(self, name) -> CommandResult:
        return await self._dispatch("layer-lock", {"name": name})

    async def layer_unlock(self, name) -> CommandResult:
        return await self._dispatch("layer-unlock", {"name": name})

    # --- Block operations ---

    async def block_list(self) -> CommandResult:
        return await self._dispatch("block-list", {})

    async def block_insert(self, name, x, y, scale=1.0, rotation=0.0, block_id=None) -> CommandResult:
        return await self._dispatch("block-insert", {"name": name, "x": x, "y": y, "scale": scale, "rotation": rotation, "block_id": block_id})

    async def block_insert_with_attributes(self, name, x, y, scale=1.0, rotation=0.0, attributes=None) -> CommandResult:
        return await self._dispatch("block-insert-with-attributes", {"name": name, "x": x, "y": y, "scale": scale, "rotation": rotation, "attributes": attributes or {}})

    async def block_get_attributes(self, entity_id) -> CommandResult:
        return await self._dispatch("block-get-attributes", {"entity_id": entity_id})

    async def block_update_attribute(self, entity_id, tag, value) -> CommandResult:
        return await self._dispatch("block-update-attribute", {"entity_id": entity_id, "tag": tag, "value": value})

    async def block_define(self, name, entities) -> CommandResult:
        return await self._dispatch("block-define", {"name": name, "entities": entities})

    # --- Annotation ---

    async def create_text(self, x, y, text, height=2.5, rotation=0.0, layer=None) -> CommandResult:
        return await self._dispatch("create-text", {"x": x, "y": y, "text": encode_autocad_text(text), "height": height, "rotation": rotation, "layer": layer})

    async def create_dimension_linear(self, x1, y1, x2, y2, dim_x, dim_y) -> CommandResult:
        try:
            return CommandResult(
                ok=True,
                payload=self._create_dimension_via_com(
                    "linear", x1=x1, y1=y1, x2=x2, y2=y2, dim_x=dim_x, dim_y=dim_y
                ),
            )
        except Exception:
            return await self._dispatch("create-dimension-linear", {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "dim_x": dim_x, "dim_y": dim_y})

    async def create_dimension_aligned(self, x1, y1, x2, y2, offset) -> CommandResult:
        try:
            return CommandResult(
                ok=True,
                payload=self._create_dimension_via_com(
                    "aligned", x1=x1, y1=y1, x2=x2, y2=y2, offset=offset
                ),
            )
        except Exception:
            return await self._dispatch("create-dimension-aligned", {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "offset": offset})

    async def create_dimension_angular(self, cx, cy, x1, y1, x2, y2) -> CommandResult:
        try:
            return CommandResult(
                ok=True,
                payload=self._create_dimension_via_com(
                    "angular", cx=cx, cy=cy, x1=x1, y1=y1, x2=x2, y2=y2
                ),
            )
        except Exception:
            return await self._dispatch("create-dimension-angular", {"cx": cx, "cy": cy, "x1": x1, "y1": y1, "x2": x2, "y2": y2})

    async def create_dimension_radius(self, cx, cy, radius, angle) -> CommandResult:
        try:
            return CommandResult(
                ok=True,
                payload=self._create_dimension_via_com(
                    "radius", cx=cx, cy=cy, radius=radius, angle=angle
                ),
            )
        except Exception:
            return await self._dispatch("create-dimension-radius", {"cx": cx, "cy": cy, "radius": radius, "angle": angle})

    def _create_dimension_via_com(self, kind: str, **data) -> dict:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        application = win32com.client.GetActiveObject("AutoCAD.Application")
        modelspace = application.ActiveDocument.ModelSpace

        def point(x, y):
            return win32com.client.VARIANT(
                pythoncom.VT_ARRAY | pythoncom.VT_R8, [float(x), float(y), 0.0]
            )

        if kind in ("linear", "aligned"):
            x1, y1, x2, y2 = data["x1"], data["y1"], data["x2"], data["y2"]
            first, second = point(x1, y1), point(x2, y2)
            if kind == "linear":
                text_point = point(data["dim_x"], data["dim_y"])
                rotation = 0.0 if abs(x2 - x1) >= abs(y2 - y1) else math.pi / 2.0
                entity = modelspace.AddDimRotated(first, second, text_point, rotation)
            else:
                dx, dy = x2 - x1, y2 - y1
                length = math.hypot(dx, dy)
                if length == 0:
                    raise ValueError("Aligned dimension points must differ")
                offset = float(data["offset"])
                text_point = point(
                    (x1 + x2) / 2.0 - dy / length * offset,
                    (y1 + y2) / 2.0 + dx / length * offset,
                )
                entity = modelspace.AddDimAligned(first, second, text_point)
        elif kind == "radius":
            angle = math.radians(float(data["angle"]))
            center = point(data["cx"], data["cy"])
            chord = point(
                data["cx"] + data["radius"] * math.cos(angle),
                data["cy"] + data["radius"] * math.sin(angle),
            )
            entity = modelspace.AddDimRadial(center, chord, max(5.0, data["radius"] * 0.5))
        elif kind == "angular":
            cx, cy = data["cx"], data["cy"]
            first_angle = math.atan2(data["y1"] - cy, data["x1"] - cx)
            second_angle = math.atan2(data["y2"] - cy, data["x2"] - cx)
            delta = (second_angle - first_angle) % (2.0 * math.pi)
            mid_angle = first_angle + delta / 2.0
            radius = max(
                math.hypot(data["x1"] - cx, data["y1"] - cy),
                math.hypot(data["x2"] - cx, data["y2"] - cy),
            )
            entity = modelspace.AddDimAngular(
                point(cx, cy),
                point(data["x1"], data["y1"]),
                point(data["x2"], data["y2"]),
                point(cx + radius * 0.7 * math.cos(mid_angle), cy + radius * 0.7 * math.sin(mid_angle)),
            )
        else:
            raise ValueError(f"Unsupported COM dimension kind: {kind}")

        try:
            entity.Layer = "DIM"
        except Exception:
            pass
        entity.Update()
        self._auto_fit_view()
        return {
            "entity_type": "DIMENSION",
            "handle": str(entity.Handle),
            "renderer": "autocad-com",
        }

    async def create_leader(self, points, text) -> CommandResult:
        pts_str = ";".join(f"{p[0]},{p[1]}" for p in points)
        return await self._dispatch(
            "create-leader", {"points_str": pts_str, "text": encode_autocad_text(text)}
        )

    # --- P&ID ---

    async def pid_setup_layers(self) -> CommandResult:
        return await self._dispatch("pid-setup-layers", {})

    async def pid_insert_symbol(self, category, symbol, x, y, scale=1.0, rotation=0.0) -> CommandResult:
        return await self._dispatch("pid-insert-symbol", {"category": category, "symbol": symbol, "x": x, "y": y, "scale": scale, "rotation": rotation})

    async def pid_list_symbols(self, category) -> CommandResult:
        return await self._dispatch("pid-list-symbols", {"category": category})

    async def pid_draw_process_line(self, x1, y1, x2, y2) -> CommandResult:
        return await self._dispatch("pid-draw-process-line", {"x1": x1, "y1": y1, "x2": x2, "y2": y2})

    async def pid_connect_equipment(self, x1, y1, x2, y2) -> CommandResult:
        return await self._dispatch("pid-connect-equipment", {"x1": x1, "y1": y1, "x2": x2, "y2": y2})

    async def pid_add_flow_arrow(self, x, y, rotation=0.0) -> CommandResult:
        return await self._dispatch("pid-add-flow-arrow", {"x": x, "y": y, "rotation": rotation})

    async def pid_add_equipment_tag(self, x, y, tag, description="") -> CommandResult:
        return await self._dispatch("pid-add-equipment-tag", {"x": x, "y": y, "tag": tag, "description": description})

    async def pid_add_line_number(self, x, y, line_num, spec) -> CommandResult:
        return await self._dispatch("pid-add-line-number", {"x": x, "y": y, "line_num": line_num, "spec": spec})

    async def pid_insert_valve(self, x, y, valve_type, rotation=0.0, attributes=None) -> CommandResult:
        return await self._dispatch("pid-insert-valve", {"x": x, "y": y, "valve_type": valve_type, "rotation": rotation, "attributes": attributes or {}})

    async def pid_insert_instrument(self, x, y, instrument_type, rotation=0.0, tag_id="", range_value="") -> CommandResult:
        return await self._dispatch("pid-insert-instrument", {"x": x, "y": y, "instrument_type": instrument_type, "rotation": rotation, "tag_id": tag_id, "range_value": range_value})

    async def pid_insert_pump(self, x, y, pump_type, rotation=0.0, attributes=None) -> CommandResult:
        return await self._dispatch("pid-insert-pump", {"x": x, "y": y, "pump_type": pump_type, "rotation": rotation, "attributes": attributes or {}})

    async def pid_insert_tank(self, x, y, tank_type, scale=1.0, attributes=None) -> CommandResult:
        return await self._dispatch("pid-insert-tank", {"x": x, "y": y, "tank_type": tank_type, "scale": scale, "attributes": attributes or {}})

    # --- View ---

    async def zoom_extents(self) -> CommandResult:
        fit = self._auto_fit_view(force=True)
        if fit.get("fitted"):
            return CommandResult(ok=True, payload=fit)
        return await self._dispatch("zoom-extents", {})

    async def zoom_window(self, x1, y1, x2, y2) -> CommandResult:
        return await self._dispatch("zoom-window", {"x1": x1, "y1": y1, "x2": x2, "y2": y2})

    async def show_window(self, activate: bool = True) -> CommandResult:
        return CommandResult(ok=True, payload=self._ensure_autocad_visible(activate=activate))

    async def get_screenshot(self) -> CommandResult:
        if self._screenshot_provider:
            data = self._screenshot_provider.capture()
            if data:
                return CommandResult(ok=True, payload=data)
        return CommandResult(ok=False, error="Screenshot capture failed")
