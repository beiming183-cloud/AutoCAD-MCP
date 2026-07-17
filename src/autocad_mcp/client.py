"""Lazy backend singleton, _safe/_error/_json helpers, screenshot utility."""

from __future__ import annotations

import asyncio
import base64
import functools
import json
from typing import Any

import structlog
from mcp.types import CallToolResult, ImageContent, TextContent

from autocad_mcp.backends.base import AutoCADBackend, CommandResult
from autocad_mcp.config import ONLY_TEXT_FEEDBACK, _current_backend_env, detect_backend
from autocad_mcp.errors import error_payload, exception_context

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Lazy backend singleton
# ---------------------------------------------------------------------------

_backend: AutoCADBackend | None = None
_init_lock = asyncio.Lock()


async def get_backend() -> AutoCADBackend:
    """Return (and lazily initialize) the backend singleton.

    Uses an asyncio Lock to prevent concurrent initialization races
    when multiple MCP tool calls arrive simultaneously.
    """
    global _backend
    if _backend is not None:
        return _backend
    async with _init_lock:
        # Double-check after acquiring lock (another task may have initialized)
        if _backend is not None:
            return _backend

        log.info("backend_init_start")
        backend_name = detect_backend()
        log.info("backend_detected", backend=backend_name)

        if backend_name == "file_ipc":
            from autocad_mcp.backends.file_ipc import FileIPCBackend

            backend = FileIPCBackend()
        else:
            from autocad_mcp.backends.ezdxf_backend import EzdxfBackend

            backend = EzdxfBackend()

        log.info("backend_instance_created", backend=backend_name)
        result = await backend.initialize()
        log.info("backend_initialize_returned", backend=backend_name, ok=result.ok)
        if not result.ok:
            raise RuntimeError(f"Backend init failed: {result.error}")

        _backend = backend
        log.info("backend_initialized", backend=_backend.name)
        return _backend


async def ensure_backend_ready() -> CommandResult:
    """Self-heal the configured backend without conflating status with initialization."""
    global _backend
    if _current_backend_env() == "ezdxf":
        backend = await get_backend()
        status = await backend.status()
        if status.ok and isinstance(status.payload, dict):
            status.payload["ready"] = True
        return status

    async with _init_lock:
        from autocad_mcp.backends.file_ipc import FileIPCBackend

        backend = _backend if isinstance(_backend, FileIPCBackend) else FileIPCBackend()
        result = await backend.ensure_ready()
        if result.ok:
            _backend = backend
        return result


# ---------------------------------------------------------------------------
# JSON serialization helper
# ---------------------------------------------------------------------------


def _json(data: Any) -> str:
    """Serialize to compact JSON string."""
    return json.dumps(data, default=str, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Error formatting with actionable hints
# ---------------------------------------------------------------------------


def tool_error(
    message: str,
    *,
    code: str | None = None,
    context: str = "",
    recoverable: bool | None = None,
    recommended_action: str | None = None,
    details: Any = None,
) -> CallToolResult:
    full_message = f"[{context}] {message}" if context else message
    payload = {
        "ok": False,
        "error": error_payload(
            full_message,
            code=code,
            recoverable=recoverable,
            recommended_action=recommended_action,
        ),
    }
    if details is not None:
        payload["details"] = details
    serialized = _json(payload)
    return CallToolResult(
        content=[TextContent(type="text", text=serialized)],
        structuredContent=payload,
        isError=True,
    )


def _error(
    e: Exception,
    context: str = "",
    *,
    parameters: dict | None = None,
) -> CallToolResult:
    """Format an exception with an actionable hint."""
    msg, details = exception_context(
        e,
        operation=context or "mcp-tool",
        parameters=parameters,
        system_call="tool-handler",
        file_path=str(getattr(e, "filename", "") or "") or None,
    )
    msg_lower = msg.lower()

    if "window not found" in msg_lower or "no autocad" in msg_lower:
        hint = "AutoCAD is not running or no drawing is open. Use system.ensure_ready."
    elif "timeout" in msg_lower:
        hint = "Command timed out. AutoCAD may be in a modal dialog. Press ESC in AutoCAD and retry."
    elif "not supported" in msg_lower or "backend" in msg_lower:
        hint = "Operation not supported on current backend. Check system(operation='status') for capabilities."
    elif "dispatcher" in msg_lower or "mcp_dispatch" in msg_lower:
        hint = "mcp_dispatch.lsp not loaded. In AutoCAD command line, type: (load \"mcp_dispatch.lsp\")"
    else:
        hint = "Unexpected error. Check AutoCAD is responsive and retry."

    code = getattr(e, "error_code", None)
    custom_details = getattr(e, "details", None)
    if code is None and getattr(e, "errno", None) is not None:
        code = "E_SYSTEM_CALL_FAILED"
    if code == "E_PYWIN32_BROKEN":
        hint = "Repair pywin32 in the exact Windows Python used by this MCP, then restart the MCP process."
    elif code == "E_AUTOCAD_PROFILE_UNWRITABLE":
        hint = "Make the Activity Insights directory writable, set AUTOCAD_MCP_ACTIVITY_INSIGHTS_PATH, or disable Activity Insights before restarting AutoCAD."
    return tool_error(
        msg,
        code=code,
        recommended_action=hint,
        details=custom_details or details,
    )


# ---------------------------------------------------------------------------
# _safe decorator for tool error handling
# ---------------------------------------------------------------------------


def _safe(tool_name: str):
    """Wrap an async tool handler with uniform error handling."""

    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except Exception as e:
                op = kwargs.get("operation", "unknown")
                log.error("tool_error", tool=tool_name, operation=op, error=str(e))
                return _error(e, f"{tool_name}.{op}", parameters=kwargs)

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Screenshot helper
# ---------------------------------------------------------------------------


def _format_result(
    result: CommandResult,
    include_screenshot: bool = False,
    screenshot_data: str | None = None,
) -> list[TextContent | ImageContent] | str | CallToolResult:
    """Format a CommandResult for MCP response.

    Returns a list with TextContent + optional ImageContent if screenshot requested,
    or a plain JSON string if no screenshot.
    """
    text = _json(result.to_dict())

    if not result.ok:
        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structuredContent=result.to_dict(),
            isError=True,
        )

    if not include_screenshot or ONLY_TEXT_FEEDBACK or not screenshot_data:
        return text

    return [
        TextContent(type="text", text=text),
        ImageContent(
            type="image",
            data=screenshot_data,
            mimeType="image/png",
        ),
    ]


async def add_screenshot_if_available(
    result: CommandResult,
    include_screenshot: bool = False,
) -> list[TextContent | ImageContent] | str | CallToolResult:
    """Conditionally append a screenshot to the result."""
    if not result.ok:
        return _format_result(result)
    if not include_screenshot or ONLY_TEXT_FEEDBACK:
        return _json(result.to_dict())

    backend = await get_backend()
    screenshot_result = await backend.get_screenshot()

    if screenshot_result.ok and screenshot_result.payload:
        return _format_result(result, True, screenshot_result.payload)

    return _json(result.to_dict())
