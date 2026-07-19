"""Lazy backend singleton, _safe/_error/_json helpers, screenshot utility."""

from __future__ import annotations

import asyncio
import base64
import functools
import hashlib
import inspect
import json
import os
import struct
from typing import Any

import structlog
from mcp.types import CallToolResult, ImageContent, TextContent

from autocad_mcp.backends.base import AutoCADBackend, CommandResult
from autocad_mcp.config import ONLY_TEXT_FEEDBACK, _current_backend_env, detect_backend
from autocad_mcp.errors import error_payload, exception_context, infer_error_code
from autocad_mcp.transport_guard import (
    TransportGuard,
    TransportGuardConfig,
    TransportGuardError,
    TransportLease,
    global_transport_guard,
)
from autocad_mcp.workspace import output_root

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Lazy backend singleton
# ---------------------------------------------------------------------------

_backend: AutoCADBackend | None = None
_init_lock = asyncio.Lock()


class McpAdmissionError(RuntimeError):
    """A bounded MCP request could not be admitted without waiting forever."""

    def __init__(self, message: str, *, code: str, details: dict[str, Any]):
        super().__init__(message)
        self.error_code = code
        self.recoverable = True
        self.recommended_action = "serialize_autocad_calls_or_use_create_batch_then_retry"
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        """Keep the provisional exception directly serializable."""
        return {
            "ok": False,
            "error": {
                "code": self.error_code,
                "message": str(self),
                "recoverable": self.recoverable,
                "recommended_action": self.recommended_action,
            },
            "details": dict(self.details),
        }


