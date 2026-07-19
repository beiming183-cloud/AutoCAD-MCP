"""Client and worker discovery for the transactional AutoCAD .NET plugin.

The native plugin uses a current-user Windows named pipe with a four-byte
little-endian frame length followed by UTF-8 JSON.  This module deliberately
has no pywin32 dependency so the industrial transaction path remains usable
when the optional COM compatibility runtime is unhealthy.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import struct
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from autocad_mcp.backends.base import CommandResult
from autocad_mcp.com_sta import ComProcessBusyError, ComProcessLease


PROTOCOL_VERSION = 1
MAXIMUM_MESSAGE_BYTES = 8 * 1024 * 1024
_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


class NativePipeError(RuntimeError):
    """Structured native transport or discovery failure."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        recoverable: bool = True,
        recommended_action: str | None = None,
        details: Any = None,
    ):
        super().__init__(message)
        self.error_code = code
        self.recoverable = recoverable
        self.recommended_action = recommended_action
        self.details = details


def _snake_name(value: str) -> str:
    return _CAMEL_BOUNDARY.sub("_", value).lower()


def snake_case_keys(value: Any) -> Any:
    """Normalize plugin camelCase envelopes to the public Python contract."""
    if isinstance(value, dict):
        return {_snake_name(str(key)): snake_case_keys(item) for key, item in value.items()}
    if isinstance(value, list):
        return [snake_case_keys(item) for item in value]
    return value


def camel_case_keys(value: Any) -> Any:
    """Normalize public snake_case operation fields for the .NET protocol."""
    if isinstance(value, dict):
        converted = {}
        for key, item in value.items():
            parts = str(key).split("_")
            name = parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])
            converted[name] = camel_case_keys(item)
        return converted
    if isinstance(value, list):
        return [camel_case_keys(item) for item in value]
    return value


