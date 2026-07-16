"""Tests for drafting profiles, safe text, and structured batches."""

from __future__ import annotations

from autocad_mcp.backends.ezdxf_backend import EzdxfBackend
from autocad_mcp.backends.file_ipc import FileIPCBackend
from autocad_mcp.drafting import encode_autocad_text, lineweight_hundredths


def test_encode_autocad_text_preserves_ascii_and_encodes_chinese():
    assert encode_autocad_text("A轴") == r"A\U+8F74"
    assert encode_autocad_text("%%c50") == "%%c50"


def test_lineweight_hundredths():
    assert lineweight_hundredths("0.20") == 20
    assert lineweight_hundredths(0.5) == 50
    assert lineweight_hundredths(-3) == -3


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


async def test_file_ipc_encodes_text_before_dispatch():
    backend = FileIPCBackend()
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