def _bounded_int_env(name: str, default: int, lower: int, upper: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(lower, min(upper, value))


class RequestAdmission:
    """Compatibility facade for the provisional request gate.

    The server uses the process-wide :func:`global_transport_guard` directly.
    This facade keeps older integrations working while ensuring they exercise
    the same bounded FIFO implementation instead of a second lock.
    """

    def __init__(self, guard: TransportGuard | None = None) -> None:
        if guard is None:
            config = TransportGuardConfig.from_env()
            # The provisional API treated zero as unlimited. Preserve that
            # legacy behavior only for independently-created facades.
            if os.environ.get("AUTOCAD_MCP_MAX_CALLS_PER_WINDOW") == "0":
                config = TransportGuardConfig(
                    max_queue=config.max_queue,
                    window_seconds=config.window_seconds,
                    window_budget=1_000_000,
                    queue_timeout=config.queue_timeout,
                )
            guard = TransportGuard(config)
        self._guard = guard
        self._leases: dict[int, list[TransportLease]] = {}

    async def acquire(self, tool: str) -> None:
        task = asyncio.current_task()
        task_id = id(task)
        try:
            lease = await self._guard.acquire(tool)
        except TransportGuardError as exc:
            code = exc.error_code
            details = dict(exc.details)
            if code == "E_TRANSPORT_BUDGET_EXCEEDED":
                code = "E_MCP_CALL_RATE_LIMITED"
                details.setdefault("max_calls", details.get("window_budget"))
                details.setdefault("calls_in_window", details.get("window_used"))
            elif code == "E_TRANSPORT_QUEUE_TIMEOUT":
                code = "E_MCP_REQUEST_QUEUE_TIMEOUT"
                details.setdefault(
                    "queue_timeout_seconds", details.get("timeout_seconds")
                )
            raise McpAdmissionError(
                str(exc), code=code, details=details
            ) from exc
        self._leases.setdefault(task_id, []).append(lease)

    def release(self) -> None:
        task_id = id(asyncio.current_task())
        leases = self._leases.get(task_id)
        if not leases:
            return
        lease = leases.pop()
        if not leases:
            self._leases.pop(task_id, None)
        # Keep the historical synchronous release API; schedule the actual
        # condition notification on the current event loop.
        try:
            asyncio.get_running_loop().create_task(lease.release())
        except RuntimeError:
            pass

    def snapshot(self) -> dict[str, Any]:
        status = self._guard.status()
        return {
            **status,
            "max_calls_per_window": status["window_budget"],
            "calls_in_window": status["window_used"],
            "queue_timeout_seconds": status["limits"]["queue_timeout"],
            "serial": True,
            "accepted_total": status["stats"]["admitted"],
            "throttled_total": status["stats"]["queue_rejected"]
            + status["stats"]["budget_rejected"],
            "queue_timeout_total": status["stats"]["queue_timeouts"],
        }


_request_admission = RequestAdmission(global_transport_guard())


def transport_guard() -> TransportGuard:
    """Return the process-wide FIFO gate used by decorated MCP tools."""
    return global_transport_guard()


def request_admission_snapshot() -> dict[str, Any]:
    """Return bounded request-gate telemetry for ``system.runtime``."""
    return transport_guard().status()


async def reset_backend() -> None:
    """Dispose the current backend before a forced re-initialization.

    This is a transport-only shutdown.  It never closes the user's AutoCAD
    document or terminates AutoCAD; it only releases MCP-owned workers.
    """
    global _backend
    async with _init_lock:
        backend = _backend
        _backend = None
        if backend is None:
            return
        shutdown = getattr(backend, "shutdown", None)
        if shutdown is None:
            return
        try:
            result = shutdown()
            if inspect.isawaitable(result):
                await result
        except Exception:
            log.warning("backend_shutdown_failed", backend=backend.name, exc_info=True)


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


def _response_limit_bytes() -> int:
    return _bounded_int_env("AUTOCAD_MCP_MAX_RESPONSE_BYTES", 524288, 65536, 8388608)


def _response_summary(data: Any, original_bytes: int, digest: str) -> dict[str, Any]:
    """Build a small, deterministic envelope when a result is too large."""
    summary: dict[str, Any] = {
        "ok": data.get("ok") if isinstance(data, dict) else None,
        "truncated": True,
        "original_bytes": original_bytes,
        "max_response_bytes": _response_limit_bytes(),
        "sha256": digest,
        "recommended_action": "request_a_narrower_limit_or_read_the_saved_artifact",
    }
    if isinstance(data, dict):
        omitted: dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(value, (list, tuple, dict)):
                omitted[str(key)] = {
                    "type": type(value).__name__,
                    "count": len(value),
                }
        if omitted:
            summary["omitted"] = omitted
        for key in ("error", "payload", "details"):
            value = data.get(key)
            if value is not None and not isinstance(value, (list, tuple, dict)):
                summary[key] = value
    return summary


def _json(data: Any) -> str:
    """Serialize compact JSON, refusing unbounded tool output."""
    serialized = json.dumps(data, default=str, separators=(",", ":"), ensure_ascii=False)
    encoded = serialized.encode("utf-8")
    limit = _response_limit_bytes()
    if len(encoded) <= limit:
        return serialized
    digest = hashlib.sha256(encoded).hexdigest()
    compact = _response_summary(data, len(encoded), digest)
    return json.dumps(compact, default=str, separators=(",", ":"), ensure_ascii=False)


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


def _journal_call_identity(fn, args, kwargs) -> tuple[str | None, str | None]:
    """Extract the operation/key without assuming FastMCP's call style.

    FastMCP normally invokes tools with keyword arguments, while unit tests
    and direct integrations often use positional arguments.  Binding against
    the original function keeps the abandoned-journal recovery path correct in
    both cases.  ``drawing`` historically carries its key inside ``data``;
    retain that compatibility while all other tools use a top-level field.
    """
    operation = kwargs.get("operation")
    idempotency_key = kwargs.get("idempotency_key")
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
        operation = bound.arguments.get("operation", operation)
        idempotency_key = bound.arguments.get("idempotency_key", idempotency_key)
        data = bound.arguments.get("data")
    except (TypeError, ValueError):
        data = kwargs.get("data")
    if not idempotency_key and isinstance(data, dict):
        idempotency_key = data.get("idempotency_key")
    operation_text = str(operation).strip() if operation is not None else None
    key_text = str(idempotency_key).strip() if idempotency_key else None
    return operation_text or None, key_text or None


def _close_abandoned_journal(
    *,
    tool_name: str,
    operation: str | None,
    idempotency_key: str | None,
    exception: Exception,
    parameters: dict[str, Any],
) -> None:
    """Best-effort terminalization for a journal record left by an exception.

    This function must never mask the original tool error.  It only touches a
    matching ``accepted`` record, so a concurrent normal commit/failure cannot
    be overwritten.  The journal is intentionally independent of the backend
    lifecycle: no AutoCAD/COM call is made here.
    """
    if not idempotency_key or not operation:
        return
    message, details = exception_context(
        exception,
        operation=f"{tool_name}.{operation}",
        parameters=parameters,
        system_call="tool-handler",
    )
    code = getattr(exception, "error_code", None) or infer_error_code(message)
    recoverable = getattr(exception, "recoverable", False)
    stored = {
        "ok": False,
        "error": error_payload(
            message,
            code=code,
            recoverable=recoverable,
            recommended_action=getattr(exception, "recommended_action", None),
        ),
        "details": details,
    }
    try:
        # Reuse the server's singleton so tests/integrations that inject a
        # managed journal root are finalized in the same file, rather than
        # creating a second journal under a different workspace.
        from autocad_mcp.server import _operation_journal

        record = _operation_journal().fail_if_accepted(
            idempotency_key,
            stored,
            retryable=bool(recoverable),
            operation=f"{tool_name}.{operation}",
        )
        if record is not None and record.get("abandoned"):
            log.warning(
                "journal_abandoned_request_closed",
                tool=tool_name,
                operation=operation,
                idempotency_key=idempotency_key,
            )
    except Exception as journal_error:
        # Returning the original structured tool error is more useful than
        # crashing the MCP process because its diagnostic journal is locked or
        # unavailable.
        log.warning(
            "journal_abandoned_request_close_failed",
            tool=tool_name,
            operation=operation,
            idempotency_key=idempotency_key,
            error=str(journal_error),
        )


def _safe(tool_name: str):
    """Wrap an async tool handler with uniform error handling."""

    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            operation, _ = _journal_call_identity(fn, args, kwargs)
            operation_name = operation or "unknown"

            async def invoke():
                return await fn(*args, **kwargs)

            try:
                return await transport_guard().execute(
                    f"{tool_name}.{operation_name}",
                    invoke,
                    metadata={"tool": tool_name, "operation": operation_name},
                )
            except TransportGuardError as e:
                # Admission failures are expected, bounded outcomes, not
                # backend exceptions. Preserve their full retry metadata.
                log.warning(
                    "transport_admission_rejected",
                    tool=tool_name,
                    operation=operation_name,
                    code=e.error_code,
                    details=e.details,
                )
                return tool_error(
                    str(e),
                    code=e.error_code,
                    recoverable=e.recoverable,
                    recommended_action=e.recommended_action,
                    details=e.details,
                )
            except Exception as e:
                log.error("tool_error", tool=tool_name, operation=operation_name, error=str(e))
                operation, idempotency_key = _journal_call_identity(fn, args, kwargs)
                _close_abandoned_journal(
                    tool_name=tool_name,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    exception=e,
                    parameters=kwargs,
                )
                return _error(e, f"{tool_name}.{operation_name}", parameters=kwargs)

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Screenshot helper
# ---------------------------------------------------------------------------


def _png_dimensions(raw: bytes) -> tuple[int | None, int | None]:
    if len(raw) >= 24 and raw[:8] == b"\x89PNG\r\n\x1a\n" and raw[12:16] == b"IHDR":
        return struct.unpack(">II", raw[16:24])
    return None, None


def _persist_screenshot(data: str | bytes, *, stem: str = "mcp-capture") -> tuple[dict[str, Any], bytes]:
    """Write a screenshot once and return path/hash metadata, never the payload."""
    if isinstance(data, bytes):
        raw = data
    else:
        text = str(data)
        if text.startswith("data:") and "," in text:
            text = text.split(",", 1)[1]
        raw = base64.b64decode(text, validate=True)
    if not raw:
        raise ValueError("empty screenshot payload")
    digest = hashlib.sha256(raw).hexdigest()
    folder = output_root() / "previews" / "mcp-captures"
    folder.mkdir(parents=True, exist_ok=True)
    safe_stem = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in stem)[:48]
    path = folder / f"{safe_stem or 'mcp-capture'}-{digest[:16]}.png"
    if not path.exists():
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(raw)
        temporary.replace(path)
    width, height = _png_dimensions(raw)
    return {
        "path": str(path),
        "bytes": len(raw),
        "sha256": digest,
        "width": width,
        "height": height,
        "inline": False,
    }, raw


