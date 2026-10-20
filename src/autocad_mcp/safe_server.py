"""Narrow MCP surface for Ngoc Viet CNC's isolated DXF staging workflow."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from .safe_dxf import (
    PlateSpec,
    SquareCenterEdgeSpec,
    create_laser_plate as create_plate,
    create_square_center_edge_plate as create_square_plate,
    inspect_dxf as inspect_file,
    list_managed_dxfs,
    resize_laser_plate as resize_plate,
    validate_laser_plate as validate_plate,
    workspace_root,
)


mcp = FastMCP("ngoc-viet-cnc-safe-dxf")


def _json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


@mcp.tool(annotations={"title": "Create Laser Test Plate", "readOnlyHint": False, "destructiveHint": False})
def create_laser_plate(
    output_name: str,
    width_mm: float = 300.0,
    height_mm: float = 200.0,
    hole_diameter_mm: float = 10.0,
    edge_offset_mm: float = 20.0,
) -> str:
    """Create a new, non-overwriting DXF plate inside the managed test workspace."""
    return _json(create_plate(output_name, PlateSpec(width_mm, height_mm, hole_diameter_mm, edge_offset_mm)))


@mcp.tool(annotations={"title": "Create Square Center and Edge Hole Plate", "readOnlyHint": False, "destructiveHint": False})
def create_square_center_edge_plate(
    output_name: str,
    side_mm: float = 400.0,
    center_hole_diameter_mm: float = 50.0,
    edge_hole_diameter_mm: float = 15.0,
    edge_offset_mm: float = 20.0,
) -> str:
    """Create a square with one centre hole and four midpoint edge-offset holes."""
    return _json(
        create_square_plate(
            output_name,
            SquareCenterEdgeSpec(side_mm, center_hole_diameter_mm, edge_hole_diameter_mm, edge_offset_mm),
        )
    )


@mcp.tool(annotations={"title": "Resize Laser Test Plate", "readOnlyHint": False, "destructiveHint": False})
def resize_laser_plate(
    source_name: str,
    output_name: str,
    width_mm: float,
    height_mm: float,
    hole_diameter_mm: float = 10.0,
    edge_offset_mm: float = 20.0,
) -> str:
    """Create a resized revision; the source is validated and never overwritten."""
    return _json(resize_plate(source_name, output_name, PlateSpec(width_mm, height_mm, hole_diameter_mm, edge_offset_mm)))


@mcp.tool(annotations={"title": "Inspect Managed DXF", "readOnlyHint": True, "destructiveHint": False})
def inspect_dxf(name: str) -> str:
    """Read geometry, layers, units, duplicates, audit state, and cut length."""
    return _json(inspect_file(name))


@mcp.tool(annotations={"title": "Validate Laser Plate", "readOnlyHint": True, "destructiveHint": False})
def validate_laser_plate(
    name: str,
    width_mm: float = 300.0,
    height_mm: float = 200.0,
    hole_diameter_mm: float = 10.0,
    edge_offset_mm: float = 20.0,
) -> str:
    """Validate a managed DXF against an explicit laser-plate contract."""
    return _json(validate_plate(name, PlateSpec(width_mm, height_mm, hole_diameter_mm, edge_offset_mm)))


@mcp.tool(annotations={"title": "List Managed DXFs", "readOnlyHint": True, "destructiveHint": False})
def list_test_dxfs() -> str:
    """List only DXF files inside the managed staging workspace."""
    return _json({"workspace": str(workspace_root()), "files": list_managed_dxfs()})


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
