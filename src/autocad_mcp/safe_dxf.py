"""Fail-closed DXF-only workflow for Ngoc Viet CNC staging.

This module deliberately does not import the AutoCAD, COM, native-pipe, LISP,
or supervisor backends. It accepts only managed DXF basenames and never
overwrites an existing output.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import ezdxf
from ezdxf import recover


DXF_ROOT_ENV = "NGOC_VIET_DXF_ROOT"
DEFAULT_ROOT = Path.cwd() / "staging" / "ngoc-viet-cnc-dxf"
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}\.dxf$", re.IGNORECASE)
OUTER_LAYER = "CUT_OUTER"
INNER_LAYER = "CUT_INNER"
ALLOWED_ENTITY_TYPES = {"LWPOLYLINE", "CIRCLE"}
TOLERANCE_MM = 1e-6


@dataclass(frozen=True)
class PlateSpec:
    width_mm: float = 300.0
    height_mm: float = 200.0
    hole_diameter_mm: float = 10.0
    edge_offset_mm: float = 20.0

    def validated(self) -> "PlateSpec":
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not math.isfinite(float(value)) or float(value) <= 0:
                raise ValueError(f"{name} must be a finite positive number")
        radius = self.hole_diameter_mm / 2.0
        if self.edge_offset_mm <= radius:
            raise ValueError("edge_offset_mm must be greater than the hole radius")
        if self.width_mm <= 2 * self.edge_offset_mm or self.height_mm <= 2 * self.edge_offset_mm:
            raise ValueError("plate dimensions must be greater than twice edge_offset_mm")
        return self

    @property
    def hole_centres(self) -> list[tuple[float, float]]:
        o = self.edge_offset_mm
        return [
            (o, o),
            (self.width_mm - o, o),
            (self.width_mm - o, self.height_mm - o),
            (o, self.height_mm - o),
        ]


@dataclass(frozen=True)
class SquareCenterEdgeSpec:
    side_mm: float = 400.0
    center_hole_diameter_mm: float = 50.0
    edge_hole_diameter_mm: float = 15.0
    edge_offset_mm: float = 20.0

    def validated(self) -> "SquareCenterEdgeSpec":
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not math.isfinite(float(value)) or float(value) <= 0:
                raise ValueError(f"{name} must be a finite positive number")
        if self.edge_offset_mm <= self.edge_hole_diameter_mm / 2.0:
            raise ValueError("edge_offset_mm must be greater than the edge-hole radius")
        if self.side_mm <= 2.0 * self.edge_offset_mm:
            raise ValueError("side_mm must be greater than twice edge_offset_mm")
        if self.center_hole_diameter_mm >= self.side_mm:
            raise ValueError("center hole must fit inside the square")
        return self

    @property
    def holes(self) -> list[tuple[tuple[float, float], float]]:
        middle = self.side_mm / 2.0
        offset = self.edge_offset_mm
        return [
            ((middle, middle), self.center_hole_diameter_mm / 2.0),
            ((middle, offset), self.edge_hole_diameter_mm / 2.0),
            ((middle, self.side_mm - offset), self.edge_hole_diameter_mm / 2.0),
            ((offset, middle), self.edge_hole_diameter_mm / 2.0),
            ((self.side_mm - offset, middle), self.edge_hole_diameter_mm / 2.0),
        ]


def workspace_root() -> Path:
    root = Path(os.environ.get(DXF_ROOT_ENV, DEFAULT_ROOT)).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_managed_dxf(name: str, *, must_exist: bool = False) -> Path:
    if not isinstance(name, str) or not SAFE_NAME.fullmatch(name):
        raise ValueError("name must be a simple managed .dxf basename")
    if Path(name).name != name or "/" in name or "\\" in name:
        raise ValueError("directories and path traversal are not allowed")
    root = workspace_root()
    candidate = (root / name).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("DXF path escaped the managed staging root") from exc
    if must_exist and (not candidate.is_file() or candidate.is_symlink()):
        raise FileNotFoundError(f"managed DXF does not exist: {name}")
    return candidate


def _new_document() -> ezdxf.document.Drawing:
    doc = ezdxf.new("R2013", setup=True)
    doc.units = ezdxf.units.MM
    doc.header["$INSUNITS"] = ezdxf.units.MM
    for name, color in ((OUTER_LAYER, 1), (INNER_LAYER, 3)):
        if name not in doc.layers:
            doc.layers.add(name=name, color=color)
    return doc


def _save_new(doc: ezdxf.document.Drawing, target: Path) -> None:
    if target.exists():
        raise FileExistsError(f"refusing to overwrite existing DXF: {target.name}")
    temporary = target.with_name(f".{target.stem}.{uuid.uuid4().hex}.writing.dxf")
    try:
        doc.saveas(temporary)
        try:
            # A hard-link publish is atomic and fails if the target appears
            # concurrently. Unlike Path.replace(), it can never overwrite.
            os.link(temporary, target)
        except FileExistsError as exc:
            raise FileExistsError(f"refusing to overwrite existing DXF: {target.name}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def create_laser_plate(name: str, spec: PlateSpec) -> dict[str, Any]:
    spec = spec.validated()
    target = resolve_managed_dxf(name)
    doc = _new_document()
    msp = doc.modelspace()
    msp.add_lwpolyline(
        [(0.0, 0.0), (spec.width_mm, 0.0), (spec.width_mm, spec.height_mm), (0.0, spec.height_mm)],
        close=True,
        dxfattribs={"layer": OUTER_LAYER},
    )
    radius = spec.hole_diameter_mm / 2.0
    for centre in spec.hole_centres:
        msp.add_circle(centre, radius, dxfattribs={"layer": INNER_LAYER})
    _save_new(doc, target)
    report = validate_laser_plate(name, spec)
    report.update({"created": True, "path": str(target)})
    return report


def create_square_center_edge_plate(name: str, spec: SquareCenterEdgeSpec) -> dict[str, Any]:
    spec = spec.validated()
    target = resolve_managed_dxf(name)
    doc = _new_document()
    msp = doc.modelspace()
    side = spec.side_mm
    expected_points = [(0.0, 0.0), (side, 0.0), (side, side), (0.0, side)]
    msp.add_lwpolyline(expected_points, close=True, dxfattribs={"layer": OUTER_LAYER})
    for centre, radius in spec.holes:
        msp.add_circle(centre, radius, dxfattribs={"layer": INNER_LAYER})
    _save_new(doc, target)

    inspection = inspect_dxf(name)
    polylines = [item for item in inspection["geometry"] if item["type"] == "LWPOLYLINE"]
    circles = [item for item in inspection["geometry"] if item["type"] == "CIRCLE"]
    actual_holes = sorted(
        (_rounded(item["center_mm"][0]), _rounded(item["center_mm"][1]), _rounded(item["radius_mm"]))
        for item in circles
    )
    expected_holes = sorted((_rounded(x), _rounded(y), _rounded(radius)) for (x, y), radius in spec.holes)
    checks = {
        "units_mm": inspection["units"] == "mm",
        "entity_count_6": inspection["entity_count"] == 6,
        "outer_closed": len(polylines) == 1 and polylines[0]["closed"],
        "outer_dimensions": len(polylines) == 1 and polylines[0]["points_mm"] == expected_points,
        "five_holes": actual_holes == expected_holes,
        "layers": inspection["layers"] == {OUTER_LAYER: 1, INNER_LAYER: 5},
        "no_duplicates": inspection["duplicate_count"] == 0,
        "no_unsupported_entities": not inspection["unsupported_entity_handles"],
        "no_dxf_audit_errors": inspection["audit_error_count"] == 0,
        "six_closed_contours": inspection["closed_contours"] == 6,
    }
    return {
        "valid": all(checks.values()),
        "checks": checks,
        "expected": asdict(spec),
        "inspection": inspection,
        "created": True,
        "path": str(target),
    }


def resize_laser_plate(source_name: str, output_name: str, spec: PlateSpec) -> dict[str, Any]:
    source = resolve_managed_dxf(source_name, must_exist=True)
    target = resolve_managed_dxf(output_name)
    if source == target:
        raise ValueError("output_name must differ from source_name; in-place overwrite is forbidden")
    source_report = validate_laser_plate(source_name)
    if not source_report["valid"]:
        raise ValueError("source DXF failed validation and cannot be resized")
    result = create_laser_plate(output_name, spec)
    result["derived_from"] = source_name
    return result


def _rounded(value: float) -> float:
    return round(float(value), 9)


def _entity_key(entity: Any) -> tuple[Any, ...]:
    if entity.dxftype() == "CIRCLE":
        centre = entity.dxf.center
        return ("CIRCLE", _rounded(centre.x), _rounded(centre.y), _rounded(entity.dxf.radius))
    if entity.dxftype() == "LWPOLYLINE":
        points = [(_rounded(p[0]), _rounded(p[1])) for p in entity.get_points("xy")]
        rotations = [tuple(points[i:] + points[:i]) for i in range(len(points))]
        reverse = list(reversed(points))
        rotations.extend(tuple(reverse[i:] + reverse[:i]) for i in range(len(reverse)))
        return ("LWPOLYLINE", bool(entity.closed), min(rotations) if rotations else ())
    return (entity.dxftype(), str(entity.dxf.handle))


def inspect_dxf(name: str) -> dict[str, Any]:
    path = resolve_managed_dxf(name, must_exist=True)
    doc, auditor = recover.readfile(path)
    entities = list(doc.modelspace())
    counts: dict[str, int] = {}
    layers: dict[str, int] = {}
    duplicates: list[tuple[Any, ...]] = []
    seen: set[tuple[Any, ...]] = set()
    closed_contours = 0
    total_cut_length = 0.0
    unsupported: list[str] = []
    geometry: list[dict[str, Any]] = []
    for entity in entities:
        kind = entity.dxftype()
        counts[kind] = counts.get(kind, 0) + 1
        layer = str(entity.dxf.get("layer", "0"))
        layers[layer] = layers.get(layer, 0) + 1
        if kind not in ALLOWED_ENTITY_TYPES:
            unsupported.append(str(entity.dxf.handle))
            continue
        key = _entity_key(entity)
        if key in seen:
            duplicates.append(key)
        seen.add(key)
        if kind == "CIRCLE":
            centre = entity.dxf.center
            radius = float(entity.dxf.radius)
            closed_contours += 1
            total_cut_length += 2.0 * math.pi * radius
            geometry.append({"type": kind, "layer": layer, "center_mm": [centre.x, centre.y], "radius_mm": radius})
        elif kind == "LWPOLYLINE":
            points = [(float(p[0]), float(p[1])) for p in entity.get_points("xy")]
            if entity.closed:
                closed_contours += 1
            segments = list(zip(points, points[1:]))
            if entity.closed and len(points) > 1:
                segments.append((points[-1], points[0]))
            length = sum(math.dist(a, b) for a, b in segments)
            total_cut_length += length
            geometry.append({"type": kind, "layer": layer, "closed": bool(entity.closed), "points_mm": points, "length_mm": length})
    return {
        "name": name,
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "dxf_version": doc.dxfversion,
        "units_code": int(doc.header.get("$INSUNITS", 0)),
        "units": "mm" if int(doc.header.get("$INSUNITS", 0)) == ezdxf.units.MM else "not-mm",
        "entity_count": len(entities),
        "entity_counts": counts,
        "layers": layers,
        "closed_contours": closed_contours,
        "duplicate_count": len(duplicates),
        "unsupported_entity_handles": unsupported,
        "audit_error_count": len(auditor.errors),
        "audit_fix_count": len(auditor.fixes),
        "total_cut_length_mm": total_cut_length,
        "geometry": geometry,
    }


def validate_laser_plate(name: str, expected: PlateSpec | None = None) -> dict[str, Any]:
    expected = (expected or PlateSpec()).validated()
    inspection = inspect_dxf(name)
    polylines = [item for item in inspection["geometry"] if item["type"] == "LWPOLYLINE"]
    circles = [item for item in inspection["geometry"] if item["type"] == "CIRCLE"]
    expected_points = [(0.0, 0.0), (expected.width_mm, 0.0), (expected.width_mm, expected.height_mm), (0.0, expected.height_mm)]
    expected_centres = sorted([(_rounded(x), _rounded(y)) for x, y in expected.hole_centres])
    actual_centres = sorted([(_rounded(c["center_mm"][0]), _rounded(c["center_mm"][1])) for c in circles])
    checks = {
        "units_mm": inspection["units"] == "mm",
        "entity_count_5": inspection["entity_count"] == 5,
        "one_outer_polyline": len(polylines) == 1,
        "outer_closed": len(polylines) == 1 and polylines[0]["closed"],
        "outer_dimensions": len(polylines) == 1 and all(
            math.dist(a, b) <= TOLERANCE_MM for a, b in zip(polylines[0]["points_mm"], expected_points)
        ),
        "four_holes": len(circles) == 4,
        "hole_diameter": len(circles) == 4 and all(
            abs(2.0 * float(circle["radius_mm"]) - expected.hole_diameter_mm) <= TOLERANCE_MM for circle in circles
        ),
        "hole_positions": actual_centres == expected_centres,
        "layers": inspection["layers"] == {OUTER_LAYER: 1, INNER_LAYER: 4},
        "no_duplicates": inspection["duplicate_count"] == 0,
        "no_unsupported_entities": not inspection["unsupported_entity_handles"],
        "no_dxf_audit_errors": inspection["audit_error_count"] == 0,
        "five_closed_contours": inspection["closed_contours"] == 5,
    }
    return {
        "valid": all(checks.values()),
        "checks": checks,
        "expected": asdict(expected),
        "inspection": inspection,
    }


def list_managed_dxfs() -> list[dict[str, Any]]:
    return [
        {"name": path.name, "bytes": path.stat().st_size}
        for path in sorted(workspace_root().glob("*.dxf"))
        if path.is_file() and not path.is_symlink()
    ]
