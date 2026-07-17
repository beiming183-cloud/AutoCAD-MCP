"""Tests for validated industrial delivery jobs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autocad_mcp.backends.base import AutoCADBackend, BackendCapabilities, CommandResult
from autocad_mcp.delivery import _export_checks, _validation_checks, deliver_drawing


class FakeDeliveryBackend(AutoCADBackend):
    def __init__(self, entity_count=3, layers=None, types=None, fail_step=None):
        self.entity_count = entity_count
        self.layers = layers or {"OUTLINE": entity_count}
        self.types = types or {"LINE": entity_count}
        self.fail_step = fail_step
        self.active_doc_id = "doc-source"
        self.active_path = "D:/CAD-Automation/drawings/source.dwg"
        self.revision = 7

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

    async def document_context(self):
        return CommandResult(
            ok=True,
            payload={
                "doc_id": self.active_doc_id,
                "active_doc_id": self.active_doc_id,
                "requested_path": self.active_path,
                "active_path": self.active_path,
                "revision": self.revision,
            },
        )

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

    async def drawing_copy_dwg(self, path):
        if self.fail_step == "dwg":
            return CommandResult(ok=False, error="copy failed")
        result = self._write(path, b"DWG")
        result.payload["read_only_context"] = True
        if self.fail_step == "switch-doc":
            self.active_doc_id = "doc-copy"
            self.active_path = str(path)
        return result

    async def drawing_plot_pdf(
        self,
        path,
        paper="A4",
        orientation="auto",
        plot_style="monochrome.ctb",
        scale_mode="fit",
        scale="1:1",
        center=True,
    ):
        if self.fail_step == "pdf":
            return CommandResult(ok=False, error="plot failed")
        result = self._write(path, b"PDF")
        result.payload.update(
            paper=paper,
            orientation=orientation,
            plot_style=plot_style,
            scale_mode=scale_mode,
            scale=scale if scale_mode == "fixed" else "fit",
            center=center,
            paper_units="millimeters" if paper.upper().startswith("A") else "inches",
        )
        return result

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
    assert manifest["source_document_after"]["active_doc_id"] == "doc-source"
    assert manifest["source_document_after"]["active_path"].endswith("source.dwg")
    assert all(
        check["passed"]
        for check in json.loads((root / "reports" / "validation.json").read_text(encoding="utf-8"))["checks"]
    )


@pytest.mark.asyncio
async def test_delivery_rejects_copy_that_switches_active_document(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOCAD_MCP_OUTPUT_ROOT", str(tmp_path / "CAD-Automation"))

    result = await deliver_drawing(
        FakeDeliveryBackend(fail_step="switch-doc"),
        {"name": "must-not-switch", "doc_id": "doc-source"},
    )

    assert result.ok is False
    manifest = json.loads(Path(result.payload["manifest"]).read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert "active_doc_id" in manifest["error"]


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


@pytest.mark.asyncio
async def test_delivery_applies_a3_fixed_scale(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOCAD_MCP_OUTPUT_ROOT", str(tmp_path / "CAD-Automation"))

    result = await deliver_drawing(
        FakeDeliveryBackend(),
        {
            "name": "a3-release",
            "plot": {
                "paper": "A3",
                "orientation": "landscape",
                "scale_mode": "fixed",
                "scale": "1:1",
                "center": True,
            },
        },
    )

    assert result.ok is True
    manifest = json.loads(Path(result.payload["manifest"]).read_text(encoding="utf-8"))
    assert manifest["plot"]["actual"]["paper"] == "A3"
    assert manifest["plot"]["actual"]["scale"] == "1:1"
    assert manifest["plot"]["actual"]["paper_units"] == "millimeters"
    assert all(check["passed"] for check in manifest["validation"]["checks"])


@pytest.mark.asyncio
async def test_delivery_rejects_fixed_title_scale_with_fit_plot(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOCAD_MCP_OUTPUT_ROOT", str(tmp_path / "CAD-Automation"))

    result = await deliver_drawing(
        FakeDeliveryBackend(),
        {
            "name": "scale-conflict",
            "metadata": {"scale": "1:1"},
            "plot": {"scale_mode": "fit"},
        },
    )

    assert result.ok is False
    assert result.error_code == "E_PLOT_SCALE_MISMATCH"


def test_export_checks_compare_types_layers_bounds_digest_and_units():
    source = {
        "entity_count": 2,
        "counts_by_type": {"LINE": 2},
        "counts_by_layer": {"OUTLINE": 2},
        "bounds": {"min": [0, 0], "max": [10, 5]},
        "geometry_digest": "abc",
        "units": {"code": 4, "name": "millimeters"},
    }
    exported = dict(source, geometry_digest="different")

    checks = _export_checks(source, exported, 0.000001)

    assert next(item for item in checks if item["name"] == "dxf_geometry_digest_matches_source")[
        "passed"
    ] is False


def test_geometry_warnings_do_not_fail_release_gate():
    audit = {
        "entity_count": 1,
        "counts_by_type": {"LINE": 1},
        "counts_by_layer": {"OUTLINE": 1},
        "geometry_drc": {
            "status": "WARNING",
            "issue_count": 2,
            "failure_count": 0,
            "warning_count": 2,
        },
    }

    check = next(item for item in _validation_checks(audit, {}) if item["name"] == "geometry_drc")

    assert check["passed"] is True
    assert check["actual"] == 0
