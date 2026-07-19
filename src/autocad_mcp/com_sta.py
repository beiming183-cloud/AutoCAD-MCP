"""Single-threaded COM execution for the AutoCAD compatibility channel.

AutoCAD ActiveX is an STA-oriented API.  This module keeps every compatibility
COM call on one dedicated thread, pumps Windows messages between calls, and only
retries rejected calls when the caller declares the operation idempotent.
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import queue
import random
import sys
import threading
import time
from concurrent.futures import Future, InvalidStateError
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, TypeVar


T = TypeVar("T")

RPC_E_CALL_REJECTED = -2147418111
RPC_E_SERVERCALL_RETRYLATER = -2147417846
RETRYABLE_HRESULTS = {RPC_E_CALL_REJECTED, RPC_E_SERVERCALL_RETRYLATER}


class ComStaTimeoutError(TimeoutError):
    """A COM call exceeded its boundary and poisoned the STA worker."""

    error_code = "E_COM_STA_TIMEOUT"
    recoverable = False
    recommended_action = "restart_the_mcp_worker_before_retrying_autocad_com"

    def __init__(self, operation: str, timeout: float | None):
        self.operation = operation
        self.timeout = timeout
        self.details = {
            "operation": operation,
            "timeout_seconds": timeout,
            "sta_poisoned": True,
        }
        super().__init__(
            f"COM operation timed out and the STA worker was quarantined: {operation}"
        )


class ComStaUnavailableError(RuntimeError):
    """The STA cannot accept work after a timeout or explicit close."""

    error_code = "E_COM_STA_UNAVAILABLE"
    recoverable = False
    recommended_action = "restart_the_mcp_worker_before_retrying_autocad_com"

    def __init__(self, *, poisoned: bool, operation: str | None = None):
        self.operation = operation
        self.details = {
            "operation": operation,
            "sta_poisoned": bool(poisoned),
        }
        state = "poisoned" if poisoned else "closed"
        super().__init__(f"COM STA worker is {state}; refusing new work")


class ComProcessBusyError(RuntimeError):
    """Another MCP process currently owns the AutoCAD COM turn."""

    error_code = "E_AUTOCAD_COM_BUSY"
    recoverable = True
    recommended_action = "wait_for_the_other_autocad_mcp_request_then_retry"

    def __init__(self, mutex_name: str, timeout: float):
        self.mutex_name = mutex_name
        self.timeout = timeout
        self.details = {
            "mutex_name": mutex_name,
            "timeout_seconds": timeout,
            "cross_process_serialization": True,
        }
        super().__init__(
            f"Another AutoCAD MCP process owns the COM turn: {mutex_name}"
        )


class ComProcessLease:
    """Small named-mutex lease shared by all MCP processes in this session."""

    _WAIT_OBJECT_0 = 0x00000000
    _WAIT_ABANDONED = 0x00000080
    _WAIT_TIMEOUT = 0x00000102
    _INFINITE = 0xFFFFFFFF

    def __init__(self, timeout: float):
        self.timeout = max(0.05, float(timeout))
        session = os.environ.get("AUTOCAD_MCP_COM_MUTEX_SCOPE", "user").strip()
        # ``Local`` keeps independent Windows sessions from blocking one
        # another.  The product suffix prevents unrelated CAD integrations
        # from sharing this lock accidentally.
        safe_scope = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in session)
        self.name = f"Local\\AutoCAD-MCP-COM-{safe_scope or 'user'}"
        self.handle = None
        self._acquired = False
        self._enabled = (
            sys.platform == "win32"
            and os.environ.get("AUTOCAD_MCP_COM_MUTEX", "true").strip().lower()
            in ("1", "true", "yes", "on")
        )

    def __enter__(self):
        if not self._enabled:
            return self
        try:
            kernel32 = ctypes.windll.kernel32
            self.handle = kernel32.CreateMutexW(None, False, self.name)
            if not self.handle:
                # A restricted host should not turn a healthy single-process
                # installation into a hard failure; the STA still serializes
                # calls within this process.
                self._enabled = False
                return self
            result = kernel32.WaitForSingleObject(
                self.handle, int(self.timeout * 1000.0)
            )
            if result not in (self._WAIT_OBJECT_0, self._WAIT_ABANDONED):
                if result == self._WAIT_TIMEOUT:
                    raise ComProcessBusyError(self.name, self.timeout)
                raise OSError(f"WaitForSingleObject returned 0x{result:08X}")
            self._acquired = True
            return self
        except ComProcessBusyError:
            self._close()
            raise
        except Exception:
            self._close()
            self._enabled = False
            return self

    def _close(self) -> None:
        if self.handle:
            try:
                kernel32 = ctypes.windll.kernel32
                if self._enabled and self._acquired:
                    kernel32.ReleaseMutex(self.handle)
                kernel32.CloseHandle(self.handle)
            except Exception:
                pass
            self.handle = None
            self._acquired = False

    def __exit__(self, exc_type, exc, traceback):
        self._close()
        return False


# Backward-compatible internal name for older tests/integrations.
_ComProcessLease = ComProcessLease


def com_process_mutex_info() -> dict[str, Any]:
    """Expose the cross-process COM serialization policy for diagnostics."""
    lease = ComProcessLease(0.05)
    raw_timeout = os.environ.get("AUTOCAD_MCP_COM_MUTEX_TIMEOUT", "5")
    try:
        timeout = max(0.05, min(120.0, float(raw_timeout)))
    except (TypeError, ValueError):
        timeout = 5.0
    return {
        "enabled": lease._enabled,
        "name": lease.name,
        "timeout_seconds": timeout,
    }


def com_hresult(error: BaseException) -> int | None:
    """Extract a signed HRESULT from pywin32 and compatible exceptions."""
    value = getattr(error, "hresult", None)
    if value is None and getattr(error, "args", None):
        value = error.args[0]
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    if value > 0x7FFFFFFF:
        value -= 0x100000000
    return value


@dataclass
class _WorkItem:
    operation: str
    callback: Callable[[], Any]
    future: Future
    idempotent: bool
    max_retries: int


class ComStaExecutor:
    """Serialize COM work on a dedicated STA thread with a message pump."""

    def __init__(self, name: str = "autocad-mcp-com-sta"):
        self.name = name
        self._queue: queue.Queue[_WorkItem | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._start_lock = threading.Lock()
        self._ready = threading.Event()
        self._closed = threading.Event()
        self._poisoned = False
        self._poison_reason: dict[str, Any] | None = None
        self._startup_error: BaseException | None = None
        self._pythoncom = None
        self._metrics = {
            "calls": 0,
            "retries": 0,
            "failures": 0,
            "timeouts": 0,
            "cross_process_busy": 0,
        }

    @property
    def in_executor_thread(self) -> bool:
        return self._thread_id == threading.get_ident()

    def _start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        with self._start_lock:
            if self._thread and self._thread.is_alive():
                return
            if self._poisoned:
                raise ComStaUnavailableError(poisoned=True)
            if self._closed.is_set():
                raise ComStaUnavailableError(poisoned=False)
            self._ready.clear()
            self._startup_error = None
            self._thread = threading.Thread(
                target=self._run,
                name=self.name,
                daemon=True,
            )
            self._thread.start()
        if not self._ready.wait(5.0):
            raise TimeoutError("COM STA executor did not initialize within 5 seconds")
        if self._startup_error is not None:
            raise RuntimeError("COM STA executor failed to initialize") from self._startup_error

    def _initialize_com(self):
        if sys.platform != "win32":
            return None
        import pythoncom

        initialize_ex = getattr(pythoncom, "CoInitializeEx", None)
        if initialize_ex is not None:
            initialize_ex(getattr(pythoncom, "COINIT_APARTMENTTHREADED", 2))
        else:
            pythoncom.CoInitialize()
        return pythoncom

    def _pump(self) -> None:
        if self._pythoncom is None:
            return
        pump = getattr(self._pythoncom, "PumpWaitingMessages", None)
        if pump is not None:
            pump()

    def _run(self) -> None:
        self._thread_id = threading.get_ident()
        try:
            self._pythoncom = self._initialize_com()
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
            self._thread_id = None
            return

        self._ready.set()
        try:
            while not self._closed.is_set():
                try:
                    item = self._queue.get(timeout=0.02)
                except queue.Empty:
                    self._pump()
                    continue
                if item is None:
                    break
                self._execute(item)
                self._pump()
        finally:
            if self._pythoncom is not None:
                uninitialize = getattr(self._pythoncom, "CoUninitialize", None)
                if uninitialize is not None:
                    try:
                        uninitialize()
                    except Exception:
                        pass
            self._thread_id = None

    def _execute(self, item: _WorkItem) -> None:
        attempt = 0
        while True:
            if item.future.cancelled():
                return
            self._metrics["calls"] += 1
            try:
                mutex_timeout = os.environ.get("AUTOCAD_MCP_COM_MUTEX_TIMEOUT", "5")
                try:
                    mutex_timeout_value = max(0.05, min(120.0, float(mutex_timeout)))
                except (TypeError, ValueError):
                    mutex_timeout_value = 5.0
                try:
                    with ComProcessLease(mutex_timeout_value):
                        result = item.callback()
                except ComProcessBusyError:
                    self._metrics["cross_process_busy"] += 1
                    raise
                if not item.future.cancelled():
                    try:
                        item.future.set_result(result)
                    except InvalidStateError:
                        # The async waiter may have timed out while the COM
                        # callback was returning. Its result is intentionally
                        # discarded; the caller already received the timeout.
                        pass
                return
            except BaseException as exc:
                if item.future.cancelled():
                    return
                retryable = (
                    item.idempotent
                    and com_hresult(exc) in RETRYABLE_HRESULTS
                    and attempt < item.max_retries
                )
                if not retryable:
                    self._metrics["failures"] += 1
                    try:
                        item.future.set_exception(exc)
                    except InvalidStateError:
                        pass
                    return
                attempt += 1
                self._metrics["retries"] += 1
                delay = min(0.05 * (2 ** (attempt - 1)), 0.8)
                delay += random.uniform(0.0, min(0.025, delay / 4.0))
                deadline = time.monotonic() + delay
                while time.monotonic() < deadline:
                    self._pump()
                    time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))

    def submit(
        self,
        operation: str,
        callback: Callable[[], T],
        *,
        idempotent: bool = False,
        max_retries: int = 6,
    ) -> Future:
        if self._poisoned:
            raise ComStaUnavailableError(poisoned=True, operation=operation)
        if self._closed.is_set():
            raise ComStaUnavailableError(poisoned=False, operation=operation)
        if self.in_executor_thread:
            future: Future = Future()
            try:
                future.set_result(callback())
            except BaseException as exc:
                future.set_exception(exc)
            return future
        self._start()
        future = Future()
        self._queue.put(
            _WorkItem(
                operation=operation,
                callback=callback,
                future=future,
                idempotent=bool(idempotent),
                max_retries=max(0, int(max_retries)),
            )
        )
        return future

    def _poison(self, operation: str, timeout: float | None) -> None:
        """Quarantine a potentially blocked callback and fail queued work."""
        self._poisoned = True
        self._poison_reason = {
            "operation": operation,
            "timeout_seconds": timeout,
            "recorded_at_epoch": time.time(),
        }
        self._closed.set()
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is not None and not item.future.done():
                try:
                    item.future.set_exception(
                        ComStaUnavailableError(poisoned=True, operation=item.operation)
                    )
                except InvalidStateError:
                    pass
        # The callback may still be running. The daemon STA thread will exit
        # as soon as it returns; the caller must restart the worker before any
        # further COM work is attempted.
        self._queue.put(None)

    def call(
        self,
        operation: str,
        callback: Callable[[], T],
        *,
        idempotent: bool = False,
        max_retries: int = 6,
        timeout: float | None = 30.0,
    ) -> T:
        future = self.submit(
            operation,
            callback,
            idempotent=idempotent,
            max_retries=max_retries,
        )
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            self._metrics["timeouts"] += 1
            future.cancel()
            self._poison(operation, timeout)
            raise ComStaTimeoutError(operation, timeout) from None

    async def call_async(
        self,
        operation: str,
        callback: Callable[[], T],
        *,
        idempotent: bool = False,
        max_retries: int = 6,
        timeout: float | None = 30.0,
    ) -> T:
        concurrent_future = self.submit(
            operation,
            callback,
            idempotent=idempotent,
            max_retries=max_retries,
        )
        wrapped = asyncio.wrap_future(concurrent_future)
        try:
            if timeout is None:
                return await wrapped
            return await asyncio.wait_for(wrapped, timeout)
        except asyncio.TimeoutError:
            self._metrics["timeouts"] += 1
            concurrent_future.cancel()
            self._poison(operation, timeout)
            raise ComStaTimeoutError(operation, timeout) from None

    def snapshot(self) -> dict[str, Any]:
        return {
            "ready": bool(self._thread and self._thread.is_alive() and self._ready.is_set()),
            "closed": self._closed.is_set(),
            "poisoned": self._poisoned,
            "poison_reason": dict(self._poison_reason) if self._poison_reason else None,
            "thread_id": self._thread_id,
            "pending": self._queue.qsize(),
            "cross_process_mutex": com_process_mutex_info(),
            **self._metrics,
        }

    def cooperative_sleep(self, seconds: float) -> None:
        """Sleep on the STA thread while continuing to pump Windows messages."""
        deadline = time.monotonic() + max(0.0, float(seconds))
        while time.monotonic() < deadline:
            if self.in_executor_thread:
                self._pump()
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))

    def close(self, timeout: float = 2.0) -> None:
        if not self._closed.is_set():
            self._closed.set()
            self._queue.put(None)
        if self._thread and self._thread.is_alive() and not self.in_executor_thread:
            self._thread.join(max(0.0, timeout))


def sta_sync_method(
    operation: str | None = None,
    *,
    idempotent: bool = False,
    timeout: float | None = 30.0,
):
    """Run a synchronous instance method through ``self._com_executor``."""

    def decorate(method):
        @wraps(method)
        def wrapped(self, *args, **kwargs):
            executor: ComStaExecutor = self._com_executor
            if executor.in_executor_thread:
                return method(self, *args, **kwargs)
            return executor.call(
                operation or method.__name__,
                lambda: method(self, *args, **kwargs),
                idempotent=idempotent,
                timeout=timeout,
            )

        return wrapped

    return decorate


def sta_async_method(
    operation: str | None = None,
    *,
    idempotent: bool = False,
    timeout: float | None = 30.0,
):
    """Run a COM-only coroutine on the STA thread.

    Decorated coroutines must not await objects tied to the MCP server event
    loop.  They may call other STA-decorated methods, which execute inline.
    """

    def decorate(method):
        @wraps(method)
        async def wrapped(self, *args, **kwargs):
            executor: ComStaExecutor = self._com_executor
            if executor.in_executor_thread:
                return await method(self, *args, **kwargs)

            def invoke():
                return asyncio.run(method(self, *args, **kwargs))

            return await executor.call_async(
                operation or method.__name__,
                invoke,
                idempotent=idempotent,
                timeout=timeout,
            )

        return wrapped

    return decorate
