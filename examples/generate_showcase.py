"""Generate the README showcase from native AutoCAD product views."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path


async def main(output: Path, *, pause: float = 0.0, keep_open: bool = False) -> None:
    import pythoncom
    import win32com.client

    from autocad_mcp.backends.file_ipc import FileIPCBackend

    pythoncom.CoInitialize()
    backend = FileIPCBackend()
    ready = await backend.ensure_ready()
    if not ready.ok:
        raise RuntimeError(ready.to_dict())
    created_document = None
    for _ in range(6):
        await backend.recover()
        await asyncio.sleep(1.0)
        created_document = await backend.drawing_create(None)
        if created_document.ok:
            break
    if created_document is None or not created_document.ok:
        raise RuntimeError(created_document.to_dict() if created_document else "no result")
    document = win32com.client.GetActiveObject("AutoCAD.Application").ActiveDocument
    try:
        async def recording_pause() -> None:
            if pause > 0:
                await asyncio.sleep(pause)

        async def require(operation, *args):
            result = await operation(*args)
            if not result.ok:
                raise RuntimeError(result.to_dict())
            await recording_pause()
            return result.payload["handle"]

        # A native machined bearing plate: every hole is a real B-rep subtraction.
        plate = await require(backend.solid_create_box, [0, 0, 6], 120, 80, 12, None)
        bore = await require(backend.solid_create_cylinder, [0, 0, -1], 14, 14, None)
        plate = await require(backend.solid_boolean, plate, bore, "subtract")
        for x in (-46, 46):
            for y in (-26, 26):
                cutter = await require(
                    backend.solid_create_cylinder, [x, y, -1], 5, 14, None
                )
                plate = await require(backend.solid_boolean, plate, cutter, "subtract")

        async def create_ring(feature_id, center, outer_radius, height):
            result = await backend.product_create_feature(
                "rotary_layer",
                {
                    "feature_id": feature_id,
                    "component_id": "BEARING_PLATE",
                    "center": center,
                    "outer_radius": outer_radius,
                    "inner_radius": 14,
                    "height": height,
                    "axis_point": center,
                    "axis_direction": [0, 0, 1],
                    "rotation_angle": 0,
                    "motion_limit": [0, 0],
                    "clearance": 0,
                    "source_authority": "concept",
                    "layer": "0",
                },
            )
            if not result.ok or result.payload.get("diff"):
                raise RuntimeError(result.to_dict())
            await recording_pause()
            return result.payload["handle"]

        boss = await create_ring("BOSS", [0, 0, 18], 25, 12)
        collar = await create_ring("COLLAR", [0, 0, 28], 18, 8)

        colors = [(35, 86, 126), (220, 128, 44), (57, 148, 157)]
        for handle, color in zip((plate, boss, collar), colors):
            entity = document.HandleToObject(handle)
            try:
                true_color = entity.TrueColor
                true_color.SetRGB(*color)
                entity.TrueColor = true_color
            except Exception:
                entity.Color = 5
        document.SendCommand("_.VSCURRENT _ShadedWithEdges ")
        if not backend._wait_for_autocad_idle(timeout=8.0):
            raise RuntimeError("AutoCAD did not finish changing visual style")
        output.parent.mkdir(parents=True, exist_ok=True)
        rendered = await backend.product_render_view(
            "iso",
            str(output.resolve()),
            {
                "paper": "A4",
                "orientation": "landscape",
                "dpi": 180,
                "framing_fill": 0.8,
                "plot_style": "",
                "force": True,
            },
        )
        if not rendered.ok:
            raise RuntimeError(rendered.to_dict())
    finally:
        if keep_open:
            print("Recording complete; AutoCAD document left open.")
        else:
            try:
                active = win32com.client.GetActiveObject("AutoCAD.Application").ActiveDocument
                active.SendCommand("_.VSCURRENT _2dwireframe ")
                backend._wait_for_autocad_idle(timeout=5.0)
                active.Close(False)
            except Exception:
                pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default=str(Path(__file__).parents[1] / "docs" / "assets" / "autocad-mcp-showcase.png"),
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="show AutoCAD in the foreground, pause between steps, and leave the final document open",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=None,
        help="seconds to pause after each created or modified solid",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="leave the generated AutoCAD document open after rendering",
    )
    args = parser.parse_args()
    if args.record:
        os.environ["AUTOCAD_MCP_WINDOW_MODE"] = "foreground"
        os.environ["AUTOCAD_MCP_ACTIVATE_ON_DRAW"] = "true"
    else:
        os.environ.setdefault("AUTOCAD_MCP_WINDOW_MODE", "minimized")
        os.environ.setdefault("AUTOCAD_MCP_ACTIVATE_ON_DRAW", "false")
    pause = args.pause if args.pause is not None else (1.5 if args.record else 0.0)
    asyncio.run(
        main(
            Path(args.output),
            pause=max(0.0, pause),
            keep_open=args.keep_open or args.record,
        )
    )
