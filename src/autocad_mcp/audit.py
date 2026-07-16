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
                start=_point(entity.start_point),
                end=_point(entity.end_point),
            )
    elif entity_type == "LWPOLYLINE":
        vertices = list(entity.get_points(format="xyb"))
        data["points"] = [[_number(x), _number(y)] for x, y, _ in vertices]
        data["bulges"] = [_number(bulge) for _, _, bulge in vertices]
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
        if entity_type == "MTEXT":
            data["width"] = _number(entity.dxf.get("width", 0))
            data["attachment_point"] = int(entity.dxf.get("attachment_point", 1))
    elif entity_type == "INSERT":
        data.update(
            name=entity.dxf.get("name", ""),
            insert=_point(entity.dxf.insert),
            rotation=_number(entity.dxf.get("rotation", 0)),
            xscale=_number(entity.dxf.get("xscale", 1)),
            yscale=_number(entity.dxf.get("yscale", 1)),
        )
        data["attributes"] = [
            {"tag": attrib.dxf.get("tag", ""), "text": attrib.dxf.get("text", "")}
            for attrib in entity.attribs
        ]
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
        "ARC": ("center", "radius", "start_angle", "end_angle", "start", "end"),
        "LWPOLYLINE": ("points", "bulges", "closed"),
        "POLYLINE": ("points", "closed"),
        "POLYLINE2D": ("points", "closed"),
        "POLYLINE3D": ("points", "closed"),
        "TEXT": ("insert", "text", "height", "rotation"),
        "MTEXT": ("insert", "text", "height", "width", "rotation", "attachment_point"),
        "INSERT": ("name", "insert", "rotation", "xscale", "yscale", "attributes"),
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


def _point_segment_distance(point: list, start: list, end: list) -> float:
    dx = float(end[0]) - float(start[0])
    dy = float(end[1]) - float(start[1])
    length_squared = dx * dx + dy * dy
    if length_squared == 0:
        return _distance(point, start)
    ratio = (
        (float(point[0]) - float(start[0])) * dx
        + (float(point[1]) - float(start[1])) * dy
    ) / length_squared
    ratio = max(0.0, min(1.0, ratio))
    projection = [float(start[0]) + ratio * dx, float(start[1]) + ratio * dy]
    return _distance(point, projection)


def _line_intersection(a: list, b: list, c: list, d: list, tolerance: float) -> list[float] | None:
    denominator = (float(a[0]) - float(b[0])) * (float(c[1]) - float(d[1])) - (
        float(a[1]) - float(b[1])
    ) * (float(c[0]) - float(d[0]))
    if abs(denominator) <= tolerance:
        return None
    determinant_ab = float(a[0]) * float(b[1]) - float(a[1]) * float(b[0])
    determinant_cd = float(c[0]) * float(d[1]) - float(c[1]) * float(d[0])
    return [
        (
            determinant_ab * (float(c[0]) - float(d[0]))
            - (float(a[0]) - float(b[0])) * determinant_cd
        )
        / denominator,
        (
            determinant_ab * (float(c[1]) - float(d[1]))
            - (float(a[1]) - float(b[1])) * determinant_cd
        )
        / denominator,
    ]


def _entity_segments(entity: dict[str, Any]) -> list[dict[str, Any]]:
    entity_type = str(entity.get("type", "UNKNOWN"))
    handle = str(entity.get("handle", ""))
    layer = str(entity.get("layer", "0"))
    semantics = dict(entity.get("semantics") or {})
    if entity_type == "LINE":
        points = [entity.get("start"), entity.get("end")]
        closed = False
        bulges = [0]
    elif entity_type in ("LWPOLYLINE", "POLYLINE", "POLYLINE2D", "POLYLINE3D"):
        points = [point for point in entity.get("points", []) if point and len(point) >= 2]
        closed = bool(entity.get("closed"))
        bulges = list(entity.get("bulges", []))
    else:
        return []
    segments = []
    count = len(points) if closed else max(0, len(points) - 1)
    for index in range(count):
        # A bulged LWPOLYLINE segment is curved and must not be audited as a chord crossing.
        if index < len(bulges) and abs(float(bulges[index] or 0)) > 0.000000001:
            continue
        segments.append(
            {
                "entity": handle,
                "segment": index,
                "layer": layer,
                "component_id": semantics.get("component_id"),
                "line_class": semantics.get("line_class"),
                "start": points[index],
                "end": points[(index + 1) % len(points)],
            }
        )
    return segments


