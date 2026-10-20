"""Create and independently verify the square five-hole DXF acceptance part."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path

import dxfgrabber

from autocad_mcp.safe_dxf import (
    INNER_LAYER,
    OUTER_LAYER,
    _new_document,
    _save_new,
    inspect_dxf,
    resolve_managed_dxf,
)


BASE_NAME = "square_center_and_4edge_holes"
WIDTH_MM = 200.0
HEIGHT_MM = 200.0
OUTER_POINTS = [(0.0, 0.0), (WIDTH_MM, 0.0), (WIDTH_MM, HEIGHT_MM), (0.0, HEIGHT_MM)]
HOLES = [
    ((100.0, 100.0), 10.0),
    ((100.0, 20.0), 5.0),
    ((100.0, 180.0), 5.0),
    ((20.0, 100.0), 5.0),
    ((180.0, 100.0), 5.0),
]
TOLERANCE_MM = 1e-9


def _available_names(output_dir: Path) -> tuple[str, Path]:
    revision = 1
    while True:
        suffix = "" if revision == 1 else f"_rev{revision}"
        dxf_name = f"{BASE_NAME}{suffix}.dxf"
        report_path = output_dir / f"{BASE_NAME}{suffix}.validation.md"
        if not (output_dir / dxf_name).exists() and not report_path.exists():
            return dxf_name, report_path
        revision += 1


def _independent_validation(path: Path) -> dict[str, object]:
    drawing = dxfgrabber.readfile(str(path))
    entities = list(drawing.entities)
    polylines = [entity for entity in entities if entity.dxftype == "LWPOLYLINE"]
    circles = [entity for entity in entities if entity.dxftype == "CIRCLE"]
    points = (
        [(round(float(x), 9), round(float(y), 9)) for x, y, *_ in polylines[0].points]
        if len(polylines) == 1
        else []
    )
    holes = sorted(
        (
            round(float(circle.center[0]), 9),
            round(float(circle.center[1]), 9),
            round(float(circle.radius), 9),
        )
        for circle in circles
    )
    expected_holes = sorted((x, y, radius) for (x, y), radius in HOLES)
    entity_keys = [
        ("LWPOLYLINE", bool(entity.is_closed), tuple(points))
        if entity.dxftype == "LWPOLYLINE"
        else (
            "CIRCLE",
            round(float(entity.center[0]), 9),
            round(float(entity.center[1]), 9),
            round(float(entity.radius), 9),
        )
        for entity in entities
    ]
    duplicate_count = len(entity_keys) - len(set(entity_keys))
    outer_length = 0.0
    if len(points) == 4 and bool(polylines[0].is_closed):
        outer_length = sum(math.dist(points[index], points[(index + 1) % len(points)]) for index in range(len(points)))
    total_cut_length = outer_length + sum(2.0 * math.pi * float(circle.radius) for circle in circles)
    layer_counts: dict[str, int] = {}
    for entity in entities:
        layer_counts[entity.layer] = layer_counts.get(entity.layer, 0) + 1

    expected_cut_length = sum(
        math.dist(OUTER_POINTS[index], OUTER_POINTS[(index + 1) % len(OUTER_POINTS)])
        for index in range(len(OUTER_POINTS))
    ) + sum(2.0 * math.pi * radius for _, radius in HOLES)
    checks = {
        "units_mm": int(drawing.header.get("$INSUNITS", 0)) == 4,
        "entity_count_6": len(entities) == 6,
        "outer_dimensions": points == OUTER_POINTS,
        "outer_closed": len(polylines) == 1 and bool(polylines[0].is_closed),
        "five_holes": len(circles) == 5,
        "hole_geometry": holes == expected_holes,
        "layers": layer_counts == {OUTER_LAYER: 1, INNER_LAYER: 5},
        "no_duplicates": duplicate_count == 0,
        "cut_length": abs(total_cut_length - expected_cut_length) <= TOLERANCE_MM,
    }
    return {
        "library": "dxfgrabber",
        "library_version": getattr(dxfgrabber, "__version__", "1.0.1"),
        "valid": all(checks.values()),
        "checks": checks,
        "overall_size_mm": [WIDTH_MM, HEIGHT_MM],
        "outer_points_mm": points,
        "outer_closed": bool(polylines[0].is_closed) if len(polylines) == 1 else False,
        "holes": [{"center_mm": [x, y], "diameter_mm": 2.0 * radius} for x, y, radius in holes],
        "hole_count": len(circles),
        "layer_counts": layer_counts,
        "duplicate_count": duplicate_count,
        "total_cut_length_mm": total_cut_length,
    }


def _markdown_report(name: str, path: Path, independent: dict[str, object], inspection: dict[str, object]) -> str:
    holes = independent["holes"]
    hole_rows = "\n".join(
        f"| {index} | ({hole['center_mm'][0]:g}, {hole['center_mm'][1]:g}) | {hole['diameter_mm']:g} | CUT_INNER |"
        for index, hole in enumerate(holes, start=1)
    )
    status = "PASS" if independent["valid"] and not inspection["duplicate_count"] else "FAIL"
    return f"""# DXF acceptance report: {name}

- Status: **{status}**
- Scope: headless DXF staging only; AutoCAD production and laser hardware were not contacted.
- File: `{path}`
- SHA-256: `{hashlib.sha256(path.read_bytes()).hexdigest()}`
- Independent reader: `dxfgrabber {independent['library_version']}`
- Units: mm (`$INSUNITS = 4`)
- Overall size: {WIDTH_MM:g} x {HEIGHT_MM:g} mm; origin at (0, 0)
- Outer contour: closed `LWPOLYLINE` on `CUT_OUTER`
- Hole count: {independent['hole_count']}
- Duplicate geometry: {independent['duplicate_count']}
- Closed contours: {inspection['closed_contours']}
- Unsupported entities: {len(inspection['unsupported_entity_handles'])}
- DXF audit errors/fixes: {inspection['audit_error_count']}/{inspection['audit_fix_count']}
- Layers: `CUT_OUTER = 1`, `CUT_INNER = 5`
- Total cut length: {independent['total_cut_length_mm']:.9f} mm

| Hole | Center (mm) | Diameter (mm) | Layer |
|---:|---:|---:|---|
{hole_rows}

All independent checks: **{'PASS' if independent['valid'] else 'FAIL'}**.
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts"))
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    os.environ["NGOC_VIET_DXF_ROOT"] = str(output_dir)
    name, report_path = _available_names(output_dir)

    doc = _new_document()
    modelspace = doc.modelspace()
    modelspace.add_lwpolyline(OUTER_POINTS, close=True, dxfattribs={"layer": OUTER_LAYER})
    for center, radius in HOLES:
        modelspace.add_circle(center, radius, dxfattribs={"layer": INNER_LAYER})
    path = resolve_managed_dxf(name)
    _save_new(doc, path)

    independent = _independent_validation(path)
    inspection = inspect_dxf(name)
    report = _markdown_report(name, path, independent, inspection)
    with report_path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(report)

    result = {
        "status": "PASS" if independent["valid"] and inspection["duplicate_count"] == 0 else "FAIL",
        "file": str(path),
        "report": str(report_path),
        "independent": independent,
        "inspection": inspection,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
