"""Durable idempotency journal tests."""

import pytest

from autocad_mcp.backends.base import CommandResult
from autocad_mcp.journal import OperationJournal
import autocad_mcp.server as server


def test_committed_operation_replays_and_conflicting_request_is_rejected(tmp_path):
    journal = OperationJournal(tmp_path / "journal")
    first = journal.begin(
        "create-part-1",
        operation="solid.create_box",
        request={"length": 10},
        context={"doc_id": "doc-1", "revision": 0},
    )
    journal.commit("create-part-1", {"ok": True, "payload": {"handle": "A1"}})
    replay = journal.begin(
        "create-part-1",
        operation="solid.create_box",
        request={"length": 10},
        context={"doc_id": "doc-1", "revision": 0},
    )
    conflict = journal.begin(
        "create-part-1",
        operation="solid.create_box",
        request={"length": 11},
        context={"doc_id": "doc-1", "revision": 0},
    )

    assert first.action == "execute"
    assert replay.action == "replay"
    assert replay.record["result"]["payload"]["handle"] == "A1"
    assert conflict.action == "conflict"


def test_failed_operation_can_start_a_new_attempt(tmp_path):
    journal = OperationJournal(tmp_path / "journal")
    journal.begin("op-1", operation="entity.line", request={"x": 1})
    journal.fail("op-1", {"code": "E_AUTOCAD_BUSY"}, retryable=True)

    retry = journal.begin("op-1", operation="entity.line", request={"x": 1})

    assert retry.action == "execute"
    assert retry.record["attempt"] == 2


def test_nonretryable_failure_is_replayed_without_executing_again(tmp_path):
    journal = OperationJournal(tmp_path / "journal")
    journal.begin("op-terminal", operation="solid.boolean", request={"primary": "A"})
    journal.fail(
        "op-terminal",
        {
            "ok": False,
            "error": {
                "code": "E_MUTATION_CONTEXT_UNAVAILABLE",
                "message": "mutation completed but context readback failed",
                "recoverable": False,
            },
            "details": {"handle": "B", "mutation_committed": True},
        },
        retryable=False,
    )

    replay = journal.begin(
        "op-terminal", operation="solid.boolean", request={"primary": "A"}
    )

    assert replay.action == "replay"
    assert replay.record["error"]["details"]["handle"] == "B"


def test_fail_if_accepted_closes_only_matching_open_operation(tmp_path):
    journal = OperationJournal(tmp_path / "journal")
    journal.begin("open-1", operation="entity.create_line", request={"x": 1})

    closed = journal.fail_if_accepted(
        "open-1",
        {"ok": False, "error": {"code": "E_INTERNAL", "message": "boom"}},
        operation="entity.create_line",
    )
    assert closed is not None
    assert closed["state"] == "failed"
    assert closed["abandoned"] is True

    # A committed or differently-owned record must never be overwritten by
    # an exception handler racing with the normal journal epilogue.
    journal.begin("open-2", operation="entity.create_circle", request={"r": 1})
    untouched = journal.fail_if_accepted(
        "open-2",
        {"ok": False},
        operation="entity.create_line",
    )
    assert untouched is not None
    assert untouched["state"] == "accepted"


@pytest.mark.parametrize("result_ok", [True, False])
def test_finish_journaled_mutation_recovers_after_finalization_error_without_mutating_result(
    monkeypatch, result_ok
):
    """A failed commit/fail must close the accepted record once and preserve data."""

    class FailingFinalizer:
        def __init__(self):
            self.recovery_calls = []

        def commit(self, key, result):
            raise OSError("simulated journal replace failure")

        def fail(self, key, result, *, retryable):
            raise OSError("simulated journal replace failure")

        def fail_if_accepted(self, key, error, *, retryable=False):
            self.recovery_calls.append((key, error, retryable))
            return {"state": "failed", "abandoned": True}

    journal = FailingFinalizer()
    monkeypatch.setattr(server, "_journal", journal)
    source_payload = {"handle": "A1", "nested": {"requested": [1, 2]}}
    source = CommandResult(ok=result_ok, payload=source_payload, error="native failure" if not result_ok else None)

    outcome = server._finish_journaled_mutation("journal-key", source)

    assert not outcome.ok
    assert outcome.error_code == "E_IDEMPOTENCY_JOURNAL_FAILED"
    assert len(journal.recovery_calls) == 1
    assert journal.recovery_calls[0][0] == "journal-key"
    assert outcome.payload["journal_recovery"]["attempted"] is True
    assert outcome.payload["journal_recovery"]["state"] == "failed"
    assert outcome.payload["mutation_committed"] is result_ok
    # The backend result and the durable snapshot remain untouched by the
    # reconciliation metadata added to the wrapper response.
    assert source_payload == {"handle": "A1", "nested": {"requested": [1, 2]}}
    assert outcome.payload["original_result"]["details" if not result_ok else "payload"] == (
        source_payload
    )


def test_finish_journaled_mutation_reports_unknown_when_recovery_also_fails(monkeypatch):
    class UnavailableJournal:
        def commit(self, key, result):
            raise OSError("commit unavailable")

        def fail_if_accepted(self, key, error, *, retryable=False):
            raise PermissionError("journal locked")

    monkeypatch.setattr(server, "_journal", UnavailableJournal())
    source = CommandResult(ok=True, payload={"handle": "A2"})

    outcome = server._finish_journaled_mutation("unknown-key", source)

    assert not outcome.ok
    assert outcome.payload["journal_state"] == "unknown"
    recovery = outcome.payload["journal_recovery"]
    assert recovery["attempted"] is True
    assert recovery["error"]["type"] == "PermissionError"
    assert outcome.payload["original_result"] == {
        "ok": True,
        "payload": {"handle": "A2"},
    }
