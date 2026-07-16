"""Tests for validated industrial delivery jobs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autocad_mcp.backends.base import AutoCADBackend, BackendCapabilities, CommandResult
from autocad_mcp.delivery import deliver_drawing


class FakeDeliveryBackend(AutoCADBackend):
    def __init__(self, entity_count=3, layers=None, types=None, fail_step=None):
        self.entity_count = entity_count
        self.layers = layers or {"OUTLINE": entity_count}
        self.types = types or {"LINE": entity_count}
        self.fail_step = fail_step

    @property
    def name(self):
        return "file_ipc"

    @property
    def capabilities(self):
        return BackendCapabilities(can_save=True, can_plot_pdf=True)

    async def initialize(self):
        return CommandResult(ok=True)

    async def status(self):
        return CommandResult(ok=True)

    def _write(self, path, content):
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)
        return CommandResult(ok=True, payload={"path": str(output)})

    async def drawing_audit(self, **kwargs):
        return CommandResult(
            ok=True,
            payload={
                "entity_count": self.entity_count,
                "counts_by_layer": self.layers,
                "counts_by_type": self.types,
            },
        )

    async def drawing_save(self, path=None):
        if self.fail_step == "dwg":
            return CommandResult(ok=False, error="save failed")
        return self._write(path, b"DWG")

    async def drawing_plot_pdf(self, path):
        if self.fail_step == "pdf":
            return CommandResult(ok=False, error="plot failed")
        return self._write(path, b"PDF")

    async def drawing_save_as_dxf(self, path):
        if self.fail_step == "dxf":
            return CommandResult(ok=False, error="export failed")
        return self._write(path, b"DXF")

    async def drawing_audit_dxf(self, path, limit=50, include_entities=True):
        return CommandResult(
            ok=True,
            payload={
                "path": path,
                "entity_count": self.entity_count,
                "counts_by_layer": self.layers,
                "counts_by_type": self.types,
            },
        )


@pytest.mark.asyncio
async def test_delivery_creates_manifest_audit_and_checksums(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOCAD_MCP_OUTPUT_ROOT", str(tmp_path / "CAD-Automation"))
    backend = FakeDeliveryBackend()

    result = await deliver_drawing(
        backend,
        {
            "name": "gearbox",
            "metadata": {"drawing_number": "GB-001"},
            "validation": {"min_entities": 3, "required_layers": ["OUTLINE"], "required_types": ["LINE"]},
        },
    )

    assert result.ok is True
    root = Path(result.payload["root"])
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "complete"
    assert manifest["metadata"]["drawing_number"] == "GB-001"
    assert all(check["passed"] for check in manifest["validation"]["checks"])
    assert {"dwg", "dxf", "pdf", "audit", "request", "validation"} == set(manifest["artifacts"])
    assert all(len(item["sha256"]) == 64 for item in manifest["artifacts"].values())
    assert json.loads((root / "audits" / "drawing-audit.json").read_text(encoding="utf-8"))["entity_count"] == 3
    assert all(
        check["passed"]
        for check in json.loads((root / "reports" / "validation.json").read_text(encoding="utf-8"))["checks"]
    )


@pytest.mark.asyncio
async def test_delivery_stops_when_validation_gate_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOCAD_MCP_OUTPUT_ROOT", str(tmp_path / "CAD-Automation"))

    result = await deliver_drawing(
        FakeDeliveryBackend(entity_count=0, layers={}, types={}),
        {"name": "empty", "validation": {"min_entities": 1}},
    )

    assert result.ok is False
    manifests = list((tmp_path / "CAD-Automation" / "jobs").glob("*/manifest.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["steps"][-1]["name"] == "audit-source"


@pytest.mark.asyncio
async def test_delivery_records_backend_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOCAD_MCP_OUTPUT_ROOT", str(tmp_path / "CAD-Automation"))

    result = await deliver_drawing(FakeDeliveryBackend(fail_step="pdf"), {"name": "failed-plot"})

    assert result.ok is False
    manifest_path = next((tmp_path / "CAD-Automation" / "jobs").glob("*/manifest.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["steps"][-1]["name"] == "plot-pdf"
    assert manifest["steps"][-1]["error"] == "plot failed"