def _screenshot_result(
    result: CommandResult,
    *,
    include_image: bool = False,
    stem: str = "mcp-capture",
) -> list[TextContent | ImageContent] | str | CallToolResult:
    """Return screenshot metadata by default; inline image requires opt-in."""
    if not result.ok or not result.payload:
        return _format_result(result)
    try:
        metadata, raw = _persist_screenshot(result.payload, stem=stem)
    except Exception as exc:
        return _json(
            {
                "ok": False,
                "error": {
                    "message": "Screenshot was captured but could not be persisted",
                    "code": "E_SCREENSHOT_PERSIST_FAILED",
                },
                "details": {"type": type(exc).__name__, "message": str(exc)},
            }
        )
    payload = {"screenshot": metadata}
    if isinstance(result.payload, dict):
        payload.update({k: v for k, v in result.payload.items() if k not in {"data", "base64", "image"}})
    allow_inline = os.environ.get("AUTOCAD_MCP_ALLOW_INLINE_IMAGES", "false").strip().lower() in (
        "1", "true", "yes", "on"
    )
    max_inline = _bounded_int_env("AUTOCAD_MCP_MAX_INLINE_IMAGE_BYTES", 262144, 16384, 8388608)
    if include_image and allow_inline and len(raw) <= max_inline:
        metadata["inline"] = True
    text = _json({"ok": True, "payload": payload})
    if metadata["inline"]:
        return [
            TextContent(type="text", text=text),
            ImageContent(type="image", data=base64.b64encode(raw).decode("ascii"), mimeType="image/png"),
        ]
    return text


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

    if not include_screenshot or not screenshot_data:
        return text
    # Route all image responses through the persistence/size gate.  The
    # previous implementation copied base64 directly into the MCP transcript.
    return _screenshot_result(
        CommandResult(ok=True, payload=screenshot_data),
        include_image=not ONLY_TEXT_FEEDBACK,
        stem="tool-capture",
    )


async def add_screenshot_if_available(
    result: CommandResult,
    include_screenshot: bool = False,
) -> list[TextContent | ImageContent] | str | CallToolResult:
    """Conditionally append a screenshot to the result."""
    if not result.ok:
        return _format_result(result)
    if not include_screenshot:
        return _json(result.to_dict())

    backend = await get_backend()
    screenshot_result = await backend.get_screenshot()

    if screenshot_result.ok and screenshot_result.payload:
        # Keep the operation result and screenshot metadata together while
        # never returning the raw base64 unless explicitly enabled.
        rendered = _screenshot_result(
            screenshot_result,
            include_image=not ONLY_TEXT_FEEDBACK,
            stem="tool-capture",
        )
        if isinstance(rendered, str):
            try:
                envelope = json.loads(rendered)
                if envelope.get("ok"):
                    envelope["operation_result"] = result.to_dict()
                    return _json(envelope)
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        return rendered

    return _json(result.to_dict())
