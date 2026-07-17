"""Generate a native AutoCAD promotional view of a compact rotary actuator."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path


async def main(output: Path, *, pause: float = 0.0, keep_open: bool = True) -> None:
    import pythoncom
    import win32com.client

    from autocad_mcp.backends.file_ipc import FileIPCBackend

    pythoncom.CoInitialize()
    backend = FileIPCBackend()
    ready = await backend.ensure_ready()
    if not ready.ok:
        raise RuntimeError(ready.to_dict())

    created = await backend.drawing_create(None)
    if not created.ok:
        raise RuntimeError(created.to_dict())

    document = win32com.client.GetActiveObject("AutoCAD.Application").ActiveDocument

    async def wait_for_recording() -> None:
        if pause > 0:
            await asyncio.sleep(pause)

    async def require(operation, *args):
        result = await operation(*args)
        if not result.ok:
            raise RuntimeError(result.to_dict())
        await wait_for_recording()
        return result.payload["handle"]

    async def ring(feature_id, center, outer_radius, inner_radius, height):
        result = await backend.product_create_feature(
            "rotary_layer",
            {
                "feature_id": feature_id,
                "component_id": "ROTARY_ACTUATOR",
                "center": center,
                "outer_radius": outer_radius,
                "inner_radius": inner_radius,
                "height": height,
                "axis_point": center,
                "axis_direction": [0, 0, 1],
                "rotation_angle": 0,
                "motion_limit": [-90, 90],
                "clearance": 2,
                "source_authority": "concept",
                "layer": "0",
            },
        )
        if not result.ok or result.payload.get("diff"):
            raise RuntimeError(result.to_dict())
        await wait_for_recording()
        return result.payload["handle"]

    # Main structure: a machined base, recessed actuator housing, and layered rotor.
    base = await require(backend.solid_create_box, [0, 0, 7], 170, 118, 14, None)
    for x, y in ((-68, -42), (-68, 42), (68, -42), (68, 42)):
        cutter = await require(backend.solid_create_cylinder, [x, y, -1], 6, 16, None)
        base = await require(backend.solid_boolean, base, cutter, "subtract")

    housing = await require(backend.solid_create_cylinder, [0, 0, 14], 52, 52, None)
    pocket = await require(backend.solid_create_cylinder, [0, 0, 53], 38, 14, None)
    housing = await require(backend.solid_boolean, housing, pocket, "subtract")

    lower_ring = await ring("LOWER_BEARING_RING", [0, 0, 18], 57, 48, 8)
    upper_cover = await ring("UPPER_COVER", [0, 0, 66], 47, 28, 8)
    rotor = await ring("ROTOR_RING", [0, 0, 75], 31, 12, 10)
    shaft = await require(backend.solid_create_cylinder, [0, 0, 69], 12, 34, None)
    shaft_flange = await ring("SHAFT_FLANGE", [0, 0, 92], 22, 12, 6)

    # Six visible cover fasteners and four base fasteners make the assembly read as engineered.
    cover_bolts = []
    for index in range(6):
        import math

        angle = math.radians(index * 60)
        cover_bolts.append(
            await require(
                backend.solid_create_cylinder,
                [38 * math.cos(angle), 38 * math.sin(angle), 70],
                4,
                5,
                None,
            )
        )

    base_bolts = []
    for x, y in ((-68, -42), (-68, 42), (68, -42), (68, 42)):
        base_bolts.append(await require(backend.solid_create_cylinder, [x, y, 14], 5, 4, None))

    # A side electronics/motor module is a real analytic rounded B-rep, not a blocky decal.
    side_module_result = await backend.product_create_feature(
        "rounded_box",
        {
            "feature_id": "SIDE_MOTOR_MODULE",
            "component_id": "ROTARY_ACTUATOR",
            "center": [78, 0, 43],
            "dimensions": [38, 58, 48],
            "radius": 6,
            "source_authority": "concept",
            "layer": "0",
        },
    )
    if not side_module_result.ok or side_module_result.payload.get("diff"):
        raise RuntimeError(side_module_result.to_dict())
    side_module = side_module_result.payload["handle"]
    await wait_for_recording()

    indicator_colors = [(220, 128, 44), (57, 148, 157), (190, 58, 58)]
    indicators = []
    for y, color in zip((-14, 0, 14), indicator_colors):
        indicators.append(await require(backend.solid_create_cylinder, [78, y, 67], 4, 5, None))

    # Keep the assembly legible in a single native shaded-with-edges view.
    colors = {
        base: (28, 62, 91),
        housing: (48, 105, 137),
        lower_ring: (40, 143, 154),
        upper_cover: (50, 144, 160),
        rotor: (215, 115, 38),
        shaft: (164, 173, 179),
        shaft_flange: (226, 145, 46),
        side_module: (35, 82, 111),
    }
    for handle in cover_bolts + base_bolts:
        colors[handle] = (218, 174, 54)
    for handle, color in zip(indicators, indicator_colors):
        colors[handle] = color

    for handle, color in colors.items():
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
            "paper": "A3",
            "orientation": "landscape",
            "dpi": 180,
            "framing_fill": 0.78,
            "plot_style": "",
            "force": True,
        },
    )
    if not rendered.ok:
        raise RuntimeError(rendered.to_dict())
    print({"ok": True, "preview": str(output.resolve()), "document_left_open": keep_open})

    if not keep_open:
        document.SendCommand("_.VSCURRENT _2dwireframe ")
        backend._wait_for_autocad_idle(timeout=5.0)
        document.Close(False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default=str(Path(__file__).parents[1] / "docs" / "assets" / "autocad-mcp-actuator-promo.png"),
    )
    parser.add_argument("--pause", type=float, default=0.0)
    parser.add_argument("--keep-open", action="store_true")
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args()
    if args.record:
        os.environ["AUTOCAD_MCP_WINDOW_MODE"] = "foreground"
        os.environ["AUTOCAD_MCP_ACTIVATE_ON_DRAW"] = "true"
    else:
        os.environ.setdefault("AUTOCAD_MCP_WINDOW_MODE", "visible")
        os.environ.setdefault("AUTOCAD_MCP_ACTIVATE_ON_DRAW", "false")
    asyncio.run(
        main(
            Path(args.output),
            pause=max(0.0, args.pause),
            keep_open=args.keep_open or args.record,
        )
    )
