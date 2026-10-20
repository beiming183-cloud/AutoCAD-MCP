"""Generate and independently verify the Ngoc Viet CNC laser acceptance DXF."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path

import dxfgrabber

from autocad_mcp.safe_dxf import PlateSpec, create_laser_plate


def independent_validation(path: Path) -> dict:
    drawing = dxfgrabber.readfile(str(path))
    entities = list(drawing.entities)
    polylines = [entity for entity in entities if entity.dxftype == "LWPOLYLINE"]
    circles = [entity for entity in entities if entity.dxftype == "CIRCLE"]
    points = [(float(x), float(y)) for x, y, *_ in polylines[0].points] if len(polylines) == 1 else []
    centres = sorted((float(circle.center[0]), float(circle.center[1])) for circle in circles)
    radii = [float(circle.radius) for circle in circles]
    expected_points = [(0.0, 0.0), (300.0, 0.0), (300.0, 200.0), (0.0, 200.0)]
    expected_centres = [(20.0, 20.0), (20.0, 180.0), (280.0, 20.0), (280.0, 180.0)]
    layers = {entity.layer for entity in entities}
    checks = {
        "units_mm": int(drawing.header.get("$INSUNITS", 0)) == 4,
        "entity_count_5": len(entities) == 5,
        "one_outer_polyline": len(polylines) == 1,
        "outer_closed": len(polylines) == 1 and bool(polylines[0].is_closed),
        "outer_300x200": points == expected_points,
        "four_holes": len(circles) == 4,
        "hole_diameter_10": len(radii) == 4 and all(abs(radius - 5.0) <= 1e-9 for radius in radii),
        "hole_positions_20_from_edges": centres == expected_centres,
        "layers": layers == {"CUT_OUTER", "CUT_INNER"},
    }
    return {
        "library": "dxfgrabber",
        "library_version": getattr(dxfgrabber, "__version__", "1.0.1"),
        "valid": all(checks.values()),
        "checks": checks,
        "units_code": int(drawing.header.get("$INSUNITS", 0)),
        "entity_count": len(entities),
        "layers": sorted(layers),
        "outer_points_mm": points,
        "outer_closed": bool(polylines[0].is_closed) if len(polylines) == 1 else False,
        "hole_centres_mm": centres,
        "hole_radii_mm": radii,
        "total_cut_length_mm": 1000.0 + sum(2.0 * math.pi * radius for radius in radii),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts"))
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    os.environ["NGOC_VIET_DXF_ROOT"] = str(output_dir)

    name = "laser_test_300x200.dxf"
    created = create_laser_plate(name, PlateSpec())
    path = output_dir / name
    independent = independent_validation(path)
    report = {
        "status": "PASS" if created["valid"] and independent["valid"] else "FAIL",
        "scope": "headless DXF staging only; AutoCAD and laser hardware were not contacted",
        "file": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "generator_validation": created,
        "independent_validation": independent,
    }
    report_path = output_dir / "laser_test_300x200.validation.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