def native_worker_root() -> Path:
    configured = os.environ.get("AUTOCAD_MCP_NATIVE_WORKER_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if not local:
        local = str(Path.home() / "AppData" / "Local")
    return (Path(local) / "AutoCAD-MCP" / "workers").resolve()


def _process_is_alive(process_id: int) -> bool:
    if process_id <= 0:
        return False
    if process_id == os.getpid():
        return True
    try:
        os.kill(process_id, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


@dataclass(frozen=True)
class NativeWorkerDescriptor:
    protocol_version: int
    capability_version: int
    plugin_version: str
    pipe_name: str
    session_id: str
    process_id: int
    started_at: str | None
    hwnd: int | None
    capabilities: tuple[str, ...]
    path: Path

    @classmethod
    def read(cls, path: Path) -> "NativeWorkerDescriptor":
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise NativePipeError(
                f"Invalid native worker descriptor: {path}",
                code="E_NATIVE_DESCRIPTOR_INVALID",
                recoverable=True,
                recommended_action="restart_autocad_to_republish_the_native_worker_descriptor",
                details={"path": str(path), "exception_type": type(exc).__name__},
            ) from exc
        normalized = snake_case_keys(raw)
        try:
            protocol_version = int(normalized["protocol_version"])
            pipe_name = str(normalized["pipe_name"]).strip()
            session_id = str(normalized["session_id"]).strip()
            process_id = int(normalized["process_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise NativePipeError(
                f"Native worker descriptor is missing required fields: {path}",
                code="E_NATIVE_DESCRIPTOR_INVALID",
                details={"path": str(path), "required": ["protocolVersion", "pipeName", "sessionId", "processId"]},
            ) from exc
        if protocol_version != PROTOCOL_VERSION:
            raise NativePipeError(
                f"Native protocol mismatch: expected {PROTOCOL_VERSION}, received {protocol_version}",
                code="E_NATIVE_PROTOCOL_MISMATCH",
                recoverable=False,
                recommended_action="install_a_matching_autocad_mcp_native_bundle",
                details={"path": str(path), "expected": PROTOCOL_VERSION, "actual": protocol_version},
            )
        if not pipe_name or not session_id or process_id <= 0:
            raise NativePipeError(
                f"Native worker descriptor contains empty identity fields: {path}",
                code="E_NATIVE_DESCRIPTOR_INVALID",
                details={"path": str(path)},
            )
        return cls(
            protocol_version=protocol_version,
            capability_version=int(normalized.get("capability_version", 1)),
            plugin_version=str(normalized.get("plugin_version", "unknown")),
            pipe_name=pipe_name,
            session_id=session_id,
            process_id=process_id,
            started_at=str(normalized.get("started_at")) if normalized.get("started_at") else None,
            hwnd=int(normalized["hwnd"]) if normalized.get("hwnd") else None,
            capabilities=tuple(str(item) for item in normalized.get("capabilities", [])),
            path=path.resolve(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "capability_version": self.capability_version,
            "plugin_version": self.plugin_version,
            "pipe_name": self.pipe_name,
            "session_id": self.session_id,
            "process_id": self.process_id,
            "started_at": self.started_at,
            "hwnd": self.hwnd,
            "capabilities": list(self.capabilities),
            "path": str(self.path),
        }


def discover_native_worker(
    *,
    root: Path | None = None,
    preferred_process_id: int | None = None,
    process_probe: Callable[[int], bool] = _process_is_alive,
) -> NativeWorkerDescriptor | None:
    """Select one live worker or fail closed when selection is ambiguous."""
    directory = Path(root or native_worker_root())
    if not directory.is_dir():
        return None
    descriptors: list[NativeWorkerDescriptor] = []
    invalid: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            descriptor = NativeWorkerDescriptor.read(path)
        except NativePipeError as exc:
            invalid.append({"path": str(path), "code": exc.error_code, "message": str(exc)})
            continue
        if process_probe(descriptor.process_id):
            descriptors.append(descriptor)

    configured_pid = os.environ.get("AUTOCAD_MCP_ACAD_PID", "").strip()
    if preferred_process_id is None and configured_pid:
        try:
            preferred_process_id = int(configured_pid)
        except ValueError as exc:
            raise NativePipeError(
                "AUTOCAD_MCP_ACAD_PID must be an integer",
                code="E_PARAMETER_REJECTED",
                recoverable=False,
                details={"value": configured_pid},
            ) from exc
    if preferred_process_id is not None:
        matches = [item for item in descriptors if item.process_id == int(preferred_process_id)]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise NativePipeError(
                f"No native AutoCAD worker matches process {preferred_process_id}",
                code="E_NATIVE_WORKER_NOT_FOUND",
                recommended_action="start_autocad_with_the_native_bundle_or_correct_AUTOCAD_MCP_ACAD_PID",
                details={"preferred_process_id": preferred_process_id, "workers": [item.to_dict() for item in descriptors]},
            )
    if not descriptors:
        return None
    if len(descriptors) > 1:
        raise NativePipeError(
            "Multiple native AutoCAD workers are available and none was selected",
            code="E_AUTOCAD_INSTANCE_AMBIGUOUS",
            recommended_action="set_AUTOCAD_MCP_ACAD_PID_to_the_intended_autocad_process",
            details={"workers": [item.to_dict() for item in descriptors], "invalid": invalid},
        )
    return descriptors[0]


class NativePipeClient:
    """One-request-per-connection named-pipe client with strict envelopes."""

    def __init__(
        self,
        descriptor: NativeWorkerDescriptor,
        *,
        token: str | None = None,
        timeout: float = 10.0,
        transport: Callable[[bytes, float], bytes] | None = None,
    ):
        self.descriptor = descriptor
        self.token = token if token is not None else os.environ.get("AUTOCAD_MCP_PLUGIN_TOKEN")
        self.timeout = max(0.25, min(300.0, float(timeout)))
        self._transport = transport or self._round_trip
        self._lock = threading.Lock()

    @property
    def pipe_path(self) -> str:
        return rf"\\.\pipe\{self.descriptor.pipe_name}"

    @classmethod
    def discover(
        cls,
        *,
        root: Path | None = None,
        preferred_process_id: int | None = None,
        timeout: float = 10.0,
    ) -> "NativePipeClient | None":
        descriptor = discover_native_worker(root=root, preferred_process_id=preferred_process_id)
        return cls(descriptor, timeout=timeout) if descriptor is not None else None

    @staticmethod
    def _read_exact(stream, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            block = stream.read(size - len(chunks))
            if not block:
                raise NativePipeError(
                    "Native worker closed the pipe before completing a response",
                    code="E_NATIVE_PIPE_CLOSED",
                    recommended_action="check_autocad_and_native_plugin_health_then_retry_with_the_same_idempotency_key",
                )
            chunks.extend(block)
        return bytes(chunks)

    def _round_trip(self, framed_request: bytes, timeout: float) -> bytes:
        if sys.platform != "win32":
            raise NativePipeError(
                "The AutoCAD native named-pipe transport requires Windows",
                code="E_UNSUPPORTED_PLATFORM",
                recoverable=False,
            )
        deadline = time.monotonic() + timeout
        last_error: OSError | None = None
        while time.monotonic() < deadline:
            try:
                with open(self.pipe_path, "r+b", buffering=0) as stream:
                    stream.write(framed_request)
                    header = self._read_exact(stream, 4)
                    length = struct.unpack("<I", header)[0]
                    if length <= 0 or length > MAXIMUM_MESSAGE_BYTES:
                        raise NativePipeError(
                            f"Native worker returned an invalid frame length: {length}",
                            code="E_NATIVE_PROTOCOL_ERROR",
                            recoverable=False,
                        )
                    return header + self._read_exact(stream, length)
            except NativePipeError:
                raise
            except OSError as exc:
                last_error = exc
                if _process_is_alive(self.descriptor.process_id) is False:
                    raise NativePipeError(
                        "The AutoCAD native worker process exited while connecting to its pipe",
                        code="E_AUTOCAD_CRASHED",
                        recoverable=True,
                        recommended_action="inspect_autocad_crash_evidence_then_restart_autocad_and_retry_with_the_same_idempotency_key",
                        details={
                            "process_id": self.descriptor.process_id,
                            "pipe_path": self.pipe_path,
                            "system_error": str(exc),
                        },
                    ) from exc
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        raise NativePipeError(
            f"Timed out connecting to native AutoCAD worker pipe {self.pipe_path}",
            code="E_NATIVE_PIPE_TIMEOUT",
            recommended_action="verify_the_native_bundle_is_loaded_and_retry_with_the_same_idempotency_key",
            details={"pipe_path": self.pipe_path, "system_error": str(last_error or "timeout")},
        )

    async def request(
        self,
        operation: str,
        *,
        data: dict[str, Any] | None = None,
        doc_id: str | None = None,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
        session_id: str | None = None,
        request_id: str | None = None,
    ) -> CommandResult:
        request_id = request_id or f"rpc-{uuid.uuid4().hex}"
        request = {
            "id": request_id,
            "operation": str(operation),
            "token": self.token,
            "sessionId": session_id or self.descriptor.session_id,
            "docId": doc_id,
            "expectedRevision": expected_revision,
            "idempotencyKey": idempotency_key,
            "data": data or {},
        }
        body = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > MAXIMUM_MESSAGE_BYTES:
            return CommandResult(
                ok=False,
                error="Native request exceeds the maximum protocol frame size",
                error_code="E_PARAMETER_REJECTED",
                recoverable=False,
                payload={"bytes": len(body), "maximum_bytes": MAXIMUM_MESSAGE_BYTES},
            )
        framed = struct.pack("<I", len(body)) + body

        def invoke() -> bytes:
            with self._lock:
                raw_mutex_timeout = os.environ.get("AUTOCAD_MCP_COM_MUTEX_TIMEOUT", "5")
                try:
                    mutex_timeout = max(0.05, min(120.0, float(raw_mutex_timeout)))
                except (TypeError, ValueError):
                    mutex_timeout = 5.0
                with ComProcessLease(mutex_timeout):
                    return self._transport(framed, self.timeout)

        try:
            response_frame = await asyncio.wait_for(
                asyncio.to_thread(invoke), timeout=self.timeout + 1.0
            )
            if len(response_frame) < 4:
                raise NativePipeError(
                    "Native worker returned a truncated frame",
                    code="E_NATIVE_PROTOCOL_ERROR",
                    recoverable=False,
                )
            length = struct.unpack("<I", response_frame[:4])[0]
            if length != len(response_frame) - 4 or length > MAXIMUM_MESSAGE_BYTES:
                raise NativePipeError(
                    "Native worker response length does not match its frame header",
                    code="E_NATIVE_PROTOCOL_ERROR",
                    recoverable=False,
                    details={"declared": length, "received": len(response_frame) - 4},
                )
            response = json.loads(response_frame[4:].decode("utf-8"))
            if not isinstance(response, dict):
                raise NativePipeError(
                    "Native worker response must be a JSON object",
                    code="E_NATIVE_PROTOCOL_ERROR",
                    recoverable=False,
                    details={"actual_type": type(response).__name__},
                )
            if str(response.get("id")) != request_id:
                raise NativePipeError(
                    "Native worker response id does not match the request",
                    code="E_NATIVE_PROTOCOL_ERROR",
                    recoverable=False,
                    details={"requested": request_id, "actual": response.get("id")},
                )
            if response.get("ok") is True:
                return CommandResult(ok=True, payload=snake_case_keys(response.get("payload")))
            error = snake_case_keys(response.get("error") or {})
            if not isinstance(error, dict):
                return CommandResult(
                    ok=False,
                    error="Native worker returned a malformed error envelope",
                    error_code="E_NATIVE_PROTOCOL_ERROR",
                    recoverable=False,
                    payload={
                        "operation": operation,
                        "request_id": request_id,
                        "actual_error_type": type(error).__name__,
                    },
                )
            return CommandResult(
                ok=False,
                error=str(error.get("message") or "Native AutoCAD operation failed"),
                error_code=str(error.get("code") or "E_NATIVE_PLUGIN_FAILURE"),
                recoverable=bool(error.get("recoverable", False)),
                recommended_action=error.get("recommended_action"),
                payload=error.get("details"),
            )
        except asyncio.TimeoutError:
            return CommandResult(
                ok=False,
                error=f"Native AutoCAD request timed out: {operation}",
                error_code="E_NATIVE_PIPE_TIMEOUT",
                recoverable=True,
                recommended_action="inspect_job_status_then_retry_with_the_same_idempotency_key",
                payload={"operation": operation, "request_id": request_id},
            )
        except ComProcessBusyError as exc:
            return CommandResult(
                ok=False,
                error=str(exc),
                error_code=exc.error_code,
                recoverable=exc.recoverable,
                recommended_action=exc.recommended_action,
                payload=exc.details,
            )
        except OSError as exc:
            process_exited = _process_is_alive(self.descriptor.process_id) is False
            return CommandResult(
                ok=False,
                error=(
                    "The AutoCAD native worker process exited during pipe I/O"
                    if process_exited
                    else f"Native AutoCAD pipe I/O failed: {exc}"
                ),
                error_code="E_AUTOCAD_CRASHED" if process_exited else "E_NATIVE_PIPE_IO",
                recoverable=True,
                recommended_action=(
                    "inspect_autocad_crash_evidence_then_restart_autocad_and_retry_with_the_same_idempotency_key"
                    if process_exited
                    else "verify_the_native_worker_pipe_and_retry_with_the_same_idempotency_key"
                ),
                payload={
                    "process_id": self.descriptor.process_id,
                    "operation": operation,
                    "exception_type": type(exc).__name__,
                    "system_error": str(exc),
                },
            )
        except NativePipeError as exc:
            if (
                exc.error_code in {"E_NATIVE_PIPE_CLOSED", "E_NATIVE_PIPE_TIMEOUT"}
                and _process_is_alive(self.descriptor.process_id) is False
            ):
                return CommandResult(
                    ok=False,
                    error="The AutoCAD native worker process exited during the request",
                    error_code="E_AUTOCAD_CRASHED",
                    recoverable=True,
                    recommended_action="inspect_autocad_crash_evidence_then_restart_autocad_and_retry_with_the_same_idempotency_key",
                    payload={
                        "process_id": self.descriptor.process_id,
                        "operation": operation,
                        "transport_error": exc.error_code,
                        "transport_details": exc.details,
                    },
                )
            return CommandResult(
                ok=False,
                error=str(exc),
                error_code=exc.error_code,
                recoverable=exc.recoverable,
                recommended_action=exc.recommended_action,
                payload=exc.details,
            )
        except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
            return CommandResult(
                ok=False,
                error=f"Invalid native AutoCAD protocol response: {exc}",
                error_code="E_NATIVE_PROTOCOL_ERROR",
                recoverable=False,
                payload={"operation": operation, "exception_type": type(exc).__name__},
            )

    async def ping(self) -> CommandResult:
        return await self.request("system.ping", session_id=self.descriptor.session_id)
