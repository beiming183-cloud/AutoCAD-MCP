"""Worker generation and document lease fencing tests."""

from autocad_mcp.session import SessionRegistry


def test_worker_replacement_invalidates_old_document_leases():
    registry = SessionRegistry()
    first_worker = registry.bind_worker(process_id=100, hwnd=200, owned=True)
    lease = registry.bind_document("hwnd:300", path="D:/a.dwg", name="a.dwg")
    valid, error, _ = registry.validate(
        doc_id=lease.doc_id,
        expected_revision=0,
        lease_token=lease.lease_token,
        worker_generation=first_worker.generation,
    )
    assert valid is True
    assert error is None

    second_worker = registry.bind_worker(process_id=101, hwnd=201, owned=True)
    valid, error, actual = registry.validate(
        doc_id=lease.doc_id,
        expected_revision=0,
        lease_token=lease.lease_token,
        worker_generation=first_worker.generation,
    )

    assert second_worker.generation == first_worker.generation + 1
    assert valid is False
    assert error == "E_DOCUMENT_ID_MISMATCH"
    assert actual is None


def test_document_revision_and_lease_token_are_fenced():
    registry = SessionRegistry()
    worker = registry.bind_worker(process_id=100, hwnd=200)
    lease = registry.bind_document("hwnd:300", path="D:/part.dwg", name="part.dwg")
    registry.record_mutation(lease.doc_id)

    stale, stale_error, _ = registry.validate(
        doc_id=lease.doc_id,
        expected_revision=0,
        lease_token=lease.lease_token,
        worker_generation=worker.generation,
    )
    wrong_lease, lease_error, _ = registry.validate(
        doc_id=lease.doc_id,
        expected_revision=1,
        lease_token="lease-wrong",
        worker_generation=worker.generation,
    )

    assert stale is False
    assert stale_error == "E_DOCUMENT_REVISION_MISMATCH"
    assert wrong_lease is False
    assert lease_error == "E_DOCUMENT_LEASE_MISMATCH"


def test_ownership_refresh_does_not_invalidate_same_worker_document_lease():
    registry = SessionRegistry()
    worker = registry.bind_worker(process_id=100, hwnd=200, owned=False)
    lease = registry.bind_document("hwnd:300", path="D:/part.dwg", name="part.dwg")

    refreshed = registry.bind_worker(
        process_id=100, hwnd=200, owned=True, session_id=worker.session_id,
        generation=worker.generation,
    )

    assert refreshed.generation == worker.generation
    assert refreshed.owned is True
    valid, error, _ = registry.validate(
        doc_id=lease.doc_id,
        expected_revision=0,
        lease_token=lease.lease_token,
        worker_generation=worker.generation,
    )
    assert valid is True
    assert error is None