def _entity_endpoints(entity: dict[str, Any]) -> list[dict[str, Any]]:
    entity_type = str(entity.get("type", "UNKNOWN"))
    handle = str(entity.get("handle", ""))
    layer = str(entity.get("layer", "0"))
    semantics = dict(entity.get("semantics") or {})
    if entity_type in ("LINE", "ARC"):
        pairs = (("start", entity.get("start")), ("end", entity.get("end")))
    elif entity_type in ("LWPOLYLINE", "POLYLINE", "POLYLINE2D", "POLYLINE3D"):
        points = [point for point in entity.get("points", []) if point and len(point) >= 2]
        if bool(entity.get("closed")) or len(points) < 2:
            return []
        pairs = (("start", points[0]), ("end", points[-1]))
    else:
        return []
    return [
        {
            "id": f"{handle}:{name}",
            "entity": handle,
            "endpoint": name,
            "layer": layer,
            "point": point,
            "component_id": semantics.get("component_id"),
            "line_class": semantics.get("line_class"),
            "intentional_open_end": semantics.get("intentional_open_end"),
        }
        for name, point in pairs
        if isinstance(point, list) and len(point) >= 2
    ]


def _entity_reference_point(entity: dict[str, Any]) -> list[float] | None:
    for key in ("center", "insert", "text_midpoint", "text_position"):
        point = entity.get(key)
        if isinstance(point, list) and len(point) >= 2:
            return [float(point[0]), float(point[1])]
    if entity.get("type") == "LINE":
        start, end = entity.get("start"), entity.get("end")
        if start and end:
            return [(float(start[0]) + float(end[0])) / 2, (float(start[1]) + float(end[1])) / 2]
    points = [point for point in entity.get("points", []) if point and len(point) >= 2]
    if points:
        return [
            (min(float(point[0]) for point in points) + max(float(point[0]) for point in points)) / 2,
            (min(float(point[1]) for point in points) + max(float(point[1]) for point in points)) / 2,
        ]
    bounds = entity.get("bounds")
    if isinstance(bounds, dict) and bounds.get("min") and bounds.get("max"):
        return [
            (float(bounds["min"][0]) + float(bounds["max"][0])) / 2,
            (float(bounds["min"][1]) + float(bounds["max"][1])) / 2,
        ]
    return None


def _policy_status(findings: list[dict[str, Any]], policy: str) -> str:
    if not findings:
        return "PASS"
    return "FAIL" if str(policy).lower() == "fail" else "WARNING"


