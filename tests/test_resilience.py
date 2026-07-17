"""Crash classification, offline DXF, structured errors, and Windows text tests."""

from __future__ import annotations

import io
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import ezdxf
import fitz
from PIL import Image

from autocad_mcp.backends.base import CommandResult
from autocad_mcp.backends.file_ipc import FileIPCBackend
from autocad_mcp.backends.ezdxf_backend import EzdxfBackend
from autocad_mcp.errors import exception_context
from autocad_mcp.logging_setup import UTF8_BOM, TeeLogWriter
from autocad_mcp.offline import audit_dxf_offline


async def test_ensure_ready_reports_crash_instead_of_missing_document(monkeypatch):
    import autocad_mcp.backends.file_ipc as file_ipc

    backend = FileIPCBackend()
    backend._hwnd = 123
    backend._acad_process_id = 456
    monkeypatch.setattr(file_ipc, "find_autocad_window", lambda: None)
    monkeypatch.setattr(
        file_ipc,
        "detect_autocad_crash_state",
        lambda hwnd=None, process_id=None: {
            "crashed": True,
            "reason": "process_exited",
            "process_id": 456,
        },
    )

    result = await backend.ensure_ready()

    assert result.ok is False
    assert result.error_code == "E_AUTOCAD_CRASHED"
    assert result.payload["crash"]["reason"] == "process_exited"


async def test_dispatcher_failure_is_contained_and_classified_as_autocad_crash(
    monkeypatch, tmp_path
):
    import autocad_mcp.backends.file_ipc as file_ipc

    backend = FileIPCBackend()
    backend._ipc_dir = tmp_path
    backend._hwnd = 123
    backend._acad_process_id = 456
    monkeypatch.setattr(backend, "_type_dispatch_trigger", lambda: False)
    monkeypatch.setattr(
        file_ipc,
        "detect_autocad_crash_state",
        lambda hwnd=None, process_id=None: {
            "crashed": True,
            "reason": "fatal_error_dialog",
            "dialog": {"title": "AutoCAD Error Aborting"},
        },
    )

    result = await backend._dispatch("create-line", {"x1": 0})

    assert result.ok is False
    assert result.error_code == "E_AUTOCAD_CRASHED"
    assert result.payload["operation"] == "create-line"
    assert list(tmp_path.iterdir()) == []


