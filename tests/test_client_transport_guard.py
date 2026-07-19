"""Transport safety tests: serialized calls and bounded tool output."""

from __future__ import annotations

import asyncio
import base64
import json

import pytest

from autocad_mcp.backends.base import CommandResult
from autocad_mcp.client import (
    McpAdmissionError,
    RequestAdmission,
    _json,
    _screenshot_result,
)


@pytest.mark.asyncio
async def test_request_admission_serializes_concurrent_calls(monkeypatch):
    monkeypatch.setenv("AUTOCAD_MCP_MAX_CALLS_PER_WINDOW", "0")
    gate = RequestAdmission()
    active = 0
    maximum = 0

    async def work():
        nonlocal active, maximum
        await gate.acquire("test")
        try:
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.005)
        finally:
            active -= 1
            gate.release()

    await asyncio.gather(*(work() for _ in range(8)))
    assert maximum == 1


@pytest.mark.asyncio
async def test_request_admission_returns_bounded_rate_error(monkeypatch):
    monkeypatch.setenv("AUTOCAD_MCP_MAX_CALLS_PER_WINDOW", "2")
    monkeypatch.setenv("AUTOCAD_MCP_CALL_WINDOW_SECONDS", "60")
    gate = RequestAdmission()
    for _ in range(2):
        await gate.acquire("test")
        gate.release()

    with pytest.raises(McpAdmissionError) as caught:
        await gate.acquire("test")
    assert caught.value.error_code == "E_MCP_CALL_RATE_LIMITED"
    assert caught.value.details["max_calls"] == 2


def test_large_json_is_replaced_by_small_evidence_envelope(monkeypatch):
    monkeypatch.setenv("AUTOCAD_MCP_MAX_RESPONSE_BYTES", "65536")
    serialized = _json({"ok": True, "entities": ["x" * 1000 for _ in range(200)]})
    payload = json.loads(serialized)
    assert payload["truncated"] is True
    assert payload["original_bytes"] > payload["max_response_bytes"]
    assert len(serialized.encode("utf-8")) < 65536


def test_screenshot_response_persists_path_without_inline_base64(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOCAD_MCP_OUTPUT_ROOT", str(tmp_path))
    # Minimal valid PNG signature + IHDR dimensions; persistence does not
    # need a full decoder and should never echo the raw image in the result.
    raw = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\x0dIHDR"
        b"\x00\x00\x00\x10\x00\x00\x00\x10\x08\x02\x00\x00\x00"
    )
    response = _screenshot_result(
        CommandResult(ok=True, payload=base64.b64encode(raw).decode("ascii")),
        include_image=False,
        stem="test-capture",
    )
    payload = json.loads(response)
    metadata = payload["payload"]["screenshot"]
    assert metadata["inline"] is False
    assert metadata["bytes"] == len(raw)
    assert metadata["width"] == 16 and metadata["height"] == 16
    assert "base64" not in response
