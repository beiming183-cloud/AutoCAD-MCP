"""Create and validate a mechanical DXF without requiring AutoCAD."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from autocad_mcp.backends.ezdxf_backend import EzdxfBackend


async def main() -> None:
    output_root = Path(
        os.environ.get("AUTOCAD_MCP_OUTPUT_ROOT", Path.cwd() / "demo-output")
    ).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    dxf_path = output_root / "autocad-mcp-headless-demo.dxf"
    png_path = output_root / "autocad-mcp-headless-demo.png"

    backend = EzdxfBackend()
    await backend.initialize()
    setup = await backend.drawing_setup_mechanical(
        {"sheet": "A4", "orientation": "landscape", "projection": "first-angle"}
    )
    if not setup.ok:
        raise RuntimeError(setup.to_dict())

    created = await backend.create_batch(
        [
            {
                "type": "rectangle",
                "x1": 0,
                "y1": 0,
                "x2": 120,
                "y2": 70,
                "layer": "OUTLINE",
                "component_id": "PLATE",
                "design_role": "geometry",
                "line_class": "outline",
                "source_authority": "concept",
            },
            *[
                {
                    "type": "circle",
                    "cx": x,
                    "cy": y,
                    "radius": 7,
                    "layer": "OUTLINE",
                    "component_id": "PLATE",
                    "design_role": "mounting_hole",
                    "line_class": "outline",
                    "source_authority": "concept",
                }
                for x in (20, 100)
                for y in (18, 52)
            ],
            {
                "type": "line",
                "x1": -5,
                "y1": 35,
                "x2": 125,
                "y2": 35,
                "layer": "CENTER",
                "component_id": "PLATE",
                "design_role": "centerline",
                "line_class": "center",
                "source_authority": "derived",
            },
        ],
        atomic=True,
        strict=True,
    )
    if not created.ok:
        raise RuntimeError(created.to_dict())

    audit = await backend.drawing_audit(
        rules={
            "require_component_id": True,
            "required_semantic_fields": ["component_id", "design_role"],
            "equal_radius_groups": [
                {
                    "name": "mounting holes",
                    "handles": created.payload["created_handles"][1:5],
                }
            ],
        }
    )
    saved = await backend.drawing_save(str(dxf_path))
    preview = await backend.drawing_render_preview(
        str(png_path), paper="A4", orientation="landscape", dpi=150, force=True
    )
    if not saved.ok or not preview.ok:
        raise RuntimeError({"save": saved.to_dict(), "preview": preview.to_dict()})

    print(
        json.dumps(
            {
                "ok": True,
                "backend": "ezdxf",
                "entity_count": audit.payload["entity_count"],
                "drc_status": audit.payload["geometry_drc"]["status"],
                "dxf": str(dxf_path),
                "preview": str(png_path),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
