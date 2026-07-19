"""Bounded, serialized admission for AutoCAD transport calls.

The COM and named-pipe layers are both effectively single-lane resources.
This module keeps that policy independent from AutoCAD itself so it can be
used by every backend and tested without starting a process.  A guard admits
requests in FIFO order, limits the number of requests waiting for the lane,
and applies a sliding-window call budget at admission time.

The public ``TransportGuardError.to_dict`` envelope is intentionally close to
the MCP error contract, but this module does not import the server or backend
packages.  Callers can therefore translate it to ``CommandResult`` or a
``CallToolResult`` without creating an import cycle.
"""

from __future__ import annotations

import asyncio
import inspect
import math
import os
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Deque, TypeVar


T = TypeVar("T")


def _bounded_int(value: Any, default: int, lower: int, upper: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(lower, min(upper, parsed))


def _bounded_float(value: Any, default: float, lower: float, upper: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(lower, min(upper, parsed))


@dataclass(frozen=True)
class TransportGuardConfig:
    """Limits used by :class:`TransportGuard`.

    ``max_queue`` counts waiting requests (the active request is not counted).
    ``window_budget`` is measured in admission cost units over
    ``window_seconds``.  Defaults are deliberately conservative enough to
    prevent accidental request storms while remaining suitable for normal
    drawing batches.
    """

    max_queue: int = 32
    window_seconds: float = 60.0
    window_budget: int = 120
    queue_timeout: float = 30.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "max_queue", _bounded_int(self.max_queue, 32, 0, 10000))
        object.__setattr__(
            self,
            "window_seconds",
            _bounded_float(self.window_seconds, 60.0, 0.001, 86400.0),
        )
        object.__setattr__(
            self,
            "window_budget",
            _bounded_int(self.window_budget, 120, 1, 1_000_000),
        )
        object.__setattr__(
            self,
            "queue_timeout",
            _bounded_float(self.queue_timeout, 30.0, 0.0, 3600.0),
        )

    @classmethod
    def from_env(cls, prefix: str = "AUTOCAD_MCP_TRANSPORT_") -> "TransportGuardConfig":
        """Read limits from environment without allowing malformed values to
        break MCP startup.

        Supported variables are ``MAX_QUEUE``, ``WINDOW_SECONDS``,
        ``WINDOW_BUDGET`` and ``QUEUE_TIMEOUT`` under the supplied prefix.
        """

        # Keep the older names as a migration bridge.  New deployments should
        # use the explicit TRANSPORT_* variables; accepting the aliases lets
        # an existing MCP profile be upgraded without silently losing its
        # safety limits.
        def env(name: str, legacy: str, default: Any) -> Any:
            return os.environ.get(
                f"{prefix}{name}", os.environ.get(legacy, default)
            )

        legacy_budget = os.environ.get("AUTOCAD_MCP_MAX_CALLS_PER_WINDOW")
        budget = env("WINDOW_BUDGET", "AUTOCAD_MCP_MAX_CALLS_PER_WINDOW", 120)
        # The provisional gate documented zero as unlimited. Keep that alias
        # behavior during migration; the namespaced setting remains bounded.
        if f"{prefix}WINDOW_BUDGET" not in os.environ and legacy_budget == "0":
            budget = 1_000_000

        return cls(
            max_queue=env("MAX_QUEUE", "AUTOCAD_MCP_TRANSPORT_QUEUE_MAX", 32),
            window_seconds=env(
                "WINDOW_SECONDS", "AUTOCAD_MCP_CALL_WINDOW_SECONDS", 60.0
            ),
            window_budget=budget,
            queue_timeout=env(
                "QUEUE_TIMEOUT", "AUTOCAD_MCP_REQUEST_QUEUE_TIMEOUT", 30.0
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_queue": self.max_queue,
            "window_seconds": self.window_seconds,
            "window_budget": self.window_budget,
            "queue_timeout": self.queue_timeout,
        }


class TransportGuardError(RuntimeError):
    """Structured, recoverable admission failure.

    ``details`` is stable JSON-compatible data and can be placed directly in
    an MCP error response.  No transport or AutoCAD operation is attempted
    when this exception is raised.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str,
        operation: str | None = None,
        details: dict[str, Any] | None = None,
        recoverable: bool = True,
        recommended_action: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = code
        self.operation = operation
        self.recoverable = recoverable
        self.recommended_action = recommended_action
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        error = {
            "code": self.error_code,
            "message": str(self),
            "recoverable": self.recoverable,
        }
        if self.recommended_action:
            error["recommended_action"] = self.recommended_action
        payload: dict[str, Any] = {"ok": False, "error": error}
        if self.operation is not None:
            payload["operation"] = self.operation
        if self.details:
            payload["details"] = dict(self.details)
        return payload


@dataclass(frozen=True)
class TransportLease:
    """A permit returned by ``TransportGuard.acquire``.

    ``release`` is idempotent, which lets cancellation and exception paths
    safely use a ``finally`` block.  The guard reference is private so callers
    cannot accidentally mutate scheduler state.
    """

    operation: str
    request_id: str
    cost: int
    queued_at: float
    started_at: float
    _guard: "TransportGuard" = field(repr=False, compare=False)
    _released: bool = field(default=False, repr=False, compare=False)

    async def release(self) -> None:
        if self._released:
            return
        # Frozen dataclasses still allow this private state transition through
        # object.__setattr__; it keeps the public lease immutable for logging.
        object.__setattr__(self, "_released", True)
        await self._guard._release(self)

    async def __aenter__(self) -> "TransportLease":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.release()


@dataclass
class _Pending:
    operation: str
    request_id: str
    cost: int
    queued_at: float


class TransportGuard:
    """Serialize and rate-limit calls to an AutoCAD transport.

    The guard is event-loop friendly and FIFO within the MCP process.  It has
    no COM, Win32, subprocess, or file side effects.  A single shared guard
    should be used by all transport entry points; ``global_transport_guard``
    provides that instance for normal server integration.
    """

    def __init__(
        self,
        config: TransportGuardConfig | None = None,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.config = config or TransportGuardConfig.from_env()
        self._clock = clock or time.monotonic
        self._condition = asyncio.Condition()
        self._active: TransportLease | None = None
        self._queue: Deque[_Pending] = deque()
        # Entries are (monotonic timestamp, cost, request id), allowing a
        # cancelled request to refund exactly its own reservation even when a
        # deterministic test clock gives several calls the same timestamp.
        self._admissions: Deque[tuple[float, int, str]] = deque()
        self._window_used = 0
        self._closed = False
        self._stats = {
            "admitted": 0,
            "completed": 0,
            "queue_rejected": 0,
            "budget_rejected": 0,
            "queue_timeouts": 0,
            "cancelled": 0,
        }

    @property
    def active(self) -> bool:
        return self._active is not None

    def _prune(self, now: float) -> None:
        cutoff = now - self.config.window_seconds
        while self._admissions and self._admissions[0][0] <= cutoff:
            _, cost, _ = self._admissions.popleft()
            self._window_used -= cost
        if self._window_used < 0:  # defensive against a bad injected clock
            self._window_used = 0

    def _retry_after(self, now: float, cost: int) -> float:
        """Estimate when enough budget should be available."""
        needed = max(0, self._window_used + cost - self.config.window_budget)
        released = 0
        for timestamp, amount, _ in self._admissions:
            released += amount
            if released >= needed:
                return max(0.0, timestamp + self.config.window_seconds - now)
        return self.config.window_seconds

    def _budget_details(self, operation: str, cost: int, now: float) -> dict[str, Any]:
        return {
            "operation": operation,
            "requested_cost": cost,
            "window_used": self._window_used,
            "window_budget": self.config.window_budget,
            "window_seconds": self.config.window_seconds,
            "retry_after_seconds": round(self._retry_after(now, cost), 3),
            "queue_depth": len(self._queue),
            "active": self.active,
            "limits": self.config.to_dict(),
        }

    def _validate_request(self, operation: str, cost: int) -> tuple[str, int]:
        name = str(operation or "").strip()
        if not name:
            raise TransportGuardError(
                "Transport operation must be a non-empty string",
                code="E_TRANSPORT_PARAMETER_REJECTED",
                operation=name or None,
                details={"field": "operation"},
                recoverable=False,
            )
        try:
            weight = int(cost)
            integral = not isinstance(cost, bool) and float(cost) == weight
        except (TypeError, ValueError, OverflowError):
            integral = False
        if not integral:
            raise TransportGuardError(
                "Transport cost must be a positive integer",
                code="E_TRANSPORT_PARAMETER_REJECTED",
                operation=name,
                details={"field": "cost", "value": cost},
                recoverable=False,
            )
        if weight < 1:
            raise TransportGuardError(
                "Transport cost must be a positive integer",
                code="E_TRANSPORT_PARAMETER_REJECTED",
                operation=name,
                details={"field": "cost", "value": cost},
                recoverable=False,
            )
        return name, weight

    async def acquire(
        self,
        operation: str,
        *,
        cost: int = 1,
        timeout: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TransportLease:
        """Wait for and reserve the single transport lane.

        Budget is charged when admitted (before waiting for the active call),
        so a burst cannot hide behind the queue and later exceed the limit.
        Cancellation removes a queued request and refunds its reservation.
        """

        name, weight = self._validate_request(operation, cost)
        if weight > self.config.window_budget:
            raise TransportGuardError(
                "Transport request cost exceeds the configured window budget",
                code="E_TRANSPORT_BUDGET_EXCEEDED",
                operation=name,
                details={
                    "requested_cost": weight,
                    "window_budget": self.config.window_budget,
                    "window_seconds": self.config.window_seconds,
                    "retry_after_seconds": None,
                    "queue_depth": len(self._queue),
                    "limits": self.config.to_dict(),
                    "metadata": metadata or {},
                },
                recommended_action="split_the_request_or_increase_the_transport_window_budget",
            )

        queued_at = self._clock()
        request_id = uuid.uuid4().hex
        pending = _Pending(name, request_id, weight, queued_at)
        try:
            raw_timeout = (
                self.config.queue_timeout if timeout is None else float(timeout)
            )
            if not math.isfinite(raw_timeout) or raw_timeout < 0:
                raise ValueError
            effective_timeout = min(3600.0, raw_timeout)
        except (TypeError, ValueError, OverflowError):
            raise TransportGuardError(
                "Transport queue timeout must be a finite number",
                code="E_TRANSPORT_PARAMETER_REJECTED",
                operation=name,
                details={"field": "timeout", "value": timeout},
                recoverable=False,
            ) from None
        # A zero timeout means "do not wait".  It is still a valid setting
        # for an idle lane; a busy lane receives E_TRANSPORT_QUEUE_TIMEOUT.
        deadline = queued_at + effective_timeout

        async with self._condition:
            if self._closed:
                raise TransportGuardError(
                    "Transport guard is closed",
                    code="E_TRANSPORT_CLOSED",
                    operation=name,
                    recoverable=False,
                    recommended_action="create_a_new_transport_guard_before_retrying",
                )
            # ``max_queue=0`` means "no waiting": an idle lane may still
            # accept one immediate request, while a busy lane rejects the
            # next caller instead of making the setting unusable.
            if len(self._queue) >= self.config.max_queue and (
                self._active is not None or len(self._queue) > 0
            ):
                self._stats["queue_rejected"] += 1
                raise TransportGuardError(
                    "Transport wait queue is full",
                    code="E_TRANSPORT_QUEUE_FULL",
                    operation=name,
                    details={
                        "queue_depth": len(self._queue),
                        "max_queue": self.config.max_queue,
                        "active": self.active,
                        "limits": self.config.to_dict(),
                        "metadata": metadata or {},
                    },
                    recommended_action="wait_for_the_current_transport_batch_to_finish_then_retry",
                )

            now = self._clock()
            self._prune(now)
            if self._window_used + weight > self.config.window_budget:
                self._stats["budget_rejected"] += 1
                raise TransportGuardError(
                    "Transport call budget is exhausted for the current window",
                    code="E_TRANSPORT_BUDGET_EXCEEDED",
                    operation=name,
                    details={**self._budget_details(name, weight, now), "metadata": metadata or {}},
                    recommended_action="wait_for_retry_after_seconds_before_retrying",
                )

            self._admissions.append((now, weight, request_id))
            self._window_used += weight
            self._queue.append(pending)
            self._stats["admitted"] += 1
            try:
                while self._active is not None or self._queue[0] is not pending:
                    if self._closed:
                        raise TransportGuardError(
                            "Transport guard was closed while the request was queued",
                            code="E_TRANSPORT_CLOSED",
                            operation=name,
                            recoverable=False,
                            recommended_action="create_a_new_transport_guard_before_retrying",
                        )
                    remaining = None if deadline is None else max(0.0, deadline - self._clock())
                    if remaining == 0.0:
                        raise asyncio.TimeoutError
                    if remaining is None:
                        await self._condition.wait()
                    else:
                        await asyncio.wait_for(self._condition.wait(), remaining)
                self._queue.popleft()
                started_at = self._clock()
                lease = TransportLease(
                    operation=name,
                    request_id=request_id,
                    cost=weight,
                    queued_at=queued_at,
                    started_at=started_at,
                    _guard=self,
                )
                self._active = lease
                return lease
            except TransportGuardError:
                try:
                    self._queue.remove(pending)
                except ValueError:
                    pass
                for index, (timestamp, amount, token) in enumerate(self._admissions):
                    if token == request_id:
                        del self._admissions[index]
                        self._window_used -= weight
                        break
                self._condition.notify_all()
                raise
            except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
                try:
                    self._queue.remove(pending)
                except ValueError:
                    pass
                # Refund a reservation that never reached the lane.
                for index, (timestamp, amount, token) in enumerate(self._admissions):
                    if token == request_id:
                        del self._admissions[index]
                        self._window_used -= weight
                        break
                if isinstance(exc, asyncio.TimeoutError):
                    self._stats["queue_timeouts"] += 1
                else:
                    self._stats["cancelled"] += 1
                self._condition.notify_all()
                if isinstance(exc, asyncio.TimeoutError):
                    raise TransportGuardError(
                        "Transport queue wait timed out",
                        code="E_TRANSPORT_QUEUE_TIMEOUT",
                        operation=name,
                        details={
                            "queue_depth": len(self._queue),
                            "timeout_seconds": effective_timeout,
                            "limits": self.config.to_dict(),
                            "metadata": metadata or {},
                        },
                        recommended_action="retry_after_the_active_transport_call_finishes",
                    ) from None
                raise

    async def _release(self, lease: TransportLease) -> None:
        async with self._condition:
            if self._active is lease:
                self._active = None
                self._stats["completed"] += 1
                self._condition.notify_all()

    async def execute(
        self,
        operation: str,
        callback: Callable[[], T | Awaitable[T]],
        *,
        cost: int = 1,
        timeout: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> T:
        """Run a sync or async callback under the transport lane."""

        lease = await self.acquire(
            operation, cost=cost, timeout=timeout, metadata=metadata
        )
        try:
            result = callback()
            if inspect.isawaitable(result):
                return await result
            return result
        finally:
            # A cancelled MCP task must not leave the single lane occupied.
            # Shield the release so the condition notification still runs
            # even when cancellation is delivered during the callback.
            await asyncio.shield(lease.release())

    @asynccontextmanager
    async def protect(
        self,
        operation: str,
        *,
        cost: int = 1,
        timeout: float | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        """Context-manager spelling for callers that already own a callback."""

        lease = await self.acquire(
            operation, cost=cost, timeout=timeout, metadata=metadata
        )
        try:
            yield lease
        finally:
            await asyncio.shield(lease.release())

    async def close(self) -> None:
        """Reject future calls and cancel queued calls; never touches CAD."""

        async with self._condition:
            self._closed = True
            self._condition.notify_all()

    async def reset(self) -> None:
        """Reset counters after all active work has completed (test/helper API)."""

        async with self._condition:
            if self._active is not None or self._queue:
                raise RuntimeError("cannot reset a transport guard with work in flight")
            self._admissions.clear()
            self._window_used = 0
            self._closed = False
            for key in self._stats:
                self._stats[key] = 0

    def status(self) -> dict[str, Any]:
        """Return JSON-compatible metrics without waiting on the lane."""

        now = self._clock()
        self._prune(now)
        return {
            "closed": self._closed,
            "active": self.active,
            "queue_depth": len(self._queue),
            "max_queue": self.config.max_queue,
            "window_used": self._window_used,
            "window_budget": self.config.window_budget,
            "window_seconds": self.config.window_seconds,
            "budget_remaining": max(0, self.config.window_budget - self._window_used),
            "limits": self.config.to_dict(),
            "stats": dict(self._stats),
        }


_GLOBAL_LOCK = threading.Lock()
_GLOBAL_GUARD: TransportGuard | None = None


def global_transport_guard(
    config: TransportGuardConfig | None = None,
) -> TransportGuard:
    """Return the process-wide guard used by transport entry points."""

    global _GLOBAL_GUARD
    with _GLOBAL_LOCK:
        if _GLOBAL_GUARD is None:
            _GLOBAL_GUARD = TransportGuard(config)
        return _GLOBAL_GUARD


def reset_global_transport_guard() -> None:
    """Drop the singleton reference (for tests/process reconfiguration)."""

    global _GLOBAL_GUARD
    with _GLOBAL_LOCK:
        _GLOBAL_GUARD = None


__all__ = [
    "TransportGuard",
    "TransportGuardConfig",
    "TransportGuardError",
    "TransportLease",
    "global_transport_guard",
    "reset_global_transport_guard",
]
