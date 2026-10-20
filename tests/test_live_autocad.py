from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from autocad_mcp.live_autocad import (
    LIVE_APPROVAL_ENV,
    LIVE_APPROVAL_VALUE,
    LIVE_ROOT_ENV,
    LiveAutoCADAdapter,
    LivePlateSpec,
    resolve_live_dxf,
)


class FakeLayerCollection:
    def __init__(self) -> None:
        self.names: set[str] = set()

    def Item(self, name: str):
        if name not in self.names:
            raise KeyError(name)
        return name

    def Add(self, name: str):
        self.names.add(name)
        return name


class FakePolyline:
    ObjectName = "AcDbPolyline"

    def __init__(self, coordinates: list[float]) -> None:
        self.Coordinates = coordinates
        self.Closed = False
        self.Layer = "0"


class FakeCircle:
    ObjectName = "AcDbCircle"

    def __init__(self, center: list[float], radius: float) -> None:
        self.Center = center
        self.Radius = radius
        self.Layer = "0"


class FakeModelSpace(list):
    def AddLightWeightPolyline(self, coordinates):
        entity = FakePolyline(list(coordinates))
        self.append(entity)
        return entity

    def AddCircle(self, center, radius):
        entity = FakeCircle(list(center), radius)
        self.append(entity)
        return entity


class FakeDocument:
    def __init__(self, documents, name: str = "Drawing1.dwg") -> None:
        self._documents = documents
        self.Name = name
        self.FullName = ""
        self.ReadOnly = False
        self.ModelSpace = FakeModelSpace()
        self.Layers = FakeLayerCollection()
        self.variables: dict[str, object] = {}

    def SetVariable(self, name: str, value: object) -> None:
        self.variables[name] = value

    def SaveAs(self, filename: str, filetype: int) -> None:
        path = Path(filename)
        path.write_text("fake dxf", encoding="ascii")
        self.Name = path.name
        self.FullName = str(path.resolve())
        self._documents.by_path[str(path.resolve())] = self

    def Activate(self) -> None:
        self._documents.application.ActiveDocument = self


class FakeDocuments:
    def __init__(self) -> None:
        self.by_path: dict[str, FakeDocument] = {}
        self.application = None

    def __iter__(self):
        return iter(self.by_path.values())

    def Add(self):
        return FakeDocument(self)

    def Open(self, filename: str, read_only: bool):
        return self.by_path[str(Path(filename).resolve())]


class FakeApplication:
    Name = "AutoCAD"
    Version = "24.0"

    def __init__(self) -> None:
        self.Documents = FakeDocuments()
        self.Documents.application = self
        self.ActiveDocument = FakeDocument(self.Documents)
        self.zoom_count = 0

    def ZoomExtents(self) -> None:
        self.zoom_count += 1


@pytest.fixture
def live_root(tmp_path, monkeypatch):
    monkeypatch.setenv(LIVE_ROOT_ENV, str(tmp_path))
    return tmp_path


def test_live_gate_is_fail_closed(monkeypatch):
    monkeypatch.delenv(LIVE_APPROVAL_ENV, raising=False)
    with pytest.raises(PermissionError, match="disabled"):
        LiveAutoCADAdapter(application=FakeApplication()).status()


def test_live_path_restrictions_and_no_overwrite(live_root):
    application = FakeApplication()
    adapter = LiveAutoCADAdapter(
        application=application,
        variant_factory=list,
        require_approval=False,
    )
    with pytest.raises(ValueError):
        resolve_live_dxf("../escape.dxf")
    result = adapter.create_plate("live-test.dxf", LivePlateSpec())
    assert result["entity_count"] == 6
    assert result["entity_counts"] == {"LWPOLYLINE": 1, "CIRCLE": 5}
    assert application.zoom_count == 1
    with pytest.raises(FileExistsError, match="overwrite"):
        adapter.create_plate("live-test.dxf", LivePlateSpec())


def test_read_and_revision_preserve_source(live_root):
    application = FakeApplication()
    adapter = LiveAutoCADAdapter(
        application=application,
        variant_factory=list,
        require_approval=False,
    )
    adapter.create_plate("source.dxf", LivePlateSpec())
    source_before = (live_root / "source.dxf").read_bytes()
    inspected = adapter.inspect_managed("source.dxf")
    assert inspected["entity_count"] == 6
    revised = adapter.modify_hole_revision("source.dxf", "revision.dxf", 100.0, 100.0, 24.0)
    assert revised["modified"] is True
    center = [
        entity
        for entity in revised["entities"]
        if entity["type"] == "CIRCLE" and entity["center_mm"] == [100.0, 100.0]
    ]
    assert len(center) == 1
    assert center[0]["radius_mm"] == 12.0
    assert (live_root / "source.dxf").read_bytes() == source_before
    assert (live_root / "revision.dxf").is_file()
    log = (live_root / "logs" / "live_autocad_mcp.jsonl").read_text(encoding="utf-8")
    assert LIVE_APPROVAL_VALUE not in log
    assert "modify_hole_revision" in log


@pytest.mark.asyncio
async def test_live_mcp_surface_is_allowlisted(live_root):
    environment = {
        LIVE_ROOT_ENV: str(live_root),
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
        "TEMP": os.environ["TEMP"],
        "TMP": os.environ["TMP"],
    }
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "autocad_mcp.live_autocad_server"],
        env=environment,
        cwd=str(Path(__file__).parents[1]),
    )
    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "autocad_status",
                "autocad_create_test_plate",
                "autocad_inspect_test_drawing",
                "autocad_modify_hole_revision",
            }