def test_first_document_is_created_without_active_document_dependency(monkeypatch):
    import autocad_mcp.backends.file_ipc as file_ipc

    document = types.SimpleNamespace(Name="Drawing1.dwg", FullName="")
    documents = types.SimpleNamespace(Count=0, Add=MagicMock(return_value=document))
    application = types.SimpleNamespace(Documents=documents, ActiveDocument=document)
    client = types.SimpleNamespace(GetActiveObject=MagicMock(return_value=application))
    win32com = types.SimpleNamespace(client=client)
    pythoncom = types.SimpleNamespace(CoInitialize=lambda: None)
    monkeypatch.setitem(sys.modules, "pythoncom", pythoncom)
    monkeypatch.setitem(sys.modules, "win32com", win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", client)
    monkeypatch.setattr(
        file_ipc,
        "detect_autocad_crash_state",
        lambda hwnd=None, process_id=None: {"crashed": False},
    )

    result = FileIPCBackend()._ensure_active_document()

    assert result["ready"] is True
    assert result["created_first_document"] is True
    assert result["stability_reads"] == 3
    documents.Add.assert_called_once_with()


def test_audit_dxf_is_fully_offline(tmp_path):
    path = tmp_path / "离线检查.dxf"
    document = ezdxf.new("R2013")
    document.modelspace().add_line((0, 0), (10, 0))
    document.saveas(path)

    result = audit_dxf_offline({"path": str(path)})

    assert result.ok is True
    assert result.payload["offline"] is True
    assert result.payload["backend_required"] is False
    assert result.payload["entity_count"] == 1


def test_errno_is_returned_with_operation_system_call_path_and_recovery_context(tmp_path):
    path = tmp_path / "坏路径.dxf"
    error = OSError(22, "参数错误", str(path))

    message, details = exception_context(
        error,
        operation="drawing.audit_dxf",
        parameters={"path": str(path), "limit": 50},
        system_call="ezdxf.readfile",
        file_path=str(path),
    )

    assert message.startswith("drawing.audit_dxf failed during ezdxf.readfile")
    assert details["operation"] == "drawing.audit_dxf"
    assert details["parameter_fields"] == ["limit", "path"]
    assert details["system_call"] == "ezdxf.readfile"
    assert details["file_path"] == str(path)
    assert details["errno"] == 22
    assert details["system_message"] == "参数错误"


def test_letter_pdf_is_rejected_when_a3_landscape_was_requested(tmp_path):
    path = tmp_path / "letter.pdf"
    document = fitz.open()
    document.new_page(width=11 * 72, height=8.5 * 72)
    document.save(path)
    document.close()
    backend = FileIPCBackend()
    page = backend._read_pdf_page(str(path))
    requested = {
        "path": str(path.resolve()),
        "paper": "A3",
        "orientation": "landscape",
        "scale_mode": "fit",
        "scale": "1:1",
    }
    actual = {
        "path": str(path.resolve()),
        "scale_mode": "fit",
        "scale": "fit",
        "pdf_page": page,
    }

    differences = backend._plot_differences(requested, actual)

    assert page["detected_paper"] == "LETTER"
    assert any(item["path"] == "paper" for item in differences)


def test_utf8_bom_log_round_trips_chinese_in_windows_style_path(tmp_path):
    path = tmp_path / "中文日志" / "运行.log"
    stream = io.StringIO()
    writer = TeeLogWriter(stream, path)

    writer.write("AutoCAD 致命错误已捕获\n")
    writer.flush()

    content = path.read_bytes()
    assert content.startswith(UTF8_BOM)
    assert content[len(UTF8_BOM) :].decode("utf-8") == "AutoCAD 致命错误已捕获\n"
    assert stream.getvalue() == "AutoCAD 致命错误已捕获\n"


async def test_document_id_and_revision_reject_wrong_or_stale_mutations():
    backend = EzdxfBackend()
    await backend.initialize()
    context = (await backend.document_context()).payload

    wrong = await backend.require_document_context("wrong-doc", context["revision"])
    accepted = await backend.require_document_context(
        context["doc_id"], context["revision"]
    )
    advanced = await backend.record_document_mutation(context["doc_id"])
    stale = await backend.require_document_context(
        context["doc_id"], context["revision"]
    )

    assert wrong.error_code == "E_DOCUMENT_ID_MISMATCH"
    assert accepted.ok is True
    assert advanced.payload["revision"] == context["revision"] + 1
    assert stale.error_code == "E_DOCUMENT_REVISION_MISMATCH"


async def test_two_documents_switch_100_times_without_identity_or_revision_leak(monkeypatch):
    import autocad_mcp.backends.file_ipc as file_ipc

    class FakeDocument:
        def __init__(self, application, name, hwnd):
            self._application = application
            self.Name = name
            self.FullName = f"D:/CAD-Automation/drawings/{name}"
            self.HWND = hwnd

        def Activate(self):
            self._application.ActiveDocument = self

    class FakeDocuments:
        def __init__(self, documents):
            self._documents = documents

        @property
        def Count(self):
            return len(self._documents)

        def Item(self, index):
            return self._documents[index]

    application = types.SimpleNamespace()
    first = FakeDocument(application, "first.dwg", 1001)
    second = FakeDocument(application, "second.dwg", 1002)
    application.Documents = FakeDocuments([first, second])
    application.ActiveDocument = first
    client = types.SimpleNamespace(GetActiveObject=MagicMock(return_value=application))
    monkeypatch.setitem(sys.modules, "pythoncom", types.SimpleNamespace(CoInitialize=lambda: None))
    monkeypatch.setitem(sys.modules, "win32com", types.SimpleNamespace(client=client))
    monkeypatch.setitem(sys.modules, "win32com.client", client)

    backend = FileIPCBackend()
    first_context = backend._bind_document(first)
    second_context = backend._bind_document(second)

    for expected_revision in range(100):
        activated_first = await backend.drawing_activate(first_context["doc_id"])
        assert activated_first.ok is True
        assert activated_first.payload["active_doc_id"] == first_context["doc_id"]
        first_mutation = await backend.record_document_mutation(first_context["doc_id"])
        assert first_mutation.payload["revision"] == expected_revision + 1

        activated_second = await backend.drawing_activate(second_context["doc_id"])
        assert activated_second.ok is True
        assert activated_second.payload["active_doc_id"] == second_context["doc_id"]
        second_mutation = await backend.record_document_mutation(second_context["doc_id"])
        assert second_mutation.payload["revision"] == expected_revision + 1

    assert first_context["doc_id"] != second_context["doc_id"]
    assert backend._doc_revisions[first_context["doc_id"]] == 100
    assert backend._doc_revisions[second_context["doc_id"]] == 100


async def test_transaction_begin_commit_and_rollback_have_scoped_state(monkeypatch):
    backend = FileIPCBackend()
    state = {"revision": 0}
    commands = []

    async def document_context():
        return CommandResult(
            ok=True,
            payload={
                "doc_id": "doc-1",
                "active_doc_id": "doc-1",
                "requested_path": "D:/CAD-Automation/drawings/a.dwg",
                "active_path": "D:/CAD-Automation/drawings/a.dwg",
                "revision": state["revision"],
            },
        )

    async def dispatch(command, params):
        commands.append(command)
        return CommandResult(ok=True, payload={"command": command})

    monkeypatch.setattr(backend, "document_context", document_context)
    monkeypatch.setattr(backend, "_dispatch", dispatch)

    committed = await backend.transaction_begin("doc-1", 0)
    committed_id = committed.payload["transaction_id"]
    commit = await backend.transaction_commit(committed_id, "doc-1", 0)
    state["revision"] = commit.payload["revision"]

    rolled_back = await backend.transaction_begin("doc-1", 1)
    rolled_back_id = rolled_back.payload["transaction_id"]
    rollback = await backend.transaction_rollback(rolled_back_id, "doc-1", 1)

    assert commit.ok is True
    assert commit.payload["state"] == "committed"
    assert commit.payload["revision"] == 1
    assert rollback.ok is True
    assert rollback.payload["state"] == "rolled_back"
    assert rollback.payload["revision"] == 2
    assert backend._transactions == {}
    assert commands == [
        "transaction-begin",
        "transaction-commit",
        "transaction-begin",
        "transaction-rollback",
    ]


def test_landscape_png_is_rotated_once_and_reports_actual_dimensions(tmp_path):
    path = tmp_path / "portrait-device-output.png"
    Image.new("RGB", (120, 240), "white").save(path)

    width, height, corrected = FileIPCBackend._correct_png_orientation(
        path, "landscape"
    )
    second_width, second_height, corrected_again = (
        FileIPCBackend._correct_png_orientation(path, "landscape")
    )

    assert (width, height, corrected) == (240, 120, True)
    assert (second_width, second_height, corrected_again) == (240, 120, False)


async def test_missing_layer_fails_before_entity_creation_and_preserves_count():
    backend = EzdxfBackend()
    await backend.initialize()
    before = (await backend.entity_count()).payload["count"]

    result = await backend.create_batch(
        [
            {
                "type": "line",
                "x1": 0,
                "y1": 0,
                "x2": 10,
                "y2": 0,
                "layer": "DOES_NOT_EXIST",
            }
        ],
        atomic=True,
    )

    after = (await backend.entity_count()).payload["count"]
    assert result.ok is False
    assert result.payload["results"][0]["error"]["code"] == "E_LAYER_NOT_FOUND"
    assert before == after == 0


async def test_server_layer_precondition_returns_structured_error():
    from autocad_mcp.server import _require_existing_layer

    backend = EzdxfBackend()
    await backend.initialize()

    result = await _require_existing_layer(backend, "DOES_NOT_EXIST")

    assert result.ok is False
    assert result.error_code == "E_LAYER_NOT_FOUND"
    assert result.payload == {
        "layer": "DOES_NOT_EXIST",
        "entity_created": False,
    }


def test_industrial_capability_matrix_does_not_advertise_unimplemented_3d():
    matrix = FileIPCBackend._industrial_capability_matrix()

    assert "document_identity" in matrix["supported"]
    assert "atomic_entity_batches" in matrix["supported"]
    assert "fillet_edges" in matrix["unsupported"]
    assert "shell" in matrix["unsupported"]
    assert "parametric_assembly" in matrix["unsupported"]
    assert "offscreen_3d_render" in matrix["unsupported"]
    assert not set(matrix["supported"]) & set(matrix["unsupported"])


async def test_locked_pdf_publish_returns_structured_error_and_removes_staging(
    monkeypatch, tmp_path
):
    import autocad_mcp.backends.file_ipc as file_ipc

    backend = FileIPCBackend()
    output = tmp_path / "locked.pdf"

    def fake_plot(path, *args):
        document = fitz.open()
        document.new_page(width=420 / 25.4 * 72, height=297 / 25.4 * 72)
        document.save(path)
        document.close()
        return {
            "path": str(path),
            "paper": "A3",
            "orientation": "landscape",
            "scale_mode": "fit",
            "scale": "fit",
        }

    monkeypatch.setattr(backend, "_plot_preview_via_com", fake_plot)
    monkeypatch.setattr(
        backend,
        "_publish_staged_file",
        MagicMock(side_effect=PermissionError(13, "文件被占用", str(output))),
    )

    result = await backend.drawing_plot_pdf(str(output))

    assert result.ok is False
    assert result.error_code == "E_OUTPUT_LOCKED"
    assert result.payload["operation"] == "drawing.plot_pdf.publish"
    assert result.payload["system_call"] == "os.replace"
    assert result.payload["staging_removed"] is True
    assert not output.exists()
    assert list(tmp_path.glob("*.tmp.pdf")) == []


def test_atomic_publish_retries_a_short_autocad_plot_lock(monkeypatch, tmp_path):
    import autocad_mcp.backends.file_ipc as file_ipc

    staging = tmp_path / ".preview.tmp.pdf"
    output = tmp_path / "preview.pdf"
    staging.write_bytes(b"PDF")
    real_replace = file_ipc.os.replace
    attempts = {"count": 0}

    def temporarily_locked(source, destination):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise PermissionError(32, "AutoCAD still owns the plot file", str(source))
        return real_replace(source, destination)

    monkeypatch.setattr(file_ipc.os, "replace", temporarily_locked)
    monkeypatch.setattr(file_ipc.time, "sleep", lambda _: None)

    publication = FileIPCBackend()._publish_staged_file(
        staging, output, timeout=1.0
    )

    assert attempts["count"] == 3
    assert publication["wait_seconds"] >= 0
    assert publication["mode"] == "atomic-rename"
    assert output.read_bytes() == b"PDF"
    assert not staging.exists()


def test_atomic_publish_copies_away_from_long_autocad_source_lock(
    monkeypatch, tmp_path
):
    import autocad_mcp.backends.file_ipc as file_ipc

    backend = FileIPCBackend()
    staging = tmp_path / ".locked-by-autocad.tmp.pdf"
    output = tmp_path / "drawing.pdf"
    staging.write_bytes(b"PDF-CONTENT")
    real_replace = file_ipc.os.replace

    def source_locked(source, destination):
        if Path(source) == staging:
            raise PermissionError(32, "AutoCAD owns source", str(source))
        return real_replace(source, destination)

    monkeypatch.setattr(file_ipc.os, "replace", source_locked)
    monkeypatch.setattr(file_ipc.time, "sleep", lambda _: None)

    publication = backend._publish_staged_file(staging, output, timeout=0.0)

    assert publication["mode"] == "copy-then-atomic-rename"
    assert publication["staging_removed"] is True
    assert output.read_bytes() == b"PDF-CONTENT"
    assert list(tmp_path.glob("*.publish.pdf")) == []


def test_output_viewer_guard_targets_only_the_generated_pdf(monkeypatch, tmp_path):
    import autocad_mcp.backends.file_ipc as file_ipc

    target = tmp_path / ".generated.tmp.pdf"
    calls = {"hidden": [], "closed": [], "restored": []}
    foreground = {"value": 20}

    def enum_windows(callback, state):
        callback(20, state)
        callback(30, state)

    win32gui = types.SimpleNamespace(
        GetForegroundWindow=lambda: foreground["value"],
        GetWindowText=lambda hwnd: (
            f"{target.name} - WPS Office" if hwnd == 20 else "User document.pdf - WPS Office"
        ),
        ShowWindow=lambda hwnd, mode: calls["hidden"].append((hwnd, mode)),
        PostMessage=lambda hwnd, message, wparam, lparam: calls["closed"].append(
            (hwnd, message)
        ),
        SetForegroundWindow=lambda hwnd: calls["restored"].append(hwnd),
        IsWindow=lambda hwnd: True,
        EnumWindows=enum_windows,
    )
    win32con = types.SimpleNamespace(SW_HIDE=0, WM_CLOSE=16)
    win32process = types.SimpleNamespace(
        GetWindowThreadProcessId=lambda hwnd: (1, 2000 + hwnd)
    )
    monkeypatch.setattr(file_ipc.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "win32gui", win32gui)
    monkeypatch.setitem(sys.modules, "win32con", win32con)
    monkeypatch.setitem(sys.modules, "win32process", win32process)

    state = FileIPCBackend._start_output_viewer_guard(target)
    result = FileIPCBackend._stop_output_viewer_guard(state, grace=0.05)

    assert result["viewer_detected"] is True
    assert result["viewer_suppressed"] is True
    assert [item[0] for item in calls["hidden"]] == [20]
    assert [item[0] for item in calls["closed"]] == [20]
    assert all(event["title"].startswith(target.name) for event in result["events"])
