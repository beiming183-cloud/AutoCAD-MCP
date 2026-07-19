"""Worker ownership, lifecycle state, document identity, and revision leases."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any


class ProcessState(str, Enum):
    ABSENT = "PROCESS_ABSENT"
    STARTING = "PROCESS_STARTING"
    READY = "PROCESS_READY"
    EXITED = "PROCESS_EXITED"
    CRASHED = "PROCESS_CRASHED"


class TransportState(str, Enum):
    DISCONNECTED = "TRANSPORT_DISCONNECTED"
    COM_READY = "COM_READY"
    PLUGIN_READY = "PLUGIN_READY"
    DEGRADED = "TRANSPORT_DEGRADED"


class DocumentState(str, Enum):
    ZERO_DOC = "ZERO_DOC"
    READY = "DOCUMENT_READY"
    BUSY = "DOCUMENT_BUSY"
    CLOSED = "DOCUMENT_CLOSED"


class UiState(str, Enum):
    UNKNOWN = "UI_UNKNOWN"
    IDLE = "UI_IDLE"
    BUSY = "UI_BUSY"
    MODAL = "UI_MODAL"
    USER_TAKEOVER = "UI_USER_TAKEOVER"


@dataclass(frozen=True)
class WorkerIdentity:
    session_id: str
    generation: int
    process_id: int | None
    hwnd: int | None
    owned: bool
    launch_token: str | None
    bound_at: float


@dataclass
class DocumentLease:
    doc_id: str
    document_key: str
    path: str
    name: str
    worker_generation: int
    revision: int
    lease_token: str
    issued_at: float


class SessionRegistry:
    """Thread-safe fencing state shared by COM, plugin, and MCP layers."""

    def __init__(self):
        self._lock = threading.RLock()
        self.process_state = ProcessState.ABSENT
        self.transport_state = TransportState.DISCONNECTED
        self.document_state = DocumentState.ZERO_DOC
        self.ui_state = UiState.UNKNOWN
        self.worker: WorkerIdentity | None = None
        self.documents_by_key: dict[str, DocumentLease] = {}
        self.documents_by_id: dict[str, DocumentLease] = {}
        # Compatibility views used by the legacy backend while it migrates.
        self.doc_ids_by_key: dict[str, str] = {}
        self.doc_revisions: dict[str, int] = {}

    def bind_worker(
        self,
        *,
        process_id: int | None,
        hwnd: int | None,
        owned: bool = False,
        launch_token: str | None = None,
        session_id: str | None = None,
        generation: int | None = None,
    ) -> WorkerIdentity:
        with self._lock:
            changed = (
                self.worker is None
                or self.worker.process_id != process_id
                or self.worker.hwnd != hwnd
                or self.worker.launch_token != launch_token
                or (session_id is not None and self.worker.session_id != session_id)
                or (generation is not None and self.worker.generation != int(generation))
            )
            if changed:
                next_generation = (
                    int(generation)
                    if generation is not None
                    else (1 if self.worker is None else self.worker.generation + 1)
                )
                self.worker = WorkerIdentity(
                    session_id=session_id or f"session-{uuid.uuid4().hex}",
                    generation=next_generation,
                    process_id=process_id,
                    hwnd=hwnd,
                    owned=bool(owned),
                    launch_token=launch_token,
                    bound_at=time.time(),
                )
                self.documents_by_key.clear()
                self.documents_by_id.clear()
                self.doc_ids_by_key.clear()
                self.doc_revisions.clear()
            elif self.worker is not None and self.worker.owned != bool(owned):
                # Ownership is a policy bit, not a new process identity.  A
                # reconnect can learn that the same PID was launched by this
                # backend (or vice versa); update the bit without invalidating
                # otherwise valid document leases.
                self.worker = replace(self.worker, owned=bool(owned), launch_token=launch_token)
            self.process_state = ProcessState.READY
            return self.worker

    def bind_document(
        self,
        document_key: str,
        *,
        path: str,
        name: str,
        force_new: bool = False,
    ) -> DocumentLease:
        with self._lock:
            generation = self.worker.generation if self.worker else 0
            existing = None if force_new else self.documents_by_key.get(document_key)
            if existing is not None and existing.worker_generation == generation:
                existing.path = path
                existing.name = name
                self.document_state = DocumentState.READY
                return existing

            lease = DocumentLease(
                doc_id=f"acad-{uuid.uuid4().hex}",
                document_key=document_key,
                path=path,
                name=name,
                worker_generation=generation,
                revision=0,
                lease_token=f"lease-{uuid.uuid4().hex}",
                issued_at=time.time(),
            )
            self.documents_by_key[document_key] = lease
            self.documents_by_id[lease.doc_id] = lease
            self.doc_ids_by_key[document_key] = lease.doc_id
            self.doc_revisions[lease.doc_id] = 0
            self.document_state = DocumentState.READY
            return lease

    def context(self, lease: DocumentLease) -> dict[str, Any]:
        worker = self.worker
        return {
            "session_id": worker.session_id if worker else None,
            "worker_generation": lease.worker_generation,
            "worker_process_id": worker.process_id if worker else None,
            "worker_owned": worker.owned if worker else False,
            "doc_id": lease.doc_id,
            "active_doc_id": lease.doc_id,
            "requested_path": lease.path,
            "active_path": lease.path,
            "revision": int(lease.revision),
            "lease_token": lease.lease_token,
            "document_name": lease.name,
        }

    def validate(
        self,
        *,
        doc_id: str | None,
        expected_revision: int | None,
        lease_token: str | None = None,
        worker_generation: int | None = None,
    ) -> tuple[bool, str | None, dict[str, Any] | None]:
        with self._lock:
            lease = self.documents_by_id.get(str(doc_id)) if doc_id else None
            if lease is None:
                return False, "E_DOCUMENT_ID_MISMATCH", None
            actual = self.context(lease)
            if self.worker and lease.worker_generation != self.worker.generation:
                return False, "E_SESSION_GENERATION_MISMATCH", actual
            if worker_generation is not None and int(worker_generation) != lease.worker_generation:
                return False, "E_SESSION_GENERATION_MISMATCH", actual
            if lease_token is not None and str(lease_token) != lease.lease_token:
                return False, "E_DOCUMENT_LEASE_MISMATCH", actual
            if expected_revision is None or int(expected_revision) != int(lease.revision):
                return False, "E_DOCUMENT_REVISION_MISMATCH", actual
            return True, None, actual

    def record_mutation(self, doc_id: str) -> DocumentLease | None:
        with self._lock:
            lease = self.documents_by_id.get(str(doc_id))
            if lease is None:
                return None
            lease.revision += 1
            self.doc_revisions[lease.doc_id] = lease.revision
            return lease

    def invalidate_document(self, doc_id: str) -> None:
        with self._lock:
            lease = self.documents_by_id.pop(str(doc_id), None)
            if lease is None:
                return
            self.documents_by_key.pop(lease.document_key, None)
            self.doc_ids_by_key.pop(lease.document_key, None)
            self.doc_revisions.pop(lease.doc_id, None)
            if not self.documents_by_id:
                self.document_state = DocumentState.ZERO_DOC

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "process_state": self.process_state.value,
                "transport_state": self.transport_state.value,
                "document_state": self.document_state.value,
                "ui_state": self.ui_state.value,
                "worker": asdict(self.worker) if self.worker else None,
                "documents": [self.context(item) for item in self.documents_by_id.values()],
            }
