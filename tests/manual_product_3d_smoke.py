"""Real minimized-AutoCAD smoke for v3.10 product features and fixed views."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path


def _new_document():
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    application = win32com.client.GetActiveObject("AutoCAD.Application")
    application.Documents.Add()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            document = application.ActiveDocument
            if str(document.Name):
                return document
        except Exception:
            pass
        time.sleep(0.25)
    raise RuntimeError("AutoCAD did not create an active product smoke document")


def _close_document(document) -> None:
    try:
        document.Close(False)
    except Exception:
        pass


async def run(output_root: Path) -> dict:
    import win32gui

    from autocad_mcp.backends.file_ipc import FileIPCBackend
    from autocad_mcp.product_design import (
        clearance_sweep,
        image_content_metrics,
        interference_sample,
        measure_registered_feature,
        register_feature,
        set_motion,
    )

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_root.mkdir(parents=True, exist_ok=True)
    preview_root = output_root / f"product-3d-{timestamp}-temporary"
    preview_root.mkdir(parents=True, exist_ok=False)
    record_path = output_root / f"product-3d-{timestamp}.json"
    backend = FileIPCBackend()
    document = None
    preview_paths: list[Path] = []
    foreground_before = win32gui.GetForegroundWindow()
    summary: dict = {"timestamp": timestamp, "status": "RUNNING"}
    try:
        ready = await backend.ensure_ready()
        if not ready.ok:
            raise RuntimeError(ready.to_dict())
        await backend.minimize_window()
        created_document = await backend.drawing_create(None)
        if not created_document.ok:
            raise RuntimeError(created_document.to_dict())
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        document = win32com.client.GetActiveObject("AutoCAD.Application").ActiveDocument
        await backend.minimize_window()
        if win32gui.GetForegroundWindow() == backend._hwnd:
            raise RuntimeError("AutoCAD stole foreground focus during document creation")
        ready = await backend.ensure_ready()
        if not ready.ok:
            raise RuntimeError(ready.to_dict())
        setup = await backend.drawing_setup_mechanical(
            {"sheet": "A3", "orientation": "landscape", "projection": "first-angle"}
        )
        if not setup.ok:
            raise RuntimeError(setup.to_dict())
        context = await backend.document_context()
        doc_id = context.payload["active_doc_id"]

        requests = [
            (
                "rounded_box",
                {
                    "feature_id": "BODY",
                    "component_id": "BASE",
                    "center": [0, 0, 0],
                    "dimensions": [100, 60, 20],
                    "radius": 8,
                    "source_authority": "concept",
                    "layer": "OUTLINE",
                },
            ),
            (
                "rotary_layer",
                {
                    "feature_id": "ROTARY",
                    "component_id": "ROTARY",
                    "center": [0, 0, 18],
                    "outer_radius": 26,
                    "inner_radius": 8,
                    "height": 8,
                    "axis_point": [0, 0, 18],
                    "axis_direction": [0, 0, 1],
                    "rotation_angle": 0,
                    "motion_limit": [-60, 60],
                    "clearance": 1,
                    "source_authority": "concept",
                    "layer": "OUTLINE",
                },
            ),
            (
                "module_reservation",
                {
                    "feature_id": "USB-RESERVATION",
                    "component_id": "USB-MODULE",
                    "center": [0, 0, 32],
                    "dimensions": [20, 10, 8],
                    "radius": 2,
                    "module_status": "TBD",
                    "authority": "concept",
                    "source_authority": "concept",
                    "do_not_dimension_apertures": True,
                    "layer": "OUTLINE",
                },
            ),
        ]
        features = []
        for kind, data in requests:
            created = await backend.product_create_feature(kind, data)
            if not created.ok:
                raise RuntimeError(created.to_dict())
            if created.payload.get("diff"):
                raise RuntimeError({"message": "feature readback mismatch", "result": created.to_dict()})
            features.append(register_feature(backend, doc_id, created.payload))
        set_motion(backend, doc_id, features[1]["motion"])

        radius = measure_registered_feature(
            backend, doc_id, "BODY", "fillet_radius"
        )
        unstable_edge_call = await backend.solid_fillet_edges(
            features[0]["handle"], ["rounded_edge_x"], 4
        )
        if unstable_edge_call.ok or unstable_edge_call.error_code != "E_STABLE_FEATURE_SELECTION_UNAVAILABLE":
            raise RuntimeError("General edge selection did not fail safely")

        views = {}
        for view_name in ("front", "right", "top", "iso", "rotated_iso"):
            path = preview_root / f"{view_name}.png"
            preview_paths.append(path)
            rendered = await backend.product_render_view(
                view_name,
                str(path),
                {"paper": "A4", "orientation": "landscape", "dpi": 120, "force": True},
            )
            if not rendered.ok:
                raise RuntimeError(rendered.to_dict())
            metrics = image_content_metrics(path)
            if metrics["framing_status"] != "PASS":
                raise RuntimeError({"view": view_name, "metrics": metrics})
            views[view_name] = {
                "camera": rendered.payload["actual_camera"],
                "camera_framing": rendered.payload["camera_framing"],
                "native_framing": rendered.payload.get("framing_normalization"),
                "projection": rendered.payload["projection"],
                "metrics": metrics,
            }

        static_screen = interference_sample(backend, doc_id)
        motion_screen = clearance_sweep(
            backend, doc_id, "ROTARY", sample_count=9
        )
        summary.update(
            status="PASS",
            version="3.10.0",
            document=context.payload,
            visibility=backend._window_visibility_status(),
            document_creation=created_document.payload,
            foreground_before=foreground_before,
            foreground_after=win32gui.GetForegroundWindow(),
            autocad_was_foreground=False,
            features=[
                {
                    "feature_id": item["feature_id"],
                    "kind": item["kind"],
                    "handle": item["handle"],
                    "bounds": item["bounds"],
                    "volume": item.get("volume"),
                    "semantic_edge_count": len(item.get("semantic_edges", [])),
                }
                for item in features
            ],
            fillet_radius=radius,
            unstable_edge_error=unstable_edge_call.to_dict(),
            views=views,
            static_interference=static_screen,
            motion_clearance=motion_screen,
        )
    except Exception as exc:
        summary.update(status="FAIL", error=repr(exc))
        raise
    finally:
        if document is not None:
            _close_document(document)
        deleted = []
        cleanup_errors = []
        for path in preview_paths:
            try:
                path.unlink(missing_ok=True)
                deleted.append(path.name)
            except OSError as exc:
                cleanup_errors.append({"path": str(path), "error": str(exc)})
        try:
            preview_root.rmdir()
        except OSError:
            pass
        summary["cleanup"] = {
            "test_document_closed": document is not None,
            "temporary_previews_deleted": deleted,
            "errors": cleanup_errors,
        }
        summary["foreground_final"] = win32gui.GetForegroundWindow()
        record_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    return {"record": str(record_path), **summary}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default=r"D:\CAD-Automation\audits")
    args = parser.parse_args()
    result = asyncio.run(run(Path(args.output_root)))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
