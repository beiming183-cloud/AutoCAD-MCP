"""Structured drawing audit helpers shared by both backends."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import ezdxf


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
        data["height"] = _number(entity.dxf.get("height", entity.dxf.get("char_height")))
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

    return {key: value for key, value in data.items() if value is not None}


def _entity_points(entity: dict[str, Any]) -> Iterable[list[float | int]]:
    for key in ("start", "end", "center", "insert", "text_midpoint"):
        value = entity.get(key)
        if isinstance(value, list) and len(value) >= 2:
            if key == "center" and isinstance(entity.get("radius"), (int, float)):
                radius = float(entity["radius"])
                yield [float(value[0]) - radius, float(value[1]) - radius]
                yield [float(value[0]) + radius, float(value[1]) + radius]
            else:
                yield value
    for point in entity.get("points", []):
        if isinstance(point, list) and len(point) >= 2:
            yield point
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
    payload.update(path=str(source), dxf_version=document.dxfversion)
    return payload
