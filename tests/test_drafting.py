"""Tests for drafting profiles, safe text, and structured batches."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from autocad_mcp.backends.ezdxf_backend import EzdxfBackend
from autocad_mcp.backends.file_ipc import FileIPCBackend
from autocad_mcp.drafting import encode_autocad_text, lineweight_hundredths, tangent_arc_from_start


def test_encode_autocad_text_preserves_ascii_and_encodes_chinese():
    assert encode_autocad_text("A轴") == r"A\U+8F74"
    assert encode_autocad_text("%%c50") == "%%c50"


def test_lineweight_hundredths():
    assert lineweight_hundredths("0.20") == 20
    assert lineweight_hundredths(0.5) == 50
    assert lineweight_hundredths(-3) == -3


def test_tangent_arc_is_solved_from_shared_geometry():
    geometry = tangent_arc_from_start([0, 0], [10, 10], [1, 0])

    assert geometry["center"] == [0, 10]
    assert geometry["radius"] == 10
    assert geometry["start_angle"] == 270
    assert geometry["end_angle"] == 0


async def test_mechanical_profile_creates_required_layers():
    backend = EzdxfBackend()
    await backend.initialize()

    result = await backend.drawing_setup_mechanical()

    assert result.ok
    assert backend._doc.layers.get("OUTLINE").dxf.lineweight == 50
    assert backend._doc.layers.get("CENTER").dxf.linetype == "CENTER"
    assert backend._doc.layers.get("HIDDEN").dxf.linetype == "HIDDEN"


async def test_structured_batch_creates_entities_and_resolves_last_handle():
    backend = EzdxfBackend()
    await backend.initialize()

    result = await backend.create_batch(
        [
            {"type": "line", "x1": 0, "y1": 0, "x2": 10, "y2": 0},
            {
                "type": "rectangle",
                "x1": 0,
                "y1": 0,
                "x2": 20,
                "y2": 10,
                "layer": "OUTLINE",
            },
            {"type": "hatch", "entity_id": "$last", "angle": 0, "scale": 2},
        ]
    )

    assert result.ok
    assert result.payload["batch_ok"] is True
    assert result.payload["processed"] == 3
    assert len(backend._msp) == 3


async def test_atomic_structured_batch_rolls_back_created_entities():
    backend = EzdxfBackend()
    await backend.initialize()

    result = await backend.create_batch(
        [
            {"type": "line", "x1": 0, "y1": 0, "x2": 10, "y2": 0},
            {"type": "unsupported"},
        ],
        atomic=True,
    )

    assert result.ok is False
    assert result.error_code == "E_BATCH_ROLLED_BACK"
    assert result.payload["rolled_back"] == result.payload["created_handles"]
    assert len(backend._msp) == 0


async def test_file_ipc_atomic_batch_uses_autocad_transaction():
    from autocad_mcp.backends.base import CommandResult

    backend = FileIPCBackend()
    commands = []

    async def fake_dispatch(command, params):
        commands.append(command)
        if command == "create-line":
            payload = {"handle": "A"}
        elif command == "entity-get":
            payload = {
                "type": "LINE", "handle": "A", "layer": "0",
                "start": [0, 0], "end": [10, 0],
            }
        else:
            payload = {"transaction": command}
        return CommandResult(ok=True, payload=payload)

    backend._dispatch = fake_dispatch
    backend._create_entity_via_com = MagicMock(side_effect=RuntimeError("COM disabled in unit test"))
    backend.entity_get = AsyncMock(
        return_value=CommandResult(
            ok=True,
            payload={
                "type": "LINE", "handle": "A", "layer": "0",
                "start": [0, 0], "end": [10, 0],
            },
        )
    )
    result = await backend.create_batch(
        [
            {"type": "line", "x1": 0, "y1": 0, "x2": 10, "y2": 0},
            {"type": "unsupported"},
        ],
        atomic=True,
    )

    assert result.ok is False
    assert commands == ["transaction-begin", "create-line", "transaction-rollback"]
    assert result.payload["rolled_back"] == ["A"]


async def test_file_ipc_encodes_text_before_dispatch():
    backend = FileIPCBackend()
    backend._create_entity_via_com = MagicMock(side_effect=RuntimeError("COM disabled in unit test"))
    captured = {}

    async def fake_dispatch(command, params):
        captured.update(command=command, params=params)
        from autocad_mcp.backends.base import CommandResult

        return CommandResult(ok=True, payload={"handle": "1"})

    backend._dispatch = fake_dispatch
    result = await backend.create_text(0, 0, "输出轴")

    assert result.ok
    assert captured["command"] == "create-text"
    assert captured["params"]["text"] == r"\U+8F93\U+51FA\U+8F74"


async def test_file_ipc_encodes_explicit_trim_picks(monkeypatch):
    backend = FileIPCBackend()
    captured = {}
    monkeypatch.setattr(
        backend,
        "_get_entity_via_com",
        lambda handle: (_ for _ in ()).throw(RuntimeError("COM intentionally unavailable")),
    )

    async def fake_dispatch(command, params):
        captured.update(command=command, params=params)
        from autocad_mcp.backends.base import CommandResult

        return CommandResult(ok=True, payload={"trimmed": 1})

    backend._dispatch = fake_dispatch
    result = await backend.entity_trim(["A", "B"], [{"id": "C", "pick": [12.5, 4]}])

    assert result.ok
    assert captured == {
        "command": "entity-trim",
        "params": {"cutters_str": "A;B", "targets_str": "C@12.5,4"},
    }


def test_file_ipc_visibility_can_be_disabled(monkeypatch):
    backend = FileIPCBackend()
    monkeypatch.setenv("AUTOCAD_MCP_VISIBLE", "false")

    result = backend._ensure_autocad_visible(activate=True)

    assert result == {"configured_visible": False, "shown": False}


def test_file_ipc_auto_fit_can_be_disabled(monkeypatch):
    backend = FileIPCBackend()
    monkeypatch.setenv("AUTOCAD_MCP_AUTO_FIT", "false")

    result = backend._auto_fit_view()

    assert result == {"configured": False, "fitted": False, "suspended": False}


def test_file_ipc_identifies_geometry_commands_for_auto_fit():
    assert FileIPCBackend._should_auto_fit("create-circle") is True
    assert FileIPCBackend._should_auto_fit("entity-move") is True
    assert FileIPCBackend._should_auto_fit("entity-list") is False


async def test_solid_capabilities_and_validation_are_explicit():
    file_backend = FileIPCBackend()
    assert file_backend.capabilities.can_create_solids is True
    assert file_backend.capabilities.can_boolean_solids is True
    assert file_backend.capabilities.can_project_views is False

    invalid_box = await file_backend.solid_create_box([0, 0, 0], 0, 10, 10)
    invalid_boolean = await file_backend.solid_boolean("A", "B", "xor")
    assert invalid_box.ok is False
    assert invalid_box.error_code == "E_SOLID_OPERATION"
    assert invalid_boolean.ok is False

    dxf_backend = EzdxfBackend()
    await dxf_backend.initialize()
    unsupported = await dxf_backend.solid_create_box([0, 0, 0], 10, 10, 10)
    assert unsupported.ok is False
    assert "not supported" in unsupported.error.lower()


async def test_dxf_save_uses_non_switching_export_contract(monkeypatch, tmp_path):
    backend = FileIPCBackend()
    output = tmp_path / "drawing.dxf"
    monkeypatch.setattr(
        backend,
        "_export_dxf_via_com",
        lambda path: {
            "path": path,
            "format": "dxf",
            "active_document": "source.dwg",
            "active_document_preserved": True,
        },
    )

    result = await backend.drawing_save_as_dxf(str(output))

    assert result.ok is True
    assert result.payload["active_document_preserved"] is True
    assert result.payload["active_document"] == "source.dwg"
