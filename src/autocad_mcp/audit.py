"""Structured drawing audit helpers shared by both backends."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import ezdxf


INSUNITS_NAMES = {
    0: "unitless",
    1: "inches",
    2: "feet",
    4: "millimeters",
    5: "centimeters",
    6: "meters",
    7: "kilometers",
    21: "us-survey-feet",
}


def _number(value: Any) -> float | int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    rounded = round(number, 6)
    return int(rounded) if rounded.is_integer() else rounded


def _point(value: Any) -> list[float | int] | None:
    try:
        values = list(value)
    except (TypeError, ValueError):
        return None
    result = [_number(item) for item in values[:3]]
    return [item for item in result if item is not None]


def normalize_ezdxf_entity(entity: Any) -> dict[str, Any]:
    """Convert a common ezdxf entity into compact, stable JSON data."""
    entity_type = entity.dxftype()
    data: dict[str, Any] = {
        "type": entity_type,
        "handle": entity.dxf.get("handle", ""),
        "layer": entity.dxf.get("layer", "0"),
    }

    if entity_type == "LINE":
        data.update(start=_point(entity.dxf.start), end=_point(entity.dxf.end))
    elif entity_type in ("CIRCLE", "ARC"):
        data.update(center=_point(entity.dxf.center), radius=_number(entity.dxf.radius))
        if entity_type == "ARC":
            data.update(
                start_angle=_number(entity.dxf.start_angle),
                end_angle=_number(entity.dxf.end_angle),
            )
    elif entity_type == "LWPOLYLINE":
        data["points"] = [[_number(x), _number(y)] for x, y, *_ in entity.get_points()]
        data["closed"] = bool(entity.closed)
    elif entity_type in ("POLYLINE", "POLYLINE2D", "POLYLINE3D"):
        data["points"] = [_point(vertex.dxf.location) for vertex in entity.vertices]
        data["closed"] = bool(entity.is_closed)
    elif entity_type in ("TEXT", "MTEXT"):
        data["insert"] = _point(entity.dxf.get("insert"))
        data["text"] = entity.dxf.get("text", "")
        height_attribute = "char_height" if entity_type == "MTEXT" else "height"
        data["height"] = _number(entity.dxf.get(height_attribute))
        data["rotation"] = _number(entity.dxf.get("rotation", 0))
    elif entity_type == "INSERT":
        data.update(
            name=entity.dxf.get("name", ""),
            insert=_point(entity.dxf.insert),
            rotation=_number(entity.dxf.get("rotation", 0)),
            xscale=_number(entity.dxf.get("xscale", 1)),
            yscale=_number(entity.dxf.get("yscale", 1)),
        )
    elif entity_type == "DIMENSION":
        try:
            measurement = _number(entity.get_measurement())
        except Exception:
            measurement = None
        data.update(
            measurement=measurement,
            text=entity.dxf.get("text", ""),
            dimtype=int(entity.dxf.get("dimtype", 0)),
            text_midpoint=_point(entity.dxf.get("text_midpoint")),
        )
    elif entity_type == "HATCH":
        data["pattern"] = entity.dxf.get("pattern_name", "")
        try:
            data["area"] = _number(entity.area)
        except Exception:
            pass

    return {key: value for key, value in data.items() if value is not None}


def _entity_points(entity: dict[str, Any]) -> Iterable[list[float | int]]:
    explicit = []
    for key in ("start", "end", "center", "insert", "text_midpoint", "text_position"):
        value = entity.get(key)
        if isinstance(value, list) and len(value) >= 2:
            if key == "center" and isinstance(entity.get("radius"), (int, float)):
                radius = float(entity["radius"])
                explicit.append([float(value[0]) - radius, float(value[1]) - radius])
                explicit.append([float(value[0]) + radius, float(value[1]) + radius])
            else:
                explicit.append(value)
    for point in entity.get("points", []):
        if isinstance(point, list) and len(point) >= 2:
            explicit.append(point)
    if explicit:
        yield from explicit
        return
    bounds = entity.get("bounds")
    if isinstance(bounds, dict):
        for key in ("min", "max"):
            value = bounds.get(key)
            if isinstance(value, list) and len(value) >= 2:
                yield value


def _fingerprint(entity: dict[str, Any]) -> str:
    content = {key: value for key, value in entity.items() if key != "handle"}
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _canonical(value: Any) -> Any:
    if isinstance(value, float):
        rounded = round(value, 6)
        return int(rounded) if rounded.is_integer() else rounded
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    return value


def _geometry_signature(entity: dict[str, Any]) -> dict[str, Any]:
    entity_type = str(entity.get("type", "UNKNOWN"))
    common = {"type": entity_type, "layer": str(entity.get("layer", "0"))}
    fields = {
        "LINE": ("start", "end"),
        "CIRCLE": ("center", "radius"),
        "ARC": ("center", "radius", "start_angle", "end_angle"),
        "LWPOLYLINE": ("points", "closed"),
        "POLYLINE": ("points", "closed"),
        "POLYLINE2D": ("points", "closed"),
        "POLYLINE3D": ("points", "closed"),
        "TEXT": ("insert", "text", "height", "rotation"),
        "MTEXT": ("insert", "text", "height", "rotation"),
        "INSERT": ("name", "insert", "rotation", "xscale", "yscale"),
        "DIMENSION": ("measurement", "text"),
        "HATCH": ("pattern", "area"),
    }.get(entity_type, ())
    common.update({key: entity.get(key) for key in fields if key in entity})
    if entity_type == "DIMENSION" and common.get("text") in (None, "", "<>"):
        common.pop("text", None)
    return _canonical(common)


def geometry_digest(entities: list[dict[str, Any]]) -> str:
    signatures = [_geometry_signature(entity) for entity in entities]
    encoded = json.dumps(
        sorted(signatures, key=lambda item: json.dumps(item, sort_keys=True, default=str)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _distance(first: list, second: list) -> float:
    return math.hypot(float(second[0]) - float(first[0]), float(second[1]) - float(first[1]))


def _orientation(a: list, b: list, c: list) -> float:
    return (float(b[0]) - float(a[0])) * (float(c[1]) - float(a[1])) - (
        float(b[1]) - float(a[1])
    ) * (float(c[0]) - float(a[0]))


def _on_segment(a: list, b: list, point: list, tolerance: float) -> bool:
    return (
        min(float(a[0]), float(b[0])) - tolerance
        <= float(point[0])
        <= max(float(a[0]), float(b[0])) + tolerance
        and min(float(a[1]), float(b[1])) - tolerance
        <= float(point[1])
        <= max(float(a[1]), float(b[1])) + tolerance
    )


def _segments_intersect(a: list, b: list, c: list, d: list, tolerance: float) -> bool:
    o1, o2 = _orientation(a, b, c), _orientation(a, b, d)
    o3, o4 = _orientation(c, d, a), _orientation(c, d, b)
    if ((o1 > tolerance and o2 < -tolerance) or (o1 < -tolerance and o2 > tolerance)) and (
        (o3 > tolerance and o4 < -tolerance) or (o3 < -tolerance and o4 > tolerance)
    ):
        return True
    return any(
        abs(value) <= tolerance and _on_segment(first, second, point, tolerance)
        for value, first, second, point in (
            (o1, a, b, c),
            (o2, a, b, d),
            (o3, c, d, a),
            (o4, c, d, b),
        )
    )


def audit_geometry(
    entities: list[dict[str, Any]], *, tolerance: float = 0.000001, short_segment: float = 0.001
) -> dict[str, Any]:
    """Run deterministic 2D geometry design-rule checks."""
    findings: dict[str, list[dict[str, Any]]] = {
        "ZERO_LENGTH_SEGMENT": [],
        "SHORT_SEGMENT": [],
        "POLYLINE_DUPLICATE_VERTEX": [],
        "POLYLINE_DUPLICATE_CLOSING_VERTEX": [],
        "POLYLINE_SELF_INTERSECTION": [],
        "DUPLICATE_ENTITY": [],
    }
    signature_handles: dict[str, list[str]] = {}
    duplicate_supported = {
        "LINE", "CIRCLE", "ARC", "LWPOLYLINE", "POLYLINE", "POLYLINE2D",
        "POLYLINE3D", "TEXT", "MTEXT", "INSERT", "DIMENSION",
    }

    for entity in entities:
        entity_type = str(entity.get("type", "UNKNOWN"))
        handle = str(entity.get("handle", ""))
        if entity_type in duplicate_supported:
            signature = json.dumps(_geometry_signature(entity), sort_keys=True, default=str)
            signature_handles.setdefault(signature, []).append(handle)

        if entity_type == "LINE":
            points = [entity.get("start"), entity.get("end")]
            closed = False
        elif entity_type in ("LWPOLYLINE", "POLYLINE", "POLYLINE2D", "POLYLINE3D"):
            points = [point for point in entity.get("points", []) if point and len(point) >= 2]
            closed = bool(entity.get("closed"))
        else:
            continue
        if len(points) < 2 or any(point is None for point in points):
            continue

        if closed and _distance(points[0], points[-1]) <= tolerance:
            findings["POLYLINE_DUPLICATE_CLOSING_VERTEX"].append(
                {"entity": handle, "vertex": len(points) - 1}
            )
        segments = [(points[index], points[index + 1], index) for index in range(len(points) - 1)]
        if closed:
            segments.append((points[-1], points[0], len(points) - 1))
        for start, end, index in segments:
            length = _distance(start, end)
            if length <= tolerance:
                findings["ZERO_LENGTH_SEGMENT"].append(
                    {"entity": handle, "segment": index, "length": length}
                )
                if entity_type != "LINE":
                    findings["POLYLINE_DUPLICATE_VERTEX"].append(
                        {"entity": handle, "segment": index}
                    )
            elif length < short_segment:
                findings["SHORT_SEGMENT"].append(
                    {"entity": handle, "segment": index, "length": length}
                )

        if entity_type == "LINE":
            continue
        for first_index, (a, b, segment_a) in enumerate(segments):
            if _distance(a, b) <= tolerance:
                continue
            for second_index in range(first_index + 1, len(segments)):
                c, d, segment_b = segments[second_index]
                if _distance(c, d) <= tolerance:
                    continue
                adjacent = second_index == first_index + 1 or (
                    closed and first_index == 0 and second_index == len(segments) - 1
                )
                if adjacent:
                    continue
                if _segments_intersect(a, b, c, d, tolerance):
                    findings["POLYLINE_SELF_INTERSECTION"].append(
                        {"entity": handle, "segments": [segment_a, segment_b]}
                    )

    for handles in signature_handles.values():
        if len(handles) > 1:
            findings["DUPLICATE_ENTITY"].append({"entities": handles})

    rules = []
    for rule_id, items in findings.items():
        rules.append(
            {
                "rule_id": rule_id,
                "status": "FAIL" if items else "PASS",
                "count": len(items),
                "tolerance": tolerance,
                "findings": items[:100],
                "truncated": len(items) > 100,
            }
        )
    issue_count = sum(rule["count"] for rule in rules)
    return {
        "status": "FAIL" if issue_count else "PASS",
        "issue_count": issue_count,
        "tolerance": tolerance,
        "short_segment_threshold": short_segment,
        "rules": rules,
    }


def build_audit(
    entities: list[dict[str, Any]],
    *,
    limit: int = 50,
    include_entities: bool = True,
    changed_only: bool = False,
    previous_fingerprints: dict[str, str] | None = None,
    revision: int = 1,
    space: str = "model",
) -> tuple[dict[str, Any], dict[str, str]]:
    """Build a compact drawing audit and a fingerprint baseline."""
    safe_limit = max(0, min(int(limit), 500))
    previous = previous_fingerprints or {}
    fingerprints = {
        str(entity.get("handle", index)): _fingerprint(entity)
        for index, entity in enumerate(entities)
    }

    added = [handle for handle in fingerprints if handle not in previous]
    modified = [
        handle
        for handle, fingerprint in fingerprints.items()
        if handle in previous and previous[handle] != fingerprint
    ]
    removed = [handle for handle in previous if handle not in fingerprints]
    changed_handles = set(added + modified)

    selected = entities
    if changed_only and previous_fingerprints is not None:
        selected = [entity for entity in entities if str(entity.get("handle")) in changed_handles]

    type_counts = Counter(str(entity.get("type", "UNKNOWN")) for entity in entities)
    layer_counts = Counter(str(entity.get("layer", "0")) for entity in entities)

    points = list(point for entity in entities for point in _entity_points(entity))
    bounds = None
    if points:
        bounds = {
            "min": [min(float(point[0]) for point in points), min(float(point[1]) for point in points)],
            "max": [max(float(point[0]) for point in points), max(float(point[1]) for point in points)],
        }

    payload: dict[str, Any] = {
        "revision": revision,
        "space": space,
        "entity_count": len(entities),
        "counts_by_type": dict(sorted(type_counts.items())),
        "counts_by_layer": dict(sorted(layer_counts.items())),
        "bounds": bounds,
        "geometry_digest": geometry_digest(entities),
        "geometry_drc": audit_geometry(entities),
        "changes": {
            "baseline": previous_fingerprints is None,
            "added": added,
            "modified": modified,
            "removed": removed,
        },
        "returned_entity_count": min(len(selected), safe_limit) if include_entities else 0,
        "truncated": include_entities and len(selected) > safe_limit,
    }
    if include_entities:
        payload["entities"] = selected[:safe_limit]
    return payload, fingerprints


def audit_dxf_file(path: str, *, limit: int = 50, include_entities: bool = True) -> dict[str, Any]:
    """Read a DXF and return a normalized mathematical audit."""
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"DXF file not found: {source}")
    document = ezdxf.readfile(source)
    entities = [normalize_ezdxf_entity(entity) for entity in document.modelspace()]
    payload, _ = build_audit(entities, limit=limit, include_entities=include_entities)
    units_code = int(document.header.get("$INSUNITS", 0) or 0)
    payload.update(
        path=str(source),
        dxf_version=document.dxfversion,
        units={"code": units_code, "name": INSUNITS_NAMES.get(units_code, "unknown")},
    )
    return payload
