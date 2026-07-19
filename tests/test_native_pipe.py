from __future__ import annotations

import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from autocad_mcp.backends.file_ipc import FileIPCBackend
from autocad_mcp.native_pipe import (
    NativePipeClient,
    NativePipeError,
    NativeWorkerDescriptor,
    discover_native_worker,
)


def _descriptor_file(root: Path, process_id: int, *, session: str = "native-test") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{process_id}.json"
    path.write_text(
        json.dumps(
            {
                "protocolVersion": 1,
                "capabilityVersion": 2,
                "pluginVersion": "4.0.0",
                "pipeName": f"autocad-mcp-{process_id}",
                "sessionId": session,
                "processId": process_id,
                "hwnd": 9876,
                "capabilities": ["document.context", "transaction.execute"],
                "startedAt": "2026-07-17T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    return path


def _framed_response(payload: dict) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return struct.pack("<I", len(body)) + body


def test_descriptor_and_live_worker_discovery(tmp_path):
    path = _descriptor_file(tmp_path, 123)
    descriptor = NativeWorkerDescriptor.read(path)

    assert descriptor.capability_version == 2
    assert descriptor.hwnd == 9876
    assert descriptor.capabilities[-1] == "transaction.execute"
    assert discover_native_worker(root=tmp_path, process_probe=lambda pid: pid == 123) == descriptor


def test_worker_discovery_fails_closed_when_multiple_instances_are_live(tmp_path):
    _descriptor_file(tmp_path, 111, session="one")
    selected = _descriptor_file(tmp_path, 222, session="two")

    with pytest.raises(NativePipeError) as caught:
        discover_native_worker(root=tmp_path, process_probe=lambda _: True)
    assert caught.value.error_code == "E_AUTOCAD_INSTANCE_AMBIGUOUS"

    descriptor = discover_native_worker(
        root=tmp_path,
        preferred_process_id=222,
        process_probe=lambda _: True,
    )
    assert descriptor.path == selected.resolve()


async def test_pipe_client_frames_json_and_normalizes_native_context(tmp_path):
    descriptor = NativeWorkerDescriptor.read(_descriptor_file(tmp_path, 123))
    observed = {}

    def transport(frame: bytes, timeout: float) -> bytes:
        declared = struct.unpack("<I", frame[:4])[0]
        request = json.loads(frame[4:].decode("utf-8"))
        observed.update(request=request, declared=declared, timeout=timeout)
        return _framed_response(
            {
                "id": request["id"],
                "ok": True,
                "payload": {
                    "sessionId": "native-test",
                    "docId": "acad-1",
                    "activeDocId": "acad-1",
                    "expectedRevision": 4,
                    "nestedValue": {"featureId": "feature-1"},
                },
            }
        )

    client = NativePipeClient(descriptor, token="secret", transport=transport)
    result = await client.request(
        "document.context",
        doc_id="acad-1",
        expected_revision=4,
        request_id="request-1",
    )

    assert result.ok
    assert observed["declared"] == len(json.dumps(observed["request"], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    assert observed["request"]["token"] == "secret"
    assert result.payload["active_doc_id"] == "acad-1"
    assert result.payload["nested_value"]["feature_id"] == "feature-1"


async def test_pipe_client_preserves_structured_plugin_error(tmp_path):
    descriptor = NativeWorkerDescriptor.read(_descriptor_file(tmp_path, 123))

    def transport(frame: bytes, _: float) -> bytes:
        request = json.loads(frame[4:].decode("utf-8"))
        return _framed_response(
            {
                "id": request["id"],
                "ok": False,
                "error": {
                    "code": "E_DOCUMENT_REVISION_MISMATCH",
                    "message": "stale revision",
                    "recoverable": False,
                    "recommendedAction": "read_document_context_and_retry",
                    "details": {"actualRevision": 8},
                },
            }
        )

    result = await NativePipeClient(descriptor, transport=transport).request("transaction.execute")

    assert not result.ok
    assert result.error_code == "E_DOCUMENT_REVISION_MISMATCH"
    assert result.recommended_action == "read_document_context_and_retry"
    assert result.payload == {"actual_revision": 8}


async def test_pipe_client_rejects_non_object_response_without_raising(tmp_path):
    descriptor = NativeWorkerDescriptor.read(_descriptor_file(tmp_path, 123))

    def transport(frame: bytes, _: float) -> bytes:
        request = json.loads(frame[4:].decode("utf-8"))
        body = json.dumps([request["id"], True]).encode("utf-8")
        return struct.pack("<I", len(body)) + body

    result = await NativePipeClient(descriptor, transport=transport).request("system.ping")

    assert not result.ok
    assert result.error_code == "E_NATIVE_PROTOCOL_ERROR"


async def test_pipe_client_rejects_non_object_error_envelope_without_raising(tmp_path):
    descriptor = NativeWorkerDescriptor.read(_descriptor_file(tmp_path, 123))

    def transport(frame: bytes, _: float) -> bytes:
        request = json.loads(frame[4:].decode("utf-8"))
        return _framed_response(
            {"id": request["id"], "ok": False, "error": "malformed"}
        )

    result = await NativePipeClient(descriptor, transport=transport).request("system.ping")

    assert not result.ok
    assert result.error_code == "E_NATIVE_PROTOCOL_ERROR"


async def test_pipe_client_reports_cross_process_busy(monkeypatch, tmp_path):
    descriptor = NativeWorkerDescriptor.read(_descriptor_file(tmp_path, 123))

    class BusyLease:
        def __init__(self, _timeout):
            pass

        def __enter__(self):
            from autocad_mcp.com_sta import ComProcessBusyError

            raise ComProcessBusyError("Local\\AutoCAD-MCP-COM-user", 0.1)

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr("autocad_mcp.native_pipe.ComProcessLease", BusyLease)
    result = await NativePipeClient(
        descriptor,
        transport=lambda *_: b"never-called",
    ).request("transaction.execute")

    assert not result.ok
    assert result.error_code == "E_AUTOCAD_COM_BUSY"
    assert result.recoverable is True


async def test_pipe_client_classifies_worker_exit_during_transport(monkeypatch, tmp_path):
    descriptor = NativeWorkerDescriptor.read(_descriptor_file(tmp_path, 123))
    monkeypatch.setattr("autocad_mcp.native_pipe._process_is_alive", lambda _: False)

    def transport(_frame: bytes, _timeout: float) -> bytes:
        raise OSError(232, "The pipe is being closed")

    result = await NativePipeClient(descriptor, transport=transport).request(
        "transaction.execute"
    )

    assert not result.ok
    assert result.error_code == "E_AUTOCAD_CRASHED"
    assert result.payload["process_id"] == 123


class _FakeNativeClient:
    def __init__(self):
        self.calls = []

    async def request(self, operation, **kwargs):
        self.calls.append((operation, kwargs))
        if operation == "document.context":
            from autocad_mcp.backends.base import CommandResult

            return CommandResult(
                ok=True,
                payload={
                    "session_id": "native-test",
                    "doc_id": "acad-1",
                    "active_doc_id": "acad-1",
                    "revision": 3,
                    "active_path": "Drawing1.dwg",
                },
            )
        if operation == "document.create":
            from autocad_mcp.backends.base import CommandResult

            return CommandResult(
                ok=True,
                payload={
                    "session_id": "native-test",
                    "doc_id": "acad-2",
                    "active_doc_id": "acad-2",
                    "revision": 0,
                    "active_path": kwargs["data"].get("path", "Drawing2.dwg"),
                    "document_name": "Drawing2.dwg",
                    "diff": [],
                },
            )
        from autocad_mcp.backends.base import CommandResult

        return CommandResult(ok=True, payload={"revision": 4, "transaction_state": "committed"})


async def test_file_backend_native_path_bypasses_com_sta_and_executes_transaction(tmp_path):
    backend = FileIPCBackend()
    fake = _FakeNativeClient()
    backend._native_client = fake

    context = await backend.document_context()
    created = await backend.drawing_create(
        str(tmp_path / "part.dwg"), idempotency_key="create-part"
    )
    executed = await backend.native_transaction_execute(
        "acad-2",
        0,
        "build-part",
        [{"type": "solid.cylinder", "result_id": "bore", "base_center": [0, 0, 0], "radius": 1, "height": 2}],
        session_id="native-test",
    )

    assert context.ok and context.payload["transport"] == "native_pipe"
    assert created.ok and created.payload["name_honored"] is True
    assert executed.ok
    assert backend._com_executor.snapshot()["ready"] is False
    assert fake.calls[-1][0] == "transaction.execute"
    assert fake.calls[-1][1]["expected_revision"] == 0
    native_operation = fake.calls[-1][1]["data"]["operations"][0]
    assert native_operation["resultId"] == "bore"
    assert native_operation["baseCenter"] == [0, 0, 0]


async def test_native_document_creation_requires_idempotency_key():
    backend = FileIPCBackend()
    backend._native_client = _FakeNativeClient()

    result = await backend.drawing_create(None)

    assert not result.ok
    assert result.error_code == "E_PARAMETER_REJECTED"


def test_backend_detection_prefers_native_worker_without_pywin32(monkeypatch):
    from autocad_mcp import config

    monkeypatch.setattr(config, "WIN32_AVAILABLE", True)
    monkeypatch.setenv("AUTOCAD_MCP_BACKEND", "auto")
    monkeypatch.setenv("AUTOCAD_MCP_NATIVE_PLUGIN", "auto")
    monkeypatch.setattr(
        "autocad_mcp.native_pipe.discover_native_worker",
        lambda: SimpleNamespace(process_id=123, session_id="native-test"),
    )
    monkeypatch.setattr(
        config,
        "win32_runtime_health",
        lambda: {"ok": False, "checks": {"pywintypes": False}},
    )

    assert config.detect_backend() == "file_ipc"
