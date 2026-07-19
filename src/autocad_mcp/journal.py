"""Durable idempotency journal for AutoCAD mutation jobs."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autocad_mcp.workspace import ensure_workspace, sanitize_name, write_json_atomic


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class JournalDecision:
    action: str
    record: dict[str, Any]


class OperationJournal:
    """Persist accepted/committed/failed mutations using atomic JSON writes."""

    def __init__(self, root: Path | None = None):
        workspace = ensure_workspace()
        self.root = Path(root or (workspace["jobs"] / "_journal")).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, idempotency_key: str) -> Path:
        safe = sanitize_name(idempotency_key, "operation")
        suffix = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:12]
        return self.root / f"{safe[:64]}-{suffix}.json"

    def read(self, idempotency_key: str) -> dict[str, Any] | None:
        path = self._path(idempotency_key)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    def begin(
        self,
        idempotency_key: str,
        *,
        operation: str,
        request: Any,
        context: dict[str, Any] | None = None,
        accepted_timeout: float = 300.0,
    ) -> JournalDecision:
        if not str(idempotency_key).strip():
            raise ValueError("idempotency_key must not be empty")
        request_hash = canonical_digest(request)
        with self._lock:
            existing = self.read(idempotency_key)
            if existing is not None:
                if existing.get("request_hash") != request_hash:
                    return JournalDecision("conflict", existing)
                if existing.get("state") == "committed":
                    return JournalDecision("replay", existing)
                if existing.get("state") == "failed" and not bool(
                    existing.get("retryable", False)
                ):
                    # A terminal failure may still have committed native side
                    # effects (for example when context readback failed after
                    # a successful mutation). Replaying the recorded result is
                    # safer than executing the same write again.
                    return JournalDecision("replay", existing)
                accepted_age = time.time() - float(existing.get("updated_at", 0.0))
                if (
                    existing.get("state") == "accepted"
                    and accepted_age < max(1.0, float(accepted_timeout))
                ):
                    return JournalDecision("in_progress", existing)

            now = time.time()
            record = {
                "schema_version": 1,
                "idempotency_key": idempotency_key,
                "operation": operation,
                "request_hash": request_hash,
                "state": "accepted",
                "accepted_at": now,
                "updated_at": now,
                "attempt": int((existing or {}).get("attempt", 0)) + 1,
                "context": context or {},
            }
            write_json_atomic(self._path(idempotency_key), record)
            return JournalDecision("execute", record)

    def commit(self, idempotency_key: str, result: Any) -> dict[str, Any]:
        with self._lock:
            record = self.read(idempotency_key)
            if record is None:
                raise KeyError(f"Unknown idempotency key: {idempotency_key}")
            record.update(
                state="committed",
                updated_at=time.time(),
                committed_at=time.time(),
                result=result,
                result_hash=canonical_digest(result),
            )
            write_json_atomic(self._path(idempotency_key), record)
            return record

    def fail(self, idempotency_key: str, error: Any, *, retryable: bool) -> dict[str, Any]:
        with self._lock:
            record = self.read(idempotency_key)
            if record is None:
                raise KeyError(f"Unknown idempotency key: {idempotency_key}")
            record.update(
                state="failed",
                updated_at=time.time(),
                failed_at=time.time(),
                retryable=bool(retryable),
                error=error,
                error_hash=canonical_digest(error),
            )
            write_json_atomic(self._path(idempotency_key), record)
            return record

    def fail_if_accepted(
        self,
        idempotency_key: str,
        error: Any,
        *,
        retryable: bool = False,
        operation: str | None = None,
    ) -> dict[str, Any] | None:
        """Close an abandoned request without overwriting a newer outcome.

        Tool wrappers can catch exceptions after ``begin`` but before their
        normal ``commit``/``fail`` epilogue.  In that window the durable record
        would otherwise remain ``accepted`` forever and every retry would be
        reported as ``E_OPERATION_IN_PROGRESS``.  This helper is deliberately
        conditional: it only changes an existing record that is still
        accepted, and (when supplied) belongs to the same operation.  A
        concurrent commit/failure therefore wins over the recovery attempt.
        """
        with self._lock:
            record = self.read(idempotency_key)
            if record is None or record.get("state") != "accepted":
                return record
            if operation and str(record.get("operation")) != str(operation):
                return record
            record.update(
                state="failed",
                updated_at=time.time(),
                failed_at=time.time(),
                retryable=bool(retryable),
                error=error,
                error_hash=canonical_digest(error),
                abandoned=True,
            )
            write_json_atomic(self._path(idempotency_key), record)
            return record
