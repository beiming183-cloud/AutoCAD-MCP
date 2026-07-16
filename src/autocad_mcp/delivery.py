"""Validated, traceable CAD delivery jobs."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autocad_mcp import __version__
from autocad_mcp.backends.base import AutoCADBackend, CommandResult
from autocad_mcp.workspace import create_job, sha256_file, write_json_atomic


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _artifact(path: Path) -> dict[str, Any]:
    return {"path": str(path), "size": path.stat().st_size, "sha256": sha256_file(path)}


def _validation_checks(audit: dict[str, Any], rules: dict[str, Any]) -> list[dict[str, Any]]:
    entity_count = int(audit.get("entity_count", 0))
    type_counts = audit.get("counts_by_type") or {}
    layer_counts = audit.get("counts_by_layer") or {}
    checks = []

    def add(name: str, passed: bool, actual: Any, expected: Any) -> None:
        checks.append(
            {"name": name, "passed": bool(passed), "actual": actual, "expected": expected}
        )

    minimum = int(rules.get("min_entities", 1))
    add("minimum_entity_count", entity_count >= minimum, entity_count, f">={minimum}")
    if rules.get("max_entities") is not None:
        maximum = int(rules["max_entities"])
        add("maximum_entity_count", entity_count <= maximum, entity_count, f"<={maximum}")
    for layer in rules.get("required_layers", []):
        add(f"required_layer:{layer}", layer in layer_counts, layer_counts.get(layer, 0), ">=1")
    for entity_type in rules.get("required_types", []):
        normalized = str(entity_type).upper()
        add(
            f"required_type:{normalized}",
            normalized in type_counts,
            type_counts.get(normalized, 0),
            ">=1",
        )
    drc = audit.get("geometry_drc") or {}
    if drc and rules.get("require_geometry_clean", True):
        failures = int(drc.get("failure_count", drc.get("issue_count", 0)))
        add("geometry_drc", failures == 0, failures, 0)
    return checks


def _export_checks(
    source: dict[str, Any], exported: dict[str, Any], tolerance: float
) -> list[dict[str, Any]]:
    checks = []

    def add(name: str, passed: bool, actual: Any, expected: Any) -> None:
        checks.append(
            {"name": name, "passed": bool(passed), "actual": actual, "expected": expected}
        )

    add(
        "dxf_entity_count_matches_source",
        exported.get("entity_count") == source.get("entity_count"),
        exported.get("entity_count"),
        source.get("entity_count"),
    )
    add(
        "dxf_type_counts_match_source",
        exported.get("counts_by_type") == source.get("counts_by_type"),
        exported.get("counts_by_type"),
        source.get("counts_by_type"),
    )
    add(
        "dxf_layer_counts_match_source",
        exported.get("counts_by_layer") == source.get("counts_by_layer"),
        exported.get("counts_by_layer"),
        source.get("counts_by_layer"),
    )
    source_bounds, exported_bounds = source.get("bounds"), exported.get("bounds")
    if source_bounds is not None and exported_bounds is not None:
        differences = [
            abs(float(source_bounds[edge][axis]) - float(exported_bounds[edge][axis]))
            for edge in ("min", "max")
            for axis in (0, 1)
        ]
        add("dxf_bounds_match_source", max(differences) <= tolerance, max(differences), f"<={tolerance}")
    if source.get("geometry_digest") and exported.get("geometry_digest"):
        add(
            "dxf_geometry_digest_matches_source",
            exported["geometry_digest"] == source["geometry_digest"],
            exported["geometry_digest"],
            source["geometry_digest"],
        )
    if source.get("units") and exported.get("units"):
        add(
            "dxf_units_match_source",
            exported["units"].get("code") == source["units"].get("code"),
            exported["units"],
            source["units"],
        )
    exported_drc = exported.get("geometry_drc") or {}
    if exported_drc:
        failures = int(exported_drc.get("failure_count", exported_drc.get("issue_count", 0)))
        add("dxf_geometry_drc", failures == 0, failures, 0)
    return checks


async def deliver_drawing(
    backend: AutoCADBackend, data: dict[str, Any] | None = None
) -> CommandResult:
    """Create a validated delivery package and a durable manifest."""
    if backend.name != "file_ipc":
        return CommandResult(
            ok=False,
            error="Validated DWG/DXF/PDF delivery requires the visible AutoCAD file_ipc backend",
        )

    request = dict(data or {})
    name = request.get("name") or "drawing"
    job = create_job(name)
    root = Path(job["root"])
    stem = str(job["job_id"])
    paths = {
        "dwg": Path(job["drawings"]) / f"{stem}.dwg",
        "dxf": Path(job["dxf"]) / f"{stem}.dxf",
        "pdf": Path(job["pdf"]) / f"{stem}.pdf",
        "audit": Path(job["audits"]) / "drawing-audit.json",
        "request": Path(job["specs"]) / "request.json",
        "validation": Path(job["reports"]) / "validation.json",
        "manifest": root / "manifest.json",
    }
    rules = dict(request.get("validation") or {})
    plot = dict(request.get("plot") or {})
    metadata_sheet = str((request.get("metadata") or {}).get("sheet", ""))
    if metadata_sheet and "paper" not in plot:
        plot["paper"] = metadata_sheet.split()[0]
    if "landscape" in metadata_sheet.lower() and "orientation" not in plot:
        plot["orientation"] = "landscape"
    plot.setdefault("paper", "A4")
    plot.setdefault("orientation", "auto")
    plot.setdefault("plot_style", "monochrome.ctb")
    plot.setdefault("scale_mode", "fit")
    plot.setdefault("scale", "1:1")
    plot.setdefault("center", True)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "job_id": job["job_id"],
        "name": name,
        "status": "running",
        "started_at": _now(),
        "backend": backend.name,
        "autocad_mcp_version": __version__,
        "metadata": request.get("metadata") or {},
        "plot": {"requested": plot, "actual": None},
        "validation": {"rules": rules, "checks": []},
        "steps": [],
        "artifacts": {},
    }
    write_json_atomic(paths["request"], request)
    write_json_atomic(paths["manifest"], manifest)

    async def run_step(name: str, operation) -> CommandResult:
        started_at = _now()
        try:
            result = await operation
        except Exception as exc:
            result = CommandResult(ok=False, error=str(exc))
        manifest["steps"].append(
            {
                "name": name,
                "ok": result.ok,
                "started_at": started_at,
                "finished_at": _now(),
                "error": result.error,
            }
        )
        write_json_atomic(paths["manifest"], manifest)
        return result

    def fail(message: str) -> CommandResult:
        manifest.update(status="failed", finished_at=_now(), error=message)
        write_json_atomic(paths["validation"], manifest["validation"])
        write_json_atomic(paths["manifest"], manifest)
        return CommandResult(
            ok=False,
            error=message,
            payload={"job_id": job["job_id"], "root": str(root), "manifest": str(paths["manifest"])},
        )

    audit = await run_step(
        "audit-source",
        backend.drawing_audit(limit=0, include_entities=False, changed_only=False),
    )
    if not audit.ok or not isinstance(audit.payload, dict):
        return fail(audit.error or "Source drawing audit failed")
    checks = _validation_checks(audit.payload, rules)
    manifest["validation"]["checks"] = checks
    if not all(check["passed"] for check in checks):
        return fail("Source drawing failed validation gates")

    save_dwg = await run_step("save-dwg", backend.drawing_save(str(paths["dwg"])))
    if not save_dwg.ok:
        return fail(save_dwg.error or "DWG save failed")
    plot_pdf = await run_step(
        "plot-pdf",
        backend.drawing_plot_pdf(
            str(paths["pdf"]),
            plot["paper"],
            plot["orientation"],
            plot["plot_style"],
            plot["scale_mode"],
            plot["scale"],
            plot["center"],
        ),
    )
    if not plot_pdf.ok:
        return fail(plot_pdf.error or "PDF plot failed")
    if isinstance(plot_pdf.payload, dict):
        manifest["plot"]["actual"] = plot_pdf.payload
        compare_keys = ["paper", "scale_mode", "center"]
        if plot["orientation"] != "auto":
            compare_keys.append("orientation")
        if plot["scale_mode"] == "fixed":
            compare_keys.append("scale")
        for key in compare_keys:
            if key in plot_pdf.payload:
                expected = plot[key]
                actual = plot_pdf.payload[key]
                manifest["validation"]["checks"].append(
                    {
                        "name": f"plot_{key}_applied",
                        "passed": actual == expected,
                        "actual": actual,
                        "expected": expected,
                    }
                )
        if "paper_units" in plot_pdf.payload:
            expected_units = "millimeters" if str(plot["paper"]).upper().startswith("A") else "inches"
            manifest["validation"]["checks"].append(
                {
                    "name": "plot_paper_units_applied",
                    "passed": plot_pdf.payload["paper_units"] == expected_units,
                    "actual": plot_pdf.payload["paper_units"],
                    "expected": expected_units,
                }
            )
        if not all(check["passed"] for check in manifest["validation"]["checks"]):
            return fail("Requested plot configuration was not applied")
    save_dxf = await run_step("save-dxf", backend.drawing_save_as_dxf(str(paths["dxf"])))
    if not save_dxf.ok:
        return fail(save_dxf.error or "DXF save failed")
    dxf_audit = await run_step(
        "audit-dxf",
        backend.drawing_audit_dxf(str(paths["dxf"]), limit=500, include_entities=True),
    )
    if not dxf_audit.ok or not isinstance(dxf_audit.payload, dict):
        return fail(dxf_audit.error or "DXF audit failed")
    write_json_atomic(paths["audit"], dxf_audit.payload)

    export_checks = _export_checks(
        audit.payload, dxf_audit.payload, float(rules.get("geometry_tolerance", 0.000001))
    )
    manifest["validation"]["checks"].extend(export_checks)
    if not all(check["passed"] for check in export_checks):
        return fail("DXF geometry or metadata does not match the source drawing")

    restore = await run_step("restore-dwg", backend.drawing_save(str(paths["dwg"])))
    if not restore.ok:
        return fail(restore.error or "Failed to restore the active DWG after DXF export")
    if isinstance(restore.payload, dict) and restore.payload.get("active_document"):
        active_document = Path(restore.payload["active_document"]).resolve()
        if active_document != paths["dwg"].resolve():
            return fail("AutoCAD did not return to the primary DWG after DXF export")

    write_json_atomic(paths["validation"], manifest["validation"])
    for key in ("dwg", "dxf", "pdf", "audit", "request", "validation"):
        if not paths[key].is_file():
            return fail(f"Expected delivery artifact is missing: {paths[key]}")
        manifest["artifacts"][key] = _artifact(paths[key])
    manifest.update(status="complete", finished_at=_now())
    write_json_atomic(paths["manifest"], manifest)
    manifest_artifact = _artifact(paths["manifest"])
    return CommandResult(
        ok=True,
        payload={
            "job_id": job["job_id"],
            "root": str(root),
            "manifest": str(paths["manifest"]),
            "manifest_sha256": manifest_artifact["sha256"],
            "validation_passed": True,
            "artifacts": manifest["artifacts"],
        },
    )
