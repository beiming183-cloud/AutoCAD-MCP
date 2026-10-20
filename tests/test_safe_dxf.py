from __future__ import annotations

import json
import math
import os
import sys
from importlib.metadata import distribution
from pathlib import Path

import dxfgrabber
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from autocad_mcp.safe_dxf import PlateSpec, create_laser_plate, inspect_dxf, resize_laser_plate


@pytest.fixture
def managed_root(tmp_path, monkeypatch):
    root = tmp_path / "managed-dxf"
    monkeypatch.setenv("NGOC_VIET_DXF_ROOT", str(root))
    return root


def _independent_read(path: Path) -> dict:
    drawing = dxfgrabber.readfile(str(path))
    entities = list(drawing.entities)
    polylines = [entity for entity in entities if entity.dxftype == "LWPOLYLINE"]
    circles = [entity for entity in entities if entity.dxftype == "CIRCLE"]
    return {
        "units_code": int(drawing.header.get("$INSUNITS", 0)),
        "entity_count": len(entities),
        "layers": {entity.layer for entity in entities},
        "polyline_points": [(float(x), float(y)) for x, y, *_ in polylines[0].points],
        "polyline_closed": bool(polylines[0].is_closed),
        "circle_centres": sorted((float(circle.center[0]), float(circle.center[1])) for circle in circles),
        "circle_radii": [float(circle.radius) for circle in circles],
    }


def test_create_and_independent_readback(managed_root):
    result = create_laser_plate("laser_test_300x200.dxf", PlateSpec())
    assert result["valid"] is True
    assert all(result["checks"].values())
    assert result["inspection"]["total_cut_length_mm"] == pytest.approx(1000 + 40 * math.pi)

    independent = _independent_read(managed_root / "laser_test_300x200.dxf")
    assert independent == {
        "units_code": 4,
        "entity_count": 5,
        "layers": {"CUT_OUTER", "CUT_INNER"},
        "polyline_points": [(0.0, 0.0), (300.0, 0.0), (300.0, 200.0), (0.0, 200.0)],
        "polyline_closed": True,
        "circle_centres": [(20.0, 20.0), (20.0, 180.0), (280.0, 20.0), (280.0, 180.0)],
        "circle_radii": [5.0, 5.0, 5.0, 5.0],
    }


def test_refuses_paths_overwrite_and_invalid_geometry(managed_root):
    create_laser_plate("safe.dxf", PlateSpec())
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        create_laser_plate("safe.dxf", PlateSpec())
    for name in ("../escape.dxf", "subdir/file.dxf", "C:\\escape.dxf", "not-dxf.txt"):
        with pytest.raises(ValueError):
            create_laser_plate(name, PlateSpec())
    with pytest.raises(ValueError, match="finite positive"):
        create_laser_plate("nan.dxf", PlateSpec(width_mm=float("nan")))


def test_resize_creates_new_revision_and_preserves_source(managed_root):
    create_laser_plate("source.dxf", PlateSpec())
    source_before = (managed_root / "source.dxf").read_bytes()
    result = resize_laser_plate("source.dxf", "resized_320x220.dxf", PlateSpec(320, 220, 10, 20))
    assert result["valid"] is True
    assert result["derived_from"] == "source.dxf"
    assert (managed_root / "source.dxf").read_bytes() == source_before
    assert inspect_dxf("resized_320x220.dxf")["entity_count"] == 5
    with pytest.raises(ValueError, match="must differ"):
        resize_laser_plate("source.dxf", "source.dxf", PlateSpec())


@pytest.mark.asyncio
async def test_stdio_mcp_create_read_resize_and_validate(managed_root):
    environment = {
        "NGOC_VIET_DXF_ROOT": str(managed_root),
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
        "TEMP": os.environ["TEMP"],
        "TMP": os.environ["TMP"],
    }
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "autocad_mcp.safe_server"],
        env=environment,
        cwd=str(Path(__file__).parents[1]),
    )
    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "create_laser_plate",
                "create_square_center_edge_plate",
                "resize_laser_plate",
                "inspect_dxf",
                "validate_laser_plate",
                "list_test_dxfs",
            }
            square = await session.call_tool(
                "create_square_center_edge_plate",
                {
                    "output_name": "mcp-square-400.dxf",
                    "side_mm": 400,
                    "center_hole_diameter_mm": 50,
                    "edge_hole_diameter_mm": 15,
                    "edge_offset_mm": 20,
                },
            )
            square_result = json.loads(square.content[0].text)
            assert square_result["valid"] is True
            assert square_result["inspection"]["entity_count"] == 6
            created = await session.call_tool("create_laser_plate", {"output_name": "mcp-created.dxf"})
            assert json.loads(created.content[0].text)["valid"] is True
            inspected = await session.call_tool("inspect_dxf", {"name": "mcp-created.dxf"})
            assert json.loads(inspected.content[0].text)["entity_count"] == 5
            resized = await session.call_tool(
                "resize_laser_plate",
                {"source_name": "mcp-created.dxf", "output_name": "mcp-resized.dxf", "width_mm": 310, "height_mm": 210},
            )
            assert json.loads(resized.content[0].text)["valid"] is True
            validated = await session.call_tool(
                "validate_laser_plate",
                {"name": "mcp-resized.dxf", "width_mm": 310, "height_mm": 210},
            )
            assert json.loads(validated.content[0].text)["valid"] is True


def test_safe_surface_contains_no_command_execution_primitives():
    package = Path(__file__).parents[1] / "src" / "autocad_mcp"
    source = (package / "safe_server.py").read_text(encoding="utf-8") + (package / "safe_dxf.py").read_text(encoding="utf-8")
    forbidden = ("subprocess", "os.system", "SendCommand", "eval(", "exec(", "win32com", "native_pipe", "file_ipc")
    assert all(token not in source for token in forbidden)


def test_default_policy_forces_headless_and_blocks_command_transport(monkeypatch):
    monkeypatch.delenv("NGOC_VIET_DXF_SAFE_MODE", raising=False)
    monkeypatch.setenv("AUTOCAD_MCP_BACKEND", "file_ipc")
    monkeypatch.setenv(
        "NGOC_VIET_ALLOW_AUTOCAD_COMMAND_TRANSPORT",
        "I_ACCEPT_UNVERIFIED_AUTOCAD_COMMAND_RISK",
    )
    from autocad_mcp.config import detect_backend
    from autocad_mcp.safety import autocad_command_transport_allowed, dxf_safe_mode_enabled

    assert dxf_safe_mode_enabled() is True
    assert autocad_command_transport_allowed() is False
    assert detect_backend() == "ezdxf"


def test_installed_entrypoints_expose_only_safe_server():
    scripts = {
        entry.name: entry.value
        for entry in distribution("autocad-mcp").entry_points
        if entry.group == "console_scripts"
    }
    assert scripts == {
        "autocad-mcp": "autocad_mcp.safe_server:main",
        "ngoc-viet-autocad-live-mcp": "autocad_mcp.live_autocad_server:main",
        "ngoc-viet-autocad-mcp": "autocad_mcp.safe_server:main",
    }
