"""Manual minimized-AutoCAD smoke test for native v3.9 workflows."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path


def _new_visible_document() -> str:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    application = win32com.client.GetActiveObject("AutoCAD.Application")
    application.Documents.Add()
    deadline = time.monotonic() + 15.0
    last_error = None
    while time.monotonic() < deadline:
        try:
            document = application.ActiveDocument
            name = str(document.Name)
            if name:
                return name
        except Exception as exc:
            last_error = exc
        time.sleep(0.25)
    raise RuntimeError(f"New AutoCAD document did not become active: {last_error}")


def _close_active_document() -> None:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    application = win32com.client.GetActiveObject("AutoCAD.Application")
    application.ActiveDocument.Close(False)


def _artifact(path: Path) -> dict:
    return {
        "path": str(path),
        "size": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


async def run(output_root: Path, *, keep_artifacts: bool = False) -> dict:
    from autocad_mcp.backends.file_ipc import FileIPCBackend

    output_root = output_root / datetime.now().strftime("%Y%m%d-%H%M%S")
    output_root.mkdir(parents=True, exist_ok=False)
    backend = FileIPCBackend()
    foreground_before = None
    try:
        import win32gui

        foreground_before = win32gui.GetForegroundWindow()
    except Exception:
        pass
    startup = await backend.ensure_ready()
    if not startup.ok:
        raise RuntimeError(startup.to_dict())
    document_name = _new_visible_document()
    ready = await backend.ensure_ready()
    if not ready.ok:
        raise RuntimeError(ready.to_dict())

    setup = await backend.drawing_setup_mechanical(
        {"sheet": "A3", "orientation": "landscape", "projection": "first-angle"}
    )
    if not setup.ok:
        raise RuntimeError(setup.to_dict())

    successful_batch = await backend.create_batch(
        [
            {"type": "rectangle", "x1": 0, "y1": 0, "x2": 80, "y2": 40, "layer": "OUTLINE"},
            {"type": "circle", "cx": 20, "cy": 20, "radius": 8, "layer": "OUTLINE"},
            {"type": "circle", "cx": 60, "cy": 20, "radius": 8, "layer": "OUTLINE"},
            {"type": "line", "x1": 40, "y1": -10, "x2": 40, "y2": 50, "layer": "CENTER"},
        ],
        atomic=True,
    )
    if not successful_batch.ok:
        raise RuntimeError(successful_batch.to_dict())

    count_before = (await backend.entity_count()).payload["count"]
    failed_batch = await backend.create_batch(
        [
            {"type": "line", "x1": 0, "y1": 60, "x2": 10, "y2": 60, "layer": "OUTLINE"},
            {"type": "unsupported"},
        ],
        atomic=True,
    )
    count_after = (await backend.entity_count()).payload["count"]
    if failed_batch.ok or count_after != count_before:
        raise RuntimeError("Atomic rollback did not restore the pre-batch entity count")

    cutter = await backend.create_line(30, 55, 30, 85, "OUTLINE")
    target = await backend.create_line(0, 70, 60, 70, "OUTLINE")
    trim = await backend.entity_trim(
        [cutter.payload["handle"]],
        [{"id": target.payload["handle"], "pick": [50, 70]}],
    )
    if not trim.ok:
        raise RuntimeError(trim.to_dict())
    trimmed = await backend.entity_get(target.payload["handle"])
    if not trimmed.ok:
        raise RuntimeError(trimmed.to_dict())

    boundary = await backend.create_line(210, 55, 210, 85, "OUTLINE")
    extend_target = await backend.create_line(170, 70, 200, 70, "OUTLINE")
    extended = await backend.entity_extend(
        [boundary.payload["handle"]],
        [{"id": extend_target.payload["handle"], "pick": [200, 70]}],
    )
    extended_entity = await backend.entity_get(extend_target.payload["handle"])
    if not extended.ok or not extended_entity.ok:
        raise RuntimeError({"extend": extended.to_dict(), "entity": extended_entity.to_dict()})

    join_first = await backend.create_line(170, 90, 190, 90, "OUTLINE")
    join_second = await backend.create_line(190, 90, 210, 90, "OUTLINE")
    joined = await backend.entity_join(
        [join_first.payload["handle"], join_second.payload["handle"]]
    )
    if not joined.ok:
        raise RuntimeError(joined.to_dict())

    semantic_tags = {
        cutter.payload["handle"]: {"component_id": "TRIM-CUTTER", "line_class": "outline", "intentional_open_end": "both"},
        target.payload["handle"]: {"component_id": "TRIM-TARGET", "line_class": "outline", "intentional_open_end": "start"},
        boundary.payload["handle"]: {"component_id": "EXTEND-BOUNDARY", "line_class": "outline", "intentional_open_end": "both"},
        extend_target.payload["handle"]: {"component_id": "EXTEND-TARGET", "line_class": "outline", "intentional_open_end": "start"},
        joined.payload["handle"]: {"component_id": "JOINED-CHAIN", "line_class": "outline", "intentional_open_end": "both"},
    }
    backend._semantic_store().update(semantic_tags)

    constrained = await backend.entity_constrain(
        "horizontal", [extend_target.payload["handle"]]
    )
    if not constrained.ok:
        raise RuntimeError(constrained.to_dict())

    box = await backend.solid_create_box([100, 0, 0], 50, 40, 20, "OUTLINE")
    cylinder = await backend.solid_create_cylinder([125, 20, 0], 8, 25, "OUTLINE")
    if not box.ok or not cylinder.ok:
        raise RuntimeError({"box": box.to_dict(), "cylinder": cylinder.to_dict()})
    subtraction = await backend.solid_boolean(
        box.payload["handle"], cylinder.payload["handle"], "subtract"
    )
    if not subtraction.ok:
        raise RuntimeError(subtraction.to_dict())
    profile = await backend.create_circle(180, 120, 6, "OUTLINE")
    extrusion = await backend.solid_extrude(profile.payload["handle"], 15, erase_profile=True)
    if not extrusion.ok:
        raise RuntimeError(extrusion.to_dict())

    audit = await backend.drawing_audit(
        rules={
            "connection_tolerance": 0.05,
            "near_miss_tolerance": 0.5,
            "topology_layers": ["OUTLINE"],
        }
    )
    if not audit.ok:
        raise RuntimeError(audit.to_dict())
    if audit.payload["geometry_drc"]["status"] != "PASS":
        raise RuntimeError({"message": "Smoke geometry DRC did not pass", "audit": audit.payload})

    dwg_path = output_root / "v390-minimized-validation.dwg"
    dxf_path = output_root / "v390-minimized-validation.dxf"
    pdf_path = output_root / "v390-minimized-validation.pdf"
    png_path = output_root / "v390-minimized-validation.png"
    saved = await backend.drawing_save(str(dwg_path))
    if not saved.ok:
        raise RuntimeError(saved.to_dict())
    plotted = await backend.drawing_plot_pdf(
        str(pdf_path), paper="A3", orientation="landscape", scale_mode="fit"
    )
    if not plotted.ok:
        raise RuntimeError(plotted.to_dict())
    viewer_guard = plotted.payload.get("viewer_guard") or {}
    if viewer_guard.get("viewer_detected") and not viewer_guard.get("viewer_suppressed"):
        raise RuntimeError({"message": "PDF viewer was detected but not suppressed", "viewer_guard": viewer_guard})
    exported = await backend.drawing_save_as_dxf(str(dxf_path))
    if not exported.ok or not exported.payload.get("active_document_preserved"):
        raise RuntimeError(exported.to_dict())
    preview = await backend.drawing_render_preview(
        str(png_path), paper="A3", orientation="landscape", dpi=150, force=True
    )
    if not preview.ok:
        raise RuntimeError(preview.to_dict())

    summary = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "document": document_name,
        "ready": ready.payload,
        "window": {
            **backend._window_visibility_status(),
            "foreground_before": foreground_before,
            "foreground_after": (
                win32gui.GetForegroundWindow() if foreground_before is not None else None
            ),
        },
        "atomic_rollback": {
            "error_code": failed_batch.error_code,
            "count_before": count_before,
            "count_after": count_after,
            "rolled_back": failed_batch.payload.get("rolled_back"),
        },
        "creation_postconditions": [
            {
                "handle": entry["payload"]["handle"],
                "verified": entry["payload"]["verified"],
                "diff": entry["payload"]["diff"],
            }
            for entry in successful_batch.payload["results"]
        ],
        "trimmed_entity": trimmed.payload,
        "extended_entity": extended_entity.payload,
        "joined": joined.payload,
        "constraint": constrained.payload,
        "solid": subtraction.payload,
        "extrusion": extrusion.payload,
        "audit": {
            "status": audit.payload["geometry_drc"]["status"],
            "entity_count": audit.payload["entity_count"],
            "topology": audit.payload["geometry_drc"]["topology_graph"],
        },
        "dxf": exported.payload,
        "pdf": plotted.payload,
        "preview": preview.payload,
        "artifacts": {
            "dwg": _artifact(dwg_path),
            "dxf": _artifact(dxf_path),
            "pdf": _artifact(pdf_path),
            "png": _artifact(png_path),
        },
    }
    record_path = output_root / "v390-minimized-validation.json"
    record_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if not keep_artifacts:
        _close_active_document()
        deferred_removed = backend._cleanup_deferred_outputs(timeout=2.0)
        deleted = []
        cleanup_errors = []
        for path in output_root.iterdir():
            if path == record_path or not path.is_file():
                continue
            try:
                path.unlink()
                deleted.append(path.name)
            except OSError as exc:
                cleanup_errors.append({"path": str(path), "error": str(exc)})
        summary["cleanup"] = {
            "test_document_closed": True,
            "deferred_outputs_removed": deferred_removed,
            "artifacts_deleted": deleted,
            "errors": cleanup_errors,
        }
        record_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        if cleanup_errors:
            raise RuntimeError(
                {"message": "Smoke artifacts were not fully deleted", "cleanup": summary["cleanup"]}
            )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-root",
        default=str(Path(os.environ.get("AUTOCAD_MCP_OUTPUT_ROOT", "~/Documents/AutoCAD-MCP")).expanduser() / "outputs"),
    )
    parser.add_argument("--keep-artifacts", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(run(Path(args.output_root), keep_artifacts=args.keep_artifacts)),
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
