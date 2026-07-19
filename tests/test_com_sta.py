"""Dedicated STA executor regression tests without requiring AutoCAD."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from autocad_mcp.com_sta import (
    ComProcessBusyError,
    ComStaExecutor,
    ComStaTimeoutError,
    ComStaUnavailableError,
    RPC_E_CALL_REJECTED,
)


class RejectedCall(RuntimeError):
    hresult = RPC_E_CALL_REJECTED


def test_com_executor_serializes_concurrent_async_calls(monkeypatch):
    monkeypatch.setattr("autocad_mcp.com_sta.sys.platform", "linux")
    executor = ComStaExecutor()
    active = 0
    maximum_active = 0
    thread_ids = set()
    lock = threading.Lock()

    def operation(value):
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
            thread_ids.add(threading.get_ident())
        time.sleep(0.005)
        with lock:
            active -= 1
        return value

    async def run_calls():
        return await asyncio.gather(
            *(
                executor.call_async(
                    f"test.{index}", lambda index=index: operation(index)
                )
                for index in range(20)
            )
        )

    values = asyncio.run(run_calls())
    executor.close()

    assert values == list(range(20))
    assert maximum_active == 1
    assert len(thread_ids) == 1


def test_rejected_call_retries_only_when_declared_idempotent(monkeypatch):
    monkeypatch.setattr("autocad_mcp.com_sta.sys.platform", "linux")
    monkeypatch.setattr("autocad_mcp.com_sta.random.uniform", lambda *_: 0.0)
    executor = ComStaExecutor()
    attempts = {"count": 0}

    def rejected_twice():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RejectedCall("busy")
        return "ready"

    assert executor.call("read", rejected_twice, idempotent=True) == "ready"
    assert attempts["count"] == 3
    assert executor.snapshot()["retries"] == 2

    attempts["count"] = 0
    with pytest.raises(RejectedCall):
        executor.call("write", rejected_twice, idempotent=False)
    assert attempts["count"] == 1
    executor.close()


def test_timeout_quarantines_sta_and_rejects_follow_up_work(monkeypatch):
    monkeypatch.setattr("autocad_mcp.com_sta.sys.platform", "linux")
    executor = ComStaExecutor()
    entered = threading.Event()
    release = threading.Event()

    def blocked():
        entered.set()
        release.wait(2.0)
        return "released"

    with pytest.raises(ComStaTimeoutError):
        executor.call("blocked", blocked, timeout=0.05)
    assert entered.wait(0.5)
    assert executor.snapshot()["poisoned"] is True
    with pytest.raises(ComStaUnavailableError):
        executor.call("after-timeout", lambda: "should-not-run", timeout=0.1)

    release.set()
    executor.close(timeout=1.0)


def test_cross_process_lease_is_noop_off_windows(monkeypatch):
    monkeypatch.setattr("autocad_mcp.com_sta.sys.platform", "linux")
    from autocad_mcp.com_sta import _ComProcessLease

    with _ComProcessLease(0.1) as lease:
        assert lease.handle is None


def test_cross_process_busy_error_has_structured_recovery():
    error = ComProcessBusyError("Local\\AutoCAD-MCP-COM-user", 2.5)

    assert error.error_code == "E_AUTOCAD_COM_BUSY"
    assert error.recoverable is True
    assert error.details["timeout_seconds"] == 2.5