def audit_geometry(
    entities: list[dict[str, Any]],
    *,
    tolerance: float = 0.000001,
    short_segment: float = 0.001,
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run deterministic 2D geometry and explicit topology design-rule checks."""
    options = dict(rules or {})
    connection_tolerance = float(options.get("connection_tolerance", 0.05))
    near_miss_tolerance = float(options.get("near_miss_tolerance", 0.5))
    if connection_tolerance <= 0 or near_miss_tolerance < connection_tolerance:
        raise ValueError("Topology tolerances must satisfy 0 < connection <= near_miss")
    findings: dict[str, list[dict[str, Any]]] = {
        "ZERO_LENGTH_SEGMENT": [],
        "SHORT_SEGMENT": [],
        "POLYLINE_DUPLICATE_VERTEX": [],
        "POLYLINE_DUPLICATE_CLOSING_VERTEX": [],
        "POLYLINE_SELF_INTERSECTION": [],
        "DUPLICATE_ENTITY": [],
        "DANGLING_ENDPOINT": [],
        "NEAR_MISS_ENDPOINT": [],
        "INTERIOR_CROSSING": [],
        "UNASSIGNED_ENTITY": [],
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

    ignored_layers = {
        str(layer).upper()
        for layer in options.get(
            "ignored_topology_layers", ["CENTER", "HIDDEN", "HATCH", "DIM", "TEXT"]
        )
    }
    selected_layers = options.get("topology_layers")
    selected_layers = {str(layer).upper() for layer in selected_layers} if selected_layers else None

    def topology_entity(entity: dict[str, Any]) -> bool:
        layer = str(entity.get("layer", "0")).upper()
        line_class = str((entity.get("semantics") or {}).get("line_class", "outline")).lower()
        ignored_classes = {"center", "hidden", "leader", "dimension", "table", "construction"}
        return (
            layer not in ignored_layers
            and line_class not in ignored_classes
            and (selected_layers is None or layer in selected_layers)
        )

    topology_entities = [entity for entity in entities if topology_entity(entity)]
    endpoints = [endpoint for entity in topology_entities for endpoint in _entity_endpoints(entity)]
    all_segments = [segment for entity in topology_entities for segment in _entity_segments(entity)]
    connections: dict[str, list[dict[str, Any]]] = {endpoint["id"]: [] for endpoint in endpoints}

    for index, endpoint in enumerate(endpoints):
        best_near_miss = None
        for other in endpoints[index + 1 :]:
            if endpoint["entity"] == other["entity"]:
                continue
            distance = _distance(endpoint["point"], other["point"])
            if distance <= connection_tolerance:
                first = {"kind": "endpoint", "target": other["id"], "distance": distance}
                second = {"kind": "endpoint", "target": endpoint["id"], "distance": distance}
                connections[endpoint["id"]].append(first)
                connections[other["id"]].append(second)
            elif distance <= near_miss_tolerance and (
                best_near_miss is None or distance < best_near_miss["distance"]
            ):
                best_near_miss = {
                    "entity": endpoint["entity"],
                    "endpoint": endpoint["endpoint"],
                    "point": endpoint["point"],
                    "near_entity": other["entity"],
                    "near_endpoint": other["endpoint"],
                    "distance": distance,
                }
        for segment in all_segments:
            if endpoint["entity"] == segment["entity"]:
                continue
            distance = _point_segment_distance(endpoint["point"], segment["start"], segment["end"])
            if distance <= connection_tolerance:
                connections[endpoint["id"]].append(
                    {
                        "kind": "segment",
                        "target": f"{segment['entity']}:{segment['segment']}",
                        "distance": distance,
                    }
                )
            elif distance <= near_miss_tolerance and (
                best_near_miss is None or distance < best_near_miss["distance"]
            ):
                best_near_miss = {
                    "entity": endpoint["entity"],
                    "endpoint": endpoint["endpoint"],
                    "point": endpoint["point"],
                    "near_entity": segment["entity"],
                    "near_segment": segment["segment"],
                    "distance": distance,
                }
        if best_near_miss and not connections[endpoint["id"]]:
            findings["NEAR_MISS_ENDPOINT"].append(best_near_miss)

    for endpoint in endpoints:
        if not connections[endpoint["id"]]:
            intentional = endpoint.get("intentional_open_end")
            if intentional in {endpoint["endpoint"], "both"}:
                continue
            findings["DANGLING_ENDPOINT"].append(
                {
                    "entity": endpoint["entity"],
                    "endpoint": endpoint["endpoint"],
                    "point": endpoint["point"],
                    "layer": endpoint["layer"],
                    "component_id": endpoint.get("component_id"),
                    "line_class": endpoint.get("line_class"),
                }
            )

    for first_index, first in enumerate(all_segments):
        for second in all_segments[first_index + 1 :]:
            if first["entity"] == second["entity"]:
                continue
            if not _segments_intersect(
                first["start"], first["end"], second["start"], second["end"], tolerance
            ):
                continue
            intersection = _line_intersection(
                first["start"], first["end"], second["start"], second["end"], tolerance
            )
            if intersection is None:
                continue
            first_interior = min(
                _distance(intersection, first["start"]), _distance(intersection, first["end"])
            ) > connection_tolerance
            second_interior = min(
                _distance(intersection, second["start"]), _distance(intersection, second["end"])
            ) > connection_tolerance
            if first_interior and second_interior:
                findings["INTERIOR_CROSSING"].append(
                    {
                        "entities": [first["entity"], second["entity"]],
                        "segments": [first["segment"], second["segment"]],
                        "point": [round(value, 6) for value in intersection],
                        "components": [first.get("component_id"), second.get("component_id")],
                        "line_classes": [first.get("line_class"), second.get("line_class")],
                    }
                )

    if options.get("require_component_id", False):
        findings["UNASSIGNED_ENTITY"] = [
            {"entity": str(entity.get("handle", "")), "layer": entity.get("layer", "0")}
            for entity in topology_entities
            if not (entity.get("semantics") or {}).get("component_id")
        ]

    rule_results = []
    policy_by_rule = {
        "DANGLING_ENDPOINT": options.get("dangling_endpoint_policy", "fail"),
        "NEAR_MISS_ENDPOINT": options.get("near_miss_policy", "warning"),
        "INTERIOR_CROSSING": options.get("intersection_policy", "fail"),
    }
    for rule_id, items in findings.items():
        status = (
            _policy_status(items, policy_by_rule[rule_id])
            if rule_id in policy_by_rule
            else ("FAIL" if items else "PASS")
        )
        rule_results.append(
            {
                "rule_id": rule_id,
                "status": status,
                "count": len(items),
                "tolerance": tolerance,
                "findings": items[:100],
                "truncated": len(items) > 100,
            }
        )

    entities_by_handle = {str(entity.get("handle", "")): entity for entity in entities}
    for group in options.get("equal_radius_groups", []):
        handles = [str(handle) for handle in group.get("handles", [])]
        radii = [
            float(entities_by_handle[handle]["radius"])
            for handle in handles
            if handle in entities_by_handle
            and isinstance(entities_by_handle[handle].get("radius"), (int, float))
        ]
        threshold = float(group.get("tolerance", tolerance))
        missing = [handle for handle in handles if handle not in entities_by_handle]
        spread = max(radii) - min(radii) if radii else None
        if missing or len(radii) != len(handles) or len(radii) < 2:
            status = "NOT_EVALUATED"
        else:
            status = "PASS" if spread is not None and spread <= threshold else "FAIL"
        rule_results.append(
            {
                "rule_id": "EQUAL_RADIUS_GROUP",
                "name": group.get("name", "unnamed"),
                "status": status,
                "count": 0 if status == "PASS" else 1,
                "handles": handles,
                "radii": radii,
                "spread": spread,
                "tolerance": threshold,
                "missing": missing,
                "findings": [] if status == "PASS" else [{"handles": handles, "spread": spread}],
            }
        )

    for check in options.get("projection_checks", []):
        axis = str(check.get("axis", "x")).lower()
        axis_index = 0 if axis == "x" else 1
        source_handles = [str(handle) for handle in check.get("source_handles", [])]
        target_handles = [str(handle) for handle in check.get("target_handles", [])]
        source_points = [
            _entity_reference_point(entities_by_handle[handle])
            for handle in source_handles
            if handle in entities_by_handle
        ]
        target_points = [
            _entity_reference_point(entities_by_handle[handle])
            for handle in target_handles
            if handle in entities_by_handle
        ]
        source_values = sorted(point[axis_index] for point in source_points if point is not None)
        target_values = sorted(point[axis_index] for point in target_points if point is not None)
        offset = float(check.get("offset", 0.0))
        threshold = float(check.get("tolerance", connection_tolerance))
        missing = [
            handle
            for handle in source_handles + target_handles
            if handle not in entities_by_handle
        ]
        differences = [
            abs((target - source) - offset)
            for source, target in zip(source_values, target_values)
        ]
        if missing or not source_values or len(source_values) != len(target_values):
            status = "NOT_EVALUATED"
        else:
            status = "PASS" if max(differences, default=0.0) <= threshold else "FAIL"
        rule_results.append(
            {
                "rule_id": "PROJECTION_ALIGNMENT",
                "name": check.get("name", "unnamed"),
                "status": status,
                "count": 0 if status == "PASS" else 1,
                "axis": axis,
                "offset": offset,
                "differences": differences,
                "tolerance": threshold,
                "missing": missing,
                "findings": [] if status == "PASS" else [{"differences": differences}],
            }
        )

    for pair in options.get("tangent_pairs", []):
        line = entities_by_handle.get(str(pair.get("line")))
        curve = entities_by_handle.get(str(pair.get("curve")))
        threshold = float(pair.get("angle_tolerance_degrees", 0.1))
        position_threshold = float(pair.get("position_tolerance", connection_tolerance))
        status = "NOT_EVALUATED"
        finding = {"line": pair.get("line"), "curve": pair.get("curve")}
        if line and curve and line.get("type") == "LINE" and curve.get("type") in ("CIRCLE", "ARC"):
            start, end = line.get("start"), line.get("end")
            center, radius = curve.get("center"), curve.get("radius")
            if start and end and center and isinstance(radius, (int, float)):
                contact = min((start, end), key=lambda point: abs(_distance(point, center) - float(radius)))
                other = end if contact is start else start
                line_length = _distance(contact, other)
                radial_length = _distance(center, contact)
                position_error = abs(radial_length - float(radius))
                if line_length > tolerance and radial_length > tolerance:
                    dot = abs(
                        ((float(other[0]) - float(contact[0])) / line_length)
                        * ((float(contact[0]) - float(center[0])) / radial_length)
                        + ((float(other[1]) - float(contact[1])) / line_length)
                        * ((float(contact[1]) - float(center[1])) / radial_length)
                    )
                    angle_error = math.degrees(math.asin(min(1.0, dot)))
                    status = (
                        "PASS"
                        if position_error <= position_threshold and angle_error <= threshold
                        else "FAIL"
                    )
                    finding.update(
                        contact=contact,
                        position_error=position_error,
                        angle_error_degrees=angle_error,
                    )
        rule_results.append(
            {
                "rule_id": "TANGENCY",
                "name": pair.get("name", "unnamed"),
                "status": status,
                "count": 0 if status == "PASS" else 1,
                "position_tolerance": position_threshold,
                "angle_tolerance_degrees": threshold,
                "findings": [] if status == "PASS" else [finding],
            }
        )

    issue_count = sum(rule["count"] for rule in rule_results if rule["status"] in ("FAIL", "WARNING"))
    failure_count = sum(rule["count"] for rule in rule_results if rule["status"] == "FAIL")
    warning_count = sum(rule["count"] for rule in rule_results if rule["status"] == "WARNING")
    overall_status = "FAIL" if failure_count else ("WARNING" if warning_count else "PASS")
    return {
        "status": overall_status,
        "issue_count": issue_count,
        "failure_count": failure_count,
        "warning_count": warning_count,
        "tolerance": tolerance,
        "short_segment_threshold": short_segment,
        "connection_tolerance": connection_tolerance,
        "near_miss_tolerance": near_miss_tolerance,
        "topology_graph": {
            "node_count": len(endpoints),
            "connected_node_count": sum(bool(connections[endpoint["id"]]) for endpoint in endpoints),
            "dangling_node_count": sum(not connections[endpoint["id"]] for endpoint in endpoints),
            "nodes": [
                {**endpoint, "degree": len(connections[endpoint["id"]]), "connections": connections[endpoint["id"]]}
                for endpoint in endpoints[:200]
            ],
            "truncated": len(endpoints) > 200,
        },
        "rules": rule_results,
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
    geometry_rules: dict[str, Any] | None = None,
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
        "geometry_drc": audit_geometry(entities, rules=geometry_rules),
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
