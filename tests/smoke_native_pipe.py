"""Dependency-light smoke test for the client-independent native pipe contract."""

from __future__ import annotations

import asyncio
import json
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autocad_mcp.native_pipe import (
    NativePipeClient,
    NativePipeError,
    NativeWorkerDescriptor,
    discover_native_worker,
)


def _frame(payload: dict) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return struct.pack("<I", len(body)) + body


async def _run() -> dict:
    with tempfile.TemporaryDirectory(prefix="autocad-mcp-native-") as raw:
        root = Path(raw)
        descriptor_path = root / "123.json"
        descriptor_path.write_text(
            json.dumps(
                {
                    "protocolVersion": 1,
                    "capabilityVersion": 2,
                    "pluginVersion": "4.0.0",
                    "pipeName": "autocad-mcp-123",
                    "sessionId": "native-smoke",
                    "processId": 123,
                    "hwnd": 9876,
                    "capabilities": ["document.context", "transaction.execute"],
                }
            ),
            encoding="utf-8",
        )
        descriptor = NativeWorkerDescriptor.read(descriptor_path)
        assert discover_native_worker(root=root, process_probe=lambda pid: pid == 123) == descriptor

        def transport(frame: bytes, timeout: float) -> bytes:
            assert timeout > 0
            request = json.loads(frame[4:].decode("utf-8"))
            declared = struct.unpack("<I", frame[:4])[0]
            assert declared == len(frame) - 4
            return _frame(
                {
                    "id": request["id"],
                    "ok": True,
                    "payload": {
                        "sessionId": "native-smoke",
                        "docId": "acad-1",
                        "activeDocId": "acad-1",
                        "revision": 4,
                        "nestedValue": {"featureId": "feature-1"},
                    },
                }
            )

        client = NativePipeClient(descriptor, token="smoke-token", transport=transport)
        result = await client.request(
            "document.context",
            doc_id="acad-1",
            expected_revision=4,
            request_id="native-smoke-request",
        )
        assert result.ok
        assert result.payload["active_doc_id"] == "acad-1"
        assert result.payload["nested_value"]["feature_id"] == "feature-1"

        def error_transport(frame: bytes, _: float) -> bytes:
            request = json.loads(frame[4:].decode("utf-8"))
            return _frame(
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

        failed = await NativePipeClient(
            descriptor, transport=error_transport
        ).request("transaction.execute")
        assert not failed.ok
        assert failed.error_code == "E_DOCUMENT_REVISION_MISMATCH"
        assert failed.payload == {"actual_revision": 8}

        second = root / "456.json"
        second.write_text(descriptor_path.read_text(encoding="utf-8").replace("123", "456"), encoding="utf-8")
        try:
            discover_native_worker(root=root, process_probe=lambda _: True)
        except NativePipeError as exc:
            assert exc.error_code == "E_AUTOCAD_INSTANCE_AMBIGUOUS"
        else:
            raise AssertionError("ambiguous native workers were not rejected")

    return {"status": "passed", "protocol": 1, "error_mapping": True, "ambiguity_gate": True}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(_run()), ensure_ascii=False))
