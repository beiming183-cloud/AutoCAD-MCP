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
import hashlib
import json
import math
import os
import re
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path

import structlog

from autocad_mcp import __version__
from autocad_mcp.audit import INSUNITS_NAMES, audit_dxf_file, build_audit, geometry_digest
from autocad_mcp.backends.base import AutoCADBackend, BackendCapabilities, CommandResult
from autocad_mcp.com_sta import ComStaExecutor, com_hresult, sta_async_method, sta_sync_method
from autocad_mcp.config import (
    AutoCADStartupError,
    DOCUMENT_TIMEOUT,
    IPC_DIR,
    IPC_TIMEOUT,
    LISP_DIR,
    _autostart_autocad,
    last_autostart_record,
)
from autocad_mcp.drafting import encode_autocad_text
from autocad_mcp.errors import LayerNotFoundError, exception_context
from autocad_mcp.product_design import feature_bounds, normalize_feature
from autocad_mcp.runtime_health import (
    RuntimeHealthError,
    activity_insights_write_preflight,
    autocad_cer_snapshot,
    list_autocad_processes,
    win32_runtime_health,
)
from autocad_mcp.session import (
    DocumentState,
    ProcessState,
    SessionRegistry,
    TransportState,
    UiState,
)
from autocad_mcp.native_pipe import NativePipeClient, NativePipeError, camel_case_keys
from autocad_mcp.variables import mechanical_variable_updates, validate_variable_updates

log = structlog.get_logger()

# IPC settings
POLL_INTERVAL = 0.1  # seconds
TIMEOUT = IPC_TIMEOUT  # seconds (configurable via AUTOCAD_MCP_IPC_TIMEOUT)
STALE_THRESHOLD = 60.0  # clean up files older than this
_LAST_WINDOW_DISCOVERY: dict = {"candidates": [], "fatal_windows": [], "selection": None}


def window_discovery_status() -> dict:
    return {
        "candidates": [dict(item) for item in _LAST_WINDOW_DISCOVERY["candidates"]],
        "fatal_windows": [dict(item) for item in _LAST_WINDOW_DISCOVERY.get("fatal_windows", [])],
        "selection": _LAST_WINDOW_DISCOVERY.get("selection"),
    }


def find_autocad_window(preferred_process_id: int | None = None) -> int | None:
    """Find the AutoCAD main window, including hidden automation sessions."""
    if sys.platform != "win32":
        return None
    if preferred_process_id is None:
        configured_pid = os.environ.get("AUTOCAD_MCP_ACAD_PID", "").strip()
        if configured_pid:
            try:
                preferred_process_id = int(configured_pid)
            except ValueError:
                _LAST_WINDOW_DISCOVERY["candidates"] = []
                _LAST_WINDOW_DISCOVERY["selection"] = "invalid-preferred-process"
                return None
    try:
        import win32api
        import win32con
        import win32gui
        import win32process

        windows: list[dict] = []
        fatal_windows: list[dict] = []
        fatal_tokens = (
            "fatal error",
            "error abort",
            "unhandled exception",
            "致命错误",
            "错误中断",
            "无法继续",
            "\u9519\u8bef\u4e2d\u65ad",
        )

        def callback(hwnd, result):
            if win32gui.IsWindowVisible(hwnd):
                is_autocad = False
                process_handle = None
                process_id = None
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
                    is_autocad = "autocad" in title or "autodesk" in title
                finally:
                    if process_handle is not None:
                        try:
                            win32api.CloseHandle(process_handle)
                        except Exception:
                            pass

                if is_autocad:
                    title = str(win32gui.GetWindowText(hwnd) or "")
                    item = {
                        "hwnd": int(hwnd),
                        "process_id": int(process_id) if process_id is not None else None,
                        "visible": True,
                        "minimized": bool(win32gui.IsIconic(hwnd)),
                        "title": title,
                    }
                    if any(token in title.casefold() for token in fatal_tokens):
                        fatal_windows.append(item)
                    else:
                        result.append(item)
            return True

        win32gui.EnumWindows(callback, windows)
        _LAST_WINDOW_DISCOVERY["candidates"] = windows
        _LAST_WINDOW_DISCOVERY["fatal_windows"] = fatal_windows
        if preferred_process_id is not None:
            preferred = next(
                (
                    item
                    for item in windows
                    if item["process_id"] == int(preferred_process_id)
                ),
                None,
            )
            if preferred:
                _LAST_WINDOW_DISCOVERY["selection"] = "preferred-process"
                return preferred["hwnd"]
        if len(windows) == 1 and preferred_process_id is None:
            _LAST_WINDOW_DISCOVERY["selection"] = "single-visible-window"
            return windows[0]["hwnd"]

        executor = ComStaExecutor(name="autocad-mcp-window-discovery")
        try:
            def discover_from_com():
                import win32com.client

                application = win32com.client.GetActiveObject("AutoCAD.Application")
                hwnd = int(application.HWND)
                return hwnd if hwnd else None

            com_hwnd = executor.call(
                "window.discovery",
                discover_from_com,
                idempotent=True,
                timeout=5.0,
            )
            # COM's HWND is the authoritative fallback for a hidden startup
            # window.  It may not be present in EnumWindows until the UI has
            # finished creating its visible frame.
            if com_hwnd:
                com_hwnd = int(com_hwnd)
                com_pid = _window_process_id(com_hwnd)
                if preferred_process_id is not None and com_pid not in (
                    None,
                    int(preferred_process_id),
                ):
                    _LAST_WINDOW_DISCOVERY["selection"] = "com-pid-mismatch"
                    return None
                com_executable = _process_executable_name(com_pid)
                if com_executable not in (None, "acad.exe"):
                    _LAST_WINDOW_DISCOVERY["selection"] = "com-non-autocad-process"
                    return None
                # COM remains authoritative even when the frame is hidden or
                # has not entered EnumWindows yet.  A visible candidate from
                # another AutoCAD process must not suppress this verified
                # active object.
                _LAST_WINDOW_DISCOVERY["selection"] = (
                    "com-hwnd-visible"
                    if any(item["hwnd"] == com_hwnd for item in windows)
                    else "com-hwnd-hidden"
                )
                return com_hwnd
            _LAST_WINDOW_DISCOVERY["selection"] = "ambiguous" if windows else "not-found"
            return None
        except Exception:
            _LAST_WINDOW_DISCOVERY["selection"] = (
                "ambiguous" if len(windows) > 1 else "not-found"
            )
            return None
        finally:
            executor.close()
    except ImportError:
        return None


def _window_process_id(hwnd: int | None) -> int | None:
    if sys.platform != "win32" or not hwnd:
        return None
    try:
        import win32process

        return int(win32process.GetWindowThreadProcessId(hwnd)[1])
    except Exception:
        return None


def _process_executable_name(process_id: int | None) -> str | None:
    if sys.platform != "win32" or not process_id:
        return None
    process_handle = None
    try:
        import win32api
        import win32con
        import win32process

        process_handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
            False,
            int(process_id),
        )
        return Path(win32process.GetModuleFileNameEx(process_handle, 0)).name.casefold()
    except Exception:
        return None
    finally:
        if process_handle is not None:
            try:
                win32api.CloseHandle(process_handle)
            except Exception:
                pass


def _process_is_alive(process_id: int | None) -> bool | None:
    if sys.platform != "win32" or not process_id:
        return None
    try:
        import ctypes

        process = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(process_id))
        if not process:
            return None
        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(process, ctypes.byref(exit_code)):
                return None
            return exit_code.value == 259  # STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(process)
    except Exception:
        return None


def detect_autocad_crash_state(
    hwnd: int | None = None, process_id: int | None = None
) -> dict:
    """Detect process exit and visible AutoCAD fatal-error dialogs."""
    if sys.platform != "win32":
        return {"crashed": False}
    pid = process_id or _window_process_id(hwnd)
    if process_id and _process_is_alive(process_id) is False:
        result = {
            "crashed": True,
            "reason": "process_exited",
            "process_id": int(process_id),
        }
        result["cer"] = autocad_cer_snapshot()
        return result

    try:
        import win32gui

        fatal_tokens = (
            "fatal error",
            "error aborting",
            "unhandled exception",
            "致命错误",
            "无法继续",
        )
        fatal_tokens = fatal_tokens + (
            "\u81f4\u547d\u9519\u8bef",
            "\u9519\u8bef\u4e2d\u65ad",
            "\u65e0\u6cd5\u7ee7\u7eed",
        )
        product_tokens = ("autocad", "autodesk")
        dialogs: list[dict] = []

        def callback(window, _):
            if not win32gui.IsWindowVisible(window):
                return True
            window_pid = _window_process_id(window)
            title = win32gui.GetWindowText(window) or ""
            child_text: list[str] = []

            def child_callback(child, __):
                text = win32gui.GetWindowText(child)
                if text:
                    child_text.append(text)
                return True

            try:
                win32gui.EnumChildWindows(window, child_callback, None)
            except Exception:
                pass
            combined = " ".join([title, *child_text]).lower()
            same_process = bool(pid and window_pid == pid)
            identified_product = any(token in combined for token in product_tokens)
            fatal = any(token in combined for token in fatal_tokens)
            # Once a managed PID is known, only a dialog owned by that exact
            # process is evidence of its crash.  A stale CER/WebView report
            # often contains the word AutoCAD in its title but belongs to a
            # different process and must not poison startup detection.
            executable_name = _process_executable_name(window_pid)
            owned_by_autocad = executable_name == "acad.exe"
            relevant = same_process if pid else (identified_product and owned_by_autocad)
            if fatal and relevant:
                dialogs.append(
                    {
                        "hwnd": int(window),
                        "title": title,
                        "process_id": window_pid,
                        "message": " | ".join(child_text[:8]),
                    }
                )
            return True

        win32gui.EnumWindows(callback, None)
        if dialogs:
            result = {
                "crashed": True,
                "reason": "fatal_error_dialog",
                "process_id": pid,
                "dialog": dialogs[0],
            }
            result["cer"] = autocad_cer_snapshot()
            return result
    except Exception:
        pass
    return {"crashed": False, "process_id": pid}


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


def _distance_2d(first, second) -> float:
    return math.hypot(float(second[0]) - float(first[0]), float(second[1]) - float(first[1]))


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
        "object_name": object_name,
        "object_id": _com_value(entity, "ObjectID"),
        "owner_id": _com_value(entity, "OwnerID"),
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
                start=_com_point(_com_value(entity, "StartPoint")),
                end=_com_point(_com_value(entity, "EndPoint")),
            )
    elif entity_type in ("LWPOLYLINE", "POLYLINE", "POLYLINE3D"):
        coordinates = list(_com_value(entity, "Coordinates", []) or [])
        stride = 2 if entity_type == "LWPOLYLINE" else 3
        result["points"] = [
            [round(float(value), 6) for value in coordinates[index : index + stride]]
            for index in range(0, len(coordinates), stride)
        ]
        result["closed"] = bool(_com_value(entity, "Closed", False))
        if entity_type == "LWPOLYLINE":
            try:
                result["bulges"] = [
                    round(float(entity.GetBulge(index)), 9)
                    for index in range(len(result["points"]))
                ]
            except Exception:
                pass
    elif entity_type in ("TEXT", "MTEXT"):
        result.update(
            insert=_com_point(
                _com_value(entity, "InsertionPoint", _com_value(entity, "TextAlignmentPoint"))
            ),
            text=str(_com_value(entity, "TextString", "")),
            height=_com_value(entity, "Height", _com_value(entity, "TextHeight")),
            rotation=math.degrees(float(_com_value(entity, "Rotation", 0))),
        )
        if entity_type == "MTEXT":
            result.update(
                width=_com_value(entity, "Width"),
                attachment_point=_com_value(entity, "AttachmentPoint"),
            )
    elif entity_type == "INSERT":
        result.update(
            name=str(_com_value(entity, "EffectiveName", _com_value(entity, "Name", ""))),
            insert=_com_point(_com_value(entity, "InsertionPoint")),
            rotation=math.degrees(float(_com_value(entity, "Rotation", 0))),
            xscale=_com_value(entity, "XScaleFactor", 1),
            yscale=_com_value(entity, "YScaleFactor", 1),
        )
        try:
            result["attributes"] = [
                {
                    "tag": str(_com_value(attribute, "TagString", "")),
                    "text": str(_com_value(attribute, "TextString", "")),
                    "handle": str(_com_value(attribute, "Handle", "")),
                }
                for attribute in list(entity.GetAttributes())
            ]
        except Exception:
            result["attributes"] = []
    elif entity_type == "DIMENSION":
        result.update(
            measurement=_com_value(entity, "Measurement"),
            text=str(_com_value(entity, "TextOverride", "")),
            text_position=_com_point(_com_value(entity, "TextPosition")),
        )
    elif entity_type == "HATCH":
        raw_pattern_angle = _com_value(entity, "PatternAngle", None)
        try:
            pattern_angle = math.degrees(float(raw_pattern_angle)) if raw_pattern_angle is not None else None
        except (TypeError, ValueError):
            pattern_angle = raw_pattern_angle
        result.update(
            pattern=str(_com_value(entity, "PatternName", "")),
            angle=pattern_angle,
            scale=_com_value(entity, "PatternScale", 1.0),
            area=_com_value(entity, "Area"),
        )

    for property_name, key in (("Length", "length"), ("Area", "area")):
        value = _com_value(entity, property_name)
        if value is not None:
            result[key] = value

    try:
        minimum, maximum = entity.GetBoundingBox()
        result["bounds"] = {"min": _com_point(minimum), "max": _com_point(maximum)}
    except Exception:
        pass
    return {key: value for key, value in result.items() if value is not None}


