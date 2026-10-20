"""Dedicated stdio MCP server for fixed, explicitly approved AutoCAD operations."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from .live_autocad import LiveAutoCADAdapter, LivePlateSpec


mcp = FastMCP("ngoc-viet-cnc-live-autocad")


def _json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


@mcp.tool(annotations={"title": "Live AutoCAD Status", "readOnlyHint": True, "destructiveHint": False})
def autocad_status() -> str:
    """Report the approved live AutoCAD connection without executing a CAD command."""
    return _json(LiveAutoCADAdapter().status())


@mcp.tool(annotations={"title": "Create Live Test Plate", "readOnlyHint": False, "destructiveHint": False})
def autocad_create_test_plate(
    output_name: str,
    side_mm: float = 200.0,
    center_hole_diameter_mm: float = 20.0,
    edge_hole_diameter_mm: float = 10.0,
    edge_offset_mm: float = 20.0,
) -> str:
    """Create one new allowlisted test plate in AutoCAD and save it below the staging root."""
    spec = LivePlateSpec(side_mm, center_hole_diameter_mm, edge_hole_diameter_mm, edge_offset_mm)
    return _json(LiveAutoCADAdapter().create_plate(output_name, spec))


@mcp.tool(annotations={"title": "Inspect Live Test Drawing", "readOnlyHint": True, "destructiveHint": False})
def autocad_inspect_test_drawing(name: str) -> str:
    """Open a managed staging DXF read-only and return allowlisted entity geometry."""
    return _json(LiveAutoCADAdapter().inspect_managed(name))


@mcp.tool(annotations={"title": "Create Modified Hole Revision", "readOnlyHint": False, "destructiveHint": False})
def autocad_modify_hole_revision(
    source_name: str,
    output_name: str,
    center_x_mm: float,
    center_y_mm: float,
    new_diameter_mm: float,
) -> str:
    """Change one circle selected by exact center and save only as a new managed revision."""
    return _json(
        LiveAutoCADAdapter().modify_hole_revision(
            source_name, output_name, center_x_mm, center_y_mm, new_diameter_mm
        )
    )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
