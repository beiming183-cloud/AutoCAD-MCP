"""Offline tests for the transport admission gate.

These tests deliberately use callbacks and an injected clock only.  They do
not import pywin32, connect to AutoCAD, or start a subprocess.
"""

from __future__ import annotations

import asyncio

import pytest

from autocad_mcp.transport_guard import (
    TransportGuard,
    TransportGuardConfig,
    TransportGuardError,
    global_transport_guard,
    reset_global_transport_guard,
)


@pytest.mark.asyncio
async def test_calls_are_fifo_and_single_lane():
    guard = TransportGuard(TransportGuardConfig(max_queue=8, queue_timeout=1))
    entered: list[str] = []
    release_first = asyncio.Event()

    async def first():
        entered.append("first")
        await release_first.wait()
        return 1

    async def second():
        entered.append("second")
        return 2

    task1 = asyncio.create_task(guard.execute("line", first))
    await asyncio.sleep(0)
    task2 = asyncio.create_task(guard.execute("circle", second))
    await asyncio.sleep(0)
    assert entered == ["first"]
    assert guard.status()["queue_depth"] == 1

    release_first.set()
    assert await task1 == 1
    assert await task2 == 2
    assert entered == ["first", "second"]
    assert guard.status()["active"] is False


@pytest.mark.asyncio
async def test_queue_is_bounded_and_error_is_structured():
    guard = TransportGuard(TransportGuardConfig(max_queue=1, queue_timeout=1))
    hold = asyncio.Event()

    async def blocked():
        await hold.wait()

    first = asyncio.create_task(guard.execute("first", blocked))
    await asyncio.sleep(0)
    waiting = asyncio.create_task(guard.execute("second", blocked))
    await asyncio.sleep(0)

    with pytest.raises(TransportGuardError) as caught:
        await guard.acquire("third")
    error = caught.value
    assert error.error_code == "E_TRANSPORT_QUEUE_FULL"
    payload = error.to_dict()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "E_TRANSPORT_QUEUE_FULL"
    assert payload["details"]["queue_depth"] == 1
    assert payload["details"]["max_queue"] == 1

    hold.set()
    await first
    await waiting


@pytest.mark.asyncio
async def test_window_budget_rejects_without_running_callback():
    guard = TransportGuard(
        TransportGuardConfig(max_queue=4, window_seconds=60, window_budget=2)
    )
    calls = 0

    async def callback():
        nonlocal calls
        calls += 1

    await guard.execute("one", callback)
    with pytest.raises(TransportGuardError) as caught:
        await guard.execute("two", callback, cost=2)
    error = caught.value
    assert error.error_code == "E_TRANSPORT_BUDGET_EXCEEDED"
    assert calls == 1
    details = error.to_dict()["details"]
    assert details["window_used"] == 1
    assert details["window_budget"] == 2
    assert details["requested_cost"] == 2
    assert details["retry_after_seconds"] >= 0


@pytest.mark.asyncio
async def test_budget_expires_with_injected_clock():
    now = [100.0]
    guard = TransportGuard(
        TransportGuardConfig(max_queue=2, window_seconds=10, window_budget=1),
        clock=lambda: now[0],
    )
    await guard.execute("one", lambda: None)
    with pytest.raises(TransportGuardError):
        await guard.acquire("too-soon")
    now[0] += 10.1
    lease = await guard.acquire("after-window")
    await lease.release()
    assert guard.status()["window_used"] == 1


@pytest.mark.asyncio
async def test_queue_timeout_refunds_budget_and_does_not_stick_lane():
    guard = TransportGuard(TransportGuardConfig(max_queue=2, queue_timeout=1))
    hold = asyncio.Event()

    async def blocked():
        await hold.wait()

    first = asyncio.create_task(guard.execute("first", blocked))
    await asyncio.sleep(0)
    with pytest.raises(TransportGuardError) as caught:
        await guard.acquire("timed", timeout=0.01)
    assert caught.value.error_code == "E_TRANSPORT_QUEUE_TIMEOUT"
    assert guard.status()["queue_depth"] == 0
    hold.set()
    await first


@pytest.mark.asyncio
async def test_close_rejects_queued_work_without_affecting_active_callback():
    guard = TransportGuard(TransportGuardConfig(max_queue=2, queue_timeout=1))
    hold = asyncio.Event()

    async def blocked():
        await hold.wait()

    first = asyncio.create_task(guard.execute("first", blocked))
    await asyncio.sleep(0)
    queued = asyncio.create_task(guard.acquire("queued"))
    await asyncio.sleep(0)
    await guard.close()
    with pytest.raises(TransportGuardError) as caught:
        await queued
    assert caught.value.error_code == "E_TRANSPORT_CLOSED"
    hold.set()
    await first


def test_environment_config_is_bounded(monkeypatch):
    monkeypatch.setenv("AUTOCAD_MCP_TRANSPORT_MAX_QUEUE", "-4")
    monkeypatch.setenv("AUTOCAD_MCP_TRANSPORT_WINDOW_SECONDS", "bad")
    monkeypatch.setenv("AUTOCAD_MCP_TRANSPORT_WINDOW_BUDGET", "7")
    config = TransportGuardConfig.from_env()
    assert config.max_queue == 0
    assert config.window_seconds == 60.0
    assert config.window_budget == 7


def test_global_guard_is_process_singleton():
    reset_global_transport_guard()
    first = global_transport_guard(TransportGuardConfig(window_budget=3))
    second = global_transport_guard(TransportGuardConfig(window_budget=99))
    assert first is second
    assert second.config.window_budget == 3
    reset_global_transport_guard()