class FileIPCBackend(AutoCADBackend):
    """File IPC for full AutoCAD and AutoCAD LT via mcp_dispatch.lsp."""

    def __init__(self, *, com_executor: ComStaExecutor | None = None):
        self._com_executor = com_executor or ComStaExecutor()
        self._session = SessionRegistry()
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
        self._window_policy_applied = False
        # A user-owned CAD window is never minimized or activated implicitly.
        # This flips to True only when this backend launched the process.
        self._window_policy_owned = False
        self._acad_process_id: int | None = None
        self._doc_ids_by_key = self._session.doc_ids_by_key
        self._doc_revisions = self._session.doc_revisions
        self._transactions: dict[str, dict] = {}
        self._deferred_output_cleanup: set[Path] = set()
        self._user_foreground_hwnd: int | None = None
        self._native_client: NativePipeClient | None = None
        self._native_initialization_error: dict | None = None

    async def shutdown(self) -> None:
        """Release MCP-owned transports without closing the AutoCAD session.

        A forced MCP re-initialization must not leave the old COM STA alive.
        Dropping the native client is safe because it opens one named-pipe
        connection per request; it does not send a document-close command.
        """
        executor = self._com_executor
        try:
            executor.close()
        except Exception:
            log.warning("backend_shutdown_executor_failed", exc_info=True)
        finally:
            # Clear identity only after the STA has been asked to stop, so a
            # late callback never observes a half-reset PID/HWND pair.
            self._native_client = None
            self._command_hwnd = None
            self._hwnd = None
            self._acad_process_id = None

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
            can_create_solids=True,
            can_boolean_solids=True,
            can_project_views=False,
            can_product_features=True,
            can_fixed_camera_views=True,
        )

    @staticmethod
    def _industrial_capability_matrix() -> dict:
        """Report deterministic industrial features without advertising placeholders."""
        return {
            "supported": [
                "document_identity",
                "optimistic_revision_guard",
                "undo_transactions",
                "atomic_entity_batches",
                "layer_preconditions",
                "entity_postcondition_readback",
                "native_2d_entities",
                "basic_native_solids",
                "solid_boolean",
                "analytic_rounded_box",
                "controlled_module_reservations",
                "rotary_product_placeholders",
                "fixed_camera_native_plot_views",
                "product_review_contracts",
                "broad_phase_motion_interference_screening",
                "offline_dxf_audit",
                "native_pdf_plot",
                "native_png_plot",
                "controlled_visual_style_readback",
                "read_only_dwg_copy",
            ],
            "unsupported": {
                "stable_feature_selection": "AutoCAD COM does not expose a proven persistent semantic edge/face identifier here",
                "fillet_edges": "general native edge edits return E_STABLE_FEATURE_SELECTION_UNAVAILABLE; analytic rounded_box is supported",
                "chamfer_edges": "general native edge edits return E_STABLE_FEATURE_SELECTION_UNAVAILABLE",
                "shell": "not implemented by the current safe backend",
                "offset_face": "not implemented by the current safe backend",
                "loft": "not implemented by the current safe backend",
                "draft": "not implemented by the current safe backend",
                "parametric_assembly": "component identity, mates, configurations, and DOF solving are not implemented",
                "exact_motion_sweep_interference": "only broad-phase AABB screening is available; exact B-rep sweep remains unavailable",
                "offscreen_3d_render": "material/offscreen rendering still requires the native PlotEngine plugin; controlled shaded visual-style plots are supported",
                "surface_continuity_g0_g1_g2": "not implemented",
                "wall_thickness": "not implemented",
                "draft_angle_analysis": "not implemented",
            },
        }

    @staticmethod
    def _native_plugin_mode() -> str:
        mode = os.environ.get("AUTOCAD_MCP_NATIVE_PLUGIN", "auto").strip().lower()
        return mode if mode in {"auto", "required", "off"} else "auto"

    def _get_autocad_application(self):
        """Bind COM to the same AutoCAD process selected during preflight.

        ``GetActiveObject`` is global on Windows and can silently return a
        different AutoCAD instance when more than one is open.  Once this
        backend has a PID/HWND, refuse an unverified COM identity instead of
        risking a write to the wrong document.
        """
        import win32com.client

        application = win32com.client.GetActiveObject("AutoCAD.Application")
        expected_pid = int(self._acad_process_id or 0)
        expected_hwnd = int(self._hwnd or 0)
        actual_hwnd = int(getattr(application, "HWND", 0) or 0)
        if expected_pid:
            if not actual_hwnd:
                raise RuntimeError(
                    "E_AUTOCAD_INSTANCE_UNVERIFIED: COM application has no HWND "
                    f"for expected PID {expected_pid}"
                )
            actual_pid = _window_process_id(actual_hwnd)
            if actual_pid != expected_pid:
                raise RuntimeError(
                    "E_AUTOCAD_INSTANCE_MISMATCH: COM HWND/PID does not match "
                    f"selected AutoCAD PID {expected_pid} (actual PID {actual_pid})"
                )
        elif expected_hwnd and actual_hwnd and actual_hwnd != expected_hwnd:
            raise RuntimeError(
                "E_AUTOCAD_INSTANCE_MISMATCH: COM HWND does not match the "
                f"selected AutoCAD HWND {expected_hwnd} (actual HWND {actual_hwnd})"
            )
        return application

    def _native_window_policy(self, *, activate: bool = False) -> dict:
        """Apply the UI policy through user32 without requiring COM/pywin32."""
        configured = os.environ.get("AUTOCAD_MCP_VISIBLE", "true").lower() in (
            "1", "true", "yes", "on"
        )
        mode, requested_mode = self._configured_window_mode()
        if activate:
            mode = "foreground"
        if mode == "preserve":
            return {
                "configured_visible": configured,
                "shown": bool(self._hwnd),
                "window_mode": "preserve",
                "requested_window_mode": requested_mode,
                "activated": False,
                "preserved_user_window": True,
                "transport": "none",
                "hwnd": self._hwnd,
            }
        result = {
            "configured_visible": configured,
            "shown": False,
            "window_mode": mode,
            "requested_window_mode": requested_mode,
            "activated": False,
            "transport": "user32",
            "hwnd": self._hwnd,
        }
        if not configured or sys.platform != "win32" or not self._hwnd:
            return result
        try:
            import ctypes

            user32 = ctypes.windll.user32
            hwnd = int(self._hwnd)
            if not user32.IsWindow(hwnd):
                result["reason"] = "invalid-window-handle"
                return result
            foreground_before = int(user32.GetForegroundWindow() or 0)
            if mode == "foreground":
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
                result["activated"] = True
            elif mode == "visible":
                user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
            elif not self._window_policy_applied:
                user32.ShowWindow(hwnd, 7)  # SW_SHOWMINNOACTIVE
            self._window_policy_applied = True
            result.update(
                shown=bool(user32.IsWindowVisible(hwnd)),
                minimized=bool(user32.IsIconic(hwnd)),
                foreground_before=foreground_before or None,
                foreground_after=int(user32.GetForegroundWindow() or 0) or None,
            )
            return result
        except Exception as exc:
            result.update(reason="window-policy-failed", exception_type=type(exc).__name__)
            return result

    async def _initialize_native_transport(self) -> CommandResult | None:
        mode = self._native_plugin_mode()
        if mode == "off":
            return None
        try:
            client = NativePipeClient.discover(timeout=IPC_TIMEOUT)
        except NativePipeError as exc:
            return CommandResult(
                ok=False,
                error=str(exc),
                error_code=exc.error_code,
                recoverable=exc.recoverable,
                recommended_action=exc.recommended_action,
                payload=exc.details,
            )
        if client is None:
            if mode == "required":
                return CommandResult(
                    ok=False,
                    error="No live AutoCAD native worker descriptor was found",
                    error_code="E_NATIVE_PLUGIN_UNAVAILABLE",
                    recoverable=True,
                    recommended_action="install_and_load_the_signed_autocad_mcp_native_bundle",
                    payload={"mode": mode},
                )
            return None

        ping = await client.ping()
        if not ping.ok:
            return ping
        payload = ping.payload if isinstance(ping.payload, dict) else {}
        if int(payload.get("protocol_version", -1)) != client.descriptor.protocol_version:
            return CommandResult(
                ok=False,
                error="Native worker ping does not match its published descriptor",
                error_code="E_NATIVE_PROTOCOL_MISMATCH",
                recoverable=False,
                payload={"descriptor": client.descriptor.to_dict(), "ping": payload},
            )
        if str(payload.get("session_id")) != client.descriptor.session_id:
            return CommandResult(
                ok=False,
                error="Native worker session changed during connection",
                error_code="E_SESSION_GENERATION_MISMATCH",
                recoverable=True,
                recommended_action="rediscover_the_native_worker_and_read_document_context",
                payload={"descriptor": client.descriptor.to_dict(), "ping": payload},
            )

        self._native_client = client
        self._acad_process_id = client.descriptor.process_id
        self._hwnd = client.descriptor.hwnd
        worker = self._session.bind_worker(
            process_id=self._acad_process_id,
            hwnd=self._hwnd,
            owned=False,
            session_id=client.descriptor.session_id,
            generation=self._acad_process_id,
        )
        context = await client.request("document.context")
        created_first_document = False
        if not context.ok and context.error_code == "E_NO_ACTIVE_DOCUMENT":
            context = await client.request(
                "document.create",
                data={"template": os.environ.get("AUTOCAD_MCP_TEMPLATE", "acadiso.dwt")},
                idempotency_key=f"bootstrap-document:{client.descriptor.session_id}",
            )
            created_first_document = context.ok
        if not context.ok:
            self._native_client = None
            return context

        self._session.process_state = ProcessState.READY
        self._session.transport_state = TransportState.PLUGIN_READY
        self._session.document_state = DocumentState.READY
        self._session.ui_state = UiState.IDLE
        self._product_info = {
            "product": "AutoCAD",
            "native_plugin_version": payload.get("plugin_version"),
            "process_id": self._acad_process_id,
        }
        visibility = self._native_window_policy()
        return CommandResult(
            ok=True,
            payload={
                "autocad": {
                    **self._product_info,
                    "running": True,
                    "hwnd": self._hwnd,
                    "active_document": context.payload.get("document_name"),
                    "active_document_path": context.payload.get("active_path"),
                    "created_first_document": created_first_document,
                },
                "native_worker": {
                    **client.descriptor.to_dict(),
                    "ping": payload,
                    "context": context.payload,
                },
                "transport": "native_pipe",
                "dispatcher_isolation": "native-plugin-with-external-supervisor",
                "worker": {
                    "session_id": worker.session_id,
                    "generation": worker.generation,
                    "process_id": worker.process_id,
                    "hwnd": worker.hwnd,
                    "owned": worker.owned,
                },
                "ready": True,
                "visibility": visibility,
            },
        )

    async def initialize(self) -> CommandResult:
        """Make AutoCAD and its versioned dispatcher ready for commands."""
        return await self.ensure_ready()

    async def ensure_ready(self) -> CommandResult:
        """Discover, start, connect, load, handshake, and ping AutoCAD."""
        self._session.process_state = ProcessState.STARTING
        # Re-initialization must not inherit ownership of a prior CAD process.
        # Native/user-owned sessions are read-only unless this call launches
        # the process itself.
        self._window_policy_owned = False
        native = await self._initialize_native_transport()
        if native is not None:
            if native.ok or self._native_plugin_mode() == "required":
                return native
            self._native_initialization_error = native.to_dict()
        runtime = win32_runtime_health()
        if sys.platform == "win32" and not runtime["ok"]:
            return CommandResult(
                ok=False,
                error="The pywin32 runtime required for AutoCAD COM is unhealthy",
                error_code="E_PYWIN32_BROKEN",
                recoverable=False,
                recommended_action="repair_pywin32_in_the_same_python_used_by_the_mcp",
                payload={"runtime": runtime},
            )

        processes = list_autocad_processes()
        started_by_backend = False
        self._hwnd = find_autocad_window()
        if not self._hwnd:
            discovery = window_discovery_status()
            if discovery.get("selection") == "ambiguous":
                return CommandResult(
                    ok=False,
                    error="Multiple AutoCAD windows are available and no owned instance could be selected",
                    error_code="E_AUTOCAD_INSTANCE_AMBIGUOUS",
                    recoverable=True,
                    recommended_action="set_AUTOCAD_MCP_ACAD_PID_or_close_unrelated_instances",
                    payload=discovery,
                )
            if processes:
                return CommandResult(
                    ok=False,
                    error="acad.exe is alive but exposes no usable main window",
                    error_code="E_AUTOCAD_GHOST_PROCESS",
                    recoverable=True,
                    recommended_action="terminate_or_close_orphaned_acad_process_then_start_autocad_manually",
                    payload={"autocad_processes": processes},
                )
            prior_crash = detect_autocad_crash_state(None, self._acad_process_id)
            if prior_crash.get("crashed"):
                return self._autocad_crashed_result(prior_crash, "system.ensure_ready")
            try:
                self._hwnd = _autostart_autocad(
                    find_autocad_window,
                    crash_probe=detect_autocad_crash_state,
                )
                started_by_backend = bool(self._hwnd)
                self._window_policy_owned = started_by_backend
            except AutoCADStartupError as exc:
                return CommandResult(
                    ok=False,
                    error=str(exc),
                    error_code=exc.error_code,
                    recoverable=exc.recoverable,
                    recommended_action=exc.recommended_action,
                    payload=exc.details,
                )
            except RuntimeHealthError as exc:
                return CommandResult(
                    ok=False,
                    error=str(exc),
                    error_code=exc.error_code,
                    recoverable=False,
                    recommended_action=exc.recommended_action,
                    payload=exc.details,
                )
            except Exception as exc:
                crash = detect_autocad_crash_state(None, self._acad_process_id)
                if crash.get("crashed"):
                    return self._autocad_crashed_result(crash, "system.ensure_ready")
                return CommandResult(ok=False, error=str(exc))
        if not self._hwnd:
            crash = detect_autocad_crash_state(None, self._acad_process_id)
            if crash.get("crashed"):
                return self._autocad_crashed_result(crash, "system.ensure_ready")
            return CommandResult(
                ok=False,
                error="AutoCAD is not running and automatic startup is unavailable",
                error_code="E_AUTOCAD_NOT_RUNNING",
            )
        self._acad_process_id = _window_process_id(self._hwnd)
        launch = last_autostart_record() if started_by_backend else None
        owned = bool(
            launch
            and launch.get("launcher_pid") is not None
            and int(launch["launcher_pid"]) == int(self._acad_process_id or -1)
            and int(launch.get("hwnd") or 0) == int(self._hwnd or 0)
        )
        worker = self._session.bind_worker(
            process_id=self._acad_process_id,
            hwnd=self._hwnd,
            owned=owned,
            launch_token=launch.get("launch_token") if owned and launch else None,
        )
        crash = detect_autocad_crash_state(self._hwnd, self._acad_process_id)
        if crash.get("crashed"):
            return self._autocad_crashed_result(crash, "system.ensure_ready")

        if self._window_policy_owned or os.environ.get(
            "AUTOCAD_MCP_APPLY_WINDOW_POLICY_TO_EXISTING", "false"
        ).strip().lower() in ("1", "true", "yes", "on"):
            visibility = self._ensure_autocad_visible()
        else:
            visibility = {
                "configured_visible": True,
                "shown": bool(self._hwnd),
                "preserved_user_window": True,
                "hwnd": self._hwnd,
            }
        log.info("autocad_visibility", **visibility)

        document = self._ensure_active_document()
        if not document["ready"]:
            return CommandResult(
                ok=False,
                error=document["error"],
                error_code=document.get("error_code", "E_NO_ACTIVE_DOCUMENT"),
                payload=document.get("details"),
            )
        # Attaching to a CAD instance opened by the user must be read-only.
        # Startup policy is allowed to mutate only an instance launched by
        # this backend, and only when the caller explicitly opts in.
        activity_policy = self._apply_activity_insights_policy(
            allow_mutation=started_by_backend
        )
        self._product_info = self._discover_product()
        self._session.transport_state = TransportState.COM_READY

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
        load_attempts = 0
        self._dispatcher_version = None
        for candidate in candidates:
            if not candidate.is_file():
                continue
            normalized_path = str(candidate.resolve()).replace("\\", "/")
            for attempt in range(1, 4):
                load_attempts += 1
                self._type_command(f'(load "{normalized_path}")')
                await asyncio.sleep(0.75 * attempt)
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
            if self._dispatcher_version == __version__:
                break

        if not last_result or not last_result.ok:
            return CommandResult(
                ok=False,
                error="AutoCAD is running but the MCP dispatcher could not be loaded or pinged",
                error_code="E_DISPATCHER_NOT_LOADED",
                payload={
                    "load_attempts": load_attempts,
                    "last_result": last_result.to_dict() if last_result else None,
                },
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

        self._session.transport_state = TransportState.COM_READY
        self._session.document_state = DocumentState.READY
        self._session.ui_state = UiState.IDLE

        return CommandResult(
            ok=True,
            payload={
                "autocad": {
                    **self._product_info,
                    "running": True,
                    "hwnd": self._hwnd,
                    "active_document": document["name"],
                    "active_document_path": document.get("path"),
                    "created_first_document": document.get("created_first_document", False),
                    "activity_insights": activity_policy,
                },
                "dispatcher": {
                    "loaded": True,
                    "version": self._dispatcher_version,
                    "path": loaded_path,
                    "load_attempts": load_attempts,
                },
                "transport": "file_ipc",
                "dispatcher_isolation": "external-python-process",
                "native_initialization_error": self._native_initialization_error,
                "worker": {
                    "session_id": worker.session_id,
                    "generation": worker.generation,
                    "process_id": worker.process_id,
                    "hwnd": worker.hwnd,
                    "owned": worker.owned,
                },
                "com_sta": self._com_executor.snapshot(),
                "ready": True,
                "visibility": visibility,
            },
        )

    @sta_sync_method("activity_insights.apply", idempotent=True)
    def _apply_activity_insights_policy(self, *, allow_mutation: bool = False) -> dict:
        """Report Activity Insights policy without mutating user-owned CAD.

        Environment variables describe a desired startup policy, but they are
        not permission to rewrite an already-open document.  Mutation requires
        both a backend-owned launch and ``AUTOCAD_MCP_APPLY_ACTIVITY_POLICY``.
        """
        disable = os.environ.get("AUTOCAD_MCP_DISABLE_ACTIVITY_INSIGHTS", "").strip().lower() in (
            "1", "true", "yes", "on"
        )
        configured_path = os.environ.get("AUTOCAD_MCP_ACTIVITY_INSIGHTS_PATH", "").strip()
        if not disable and not configured_path:
            return {"configured": False}
        result = {
            "configured": True,
            "disable_requested": disable,
            "path_requested": configured_path or None,
            "restart_required": True,
            "mutation_allowed": bool(allow_mutation),
            "applied": {},
            "errors": [],
        }
        apply_requested = os.environ.get(
            "AUTOCAD_MCP_APPLY_ACTIVITY_POLICY", "false"
        ).strip().lower() in ("1", "true", "yes", "on")
        result["apply_requested"] = apply_requested
        if not allow_mutation or not apply_requested:
            result["deferred"] = True
            result["reason"] = (
                "existing_user_owned_instance"
                if not allow_mutation
                else "explicit_opt_in_required"
            )
            return result
        try:
            import pythoncom
            import win32com.client

            document = self._get_autocad_application().ActiveDocument
            if disable:
                document.SetVariable("ACTIVITYINSIGHTSSUPPORT", 0)
                result["applied"]["ACTIVITYINSIGHTSSUPPORT"] = document.GetVariable(
                    "ACTIVITYINSIGHTSSUPPORT"
                )
            if configured_path:
                document.SetVariable("ACTIVITYINSIGHTSPATH", configured_path)
                result["applied"]["ACTIVITYINSIGHTSPATH"] = str(
                    document.GetVariable("ACTIVITYINSIGHTSPATH")
                )
        except Exception as exc:
            result["errors"].append(
                {
                    "exception_type": type(exc).__name__,
                    "message": str(exc) or type(exc).__name__,
                }
            )
        return result

    async def status(self) -> CommandResult:
        native_health = await self._native_client.ping() if self._native_client else None
        crash = detect_autocad_crash_state(self._hwnd, self._acad_process_id)
        if crash.get("crashed"):
            return self._autocad_crashed_result(crash, "system.status")
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
            "native_worker": (
                {
                    **self._native_client.descriptor.to_dict(),
                    "health": native_health.to_dict(),
                }
                if self._native_client and native_health
                else None
            ),
            "native_initialization_error": self._native_initialization_error,
            "transport": "native_pipe" if self._native_client else "file_ipc",
            "ready": bool(
                (native_health and native_health.ok)
                or (self._hwnd and self._dispatcher_version == __version__)
            ),
            "dispatcher_isolation": (
                "native-plugin-with-external-supervisor"
                if self._native_client
                else "external-python-process"
            ),
            "industrial_capabilities": self._industrial_capability_matrix(),
            "session": self._session.snapshot(),
            "com_sta": self._com_executor.snapshot(),
        }
        return CommandResult(ok=True, payload=info)

    def _autocad_crashed_result(self, state: dict, operation: str) -> CommandResult:
        reason = state.get("reason", "unknown")
        dialog = state.get("dialog") or {}
        title = dialog.get("title")
        description = f"AutoCAD crashed or entered a fatal error state ({reason})"
        if title:
            description += f": {title}"
        cer_records = ((state.get("cer") or {}).get("records") or [])
        cer_stack = " ".join(str(item.get("stack_excerpt") or "") for item in cer_records)
        theme_failure = "OverridePaletteTheme" in cer_stack or "AdUiMgdPaletteTheme" in cer_stack
        recommended_action = (
            "reset_or_repair_the_autocad_profile_or_installation_then_retry_with_an_isolated_ARG_profile"
            if theme_failure
            else "close_fatal_dialog_restart_autocad_and_retry"
        )
        return CommandResult(
            ok=False,
            error=description,
            error_code="E_AUTOCAD_CRASHED",
            recoverable=True,
            recommended_action=recommended_action,
            payload={
                "operation": operation,
                "crash": state,
                "diagnosis": "palette_theme_initialization" if theme_failure else "startup_failure_unclassified",
            },
        )

    @staticmethod
    def _document_key(document) -> str:
        hwnd = _com_value(document, "HWND")
        if hwnd:
            return f"hwnd:{int(hwnd)}"
        path = str(_com_value(document, "FullName", "") or "").strip()
        name = str(_com_value(document, "Name", "") or "").strip()
        return os.path.normcase(path or name)

    def _bind_document(self, document, *, force_new: bool = False) -> dict:
        key = self._document_key(document)
        path = str(_com_value(document, "FullName", "") or _com_value(document, "Name", ""))
        name = str(_com_value(document, "Name", ""))
        lease = self._session.bind_document(
            key,
            path=path,
            name=name,
            force_new=force_new,
        )
        return {**self._session.context(lease), "backend": self.name}

    async def document_context(self) -> CommandResult:
        if self._native_client is not None:
            result = await self._native_client.request("document.context")
            if result.ok and isinstance(result.payload, dict):
                result.payload.update(backend=self.name, transport="native_pipe")
            return result
        return await self._document_context_com()

    @sta_async_method("drawing.context", idempotent=True)
    async def _document_context_com(self) -> CommandResult:
        try:
            import pythoncom
            import win32com.client

            application = self._get_autocad_application()
            # Some AutoCAD builds expose ``Documents.Count`` as a deferred
            # COM proxy while the application is minimized or still settling.
            # ActiveDocument is the authoritative readiness probe; use Count
            # only as an optional secondary check so a transient proxy failure
            # does not become a false "no document" result.
            try:
                document = application.ActiveDocument
            except Exception:
                document = None
            if document is None:
                return CommandResult(
                    ok=False,
                    error="AutoCAD has no document",
                    error_code="E_NO_ACTIVE_DOCUMENT",
                )
            try:
                if int(application.Documents.Count) == 0:
                    return CommandResult(
                        ok=False,
                        error="AutoCAD has no document",
                        error_code="E_NO_ACTIVE_DOCUMENT",
                    )
            except Exception:
                # Keep the active-document result and expose the degraded
                # collection probe to callers as a diagnostic warning.
                count_warning = True
            else:
                count_warning = False
            payload = self._bind_document(document)
            if count_warning:
                payload["warnings"] = [
                    "AutoCAD.Documents.Count was unavailable; ActiveDocument was used"
                ]
            return CommandResult(
                ok=True, payload=payload
            )
        except Exception as exc:
            crash = detect_autocad_crash_state(self._hwnd, self._acad_process_id)
            if crash.get("crashed"):
                return self._autocad_crashed_result(crash, "drawing.context")
            return self._exception_result(
                exc,
                operation="drawing.context",
                system_call="AutoCAD.Application.ActiveDocument",
            )

    async def require_document_context(
        self,
        doc_id: str | None,
        expected_revision: int | None,
        lease_token: str | None = None,
        worker_generation: int | None = None,
    ) -> CommandResult:
        context = await self.document_context()
        if not context.ok:
            return context
        actual = context.payload
        if not doc_id:
            return CommandResult(
                ok=False,
                error="A modifying operation requires doc_id",
                error_code="E_PARAMETER_REJECTED",
                recommended_action="read_document_context_and_retry",
                payload={"required": ["doc_id", "expected_revision"], "actual": actual},
            )
        if str(doc_id) != str(actual["active_doc_id"]):
            return CommandResult(
                ok=False,
                error="The active AutoCAD document does not match doc_id",
                error_code="E_DOCUMENT_ID_MISMATCH",
                recoverable=False,
                payload={"requested_doc_id": doc_id, "actual": actual},
            )
        # Compatibility contexts from alternate backends and test adapters do
        # not expose a native lease yet.  They still receive revision fencing.
        if not actual.get("lease_token"):
            if expected_revision is None or int(expected_revision) != int(actual["revision"]):
                return CommandResult(
                    ok=False,
                    error="The active AutoCAD document revision is stale or missing",
                    error_code="E_DOCUMENT_REVISION_MISMATCH",
                    recoverable=False,
                    payload={"expected_revision": expected_revision, "actual": actual},
                )
            return CommandResult(ok=True, payload=actual)
        valid, error_code, lease_actual = self._session.validate(
            doc_id=doc_id,
            expected_revision=expected_revision,
            lease_token=lease_token,
            worker_generation=worker_generation,
        )
        if not valid:
            messages = {
                "E_SESSION_GENERATION_MISMATCH": "The AutoCAD worker generation changed",
                "E_DOCUMENT_LEASE_MISMATCH": "The AutoCAD document lease is stale",
                "E_DOCUMENT_REVISION_MISMATCH": "The active AutoCAD document revision is stale or missing",
            }
            return CommandResult(
                ok=False,
                error=messages.get(error_code, "The AutoCAD document lease is invalid"),
                error_code=error_code,
                recoverable=False,
                recommended_action="read_latest_document_context_and_retry",
                payload={
                    "expected_revision": expected_revision,
                    "lease_token_supplied": lease_token is not None,
                    "worker_generation": worker_generation,
                    "actual": lease_actual or actual,
                },
            )
        return CommandResult(ok=True, payload=actual)

    async def record_document_mutation(self, doc_id: str) -> CommandResult:
        if self._native_client is not None:
            # Native database events are the sole revision authority.
            return await self.document_context()
        context = await self.document_context()
        if not context.ok:
            return context
        if str(doc_id) != str(context.payload["active_doc_id"]):
            return CommandResult(
                ok=False,
                error="Cannot advance revision for a non-active document",
                error_code="E_DOCUMENT_ID_MISMATCH",
            )
        if not context.payload.get("lease_token"):
            context.payload["revision"] = int(context.payload.get("revision", 0)) + 1
            return context
        lease = self._session.record_mutation(doc_id)
        if lease is None:
            return CommandResult(
                ok=False,
                error="Cannot advance revision for an unknown document lease",
                error_code="E_DOCUMENT_ID_MISMATCH",
            )
        context.payload.update(self._session.context(lease))
        return context

    async def drawing_activate(
        self,
        doc_id: str | None,
        expected_revision: int | None = None,
        lease_token: str | None = None,
        worker_generation: int | None = None,
    ) -> CommandResult:
        normalized_doc_id, revision, validation = self._validate_activation_request(
            doc_id, expected_revision
        )
        if validation is not None:
            return validation
        if self._native_client is not None:
            result = await self._native_client.request(
                "document.activate",
                doc_id=normalized_doc_id,
                expected_revision=revision,
            )
            if result.ok and isinstance(result.payload, dict):
                result.payload.update(
                    backend=self.name,
                    transport="native_pipe",
                    requested_doc_id=normalized_doc_id,
                    expected_revision=revision,
                )
            return result
        return await self._drawing_activate_com(
            normalized_doc_id,
            revision,
            lease_token=lease_token,
            worker_generation=worker_generation,
        )

    @sta_async_method("drawing.activate", idempotent=True)
    async def _drawing_activate_com(
        self,
        doc_id: str,
        expected_revision: int,
        *,
        lease_token: str | None = None,
        worker_generation: int | None = None,
    ) -> CommandResult:
        try:
            import pythoncom
            import win32com.client

            application = self._get_autocad_application()
            target_document = None
            target_context = None
            for index in range(int(application.Documents.Count)):
                document = application.Documents.Item(index)
                context = self._bind_document(document)
                if context["doc_id"] == doc_id:
                    target_document = document
                    target_context = context
                    break
            if target_document is None or target_context is None:
                return CommandResult(
                    ok=False,
                    error=f"Unknown document id: {doc_id}",
                    error_code="E_DOCUMENT_ID_MISMATCH",
                    recoverable=False,
                    recommended_action="read_document_context_and_retry",
                    payload={
                        "requested_doc_id": doc_id,
                        "expected_revision": expected_revision,
                    },
                )

            valid, error_code, lease_actual = self._session.validate(
                doc_id=doc_id,
                expected_revision=expected_revision,
                lease_token=lease_token,
                worker_generation=worker_generation,
            )
            if not valid:
                messages = {
                    "E_SESSION_GENERATION_MISMATCH": "The AutoCAD worker generation changed",
                    "E_DOCUMENT_LEASE_MISMATCH": "The AutoCAD document lease is stale",
                    "E_DOCUMENT_REVISION_MISMATCH": "The requested document revision is stale",
                }
                return CommandResult(
                    ok=False,
                    error=messages.get(error_code, "The document activation fence is invalid"),
                    error_code=error_code or "E_DOCUMENT_ID_MISMATCH",
                    recoverable=False,
                    recommended_action="read_latest_document_context_and_retry",
                    payload={
                        "requested_doc_id": doc_id,
                        "expected_revision": expected_revision,
                        "actual": lease_actual or target_context,
                    },
                )

            # This changes only AutoCAD's active document.  It deliberately
            # does not call the window policy or foreground APIs, so attaching
            # to a user's CAD session cannot steal focus.
            target_document.Activate()
            active_document = application.ActiveDocument
            actual = self._bind_document(active_document)
            differences = []
            if actual["active_doc_id"] != doc_id:
                differences.append(
                    {
                        "path": "active_doc_id",
                        "requested": doc_id,
                        "actual": actual["active_doc_id"],
                    }
                )
            if int(actual.get("revision", -1)) != int(expected_revision):
                differences.append(
                    {
                        "path": "revision",
                        "requested": expected_revision,
                        "actual": actual.get("revision"),
                    }
                )
            payload = {
                **actual,
                "requested_doc_id": doc_id,
                "expected_revision": expected_revision,
                "requested": target_context,
                "actual": actual,
                "diff": differences,
            }
            return CommandResult(
                ok=not differences,
                payload=payload,
                error="AutoCAD activation postcondition did not match the request"
                if differences
                else None,
                error_code=(
                    "E_DOCUMENT_ID_MISMATCH"
                    if any(item["path"] == "active_doc_id" for item in differences)
                    else "E_DOCUMENT_REVISION_MISMATCH"
                    if differences
                    else None
                ),
                recoverable=False if differences else None,
                recommended_action="read_latest_document_context_and_retry"
                if differences
                else None,
            )
        except Exception as exc:
            return self._exception_result(
                exc,
                operation="drawing.activate",
                parameters={
                    "doc_id": doc_id,
                    "expected_revision": expected_revision,
                },
                system_call="AutoCAD.Document.Activate",
            )

    async def transaction_begin(self, doc_id: str, expected_revision: int) -> CommandResult:
        guard = await self.require_document_context(doc_id, expected_revision)
        if not guard.ok:
            return guard
        if self._transactions:
            return CommandResult(
                ok=False,
                error="Only one AutoCAD transaction may be active at a time",
                error_code="E_TRANSACTION_BEGIN",
            )
        result = await self._dispatch("transaction-begin", {})
        if not result.ok:
            return result
        transaction_id = f"tx-{uuid.uuid4().hex}"
        self._transactions[transaction_id] = {
            "doc_id": doc_id,
            "base_revision": expected_revision,
        }
        return CommandResult(
            ok=True,
            payload={
                "transaction_id": transaction_id,
                "state": "active",
                **guard.payload,
            },
        )

    async def _finish_transaction(
        self,
        transaction_id: str,
        doc_id: str,
        expected_revision: int,
        *,
        rollback: bool,
    ) -> CommandResult:
        transaction = self._transactions.get(str(transaction_id))
        if not transaction or transaction["doc_id"] != doc_id:
            return CommandResult(
                ok=False,
                error="Transaction does not exist for the requested document",
                error_code="E_TRANSACTION_NOT_FOUND",
                payload={"transaction_id": transaction_id, "doc_id": doc_id},
            )
        guard = await self.require_document_context(doc_id, expected_revision)
        if not guard.ok:
            return guard
        command = "transaction-rollback" if rollback else "transaction-commit"
        result = await self._dispatch(command, {})
        if not result.ok:
            return result
        self._transactions.pop(str(transaction_id), None)
        context = await self.record_document_mutation(doc_id)
        return CommandResult(
            ok=context.ok,
            payload={
                "transaction_id": transaction_id,
                "state": "rolled_back" if rollback else "committed",
                **(context.payload if context.ok else {}),
            },
            error=context.error,
            error_code=context.error_code,
        )

    async def transaction_commit(
        self, transaction_id: str, doc_id: str, expected_revision: int
    ) -> CommandResult:
        return await self._finish_transaction(
            transaction_id, doc_id, expected_revision, rollback=False
        )

    async def transaction_rollback(
        self, transaction_id: str, doc_id: str, expected_revision: int
    ) -> CommandResult:
        return await self._finish_transaction(
            transaction_id, doc_id, expected_revision, rollback=True
        )

    async def native_transaction_execute(
        self,
        doc_id: str,
        expected_revision: int,
        idempotency_key: str,
        operations: list[dict],
        *,
        session_id: str | None = None,
    ) -> CommandResult:
        if self._native_client is None:
            return CommandResult(
                ok=False,
                error="The transactional AutoCAD native worker is not connected",
                error_code="E_NATIVE_PLUGIN_UNAVAILABLE",
                recoverable=True,
                recommended_action="install_or_load_the_signed_native_bundle_then_call_system.ensure_ready",
            )
        if not str(idempotency_key or "").strip():
            return CommandResult(
                ok=False,
                error="A native transaction requires idempotency_key",
                error_code="E_PARAMETER_REJECTED",
                recoverable=False,
            )
        if not isinstance(operations, list) or not operations:
            return CommandResult(
                ok=False,
                error="A native transaction requires a non-empty operations array",
                error_code="E_PARAMETER_REJECTED",
                recoverable=False,
            )
        return await self._native_client.request(
            "transaction.execute",
            doc_id=doc_id,
            expected_revision=expected_revision,
            idempotency_key=str(idempotency_key),
            session_id=session_id,
            data={"operations": camel_case_keys(operations)},
        )

    @staticmethod
    def _exception_result(
        exc: Exception,
        *,
        operation: str,
        parameters: dict | None = None,
        system_call: str | None = None,
        file_path: str | None = None,
        error_code: str = "E_SYSTEM_CALL_FAILED",
        recommended_action: str = "inspect_path_parameters_and_system_error",
    ) -> CommandResult:
        message, details = exception_context(
            exc,
            operation=operation,
            parameters=parameters,
            system_call=system_call,
            file_path=file_path,
        )
        # Transport wrappers such as the COM STA quarantine attach a stable
        # error contract to the exception. Preserve it instead of flattening
        # a poisoned worker into the generic ``E_SYSTEM_CALL_FAILED`` code.
        error_code = str(getattr(exc, "error_code", error_code))
        recommended_action = getattr(exc, "recommended_action", recommended_action)
        if getattr(exc, "recoverable", None) is not None:
            recoverable = bool(getattr(exc, "recoverable"))
        else:
            recoverable = None
        exception_details = getattr(exc, "details", None)
        if isinstance(exception_details, dict):
            details = {**details, "transport": exception_details}
        return CommandResult(
            ok=False,
            error=message,
            error_code=error_code,
            recoverable=recoverable,
            recommended_action=recommended_action,
            payload=details,
        )

    @sta_sync_method("product.discover", idempotent=True)
    def _discover_product(self) -> dict:
        executable = os.environ.get("AUTOCAD_MCP_ACAD_EXE", "").strip()
        product = None
        version = None
        try:
            import pythoncom
            import win32com.client

            application = self._get_autocad_application()
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

    @sta_sync_method("drawing.ensure_active", timeout=DOCUMENT_TIMEOUT + 5.0)
    def _ensure_active_document(self) -> dict:
        deadline = time.monotonic() + DOCUMENT_TIMEOUT
        last_error = None
        stable_key = None
        stable_reads = 0
        created_first_document = False
        while time.monotonic() < deadline:
            try:
                import pythoncom
                import win32com.client

                application = self._get_autocad_application()
                document_count = int(application.Documents.Count)
                if document_count == 0 and not created_first_document:
                    document = application.Documents.Add()
                    created_first_document = True
                else:
                    document = application.ActiveDocument
                name = str(document.Name)
                path = str(document.FullName or document.Name)
                document_key = self._document_key(document)
                if document_key == stable_key:
                    stable_reads += 1
                else:
                    stable_key = document_key
                    stable_reads = 1
                if stable_reads < 3:
                    self._com_executor.cooperative_sleep(0.25)
                    continue
                context = self._bind_document(document)
                return {
                    "ready": True,
                    "name": name,
                    "path": path,
                    "created_first_document": created_first_document,
                    "stability_reads": stable_reads,
                    **context,
                }
            except Exception as exc:
                last_error = exc
                crash = detect_autocad_crash_state(self._hwnd, self._acad_process_id)
                if crash.get("crashed"):
                    return {
                        "ready": False,
                        "error": "AutoCAD crashed while creating or reading the first document",
                        "error_code": "E_AUTOCAD_CRASHED",
                        "details": crash,
                    }
                self._com_executor.cooperative_sleep(0.25)
        return {
            "ready": False,
            "error": f"No active document after {DOCUMENT_TIMEOUT:g}s: {last_error}",
        }

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
                crash = detect_autocad_crash_state(self._hwnd, self._acad_process_id)
                if crash.get("crashed"):
                    return self._autocad_crashed_result(crash, command)
                return CommandResult(
                    ok=False,
                    error="AutoCAD command state remained blocked after cancellation",
                    error_code="E_COMMAND_STATE_BLOCKED",
                )

            # Poll for result
            deadline = time.time() + TIMEOUT
            while time.time() < deadline:
                crash = detect_autocad_crash_state(self._hwnd, self._acad_process_id)
                if crash.get("crashed"):
                    return self._autocad_crashed_result(crash, command)
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
            crash = detect_autocad_crash_state(self._hwnd, self._acad_process_id)
            if crash.get("crashed"):
                return self._autocad_crashed_result(crash, command)
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
        mode, _ = self._configured_window_mode()
        activate = mode == "foreground" and os.environ.get(
            "AUTOCAD_MCP_ACTIVATE_ON_DRAW", "false"
        ).lower() in ("1", "true", "yes", "on")
        if self._window_policy_owned or os.environ.get(
            "AUTOCAD_MCP_APPLY_WINDOW_POLICY_TO_EXISTING", "false"
        ).strip().lower() in ("1", "true", "yes", "on"):
            self._ensure_autocad_visible(activate=activate)
        if not self._wait_for_autocad_idle(timeout=5.0):
            self._cancel_active_command()
            if not self._wait_for_autocad_idle(timeout=2.0):
                return False
        # A missing command target used to be treated as a successful trigger;
        # the dispatcher then waited for the full IPC timeout even though no
        # command could possibly arrive in AutoCAD.  Keep the failure at the
        # transport boundary so callers receive a bounded, actionable result.
        return bool(self._type_command("(c:mcp-dispatch)"))

    @sta_sync_method("autocad.wait_idle", idempotent=True, timeout=15.0)
    def _wait_for_autocad_idle(self, timeout: float = 2.0) -> bool:
        """Wait for AutoCAD to finish unwinding the previous dispatched command."""
        if sys.platform != "win32":
            return True
        deadline = time.time() + max(0.0, timeout)
        while True:
            try:
                import pythoncom
                import win32com.client

                application = self._get_autocad_application()
                document = application.ActiveDocument
                if int(document.GetVariable("CMDACTIVE")) == 0:
                    return True
            except Exception as exc:
                if com_hresult(exc) not in {-2147418111, -2147417846}:
                    return False
            if time.time() >= deadline:
                return False
            self._com_executor.cooperative_sleep(0.05)

    @staticmethod
    def _configured_window_mode() -> tuple[str, str]:
        requested = os.environ.get(
            "AUTOCAD_MCP_WINDOW_MODE", "quiet_minimized"
        ).strip().lower()
        aliases = {
            "quiet_minimized": "minimized",
            "recording": "foreground",
            "user": "preserve",
            "unchanged": "preserve",
        }
        mode = aliases.get(requested, requested)
        if mode not in {"minimized", "visible", "foreground", "preserve"}:
            mode = "minimized"
        return mode, requested

    def _window_visibility_status(self) -> dict:
        configured = os.environ.get("AUTOCAD_MCP_VISIBLE", "true").lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        mode, requested_mode = self._configured_window_mode()
        status = {
            "configured_visible": configured,
            "window_mode": mode,
            "requested_window_mode": requested_mode,
            "hwnd": self._hwnd,
        }
        if sys.platform == "win32" and self._hwnd:
            try:
                import win32gui

                status.update(
                    visible=bool(win32gui.IsWindowVisible(self._hwnd)),
                    minimized=bool(win32gui.IsIconic(self._hwnd)),
                )
            except Exception:
                try:
                    import ctypes

                    user32 = ctypes.windll.user32
                    status.update(
                        visible=bool(user32.IsWindowVisible(int(self._hwnd))),
                        minimized=bool(user32.IsIconic(int(self._hwnd))),
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

    @sta_sync_method("view.fit", idempotent=True)
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

            application = self._get_autocad_application()
            application.ZoomExtents()
            application.Update()
            return {"configured": configured, "fitted": True, "renderer": "autocad-com"}
        except Exception:
            self._type_command("_.ZOOM _E")
            self._wait_for_autocad_idle(timeout=2.0)
            return {"configured": configured, "fitted": True, "renderer": "autocad-command"}

    @sta_sync_method("window.apply_policy", idempotent=True)
    def _ensure_autocad_visible(self, activate: bool = False) -> dict:
        """Apply a non-disruptive window policy without stealing the user's focus."""
        configured = os.environ.get("AUTOCAD_MCP_VISIBLE", "true").lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        if not configured:
            return {"configured_visible": False, "shown": False}

        mode, requested_mode = self._configured_window_mode()
        if activate:
            mode = "foreground"
        if mode == "preserve":
            return {
                "configured_visible": configured,
                "shown": bool(self._hwnd),
                "window_mode": "preserve",
                "requested_window_mode": requested_mode,
                "activated": False,
                "preserved_user_window": True,
                "transport": "none",
                "hwnd": self._hwnd,
            }

        foreground_before = None
        if sys.platform == "win32":
            try:
                import win32gui

                foreground_before = win32gui.GetForegroundWindow()
                if foreground_before and foreground_before != self._hwnd:
                    self._user_foreground_hwnd = int(foreground_before)
            except Exception:
                pass

        transport = "win32"
        try:
            import pythoncom
            import win32com.client

            application = self._get_autocad_application()
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

                if mode == "foreground":
                    win32gui.ShowWindow(self._hwnd, win32con.SW_RESTORE)
                    try:
                        win32gui.SetForegroundWindow(self._hwnd)
                    except Exception:
                        pass
                elif mode == "visible":
                    win32gui.ShowWindow(self._hwnd, win32con.SW_SHOWNOACTIVATE)
                elif not self._window_policy_applied:
                    win32gui.ShowWindow(self._hwnd, win32con.SW_SHOWMINNOACTIVE)
                if mode != "foreground" and foreground_before:
                    current = win32gui.GetForegroundWindow()
                    if current == self._hwnd and current != foreground_before:
                        try:
                            win32gui.SetForegroundWindow(foreground_before)
                        except Exception:
                            pass
            except Exception:
                pass
        self._window_policy_applied = True
        return {
            "configured_visible": True,
            "shown": bool(self._hwnd),
            "window_mode": mode,
            "requested_window_mode": requested_mode,
            "activated": mode == "foreground",
            "transport": transport,
        }

    @sta_sync_method("command.cancel")
    def _cancel_active_command(self) -> str:
        """Cancel command-line state without requiring the IPC dispatcher."""
        if sys.platform != "win32":
            return "not-windows"
        try:
            import pythoncom
            import win32com.client

            application = self._get_autocad_application()
            document = application.ActiveDocument
            active = int(document.GetVariable("CMDACTIVE"))
            if active:
                document.SendCommand("\x1b\x1b")
                self._com_executor.cooperative_sleep(0.1)
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

    def _type_command(self, command: str) -> bool:
        """Post a command-line expression to the active AutoCAD session."""
        if self._send_command_via_com(command):
            return True

        try:
            import ctypes

            WM_CHAR = 0x0102
            WM_KEYDOWN = 0x0100
            WM_KEYUP = 0x0101
            VK_ESCAPE = 0x1B
            target = self._command_hwnd or self._hwnd
            if not target:
                log.error("command_trigger_failed", error="no AutoCAD command window is bound")
                return False
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
            return True
        except Exception as e:
            log.error("command_trigger_failed", error=str(e))
            return False

    @sta_sync_method("command.send")
    def _send_command_via_com(self, command: str) -> bool:
        """Use full AutoCAD's COM API before falling back to window messages."""
        if sys.platform != "win32":
            return False
        deadline = time.time() + 5.0
        while True:
            try:
                import pythoncom
                import win32com.client

                application = self._get_autocad_application()
                document = application.ActiveDocument
                document.SendCommand(command + "\n")
                log.debug("command_sent_via_com")
                return True
            except Exception as exc:
                if com_hresult(exc) in {-2147418111, -2147417846} and time.time() < deadline:
                    self._com_executor.cooperative_sleep(0.25)
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
        result = await self._dispatch("drawing-info", {})
        context = await self.document_context()
        if result.ok and context.ok and isinstance(result.payload, dict):
            result.payload.update(context.payload)
        return result

    async def drawing_save(self, path: str | None = None) -> CommandResult:
        try:
            actual = self._save_via_com(path)
            requested = {"path": str(Path(path).expanduser().resolve()) if path else None}
            return CommandResult(
                ok=True,
                payload={
                    **actual,
                    "requested": requested,
                    "actual": actual,
                    "diff": [],
                },
            )
        except Exception as com_error:
            result = await self._dispatch("drawing-save", {"path": path})
            if result.ok and isinstance(result.payload, dict):
                _, details = exception_context(
                    com_error,
                    operation="drawing.save",
                    parameters={"path": path},
                    system_call="AutoCAD.Document.SaveAs",
                    file_path=path,
                )
                result.payload["com_fallback"] = details
                return result
            failure = self._exception_result(
                com_error,
                operation="drawing.save",
                parameters={"path": path},
                system_call="AutoCAD.Document.SaveAs",
                file_path=path,
                recommended_action="verify_document_path_permissions_and_retry",
            )
            failure.payload["dispatcher_fallback"] = result.to_dict()
            return failure

    async def drawing_save_as_dxf(self, path: str) -> CommandResult:
        try:
            actual = self._export_dxf_via_com(path)
            return CommandResult(
                ok=True,
                payload={
                    **actual,
                    "requested": {"path": str(Path(path).expanduser().resolve()), "format": "dxf"},
                    "actual": actual,
                    "diff": [],
                },
            )
        except Exception as exc:
            return self._exception_result(
                exc,
                operation="drawing.save_as_dxf",
                parameters={"path": path},
                system_call="AutoCAD.Document.Export",
                file_path=path,
                error_code="E_DXF_EXPORT",
                recommended_action="verify_dxf_output_path_and_export_settings",
            )

    @sta_async_method("drawing.copy_dwg", timeout=45.0)
    async def drawing_copy_dwg(self, path: str) -> CommandResult:
        try:
            import pythoncom
            import win32com.client

            output = Path(path).expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.unlink(missing_ok=True)
            application = self._get_autocad_application()
            document = application.ActiveDocument
            before = self._bind_document(document)
            selection_name = f"MCP_WBLOCK_{uuid.uuid4().hex[:8]}"
            selection = document.SelectionSets.Add(selection_name)
            try:
                selection.Select(5)
                document.Wblock(str(output), selection)
            finally:
                try:
                    selection.Delete()
                except Exception:
                    pass
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and not output.is_file():
                self._com_executor.cooperative_sleep(0.1)
            if not output.is_file() or output.stat().st_size <= 0:
                raise RuntimeError(f"AutoCAD Wblock did not create a non-empty DWG: {output}")
            after = self._bind_document(application.ActiveDocument)
            differences = []
            for field in ("active_doc_id", "active_path"):
                if before[field] != after[field]:
                    differences.append(
                        {"path": field, "requested": before[field], "actual": after[field]}
                    )
            if differences:
                return CommandResult(
                    ok=False,
                    error="DWG copy changed the active document",
                    error_code="E_DOCUMENT_ID_MISMATCH",
                    payload={"requested": before, "actual": after, "diff": differences},
                )
            return CommandResult(
                ok=True,
                payload={
                    "path": str(output),
                    "format": "dwg",
                    "renderer": "autocad-com-wblock",
                    "read_only_context": True,
                    "requested": {"path": str(output), "source": before},
                    "actual": {"path": str(output), "source": after},
                    "diff": [],
                    **after,
                },
            )
        except Exception as exc:
            return self._exception_result(
                exc,
                operation="drawing.copy_dwg",
                parameters={"path": path},
                system_call="AutoCAD.Document.Wblock",
                file_path=path,
                recommended_action="verify_output_path_and_wblock_availability",
            )
    async def drawing_create(
        self, name: str | None = None, idempotency_key: str | None = None
    ) -> CommandResult:
        if self._native_client is not None:
            if not str(idempotency_key or "").strip():
                return CommandResult(
                    ok=False,
                    error="Native drawing.create requires idempotency_key",
                    error_code="E_PARAMETER_REJECTED",
                    recoverable=False,
                    recommended_action="retry_with_a_unique_stable_idempotency_key",
                )
            requested = str(name).strip() if name else None
            data = {"template": os.environ.get("AUTOCAD_MCP_TEMPLATE", "acadiso.dwt")}
            if requested:
                output = Path(requested).expanduser().resolve()
                if output.suffix.lower() != ".dwg":
                    output = output.with_suffix(".dwg")
                data["path"] = str(output)
            result = await self._native_client.request(
                "document.create",
                data=data,
                idempotency_key=str(idempotency_key),
            )
            if result.ok and isinstance(result.payload, dict):
                differences = result.payload.get("diff", [])
                if differences:
                    return CommandResult(
                        ok=False,
                        error="AutoCAD created a document that did not match the requested path",
                        error_code="E_POSTCONDITION_MISMATCH",
                        recoverable=False,
                        payload={"requested": data, "actual": result.payload, "diff": differences},
                    )
                result.payload.update(
                    requested_name=requested,
                    actual_name=result.payload.get("document_name"),
                    actual_path=result.payload.get("active_path"),
                    name_honored=True,
                    backend=self.name,
                    transport="native_pipe",
                )
            return result
        return await self._drawing_create_com(name)

    @sta_async_method("drawing.create", timeout=60.0)
    async def _drawing_create_com(self, name: str | None = None) -> CommandResult:
        requested = str(name).strip() if name else None
        foreground_before = None
        focus_restored = False
        try:
            import pythoncom
            import win32com.client

            if sys.platform == "win32":
                try:
                    import win32gui

                    foreground_before = win32gui.GetForegroundWindow()
                except Exception:
                    pass

            application = self._get_autocad_application()
            document = application.Documents.Add()
            mode, _ = self._configured_window_mode()
            if sys.platform == "win32" and mode == "minimized":
                try:
                    import win32con
                    import win32gui

                    self._hwnd = int(application.HWND) or self._hwnd
                    win32gui.ShowWindow(self._hwnd, win32con.SW_SHOWMINNOACTIVE)
                except Exception:
                    pass
            if requested:
                output = Path(requested).expanduser().resolve()
                if output.suffix.lower() != ".dwg":
                    output = output.with_suffix(".dwg")
                output.parent.mkdir(parents=True, exist_ok=True)
                document.SaveAs(str(output), 64)
            actual_path = str(document.FullName or document.Name)
            actual_name = str(document.Name)
            context = self._bind_document(document, force_new=True)
            if requested:
                expected = str(Path(requested).expanduser().resolve().with_suffix(".dwg"))
                if os.path.normcase(str(Path(actual_path).resolve())) != os.path.normcase(expected):
                    raise RuntimeError(f"AutoCAD created {actual_path} instead of {expected}")

            dispatcher = Path(os.environ.get("AUTOCAD_MCP_LISP_PATH", "") or (LISP_DIR / "mcp_dispatch.lsp"))
            self._type_command(f'(load "{str(dispatcher.resolve()).replace(chr(92), "/")}")')
            self._wait_for_autocad_idle(timeout=10.0)
            self._audit_revision = 0
            self._audit_fingerprints = None
            self._semantic_store().clear()
            if sys.platform == "win32" and foreground_before:
                try:
                    import win32gui

                    current = win32gui.GetForegroundWindow()
                    if current == self._hwnd and win32gui.IsWindow(foreground_before):
                        win32gui.SetForegroundWindow(foreground_before)
                        focus_restored = True
                except Exception:
                    pass
            return CommandResult(
                ok=True,
                payload={
                    "requested_name": requested,
                    "actual_name": actual_name,
                    "actual_path": actual_path,
                    "name_honored": not requested or os.path.normcase(actual_path) == os.path.normcase(expected),
                    "requested": {"path": requested, "format": "dwg"},
                    "actual": {"path": actual_path, "name": actual_name, "format": "dwg"},
                    "diff": [],
                    "foreground_before": foreground_before,
                    "focus_restored": focus_restored,
                    **context,
                },
            )
        except Exception as exc:
            return self._exception_result(
                exc,
                operation="drawing.create",
                parameters={"name": name},
                system_call="AutoCAD.Documents.Add/Document.SaveAs",
                file_path=requested,
                recommended_action="restart_autocad_or_choose_a_valid_managed_dwg_path",
            )

    @sta_async_method("drawing.close")
    async def drawing_close(self, save: bool = False) -> CommandResult:
        try:
            import win32com.client

            application = self._get_autocad_application()
            document = application.ActiveDocument
            context = self._bind_document(document)
            document.Close(bool(save))
            self._session.invalidate_document(context["doc_id"])
            return CommandResult(
                ok=True,
                payload={
                    "closed_doc_id": context["doc_id"],
                    "saved": bool(save),
                    "document_state": self._session.document_state.value,
                },
            )
        except Exception as exc:
            return self._exception_result(
                exc,
                operation="drawing.close",
                parameters={"save": bool(save)},
                system_call="AutoCAD.Document.Close",
            )

    @sta_async_method("presentation.apply", idempotent=True)
    async def apply_presentation_style(
        self, colors: dict[str, list[int] | tuple[int, int, int]], visual_style: str
    ) -> CommandResult:
        try:
            import win32com.client

            application = self._get_autocad_application()
            document = application.ActiveDocument
            applied = []
            requested_colors = {
                str(handle): [int(value) for value in rgb]
                for handle, rgb in colors.items()
            }
            actual_colors = {}
            color_readback_errors = []
            for handle, rgb in colors.items():
                entity = document.HandleToObject(str(handle))
                red, green, blue = (max(0, min(255, int(value))) for value in rgb)
                try:
                    true_color = entity.TrueColor
                    true_color.SetRGB(red, green, blue)
                    entity.TrueColor = true_color
                except Exception:
                    entity.Color = 5
                entity.Update()
                applied.append(str(handle))
                try:
                    true_color = entity.TrueColor
                    actual_colors[str(handle)] = [
                        int(true_color.Red),
                        int(true_color.Green),
                        int(true_color.Blue),
                    ]
                except Exception as readback_error:
                    color_readback_errors.append(
                        {"handle": str(handle), "error": str(readback_error)}
                    )
            from autocad_mcp.visual_styles import autocad_visual_style_name

            document.SetVariable(
                "VSCURRENT", autocad_visual_style_name(str(visual_style))
            )
            # Force a regeneration before the plot/readback.  This is a
            # display-only operation; it does not alter model geometry.
            try:
                document.Regen(0)
            except Exception:
                pass
            application.Update()
            actual_style = str(document.GetVariable("VSCURRENT"))
            return CommandResult(
                ok=True,
                payload={
                    "colored_handles": applied,
                    "color_count": len(applied),
                    "requested_colors": requested_colors,
                    "actual_colors": actual_colors,
                    "color_readback_errors": color_readback_errors,
                    "visual_style": actual_style,
                    "view_mode": _com_value(document, "ViewMode", None),
                },
            )
        except Exception as exc:
            return self._exception_result(
                exc,
                operation="presentation.apply",
                parameters={"handle_count": len(colors), "visual_style": visual_style},
                system_call="AutoCAD.Entity.TrueColor + VSCURRENT",
            )

    @sta_sync_method("presentation.read", idempotent=True)
    def _read_visual_style(self) -> dict[str, object]:
        """Read the active viewport style on the shared COM STA."""
        import win32com.client

        document = self._get_autocad_application().ActiveDocument
        return {
            "visual_style": str(document.GetVariable("VSCURRENT")),
            "view_mode": _com_value(document, "ViewMode", None),
        }

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

    def _publish_staged_file(
        self, staging: Path, output: Path, *, timeout: float = 2.0
    ) -> dict:
        """Publish atomically, copying away from an AutoCAD-owned source lock."""
        started = time.monotonic()
        deadline = started + max(0.0, float(timeout))
        last_lock = None
        while True:
            try:
                os.replace(staging, output)
                return {
                    "mode": "atomic-rename",
                    "wait_seconds": round(time.monotonic() - started, 3),
                    "staging_removed": True,
                }
            except PermissionError as exc:
                last_lock = exc
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.1)

        publish_copy = output.with_name(
            f".{output.stem}.{uuid.uuid4().hex}.publish{output.suffix}"
        )
        try:
            shutil.copyfile(staging, publish_copy)
            os.replace(publish_copy, output)
        except Exception:
            self._remove_staging_file(publish_copy, timeout=0.0)
            raise last_lock
        staging_removed = self._remove_staging_file(staging, timeout=0.0)
        if not staging_removed:
            self._deferred_output_cleanup.add(staging)
        return {
            "mode": "copy-then-atomic-rename",
            "wait_seconds": round(time.monotonic() - started, 3),
            "staging_removed": staging_removed,
        }

    @staticmethod
    def _remove_staging_file(path: Path, *, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            try:
                path.unlink(missing_ok=True)
                return True
            except PermissionError:
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.1)
            except OSError:
                return False

    def _cleanup_deferred_outputs(self, *, timeout: float = 0.0) -> list[str]:
        removed = []
        for path in list(self._deferred_output_cleanup):
            if self._remove_staging_file(path, timeout=timeout):
                self._deferred_output_cleanup.discard(path)
                removed.append(str(path))
        return removed

    @staticmethod
    def _start_output_viewer_guard(
        path: Path, foreground_anchor: int | None = None
    ) -> dict:
        """Hide and close only a viewer window opened for this exact output file."""
        state = {
            "enabled": sys.platform == "win32",
            "target": str(path),
            "foreground_before": None,
            "events": [],
            "preexisting_pids": set(),
            "stop": threading.Event(),
            "thread": None,
        }
        if sys.platform != "win32":
            return state
        try:
            import win32con
            import win32gui
            import win32process

            target_name = path.name.casefold()
            current_foreground = win32gui.GetForegroundWindow()
            state["foreground_before"] = foreground_anchor or current_foreground

            def remember_process(hwnd, _):
                try:
                    state["preexisting_pids"].add(
                        int(win32process.GetWindowThreadProcessId(hwnd)[1])
                    )
                except Exception:
                    pass
                return True

            win32gui.EnumWindows(remember_process, None)

            def watch() -> None:
                seen = set()
                viewer_pids: set[int] = set()
                while not state["stop"].is_set():
                    def inspect(hwnd, _):
                        try:
                            title = win32gui.GetWindowText(hwnd) or ""
                            if target_name not in title.casefold() or hwnd in seen:
                                return True
                            seen.add(hwnd)
                            process_id = win32process.GetWindowThreadProcessId(hwnd)[1]
                            viewer_pids.add(int(process_id))
                            was_foreground = win32gui.GetForegroundWindow() == hwnd
                            win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
                            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                            restored = False
                            previous = state["foreground_before"]
                            if was_foreground and previous and win32gui.IsWindow(previous):
                                try:
                                    win32gui.SetForegroundWindow(previous)
                                    restored = True
                                except Exception:
                                    pass
                            state["events"].append(
                                {
                                    "hwnd": int(hwnd),
                                    "process_id": int(process_id),
                                    "title": title,
                                    "hidden": True,
                                    "close_requested": True,
                                    "foreground_restored": restored,
                                }
                            )
                        except Exception:
                            pass
                        return True

                    try:
                        win32gui.EnumWindows(inspect, None)
                    except Exception:
                        pass
                    if state["events"]:
                        try:
                            foreground = win32gui.GetForegroundWindow()
                            foreground_pid = int(
                                win32process.GetWindowThreadProcessId(foreground)[1]
                            )
                            previous = state["foreground_before"]
                            process_name = _process_executable_name(foreground_pid)
                            title = win32gui.GetWindowText(foreground) or ""
                            known_viewer = process_name in {
                                "wpspdf.exe",
                                "acrord32.exe",
                                "foxitpdfreader.exe",
                                "sumatrapdf.exe",
                            }
                            # Only suppress the exact viewer process/window
                            # observed for this staging filename.  Never close
                            # an unrelated newly launched foreground process;
                            # the user may be working in it while plotting.
                            is_target_viewer = (
                                foreground_pid in viewer_pids
                                or (known_viewer and target_name in title.casefold())
                            )
                            if foreground != previous and is_target_viewer:
                                win32gui.ShowWindow(foreground, win32con.SW_HIDE)
                                win32gui.PostMessage(
                                    foreground, win32con.WM_CLOSE, 0, 0
                                )
                                restored = False
                                if previous and win32gui.IsWindow(previous):
                                    try:
                                        win32gui.SetForegroundWindow(previous)
                                        restored = True
                                    except Exception:
                                        pass
                                state["events"].append(
                                    {
                                        "hwnd": int(foreground),
                                        "process_id": foreground_pid,
                                        "process_name": process_name,
                                        "title": title,
                                        "hidden": True,
                                        "close_requested": True,
                                        "foreground_restored": restored,
                                        "new_viewer_process": foreground_pid not in state["preexisting_pids"],
                                        "focus_recovered": True,
                                    }
                                )
                        except Exception:
                            pass
                    state["stop"].wait(0.02)

            thread = threading.Thread(
                target=watch, name="autocad-mcp-viewer-guard", daemon=True
            )
            state["thread"] = thread
            thread.start()
        except Exception as exc:
            state["enabled"] = False
            state["error"] = str(exc)
        return state

    @staticmethod
    def _stop_output_viewer_guard(state: dict, *, grace: float = 1.5) -> dict:
        thread = state.get("thread")
        if thread is not None:
            time.sleep(max(0.0, float(grace)))
            state["stop"].set()
            thread.join(timeout=1.0)
        return {
            "enabled": bool(state.get("enabled")),
            "target": state.get("target"),
            "foreground_before": state.get("foreground_before"),
            "viewer_detected": bool(state.get("events")),
            "viewer_suppressed": bool(state.get("events")),
            "events": list(state.get("events", [])),
            **({"error": state["error"]} if state.get("error") else {}),
        }

    async def drawing_plot_pdf(
        self,
        path: str,
        paper: str = "A3",
        orientation: str = "landscape",
        plot_style: str = "monochrome.ctb",
        scale_mode: str = "fit",
        scale: str = "fit",
        center: bool = True,
    ) -> CommandResult:
        output = Path(path).expanduser().resolve()
        staging = output.with_name(f".{output.stem}.{uuid.uuid4().hex}.tmp.pdf")
        requested = {
            "path": str(output),
            "paper": str(paper).upper(),
            "orientation": str(orientation).lower(),
            "plot_style": plot_style,
            "scale_mode": str(scale_mode).lower(),
            "scale": str(scale),
            "center": bool(center),
        }
        viewer_guard_state = self._start_output_viewer_guard(
            staging, self._user_foreground_hwnd
        )
        try:
            actual = self._plot_preview_via_com(
                str(staging), paper, orientation, plot_style, scale_mode, scale, center
            )
        except Exception as com_error:
            result = await self._dispatch("drawing-plot-pdf", {"path": str(staging)})
            if result.ok and staging.is_file() and staging.stat().st_size > 0:
                actual = result.payload if isinstance(result.payload, dict) else {"path": str(staging)}
                _, fallback = exception_context(
                    com_error,
                    operation="drawing.plot_pdf",
                    parameters=requested,
                    system_call="AutoCAD.Plot.PlotToFile",
                    file_path=str(output),
                )
                actual.update(renderer="autolisp-plot", com_fallback=fallback)
            else:
                fallback_error = result.error or "AutoLISP reported success but no non-empty PDF was created"
                self._remove_staging_file(staging)
                failure = self._exception_result(
                    com_error,
                    operation="drawing.plot_pdf",
                    parameters=requested,
                    system_call="AutoCAD.Plot.PlotToFile",
                    file_path=str(output),
                    recommended_action="verify_plot_device_output_path_and_autocad_state",
                )
                failure.payload["dispatcher_fallback"] = {
                    "error": fallback_error,
                    "result": result.to_dict(),
                }
                failure.payload["viewer_guard"] = self._stop_output_viewer_guard(
                    viewer_guard_state
                )
                return failure

        viewer_guard = self._stop_output_viewer_guard(viewer_guard_state)
        actual["viewer_guard"] = viewer_guard

        try:
            pdf_page = self._read_pdf_page(str(staging))
        except Exception as exc:
            self._remove_staging_file(staging)
            return self._exception_result(
                exc,
                operation="drawing.plot_pdf.verify",
                parameters=requested,
                system_call="PyMuPDF.open/page.mediabox",
                file_path=str(Path(path).expanduser().resolve()),
                error_code="E_PLOT_PAGE_MISMATCH",
                recommended_action="inspect_generated_pdf_and_plot_device",
            )

        pdf_page["path"] = str(output)
        actual = {
            **actual,
            "path": str(output),
            "staged_path": str(staging),
            "pdf_page": pdf_page,
        }
        differences = self._plot_differences(requested, actual)
        payload = {
            **actual,
            "verified": not differences,
            "requested": requested,
            "actual": actual,
            "diff": differences,
        }
        if differences:
            self._remove_staging_file(staging)
            return CommandResult(
                ok=False,
                error="Generated PDF page or plot settings do not match the requested configuration",
                error_code="E_PLOT_PAGE_MISMATCH",
                recoverable=False,
                recommended_action="inspect_plot_device_media_and_retry",
                payload=payload,
            )
        try:
            publication = self._publish_staged_file(staging, output)
        except Exception as exc:
            staging_removed = self._remove_staging_file(staging)
            failure = self._exception_result(
                exc,
                operation="drawing.plot_pdf.publish",
                parameters=requested,
                system_call="os.replace",
                file_path=str(output),
                error_code="E_OUTPUT_LOCKED",
                recommended_action="close_the_file_owner_and_retry",
            )
            failure.payload["staging_removed"] = staging_removed
            return failure
        actual.pop("staged_path", None)
        payload.pop("staged_path", None)
        payload["actual"] = actual
        payload["publication"] = publication
        return CommandResult(ok=True, payload=payload)

    @sta_async_method("drawing.audit", idempotent=True, timeout=60.0)
    async def drawing_audit(
        self,
        limit=50,
        include_entities=True,
        changed_only=False,
        layer=None,
        space="model",
        rules=None,
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

        semantics = self._semantic_store()
        entities = [
            {**entity, **({"semantics": semantics[str(entity.get("handle"))]} if str(entity.get("handle")) in semantics else {})}
            for entity in entities
        ]

        self._audit_revision += 1
        payload, fingerprints = build_audit(
            entities,
            limit=limit,
            include_entities=include_entities,
            changed_only=changed_only,
            previous_fingerprints=self._audit_fingerprints,
            revision=self._audit_revision,
            space=space.lower(),
            geometry_rules=rules,
        )
        self._audit_fingerprints = fingerprints
        payload["source"] = source
        try:
            import pythoncom
            import win32com.client

            document = self._get_autocad_application().ActiveDocument
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
        self,
        path,
        paper="A4",
        orientation="auto",
        plot_style="monochrome.ctb",
        dpi=150,
        force=True,
        background="white",
        plot_type="extents",
        normalize_framing=False,
        framing_fill=0.82,
        visual_style=None,
        preserve_visual_style=True,
    ) -> CommandResult:
        output = Path(path).expanduser().resolve()
        if output.suffix.lower() != ".png":
            return CommandResult(ok=False, error="AutoCAD preview output must use a .png extension")
        if str(background).lower() != "white":
            return CommandResult(ok=False, error="AutoCAD preview currently supports a white background")
        if int(dpi) < 72 or int(dpi) > 600:
            return CommandResult(ok=False, error="Preview DPI must be between 72 and 600")
        if output.exists() and not force:
            return CommandResult(
                ok=False,
                error=f"Preview already exists: {output}",
                error_code="E_OUTPUT_EXISTS",
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        staging = output.with_name(f".{output.stem}.{uuid.uuid4().hex}.tmp.png")
        previous_visual_style = None
        requested_visual_style = None
        response: CommandResult | None = None
        try:
            if visual_style is not None:
                # Validate again at the backend boundary for direct callers.
                from autocad_mcp.visual_styles import normalize_visual_style

                try:
                    requested_visual_style = normalize_visual_style(visual_style)
                except ValueError as exc:
                    response = CommandResult(
                        ok=False,
                        error=str(exc),
                        error_code="E_VISUAL_STYLE_NOT_ALLOWED",
                        recoverable=False,
                        recommended_action="choose_a_builtin_visual_style_and_retry",
                    )
                    return response
                try:
                    previous_visual_style = self._read_visual_style()
                except Exception as exc:
                    response = self._exception_result(
                        exc,
                        operation="drawing.render_preview.visual_style.read",
                        parameters={"visual_style": requested_visual_style},
                        system_call="AutoCAD.Document.GetVariable(VSCURRENT)",
                    )
                    return response
                applied_style = await self.apply_presentation_style({}, requested_visual_style)
                if not applied_style.ok:
                    applied_style.error_code = applied_style.error_code or "E_VISUAL_STYLE_APPLY_FAILED"
                    applied_style.recoverable = True
                    applied_style.recommended_action = "retry_after_autocad_becomes_idle"
                    response = applied_style
                    return response
            plot_options = {
                "paper": paper,
                "orientation": orientation,
                "plot_style": plot_style,
                "dpi": int(dpi),
                "plot_type": plot_type,
                "normalize_framing": normalize_framing,
                "framing_fill": framing_fill,
            }
            if requested_visual_style is not None:
                plot_options["visual_style"] = requested_visual_style
            raster = self._plot_png_via_com(staging, **plot_options)
            try:
                digest = geometry_digest(self._collect_entities_via_com())
            except Exception:
                digest = None
            try:
                publication = self._publish_staged_file(staging, output)
            except Exception as exc:
                staging_removed = self._remove_staging_file(staging)
                failure = self._exception_result(
                    exc,
                    operation="drawing.render_preview.publish",
                    parameters={
                        "path": str(output), "paper": paper, "orientation": orientation,
                        "dpi": dpi, "force": force,
                    },
                    system_call="os.replace",
                    file_path=str(output),
                    error_code="E_OUTPUT_LOCKED",
                    recommended_action="close_the_file_owner_and_retry",
                )
                failure.payload["staging_removed"] = staging_removed
                response = failure
                return response
            raster["path"] = str(output)
            raster["publication"] = publication
            response = CommandResult(
                ok=True,
                payload={
                    **raster,
                    "geometry_digest": digest,
                    "force_overwrite": bool(force),
                    "requested_visual_style": requested_visual_style,
                    # This is provisional until the finally block reads back
                    # the user's original style after restoration.
                    "visual_style_preserved": requested_visual_style is None,
                },
            )
            return response
        except Exception as exc:
            self._remove_staging_file(staging)
            response = self._exception_result(
                exc,
                operation="drawing.render_preview",
                parameters={
                    "path": str(output), "paper": paper, "orientation": orientation,
                    "dpi": dpi, "plot_type": plot_type,
                    "normalize_framing": normalize_framing,
                },
                system_call="AutoCAD.Plot.PlotToFile",
                file_path=str(output),
            )
            return response
        finally:
            if previous_visual_style and preserve_visual_style:
                restoration: dict[str, Any] = {
                    "attempted": True,
                    "expected": previous_visual_style.get("visual_style"),
                    "verified": False,
                }
                try:
                    old_style = previous_visual_style.get("visual_style")
                    if old_style:
                        restore_result = await self.apply_presentation_style({}, str(old_style))
                        restoration["result"] = restore_result.to_dict()
                        if restore_result.ok:
                            actual_restore = self._read_visual_style()
                            restoration["actual"] = actual_restore.get("visual_style")
                            try:
                                expected_canonical = normalize_visual_style(str(old_style))
                                actual_canonical = normalize_visual_style(
                                    str(actual_restore.get("visual_style", ""))
                                )
                                restoration["verified"] = (
                                    expected_canonical == actual_canonical
                                )
                            except ValueError:
                                restoration["verified"] = str(
                                    actual_restore.get("visual_style", "")
                                ).casefold() == str(old_style).casefold()
                except Exception as restore_error:
                    restoration["error"] = str(restore_error)
                if response is not None:
                    payload = response.payload if isinstance(response.payload, dict) else {}
                    payload["visual_style_restore"] = restoration
                    payload["visual_style_preserved"] = bool(restoration["verified"])
                    response.payload = payload
                    if not restoration["verified"]:
                        response.ok = False
                        response.error = (
                            "AutoCAD preview was written, but the user's visual style "
                            "could not be verified after restoration"
                        )
                        response.error_code = "E_PRESENTATION_RESTORE_FAILED"
                        response.recoverable = False
                        response.recommended_action = (
                            "restore_the_previous_visual_style_manually_before_new_CAD_operations"
                        )

    @staticmethod
    def _correct_png_orientation(
        path: Path, selected_orientation: str
    ) -> tuple[int, int, bool]:
        """Normalize raster orientation after AutoCAD device-specific rotation."""
        from PIL import Image

        with Image.open(path) as image:
            width, height = image.size
            needs_rotation = (
                selected_orientation == "landscape" and width < height
            ) or (
                selected_orientation == "portrait" and width > height
            )
            if not needs_rotation:
                return width, height, False
            rotated = image.transpose(Image.Transpose.ROTATE_90)
            rotated.save(path, format="PNG")
            return rotated.width, rotated.height, True

    @staticmethod
    def _normalize_png_content_framing(path: Path, fill_ratio: float = 0.82) -> dict:
        """Center and scale native plot pixels without capturing any desktop window."""
        from PIL import Image, ImageChops

        if fill_ratio <= 0.1 or fill_ratio >= 0.95:
            raise ValueError("framing_fill must be between 0.1 and 0.95")
        with Image.open(path) as source:
            image = source.convert("RGB")
        background = Image.new("RGB", image.size, "white")
        difference = ImageChops.difference(image, background).convert("L")
        mask = difference.point(lambda value: 255 if value > 7 else 0)
        bounds = mask.getbbox()
        if not bounds:
            raise RuntimeError("Native product view contains no non-background pixels")
        padding = 2
        left = max(0, bounds[0] - padding)
        top = max(0, bounds[1] - padding)
        right = min(image.width, bounds[2] + padding)
        bottom = min(image.height, bounds[3] + padding)
        crop = image.crop((left, top, right, bottom))
        target_width = max(1, int(round(image.width * fill_ratio)))
        target_height = max(1, int(round(image.height * fill_ratio)))
        scale = min(target_width / crop.width, target_height / crop.height)
        resized = crop.resize(
            (
                max(1, int(round(crop.width * scale))),
                max(1, int(round(crop.height * scale))),
            ),
            Image.Resampling.LANCZOS,
        )
        normalized = Image.new("RGB", image.size, "white")
        paste = (
            (image.width - resized.width) // 2,
            (image.height - resized.height) // 2,
        )
        normalized.paste(resized, paste)
        normalized.save(path, format="PNG")
        return {
            "applied": True,
            "source_bbox_pixels": list(bounds),
            "source_crop_pixels": [left, top, right, bottom],
            "scale": round(scale, 6),
            "fill_ratio": fill_ratio,
            "output_bbox_pixels": [
                paste[0], paste[1], paste[0] + resized.width, paste[1] + resized.height
            ],
            "desktop_capture_used": False,
            "native_plot_pixels_preserved": True,
        }

    @sta_sync_method("drawing.plot_png", timeout=120.0)
    def _plot_png_via_com(
        self, output: Path, *, paper: str, orientation: str, plot_style: str,
        dpi: int, plot_type: str = "extents", normalize_framing: bool = False,
        framing_fill: float = 0.82, visual_style: str | None = None,
    ) -> dict:
        import pythoncom
        import win32com.client
        from PIL import Image

        application = self._get_autocad_application()
        document = application.ActiveDocument
        layout = document.ActiveLayout
        device = "PublishToWeb PNG.pc3"
        saved = {}
        for name in (
            "ConfigName", "CanonicalMediaName", "PaperUnits", "PlotType",
            "UseStandardScale", "StandardScale", "CenterPlot", "PlotRotation",
            "StyleSheet", "PlotWithPlotStyles",
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
            layout.ConfigName = device
            layout.RefreshPlotDeviceInfo()
            selected_orientation = str(orientation).lower()
            if selected_orientation == "auto":
                ext_min = list(document.GetVariable("EXTMIN"))
                ext_max = list(document.GetVariable("EXTMAX"))
                selected_orientation = (
                    "landscape"
                    if abs(ext_max[0] - ext_min[0]) >= abs(ext_max[1] - ext_min[1])
                    else "portrait"
                )

            paper_sizes = {
                "A0": (841.0, 1189.0), "A1": (594.0, 841.0),
                "A2": (420.0, 594.0), "A3": (297.0, 420.0), "A4": (210.0, 297.0),
            }
            paper_width, paper_height = paper_sizes.get(str(paper).upper(), paper_sizes["A4"])
            if selected_orientation == "landscape":
                paper_width, paper_height = max(paper_width, paper_height), min(paper_width, paper_height)
            else:
                paper_width, paper_height = min(paper_width, paper_height), max(paper_width, paper_height)
            target_width = paper_width / 25.4 * dpi
            target_height = paper_height / 25.4 * dpi

            candidates = []
            for media in list(layout.GetCanonicalMediaNames()):
                match = re.search(r"(\d+(?:\.\d+)?)\D+x\D*(\d+(?:\.\d+)?)\D*Pixels", str(media), re.I)
                if not match:
                    continue
                width, height = float(match.group(1)), float(match.group(2))
                aspect_error = abs(math.log((width / height) / (target_width / target_height)))
                size_error = abs(math.log((width * height) / (target_width * target_height)))
                candidates.append((aspect_error * 8 + size_error, str(media), width, height))
            if not candidates:
                raise RuntimeError("AutoCAD PNG plot device exposes no usable raster media")
            _, media, _, _ = min(candidates, key=lambda item: item[0])
            layout.CanonicalMediaName = media
            selected_plot_type = str(plot_type).lower()
            if selected_plot_type not in {"extents", "display"}:
                raise ValueError("plot_type must be extents or display")
            layout.PlotType = 0 if selected_plot_type == "display" else 1
            layout.UseStandardScale = True
            layout.StandardScale = 0
            layout.CenterPlot = True
            layout.PlotRotation = 1 if selected_orientation == "landscape" else 0
            if plot_style:
                try:
                    layout.StyleSheet = plot_style
                    layout.PlotWithPlotStyles = True
                except Exception:
                    pass
            else:
                layout.PlotWithPlotStyles = False

            output.unlink(missing_ok=True)
            plotted = bool(document.Plot.PlotToFile(str(output), device))
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                if output.is_file() and output.stat().st_size > 0:
                    break
                time.sleep(0.1)
            if not output.is_file() or output.stat().st_size <= 0:
                raise RuntimeError(f"AutoCAD PNG PlotToFile returned {plotted} without an output file")
            width, height, orientation_corrected = self._correct_png_orientation(
                output, selected_orientation
            )
            framing_normalization = (
                self._normalize_png_content_framing(output, framing_fill)
                if normalize_framing else None
            )
            return {
                "path": str(output),
                "document_path": str(document.FullName or document.Name),
                "format": "png",
                "renderer": "autocad-native-png-plot",
                "paper": paper,
                "orientation": selected_orientation,
                "orientation_corrected": orientation_corrected,
                "plot_style": plot_style,
                "media": media,
                "requested_dpi": dpi,
                "dpi": round(width / (paper_width / 25.4), 3),
                "background": "white",
                "width": width,
                "height": height,
                "bytes": output.stat().st_size,
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "device": device,
                "plot_type": selected_plot_type,
                "framing_normalization": framing_normalization,
                "visual_style": visual_style,
                "shaded_display_requested": visual_style is not None,
                "material_render_verified": False,
                "render_truth": "native_plot_style_readback_only",
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

    @staticmethod
    def _read_pdf_page(path: str) -> dict:
        import fitz

        output = Path(path).expanduser().resolve()
        document = fitz.open(str(output))
        try:
            if document.page_count != 1:
                raise ValueError(f"Expected one PDF page, received {document.page_count}")
            page = document.load_page(0)
            rect = page.rect
            width_points, height_points = float(rect.width), float(rect.height)
        finally:
            document.close()
        width_mm = width_points * 25.4 / 72.0
        height_mm = height_points * 25.4 / 72.0
        standard = {
            "A0": (841.0, 1189.0),
            "A1": (594.0, 841.0),
            "A2": (420.0, 594.0),
            "A3": (297.0, 420.0),
            "A4": (210.0, 297.0),
            "LETTER": (215.9, 279.4),
        }
        normalized = sorted((width_mm, height_mm))
        detected_paper = min(
            standard,
            key=lambda name: max(
                abs(normalized[0] - min(standard[name])),
                abs(normalized[1] - max(standard[name])),
            ),
        )
        expected = sorted(standard[detected_paper])
        paper_error_mm = max(
            abs(normalized[0] - expected[0]), abs(normalized[1] - expected[1])
        )
        return {
            "path": str(output),
            "page_count": 1,
            "mediabox_points": [round(width_points, 3), round(height_points, 3)],
            "width_mm": round(width_mm, 3),
            "height_mm": round(height_mm, 3),
            "orientation": "landscape" if width_mm >= height_mm else "portrait",
            "detected_paper": detected_paper,
            "paper_error_mm": round(paper_error_mm, 3),
            "verified_standard_paper": paper_error_mm <= 2.0,
        }

    @staticmethod
    def _plot_differences(requested: dict, actual: dict) -> list[dict]:
        differences = []
        pdf_page = actual.get("pdf_page") or {}

        def compare(path: str, expected, received) -> None:
            if str(expected).casefold() != str(received).casefold():
                differences.append(
                    {"path": path, "requested": expected, "actual": received}
                )

        compare("path", requested["path"], actual.get("path"))
        compare("paper", requested["paper"], pdf_page.get("detected_paper"))
        if requested["orientation"] != "auto":
            compare("orientation", requested["orientation"], pdf_page.get("orientation"))
        compare("scale_mode", requested["scale_mode"], actual.get("scale_mode"))
        if requested["scale_mode"] == "fixed":
            compare("scale", requested["scale"], actual.get("scale"))
        if not pdf_page.get("verified_standard_paper", False):
            differences.append(
                {
                    "path": "pdf_page.mediabox",
                    "requested": requested["paper"],
                    "actual": pdf_page,
                }
            )
        return differences

    async def drawing_get_variables(self, names: list[str] | None = None) -> CommandResult:
        if names:
            # Strip $ prefix for AutoCAD compatibility (ezdxf uses $ACADVER, AutoCAD uses ACADVER)
            clean_names = [n.lstrip("$") for n in names]
            names_str = ";".join(clean_names)
        else:
            names_str = ""
        return await self._dispatch("drawing-get-variables", {"names_str": names_str})

    @sta_async_method("drawing.set_variables", idempotent=True)
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

            document = self._get_autocad_application().ActiveDocument
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
            context = await self.document_context()
            if context.ok and isinstance(result.payload, dict):
                result.payload.update(
                    {
                        **context.payload,
                        "requested_path": str(Path(path).expanduser().resolve()),
                        "diff": [],
                    }
                )
        return result

    @sta_sync_method("drawing.save", timeout=60.0)
    def _save_via_com(self, path: str | None, file_type: int | None = None) -> dict:
        import pythoncom
        import win32com.client

        application = self._get_autocad_application()
        document = application.ActiveDocument
        identity = self._bind_document(document)
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
        self._doc_ids_by_key[self._document_key(document)] = identity["doc_id"]
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

    @sta_sync_method("drawing.export_dxf", timeout=60.0)
    def _export_dxf_via_com(self, path: str) -> dict:
        import pythoncom
        import win32com.client

        output = Path(path).expanduser().resolve()
        if output.suffix.lower() != ".dxf":
            raise ValueError("DXF export path must use a .dxf extension")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.unlink(missing_ok=True)

        application = self._get_autocad_application()
        document = application.ActiveDocument
        active_before = str(document.FullName or document.Name)
        selection_name = f"MCP_EXPORT_{uuid.uuid4().hex[:8]}"
        selection = document.SelectionSets.Add(selection_name)
        selection_count = 0
        try:
            selection.Select(5)  # acSelectionSetAll
            selection_count = int(selection.Count)
            document.Export(str(output.with_suffix("")), "DXF", selection)
        finally:
            try:
                selection.Delete()
            except Exception:
                pass
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not output.is_file():
            time.sleep(0.1)
        if not output.is_file() or output.stat().st_size <= 0:
            raise RuntimeError(f"AutoCAD Export did not create a non-empty DXF: {output}")
        active_after = str(document.FullName or document.Name)
        if os.path.normcase(active_after) != os.path.normcase(active_before):
            raise RuntimeError(
                f"DXF export changed the active document: {active_before} -> {active_after}"
            )
        return {
            "path": str(output),
            "format": "dxf",
            "renderer": "autocad-com-export",
            "active_document": active_after,
            "active_document_preserved": True,
            "selection_count": selection_count,
        }

    @sta_sync_method("entity.collect", idempotent=True, timeout=60.0)
    def _collect_entities_via_com(self, layer=None, space="model") -> list[dict]:
        import pythoncom
        import win32com.client

        application = self._get_autocad_application()
        document = application.ActiveDocument
        collection = document.PaperSpace if space.lower() == "paper" else document.ModelSpace
        entities = []
        for index in range(int(collection.Count)):
            entity = collection.Item(index)
            if layer and str(_com_value(entity, "Layer", "0")) != layer:
                continue
            entities.append(_com_entity_to_dict(entity))
        return entities

    @sta_sync_method("entity.get", idempotent=True)
    def _get_entity_via_com(self, entity_id: str) -> dict:
        import pythoncom
        import win32com.client

        document = self._get_autocad_application().ActiveDocument
        entity = document.HandleToObject(str(entity_id))
        return _com_entity_to_dict(entity)

    @sta_sync_method("drawing.plot_preview", timeout=120.0)
    def _plot_preview_via_com(
        self, path, paper, orientation, plot_style, scale_mode="fit", scale="fit", center=True
    ) -> dict:
        import pythoncom
        import win32com.client

        output = Path(path).expanduser().resolve()
        if output.suffix.lower() != ".pdf":
            raise ValueError("Full AutoCAD preview output must use a .pdf extension")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.unlink(missing_ok=True)

        application = self._get_autocad_application()
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

    def _rasterize_pdf_to_png(
        self, pdf_path: Path, png_path: Path, *, dpi: int, background: str
    ) -> dict:
        import fitz

        if background != "white":
            raise ValueError("Only a white preview background is currently supported")
        document = fitz.open(str(pdf_path))
        try:
            if document.page_count < 1:
                raise RuntimeError("Preview PDF has no pages")
            page = document.load_page(0)
            scale = float(dpi) / 72.0
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            pixmap.save(str(png_path))
            width, height = int(pixmap.width), int(pixmap.height)
        finally:
            document.close()
        if not png_path.is_file() or png_path.stat().st_size <= 0:
            raise RuntimeError("PNG rasterizer did not create a non-empty image")
        return {
            "path": str(png_path),
            "format": "png",
            "dpi": int(dpi),
            "background": background,
            "width": width,
            "height": height,
            "bytes": png_path.stat().st_size,
            "sha256": hashlib.sha256(png_path.read_bytes()).hexdigest(),
        }

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

    @sta_sync_method("entity.create")
    def _create_entity_via_com(self, kind: str, params: dict, layer: str | None):
        pythoncom, win32com, document, modelspace = self._solid_context()
        target_layer = str(layer or "0")
        if target_layer != "0":
            try:
                document.Layers.Item(target_layer)
            except Exception as exc:
                raise LayerNotFoundError(
                    f"Layer does not exist: {target_layer}"
                ) from exc

        if kind == "line":
            entity = modelspace.AddLine(
                self._solid_point(pythoncom, win32com, [params["x1"], params["y1"]]),
                self._solid_point(pythoncom, win32com, [params["x2"], params["y2"]]),
            )
        elif kind == "circle":
            entity = modelspace.AddCircle(
                self._solid_point(pythoncom, win32com, [params["cx"], params["cy"]]),
                float(params["radius"]),
            )
        elif kind in {"polyline", "rectangle"}:
            points = params["points"]
            coordinates = [float(value) for point in points for value in point[:2]]
            entity = modelspace.AddLightWeightPolyline(
                win32com.client.VARIANT(
                    pythoncom.VT_ARRAY | pythoncom.VT_R8, coordinates
                )
            )
            entity.Closed = bool(params.get("closed", False))
        elif kind == "arc":
            entity = modelspace.AddArc(
                self._solid_point(pythoncom, win32com, [params["cx"], params["cy"]]),
                float(params["radius"]),
                math.radians(float(params["start_angle"])),
                math.radians(float(params["end_angle"])),
            )
        elif kind == "ellipse":
            major_axis = [
                float(params["major_x"]) - float(params["cx"]),
                float(params["major_y"]) - float(params["cy"]),
                0.0,
            ]
            entity = modelspace.AddEllipse(
                self._solid_point(pythoncom, win32com, [params["cx"], params["cy"]]),
                self._solid_point(pythoncom, win32com, major_axis),
                float(params["ratio"]),
            )
        elif kind == "mtext":
            entity = modelspace.AddMText(
                self._solid_point(pythoncom, win32com, [params["x"], params["y"]]),
                float(params["width"]),
                str(params["text"]),
            )
            entity.Height = float(params.get("height", 2.5))
        elif kind == "text":
            entity = modelspace.AddText(
                str(params["text"]),
                self._solid_point(pythoncom, win32com, [params["x"], params["y"]]),
                float(params.get("height", 2.5)),
            )
            entity.Rotation = math.radians(float(params.get("rotation", 0.0)))
        else:
            raise ValueError(f"Unsupported COM entity kind: {kind}")

        entity.Layer = target_layer
        entity.Update()
        return CommandResult(
            ok=True,
            payload={
                "entity_type": _com_entity_to_dict(entity)["type"],
                "handle": str(entity.Handle),
                "renderer": "autocad-com",
            },
        )

    async def _create_with_com_fallback(
        self, kind: str, params: dict, layer: str | None, command: str, dispatch_params: dict
    ) -> CommandResult:
        try:
            result = self._create_entity_via_com(kind, params, layer)
            self._auto_fit_view()
            return result
        except LayerNotFoundError as exc:
            return CommandResult(
                ok=False,
                error=str(exc),
                error_code="E_LAYER_NOT_FOUND",
                recoverable=False,
                recommended_action="create_or_select_an_existing_layer",
                payload={"operation": command, "layer": str(layer), "entity_created": False},
            )
        except Exception as com_error:
            result = await self._dispatch(command, dispatch_params)
            _, details = exception_context(
                com_error,
                operation=command,
                parameters=dispatch_params,
                system_call=f"AutoCAD.ModelSpace.{kind}",
            )
            if result.ok and isinstance(result.payload, dict):
                result.payload["renderer"] = "autolisp-fallback"
                result.payload["com_fallback"] = details
                return result
            failure = CommandResult(
                ok=False,
                error=f"{command} failed in both COM and dispatcher transports",
                error_code="E_SYSTEM_CALL_FAILED",
                payload={"com": details, "dispatcher": result.to_dict()},
            )
            return failure

    async def create_line(self, x1, y1, x2, y2, layer=None) -> CommandResult:
        params = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
        return await self._create_with_com_fallback(
            "line", params, layer, "create-line", {**params, "layer": layer}
        )

    async def create_circle(self, cx, cy, radius, layer=None) -> CommandResult:
        params = {"cx": cx, "cy": cy, "radius": radius}
        return await self._create_with_com_fallback(
            "circle", params, layer, "create-circle", {**params, "layer": layer}
        )

    async def create_polyline(self, points, closed=False, layer=None) -> CommandResult:
        pts_str = ";".join(f"{p[0]},{p[1]}" for p in points)
        return await self._create_with_com_fallback(
            "polyline",
            {"points": points, "closed": closed},
            layer,
            "create-polyline",
            {"points_str": pts_str, "closed": "1" if closed else "0", "layer": layer},
        )

    async def create_rectangle(self, x1, y1, x2, y2, layer=None) -> CommandResult:
        params = {
            "points": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
            "closed": True,
        }
        return await self._create_with_com_fallback(
            "rectangle", params, layer, "create-rectangle",
            {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "layer": layer},
        )

    async def create_arc(self, cx, cy, radius, start_angle, end_angle, layer=None) -> CommandResult:
        params = {"cx": cx, "cy": cy, "radius": radius, "start_angle": start_angle, "end_angle": end_angle}
        return await self._create_with_com_fallback(
            "arc", params, layer, "create-arc", {**params, "layer": layer}
        )

    async def create_ellipse(self, cx, cy, major_x, major_y, ratio, layer=None) -> CommandResult:
        params = {"cx": cx, "cy": cy, "major_x": major_x, "major_y": major_y, "ratio": ratio}
        return await self._create_with_com_fallback(
            "ellipse", params, layer, "create-ellipse", {**params, "layer": layer}
        )

    async def create_mtext(self, x, y, width, text, height=2.5, layer=None) -> CommandResult:
        params = {"x": x, "y": y, "width": width, "text": text, "height": height}
        return await self._create_with_com_fallback(
            "mtext", params, layer, "create-mtext",
            {**params, "text": encode_autocad_text(text), "layer": layer},
        )

    async def create_batch(
        self,
        entities: list[dict],
        continue_on_error: bool = False,
        atomic: bool = False,
        strict: bool = True,
    ) -> CommandResult:
        self._suspend_auto_fit += 1
        try:
            if atomic:
                begin = await self._dispatch("transaction-begin", {})
                if not begin.ok:
                    return CommandResult(
                        ok=False,
                        error=f"Unable to begin AutoCAD transaction: {begin.error}",
                        error_code="E_TRANSACTION_BEGIN",
                    )
            result = await super().create_batch(
                entities, continue_on_error, atomic=False, strict=strict
            )
            if atomic:
                transaction = await self._dispatch(
                    "transaction-commit" if result.ok else "transaction-rollback", {}
                )
                if isinstance(result.payload, dict):
                    result.payload["atomic"] = True
                    result.payload["transaction"] = transaction.to_dict()
                    if not result.ok and transaction.ok:
                        result.error_code = "E_BATCH_ROLLED_BACK"
                        result.payload["rolled_back"] = list(
                            reversed(result.payload.get("created_handles", []))
                        )
                if not transaction.ok:
                    return CommandResult(
                        ok=False,
                        payload=result.payload,
                        error=f"AutoCAD transaction finalization failed: {transaction.error}",
                        error_code="E_TRANSACTION_FINALIZE",
                    )
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
        if layer:
            layer_check = await self.layer_exists(str(layer))
            if not layer_check.ok:
                return layer_check
            if not layer_check.payload.get("exists"):
                return CommandResult(
                    ok=False,
                    error=f"Layer does not exist: {layer}",
                    error_code="E_LAYER_NOT_FOUND",
                    recoverable=False,
                    recommended_action="create_or_select_an_existing_layer",
                    payload={"layer": str(layer), "entity_created": False},
                )
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
        try:
            payload = self._get_entity_via_com(entity_id)
            semantics = self._semantic_store().get(str(entity_id))
            if semantics:
                payload["semantics"] = dict(semantics)
            return CommandResult(ok=True, payload=payload)
        except Exception as com_error:
            result = await self._dispatch("entity-get", {"entity_id": entity_id})
            if result.ok and isinstance(result.payload, dict):
                result.payload["warning"] = f"COM query unavailable: {com_error}"
            return result

    async def entity_erase(self, entity_id) -> CommandResult:
        result = await self._dispatch("entity-erase", {"entity_id": entity_id})
        if result.ok:
            self._semantic_store().pop(str(entity_id), None)
        return result

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

    async def entity_trim(self, cutters, targets) -> CommandResult:
        before = {}
        for target in targets:
            try:
                before[str(target["id"])] = self._get_entity_via_com(str(target["id"]))
            except Exception:
                pass
        cutters_str = ";".join(str(handle) for handle in cutters)
        targets_str = ";".join(
            f"{target['id']}@{target['pick'][0]},{target['pick'][1]}" for target in targets
        )
        result = await self._dispatch(
            "entity-trim", {"cutters_str": cutters_str, "targets_str": targets_str}
        )
        return self._verify_entity_changes(result, before, "TRIM")

    async def entity_extend(self, boundaries, targets) -> CommandResult:
        before = {}
        for target in targets:
            try:
                before[str(target["id"])] = self._get_entity_via_com(str(target["id"]))
            except Exception:
                pass
        boundaries_str = ";".join(str(handle) for handle in boundaries)
        targets_str = ";".join(
            f"{target['id']}@{target['pick'][0]},{target['pick'][1]}" for target in targets
        )
        result = await self._dispatch(
            "entity-extend", {"boundaries_str": boundaries_str, "targets_str": targets_str}
        )
        return self._verify_entity_changes(result, before, "EXTEND")

    async def entity_break(self, entity_id, point1, point2) -> CommandResult:
        return await self._dispatch(
            "entity-break",
            {
                "entity_id": entity_id,
                "x1": point1[0],
                "y1": point1[1],
                "x2": point2[0],
                "y2": point2[1],
            },
        )

    async def entity_join(self, entity_ids, tolerance=0.0) -> CommandResult:
        try:
            return CommandResult(
                ok=True,
                payload=self._join_lines_via_com(entity_ids, float(tolerance)),
            )
        except Exception as com_error:
            before_count = (await self.entity_count()).payload.get("count")
            result = await self._dispatch(
                "entity-join",
                {
                    "entity_ids_str": ";".join(str(handle) for handle in entity_ids),
                    "tolerance": tolerance,
                },
            )
            after_count = (await self.entity_count()).payload.get("count")
            if result.ok and before_count is not None and after_count is not None and after_count >= before_count:
                return CommandResult(
                    ok=False,
                    error=f"JOIN command completed without reducing entity count; COM join failed: {com_error}",
                    error_code="E_VALIDATION_FAILED",
                )
            if result.ok and isinstance(result.payload, dict):
                result.payload.update(
                    verified=True,
                    entity_count_before=before_count,
                    entity_count_after=after_count,
                    warning=f"COM line join unavailable: {com_error}",
                )
            return result

    async def entity_constrain(self, constraint, entity_ids) -> CommandResult:
        result = await self._dispatch(
            "entity-constrain",
            {
                "constraint": constraint,
                "entity_ids_str": ";".join(str(handle) for handle in entity_ids),
            },
        )
        if result.ok and isinstance(result.payload, dict):
            result.payload.update(
                verified=False,
                verification="AutoCAD accepted the native GEOMCONSTRAINT command; the ActiveX API does not expose a portable constraint collection.",
            )
        return result

    def _verify_entity_changes(
        self, result: CommandResult, before: dict[str, dict], operation: str
    ) -> CommandResult:
        if not result.ok:
            return result
        if not before:
            if isinstance(result.payload, dict):
                result.payload.update(
                    verified=False,
                    verification=f"{operation} command accepted; target geometry verification requires full AutoCAD COM.",
                )
            return result
        changed = []
        for handle, previous in before.items():
            try:
                current = self._get_entity_via_com(handle)
            except Exception:
                changed.append(handle)
                continue
            if current != previous:
                changed.append(handle)
        if not changed:
            return CommandResult(
                ok=False,
                error=f"{operation} command completed but no target geometry changed",
                error_code="E_VALIDATION_FAILED",
                payload={"targets": list(before)},
            )
        payload = result.payload if isinstance(result.payload, dict) else {}
        payload.update(verified=True, changed_handles=changed)
        result.payload = payload
        return result

    @sta_sync_method("entity.join")
    def _join_lines_via_com(self, entity_ids, tolerance: float) -> dict:
        pythoncom, win32com, document, modelspace = self._solid_context()
        entities = [document.HandleToObject(str(handle)) for handle in entity_ids]
        if len(entities) < 2 or any(str(entity.ObjectName) != "AcDbLine" for entity in entities):
            raise ValueError("Deterministic COM join currently requires at least two LINE entities")
        threshold = max(float(tolerance), 0.000001)
        segments = [
            [_com_point(entity.StartPoint)[:2], _com_point(entity.EndPoint)[:2]]
            for entity in entities
        ]

        endpoint_candidates = [point for segment in segments for point in segment]
        start = next(
            (
                point
                for point in endpoint_candidates
                if sum(_distance_2d(point, other) <= threshold for other in endpoint_candidates) == 1
            ),
            segments[0][0],
        )
        vertices = [start]
        unused = set(range(len(segments)))
        current = start
        while unused:
            match = None
            for index in unused:
                first, second = segments[index]
                if _distance_2d(current, first) <= threshold:
                    match = index, second
                    break
                if _distance_2d(current, second) <= threshold:
                    match = index, first
                    break
            if match is None:
                raise ValueError("LINE entities do not form one connected chain within tolerance")
            index, current = match
            unused.remove(index)
            vertices.append(current)

        closed = len(vertices) > 2 and _distance_2d(vertices[0], vertices[-1]) <= threshold
        if closed:
            vertices.pop()
        base_x = vertices[-1][0] - vertices[0][0]
        base_y = vertices[-1][1] - vertices[0][1]
        base_length = math.hypot(base_x, base_y)
        collinear = not closed and base_length > threshold and all(
            abs(
                base_x * (point[1] - vertices[0][1])
                - base_y * (point[0] - vertices[0][0])
            )
            <= threshold * base_length
            for point in vertices[1:-1]
        )

        if collinear:
            joined = entities[0]
            joined.StartPoint = self._solid_point(pythoncom, win32com, vertices[0])
            joined.EndPoint = self._solid_point(pythoncom, win32com, vertices[-1])
            for entity in entities[1:]:
                entity.Delete()
            entity_type = "LINE"
        else:
            coordinates = [coordinate for point in vertices for coordinate in point[:2]]
            variant = win32com.client.VARIANT(
                pythoncom.VT_ARRAY | pythoncom.VT_R8, coordinates
            )
            joined = modelspace.AddLightWeightPolyline(variant)
            joined.Layer = str(entities[0].Layer)
            joined.Closed = bool(closed)
            for entity in entities:
                entity.Delete()
            entity_type = "LWPOLYLINE"
        joined.Update()
        self._auto_fit_view()
        return {
            "handle": str(joined.Handle),
            "entity_type": entity_type,
            "joined_handles": [str(handle) for handle in entity_ids],
            "vertex_count": len(vertices),
            "closed": closed,
            "verified": True,
            "renderer": "autocad-com",
        }

    # --- Native 3D solid operations ---

    @staticmethod
    def _solid_point(pythoncom, win32com, value):
        coordinates = [float(item) for item in list(value)[:3]]
        while len(coordinates) < 3:
            coordinates.append(0.0)
        return win32com.client.VARIANT(
            pythoncom.VT_ARRAY | pythoncom.VT_R8, coordinates
        )

    def _solid_context(self):
        import pythoncom
        import win32com.client

        if not self._com_executor.in_executor_thread:
            raise RuntimeError("AutoCAD COM context requested outside the dedicated STA thread")
        application = self._get_autocad_application()
        document = application.ActiveDocument
        return pythoncom, win32com, document, document.ModelSpace

    def _solid_payload(self, entity, operation: str) -> dict:
        payload = _com_entity_to_dict(entity)
        payload.update(
            entity_type="3DSOLID",
            operation=operation,
            renderer="autocad-com",
            volume=_com_value(entity, "Volume"),
            centroid=_com_point(_com_value(entity, "Centroid")),
        )
        return {key: value for key, value in payload.items() if value is not None}

    @sta_sync_method("solid.region_from_profile")
    def _region_from_profile(self, profile_id: str):
        pythoncom, win32com, document, modelspace = self._solid_context()
        profile = document.HandleToObject(str(profile_id))
        curves = win32com.client.VARIANT(
            pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, [profile]
        )
        regions = list(modelspace.AddRegion(curves))
        if len(regions) != 1:
            for region in regions:
                try:
                    region.Delete()
                except Exception:
                    pass
            raise ValueError("Profile must create exactly one planar closed region")
        return pythoncom, win32com, document, modelspace, profile, regions[0]

    @sta_async_method("solid.create_box")
    async def solid_create_box(self, center, length, width, height, layer=None) -> CommandResult:
        try:
            if min(float(length), float(width), float(height)) <= 0:
                raise ValueError("Box length, width, and height must be positive")
            pythoncom, win32com, _, modelspace = self._solid_context()
            solid = modelspace.AddBox(
                self._solid_point(pythoncom, win32com, center),
                float(length),
                float(width),
                float(height),
            )
            if layer:
                solid.Layer = layer
            solid.Update()
            self._auto_fit_view()
            return CommandResult(ok=True, payload=self._solid_payload(solid, "box"))
        except Exception as exc:
            return self._exception_result(
                exc, operation="solid.create_box",
                parameters={"center": center, "length": length, "width": width, "height": height, "layer": layer},
                system_call="AutoCAD.ModelSpace.AddBox", error_code="E_SOLID_OPERATION",
            )

    @sta_async_method("solid.create_cylinder")
    async def solid_create_cylinder(self, base_center, radius, height, layer=None) -> CommandResult:
        try:
            if float(radius) <= 0 or float(height) == 0:
                raise ValueError("Cylinder radius must be positive and height must be non-zero")
            pythoncom, win32com, _, modelspace = self._solid_context()
            base = [float(item) for item in list(base_center)[:3]]
            while len(base) < 3:
                base.append(0.0)
            center = [base[0], base[1], base[2] + float(height) / 2]
            solid = modelspace.AddCylinder(
                self._solid_point(pythoncom, win32com, center), float(radius), float(height)
            )
            if layer:
                solid.Layer = layer
            solid.Update()
            self._auto_fit_view()
            payload = self._solid_payload(solid, "cylinder")
            payload.update(requested_base_center=base, actual_center=center)
            return CommandResult(ok=True, payload=payload)
        except Exception as exc:
            return self._exception_result(
                exc, operation="solid.create_cylinder",
                parameters={"base_center": base_center, "radius": radius, "height": height, "layer": layer},
                system_call="AutoCAD.ModelSpace.AddCylinder", error_code="E_SOLID_OPERATION",
            )

    async def solid_extrude(
        self, profile_id, height, taper_angle=0.0, erase_profile=False, layer=None
    ) -> CommandResult:
        region = None
        try:
            if float(height) == 0:
                raise ValueError("Extrusion height must be non-zero")
            _, _, _, modelspace, profile, region = self._region_from_profile(profile_id)
            solid = modelspace.AddExtrudedSolid(
                region, float(height), math.radians(float(taper_angle))
            )
            if layer:
                solid.Layer = layer
            if erase_profile:
                profile.Delete()
            solid.Update()
            return CommandResult(ok=True, payload=self._solid_payload(solid, "extrude"))
        except Exception as exc:
            return self._exception_result(
                exc, operation="solid.extrude",
                parameters={"profile_id": profile_id, "height": height, "taper_angle": taper_angle, "layer": layer},
                system_call="AutoCAD.ModelSpace.AddExtrudedSolid", error_code="E_SOLID_OPERATION",
            )
        finally:
            if region is not None:
                try:
                    region.Delete()
                except Exception:
                    pass
            self._auto_fit_view()

    async def solid_revolve(
        self,
        profile_id,
        axis_point,
        axis_direction,
        angle=360.0,
        erase_profile=False,
        layer=None,
    ) -> CommandResult:
        region = None
        try:
            direction = [float(item) for item in list(axis_direction)[:3]]
            while len(direction) < 3:
                direction.append(0.0)
            if math.sqrt(sum(value * value for value in direction)) <= 0.000001:
                raise ValueError("Revolve axis direction must be non-zero")
            pythoncom, win32com, _, modelspace, profile, region = self._region_from_profile(profile_id)
            solid = modelspace.AddRevolvedSolid(
                region,
                self._solid_point(pythoncom, win32com, axis_point),
                self._solid_point(pythoncom, win32com, direction),
                math.radians(float(angle)),
            )
            if layer:
                solid.Layer = layer
            if erase_profile:
                profile.Delete()
            solid.Update()
            return CommandResult(ok=True, payload=self._solid_payload(solid, "revolve"))
        except Exception as exc:
            return self._exception_result(
                exc, operation="solid.revolve",
                parameters={"profile_id": profile_id, "axis_point": axis_point, "axis_direction": axis_direction, "angle": angle, "layer": layer},
                system_call="AutoCAD.ModelSpace.AddRevolvedSolid", error_code="E_SOLID_OPERATION",
            )
        finally:
            if region is not None:
                try:
                    region.Delete()
                except Exception:
                    pass
            self._auto_fit_view()

    async def solid_sweep(self, profile_id, path_id, erase_profile=False, layer=None) -> CommandResult:
        region = None
        try:
            _, _, document, modelspace, profile, region = self._region_from_profile(profile_id)
            path = document.HandleToObject(str(path_id))
            solid = modelspace.AddExtrudedSolidAlongPath(region, path)
            if layer:
                solid.Layer = layer
            if erase_profile:
                profile.Delete()
            solid.Update()
            return CommandResult(ok=True, payload=self._solid_payload(solid, "sweep"))
        except Exception as exc:
            return self._exception_result(
                exc, operation="solid.sweep",
                parameters={"profile_id": profile_id, "path_id": path_id, "layer": layer},
                system_call="AutoCAD.ModelSpace.AddExtrudedSolidAlongPath", error_code="E_SOLID_OPERATION",
            )
        finally:
            if region is not None:
                try:
                    region.Delete()
                except Exception:
                    pass
            self._auto_fit_view()

    @sta_async_method("solid.boolean")
    async def solid_boolean(self, primary_id, tool_id, operation) -> CommandResult:
        operation_name = str(operation).lower()
        operation_codes = {"union": 0, "intersection": 1, "subtract": 2}
        if operation_name not in operation_codes:
            return CommandResult(
                ok=False,
                error="Boolean operation must be union, intersection, or subtract",
                error_code="E_SOLID_OPERATION",
            )
        try:
            _, _, document, _ = self._solid_context()
            primary = document.HandleToObject(str(primary_id))
            tool = document.HandleToObject(str(tool_id))
            primary.Boolean(operation_codes[operation_name], tool)
            primary.Update()
            self._auto_fit_view()
            return CommandResult(
                ok=True, payload=self._solid_payload(primary, f"boolean-{operation_name}")
            )
        except Exception as exc:
            return self._exception_result(
                exc, operation=f"solid.boolean.{operation_name}",
                parameters={"primary_id": primary_id, "tool_id": tool_id},
                system_call="AutoCAD.3DSolid.Boolean", error_code="E_SOLID_OPERATION",
            )

    @staticmethod
    def _delete_com_entities(entities) -> None:
        for entity in reversed(list(entities)):
            try:
                entity.Delete()
            except Exception:
                pass

    def _union_com_solids(self, solids):
        if not solids:
            raise ValueError("At least one solid is required")
        primary = solids[0]
        for tool in solids[1:]:
            primary.Boolean(0, tool)
        primary.Update()
        return primary

    def _rounded_box_via_com(self, modelspace, pythoncom, win32com, feature):
        center = [float(value) for value in feature["center"]]
        length, width, height = [float(value) for value in feature["dimensions"]]
        radius = float(feature.get("radius", 0.0))
        layer = feature.get("layer")
        solids = []

        def point(value):
            return self._solid_point(pythoncom, win32com, value)

        def box(dimensions):
            entity = modelspace.AddBox(point(center), *dimensions)
            solids.append(entity)
            return entity

        def cylinder(cylinder_center, cylinder_radius, cylinder_height, axis="z"):
            entity = modelspace.AddCylinder(
                point(cylinder_center), cylinder_radius, cylinder_height
            )
            if axis == "x":
                entity.Rotate3D(
                    point(cylinder_center),
                    point([cylinder_center[0], cylinder_center[1] + 1, cylinder_center[2]]),
                    -math.pi / 2,
                )
            elif axis == "y":
                entity.Rotate3D(
                    point(cylinder_center),
                    point([cylinder_center[0] + 1, cylinder_center[1], cylinder_center[2]]),
                    math.pi / 2,
                )
            solids.append(entity)
            return entity

        try:
            if radius <= 0:
                primary = box((length, width, height))
            else:
                inner = [length - 2 * radius, width - 2 * radius, height - 2 * radius]
                box((inner[0], width, inner[2]))
                box((length, inner[1], inner[2]))
                box((inner[0], inner[1], height))
                x_positions = [center[0] - length / 2 + radius, center[0] + length / 2 - radius]
                y_positions = [center[1] - width / 2 + radius, center[1] + width / 2 - radius]
                z_positions = [center[2] - height / 2 + radius, center[2] + height / 2 - radius]
                for y in y_positions:
                    for z in z_positions:
                        cylinder([center[0], y, z], radius, inner[0], "x")
                for x in x_positions:
                    for z in z_positions:
                        cylinder([x, center[1], z], radius, inner[1], "y")
                for x in x_positions:
                    for y in y_positions:
                        cylinder([x, y, center[2]], radius, inner[2], "z")
                for x in x_positions:
                    for y in y_positions:
                        for z in z_positions:
                            sphere = modelspace.AddSphere(point([x, y, z]), radius)
                            solids.append(sphere)
                primary = self._union_com_solids(solids)
            if layer:
                primary.Layer = str(layer)
            primary.Update()
            return primary
        except Exception:
            self._delete_com_entities(solids)
            raise

    @staticmethod
    def _rounded_box_semantic_edges(feature: dict) -> list[dict]:
        radius = float(feature.get("radius", 0.0))
        if radius <= 0:
            return []
        edges = []
        for axis in "xyz":
            for first in (-1, 1):
                for second in (-1, 1):
                    edges.append(
                        {
                            "semantic_edge_id": f"{feature['feature_id']}:{axis}:{first}:{second}",
                            "role": f"rounded_edge_{axis}",
                            "axis": axis,
                            "side_signs": [first, second],
                            "fillet_radius": radius,
                            "native_edge_index": None,
                        }
                    )
        return edges

    def _ring_via_com(self, modelspace, pythoncom, win32com, feature):
        center = [float(value) for value in feature["center"]]
        height = float(feature["height"])
        outer = modelspace.AddCylinder(
            self._solid_point(pythoncom, win32com, center),
            float(feature["outer_radius"]),
            height,
        )
        inner = None
        try:
            if float(feature.get("inner_radius", 0.0)) > 0:
                inner = modelspace.AddCylinder(
                    self._solid_point(pythoncom, win32com, center),
                    float(feature["inner_radius"]),
                    height,
                )
                outer.Boolean(2, inner)
            if feature.get("layer"):
                outer.Layer = str(feature["layer"])
            outer.Update()
            return outer
        except Exception:
            self._delete_com_entities([entity for entity in (inner, outer) if entity is not None])
            raise

    @sta_async_method("product.create_feature", timeout=60.0)
    async def product_create_feature(self, kind: str, data: dict) -> CommandResult:
        try:
            feature = normalize_feature(kind, data)
            destructive_compatibility_feature = feature["kind"] in {
                "recessed_panel",
                "port_cutout_usb_a",
                "port_cutout_usb_c",
            }
            if (
                destructive_compatibility_feature
                and os.environ.get(
                    "AUTOCAD_MCP_ALLOW_UNVERIFIED_COMPAT_CUTOUTS", "false"
                ).strip().lower()
                not in {"1", "true", "yes", "on"}
            ):
                # Reject before touching COM.  The compatibility API cannot
                # prove an atomic replacement, so the native transactional
                # worker is required for these destructive features.
                return CommandResult(
                    ok=False,
                    error=(
                        "Destructive product cutouts require the native "
                        "transactional worker on the compatibility backend"
                    ),
                    error_code="E_COMPAT_FEATURE_TRANSACTION_UNAVAILABLE",
                    recoverable=False,
                    recommended_action=(
                        "install_the_signed_native_worker_or_explicitly_enable_"
                        "AUTOCAD_MCP_ALLOW_UNVERIFIED_COMPAT_CUTOUTS"
                    ),
                    payload={
                        "feature_kind": feature["kind"],
                        "target_id": feature.get("target_id"),
                        "source_unchanged": True,
                        "native_transaction_required": True,
                    },
                )
            pythoncom, win32com, document, modelspace = self._solid_context()
            created = None
            replaced_target = None
            if feature["kind"] in {"rounded_box", "module_reservation"}:
                created = self._rounded_box_via_com(modelspace, pythoncom, win32com, feature)
            elif feature["kind"] in {"rotary_layer", "annular_gap", "detent_ring_placeholder"}:
                created = self._ring_via_com(modelspace, pythoncom, win32com, feature)
            elif feature["kind"] in {"recessed_panel", "port_cutout_usb_a", "port_cutout_usb_c"}:
                target = document.HandleToObject(str(feature["target_id"]))
                trial = target.Copy()
                cutter_feature = dict(feature)
                if feature["kind"] == "recessed_panel":
                    cutter_feature["dimensions"] = [
                        feature["dimensions"][0], feature["dimensions"][1], feature["depth"]
                    ]
                cutter = self._rounded_box_via_com(
                    modelspace, pythoncom, win32com, cutter_feature
                )
                try:
                    trial.Boolean(2, cutter)
                    trial.Update()
                    replaced_target = str(target.Handle)
                    target.Delete()
                    created = trial
                except Exception:
                    self._delete_com_entities([cutter, trial])
                    raise
            else:
                raise ValueError(f"Unsupported product feature: {feature['kind']}")
            payload = self._solid_payload(created, feature["kind"])
            requested_bounds = feature_bounds(feature)
            actual_bounds = payload.get("bounds")
            bound_diff = []
            if feature["kind"] not in {
                "recessed_panel", "port_cutout_usb_a", "port_cutout_usb_c"
            }:
                if not actual_bounds:
                    self._delete_com_entities([created])
                    raise RuntimeError("Created product solid has no readable bounding box")
                for side in ("min", "max"):
                    for index, axis in enumerate("xyz"):
                        delta = float(actual_bounds[side][index]) - float(
                            requested_bounds[side][index]
                        )
                        if abs(delta) > 0.0001:
                            bound_diff.append(
                                {
                                    "path": f"bounds.{side}.{axis}",
                                    "requested": requested_bounds[side][index],
                                    "actual": actual_bounds[side][index],
                                    "delta": delta,
                                }
                            )
                volume = payload.get("volume")
                envelope_volume = math.prod(
                    requested_bounds["max"][index] - requested_bounds["min"][index]
                    for index in range(3)
                )
                if volume is None or float(volume) <= 0 or float(volume) > envelope_volume + 0.0001:
                    bound_diff.append(
                        {
                            "path": "volume",
                            "requested": f"0 < volume <= {envelope_volume}",
                            "actual": volume,
                        }
                    )
                if bound_diff:
                    self._delete_com_entities([created])
                    raise RuntimeError(
                        f"Product feature postcondition mismatch: {bound_diff}"
                    )
            feature.update(
                handle=payload.get("handle"),
                bounds=actual_bounds or requested_bounds,
                volume=payload.get("volume"),
                semantic_edges=(
                    self._rounded_box_semantic_edges(feature)
                    if feature["kind"] in {"rounded_box", "recessed_panel", "module_reservation", "port_cutout_usb_a", "port_cutout_usb_c"}
                    else []
                ),
                replaced_target_handle=replaced_target,
                verified=True,
                requested={"bounds": requested_bounds},
                actual={"bounds": actual_bounds, "volume": payload.get("volume")},
                diff=bound_diff,
                modeling_authority="native_autocad_brep",
            )
            self._auto_fit_view()
            return CommandResult(ok=True, payload=feature)
        except Exception as exc:
            postcondition = "postcondition mismatch" in str(exc).lower()
            return self._exception_result(
                exc,
                operation=f"product.create_feature.{kind}",
                parameters=data,
                system_call="AutoCAD.ActiveX analytic solid construction",
                error_code=(
                    "E_POSTCONDITION_MISMATCH" if postcondition else "E_PRODUCT_FEATURE_FAILED"
                ),
                recommended_action=(
                    "do_not_continue_modeling_switch_to_a_verified_backend"
                    if postcondition
                    else "verify_feature_parameters_layer_and_target_then_retry"
                ),
            )

    @staticmethod
    def _camera_direction(view_name: str) -> list[float]:
        directions = {
            "front": [0.0, -1.0, 0.0],
            "right": [1.0, 0.0, 0.0],
            "top": [0.0, 0.0, 1.0],
            "bottom": [0.0, 0.0, -1.0],
            "iso": [1.0, -1.0, 1.0],
            "rotated_iso": [1.0, 1.0, 0.75],
            "section": [1.0, -1.0, 1.0],
            "exploded": [1.0, -1.0, 1.0],
        }
        if view_name not in directions:
            raise ValueError(f"Unsupported fixed camera view: {view_name}")
        return directions[view_name]

    def _fit_fixed_camera_viewport(
        self,
        viewport,
        direction: list[float],
        target: list[float],
        margin_scale: float,
        output_aspect: float,
        planned_bounds: dict | None = None,
    ) -> dict:
        if margin_scale <= 0.1 or margin_scale >= 0.98:
            raise ValueError("margin_scale must be between 0.1 and 0.98")
        entities = [] if planned_bounds is not None else self._collect_entities_via_com()
        bounds_list = [planned_bounds] if planned_bounds is not None else [
            entity.get("bounds") for entity in entities
        ]
        corners = []
        for bounds in bounds_list:
            if not bounds or not bounds.get("min") or not bounds.get("max"):
                continue
            corners.extend(
                [x, y, z]
                for x in (bounds["min"][0], bounds["max"][0])
                for y in (bounds["min"][1], bounds["max"][1])
                for z in (bounds["min"][2], bounds["max"][2])
            )
        if not corners:
            raise ValueError("Fixed camera fitting requires readable entity bounds")

        def normalize(vector):
            magnitude = math.sqrt(sum(float(value) ** 2 for value in vector))
            if magnitude <= 1e-9:
                raise ValueError("Camera direction must be non-zero")
            return [float(value) / magnitude for value in vector]

        def cross(first, second):
            return [
                first[1] * second[2] - first[2] * second[1],
                first[2] * second[0] - first[0] * second[2],
                first[0] * second[1] - first[1] * second[0],
            ]

        camera_axis = normalize(direction)
        up_reference = [0.0, 1.0, 0.0] if abs(camera_axis[2]) > 0.95 else [0.0, 0.0, 1.0]
        horizontal = normalize(cross(up_reference, camera_axis))
        vertical = normalize(cross(camera_axis, horizontal))
        projected = []
        for point in corners:
            relative = [float(point[index]) - float(target[index]) for index in range(3)]
            projected.append(
                [
                    sum(relative[index] * horizontal[index] for index in range(3)),
                    sum(relative[index] * vertical[index] for index in range(3)),
                ]
            )
        minimum = [min(point[index] for point in projected) for index in range(2)]
        maximum = [max(point[index] for point in projected) for index in range(2)]
        content_width = max(maximum[0] - minimum[0], 1e-6)
        content_height = max(maximum[1] - minimum[1], 1e-6)
        if output_aspect <= 0:
            raise ValueError("output_aspect must be positive")
        aspect = float(output_aspect)
        view_height = max(
            content_height / margin_scale,
            content_width / (aspect * margin_scale),
        )
        view_width = view_height * aspect
        viewport.Height = view_height
        viewport.Width = view_width
        try:
            import pythoncom
            import win32com.client

            viewport.Center = win32com.client.VARIANT(
                pythoncom.VT_ARRAY | pythoncom.VT_R8, [0.0, 0.0]
            )
        except Exception:
            pass
        return {
            "projected_bounds": {"min": minimum, "max": maximum},
            "content_size": [content_width, content_height],
            "view_size": [view_width, view_height],
            "margin_scale": margin_scale,
            "output_aspect": aspect,
            "horizontal_axis": horizontal,
            "vertical_axis": vertical,
            "entity_count": len(entities),
            "planned_bounds": planned_bounds,
        }

    @sta_async_method("view.prepare_recording", idempotent=True)
    async def prepare_recording_view(
        self,
        bounds: dict[str, list[float]],
        view: str = "iso",
        margin_scale: float = 0.82,
        output_aspect: float = 16 / 9,
    ) -> CommandResult:
        try:
            minimum = [float(value) for value in bounds["min"][:3]]
            maximum = [float(value) for value in bounds["max"][:3]]
            if len(minimum) != 3 or len(maximum) != 3:
                raise ValueError("bounds.min and bounds.max must contain three coordinates")
            if any(maximum[index] <= minimum[index] for index in range(3)):
                raise ValueError("Each bounds.max coordinate must exceed bounds.min")
            normalized_bounds = {"min": minimum, "max": maximum}
            pythoncom, win32com, document, _ = self._solid_context()
            application = self._get_autocad_application()
            viewport = document.ActiveViewport
            direction = self._camera_direction(str(view).lower())
            target = [
                (minimum[index] + maximum[index]) / 2.0 for index in range(3)
            ]
            document.SetVariable("PERSPECTIVE", 0)
            viewport.Direction = self._solid_point(pythoncom, win32com, direction)
            viewport.Target = self._solid_point(pythoncom, win32com, target)
            framing = self._fit_fixed_camera_viewport(
                viewport,
                direction,
                target,
                float(margin_scale),
                float(output_aspect),
                planned_bounds=normalized_bounds,
            )
            document.ActiveViewport = viewport
            view_size_before = float(document.GetVariable("VIEWSIZE"))
            desired_view_height = float(framing["view_size"][1])
            application.ZoomScaled(view_size_before / desired_view_height, 1)
            application.Update()
            framing["view_size_after"] = float(document.GetVariable("VIEWSIZE"))
            return CommandResult(
                ok=True,
                payload={
                    "mode": "recording",
                    "view": str(view).lower(),
                    "camera": {"direction": direction, "target": target},
                    "framing": framing,
                    "final_refit_required": True,
                },
            )
        except Exception as exc:
            return self._exception_result(
                exc,
                operation="view.prepare_recording",
                parameters={
                    "bounds": bounds,
                    "view": view,
                    "margin_scale": margin_scale,
                    "output_aspect": output_aspect,
                },
                system_call="AutoCAD.ActiveViewport",
                error_code="E_VIEW_FRAMING_FAILED",
            )

    @sta_sync_method("product.view.prepare", idempotent=True)
    def _prepare_product_view(self, view_name: str, options: dict) -> dict:
        pythoncom, win32com, document, _ = self._solid_context()
        application = self._get_autocad_application()
        viewport = document.ActiveViewport
        saved = {
            "direction": list(viewport.Direction),
            "target": list(viewport.Target),
            "twist": _com_value(viewport, "TwistAngle"),
            "perspective": document.GetVariable("PERSPECTIVE"),
        }
        direction = self._camera_direction(view_name)
        minimum = list(document.GetVariable("EXTMIN"))
        maximum = list(document.GetVariable("EXTMAX"))
        default_target = [
            (float(minimum[index]) + float(maximum[index])) / 2
            for index in range(3)
        ]
        target = options.get("target", default_target)
        paper_dimensions = {
            "A0": (841.0, 1189.0),
            "A1": (594.0, 841.0),
            "A2": (420.0, 594.0),
            "A3": (297.0, 420.0),
            "A4": (210.0, 297.0),
        }
        paper_size = paper_dimensions.get(
            str(options.get("paper", "A4")).upper(), paper_dimensions["A4"]
        )
        selected_orientation = str(options.get("orientation", "landscape")).lower()
        output_aspect = (
            max(paper_size) / min(paper_size)
            if selected_orientation != "portrait"
            else min(paper_size) / max(paper_size)
        )
        document.SetVariable("PERSPECTIVE", 0)
        viewport.Direction = self._solid_point(pythoncom, win32com, direction)
        viewport.Target = self._solid_point(pythoncom, win32com, target)
        twist_reset = False
        if saved["twist"] is not None:
            try:
                viewport.TwistAngle = 0.0
                twist_reset = True
            except Exception:
                pass
        framing = self._fit_fixed_camera_viewport(
            viewport,
            direction,
            target,
            float(options.get("margin_scale", 0.82)),
            output_aspect,
        )
        document.ActiveViewport = viewport
        view_size_before = float(document.GetVariable("VIEWSIZE"))
        desired_view_height = float(framing["view_size"][1])
        zoom_magnification = view_size_before / desired_view_height
        application.ZoomScaled(zoom_magnification, 1)
        application.Update()
        framing.update(
            view_size_before=view_size_before,
            desired_view_height=desired_view_height,
            zoom_magnification=zoom_magnification,
            view_size_after=float(document.GetVariable("VIEWSIZE")),
            twist_reset=twist_reset,
        )
        return {
            "direction": direction,
            "target": target,
            "framing": framing,
            "saved": saved,
        }

    @sta_sync_method("product.view.restore", idempotent=True)
    def _restore_product_view(self, saved: dict) -> dict:
        pythoncom, win32com, document, _ = self._solid_context()
        viewport = document.ActiveViewport
        viewport.Direction = self._solid_point(pythoncom, win32com, saved["direction"])
        viewport.Target = self._solid_point(pythoncom, win32com, saved["target"])
        twist_restored = False
        if saved.get("twist") is not None:
            try:
                viewport.TwistAngle = saved["twist"]
                twist_restored = True
            except Exception:
                pass
        document.ActiveViewport = viewport
        if saved.get("perspective") is not None:
            document.SetVariable("PERSPECTIVE", saved["perspective"])
        return {"restored": True, "twist_restored": twist_restored}

    async def product_render_view(
        self, view_name: str, path: str, options: dict | None = None
    ) -> CommandResult:
        options = dict(options or {})
        if view_name in {"section", "exploded"} and not options.get("prepared_geometry", False):
            return CommandResult(
                ok=False,
                error=f"{view_name} requires caller-prepared section or exploded geometry",
                error_code="E_VIEW_GEOMETRY_NOT_PREPARED",
                recoverable=True,
                recommended_action="prepare_the_named_geometry_state_and_retry",
            )
        prepared = None
        try:
            prepared = self._prepare_product_view(view_name, options)
            result = await self.drawing_render_preview(
                path,
                paper=options.get("paper", "A4"),
                orientation=options.get("orientation", "landscape"),
                plot_style=options.get("plot_style", "monochrome.ctb"),
                dpi=int(options.get("dpi", 150)),
                force=bool(options.get("force", True)),
                plot_type="display",
                normalize_framing=True,
                framing_fill=float(options.get("framing_fill", 0.82)),
                visual_style=options.get("visual_style"),
                preserve_visual_style=bool(options.get("preserve_visual_style", True)),
            )
            if result.ok:
                result.payload.update(
                    view_name=view_name,
                    requested_camera={
                        "direction": prepared["direction"],
                        "target": prepared["target"],
                    },
                    actual_camera={
                        "direction": prepared["direction"],
                        "target": prepared["target"],
                    },
                    camera_framing=prepared["framing"],
                    projection="orthographic",
                    prepared_geometry=bool(options.get("prepared_geometry", False)),
                    # A visual-style request proves only that a style was
                    # requested/read back.  PlotToFile does not prove a
                    # material renderer was used, so remain conservative.
                    material_render=False,
                    material_render_verified=False,
                    render_mode=(
                        "native_plot_visual_style_unverified"
                        if options.get("visual_style")
                        else "wireframe_or_current"
                    ),
                )
            return result
        except Exception as exc:
            return self._exception_result(
                exc,
                operation=f"product.render_view.{view_name}",
                parameters={"path": path, **options},
                system_call="AutoCAD.ActiveViewport + PlotToFile",
                file_path=path,
                error_code="E_PRODUCT_VIEW_FAILED",
            )
        finally:
            if prepared is not None:
                restore_result = None
                try:
                    restore_result = self._restore_product_view(prepared["saved"])
                except Exception as restore_error:
                    restore_result = {"restored": False, "error": str(restore_error)}
                if "result" in locals() and isinstance(result, CommandResult):
                    payload = result.payload if isinstance(result.payload, dict) else {}
                    payload["camera_restore"] = restore_result
                    payload["camera_restored"] = bool(
                        isinstance(restore_result, dict) and restore_result.get("restored")
                    )
                    result.payload = payload
                    if not payload["camera_restored"]:
                        result.ok = False
                        result.error = (
                            "Product preview was written, but the previous camera could not be restored"
                        )
                        result.error_code = "E_CAMERA_RESTORE_FAILED"
                        result.recoverable = False
                        result.recommended_action = (
                            "restore_the_previous_camera_manually_before_new_CAD_operations"
                        )

    # --- Layer operations ---

    async def layer_list(self) -> CommandResult:
        return await self._dispatch("layer-list", {})

    @sta_async_method("layer.exists", idempotent=True)
    async def layer_exists(self, name: str) -> CommandResult:
        try:
            import pythoncom
            import win32com.client

            document = self._get_autocad_application().ActiveDocument
            try:
                document.Layers.Item(str(name))
                exists = True
            except Exception:
                exists = False
            return CommandResult(ok=True, payload={"name": str(name), "exists": exists})
        except Exception as exc:
            return self._exception_result(
                exc,
                operation="layer.exists",
                parameters={"name": name},
                system_call="AutoCAD.Layers.Item",
            )

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
        params = {"x": x, "y": y, "text": text, "height": height, "rotation": rotation}
        return await self._create_with_com_fallback(
            "text", params, layer, "create-text",
            {**params, "text": encode_autocad_text(text), "layer": layer},
        )

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

    @sta_sync_method("dimension.create")
    def _create_dimension_via_com(self, kind: str, **data) -> dict:
        import pythoncom
        import win32com.client

        application = self._get_autocad_application()
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
        policy = (
            self._native_window_policy(activate=activate)
            if self._native_client is not None
            else self._ensure_autocad_visible(activate=activate)
        )
        return CommandResult(ok=True, payload=policy)

    async def minimize_window(self) -> CommandResult:
        if sys.platform != "win32" or not self._hwnd:
            return CommandResult(ok=False, error="AutoCAD window is unavailable")
        if self._native_client is not None:
            try:
                import ctypes

                ctypes.windll.user32.ShowWindow(int(self._hwnd), 7)
                self._window_policy_applied = True
                return CommandResult(
                    ok=True,
                    payload={
                        "window_mode": "minimized",
                        "activated": False,
                        "hwnd": self._hwnd,
                        "transport": "user32",
                    },
                )
            except Exception as exc:
                return CommandResult(ok=False, error=f"Failed to minimize AutoCAD: {exc}")
        try:
            import win32con
            import win32gui

            win32gui.ShowWindow(self._hwnd, win32con.SW_SHOWMINNOACTIVE)
            self._window_policy_applied = True
            return CommandResult(
                ok=True,
                payload={"window_mode": "minimized", "activated": False, "hwnd": self._hwnd},
            )
        except Exception as exc:
            return CommandResult(ok=False, error=f"Failed to minimize AutoCAD: {exc}")

    async def get_screenshot(self) -> CommandResult:
        if self._screenshot_provider:
            data = self._screenshot_provider.capture()
            if data:
                return CommandResult(ok=True, payload=data)
        return CommandResult(ok=False, error="Screenshot capture failed")
