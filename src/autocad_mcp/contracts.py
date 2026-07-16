"""Immutable entity request contracts and deterministic postcondition comparison."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable


SEMANTIC_FIELDS = {"component_id", "line_class", "intentional_open_end"}


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _point(value: Any, name: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise ValueError(f"{name} must contain at least x and y")
    return (_number(value[0], f"{name}[0]"), _number(value[1], f"{name}[1]"))


def _semantics(params: dict[str, Any]) -> tuple[tuple[str, Any], ...]:
    result: list[tuple[str, Any]] = []
    component_id = params.get("component_id")
    if component_id is not None:
        component_id = str(component_id).strip()
        if not component_id:
            raise ValueError("component_id must not be empty")
        result.append(("component_id", component_id))
    line_class = params.get("line_class")
    if line_class is not None:
        normalized = str(line_class).strip().lower()
        allowed = {"outline", "center", "hidden", "leader", "dimension", "table", "construction"}
        if normalized not in allowed:
            raise ValueError(f"line_class must be one of {sorted(allowed)}")
        result.append(("line_class", normalized))
    intentional = params.get("intentional_open_end")
    if intentional not in (None, False, "none"):
        if intentional is True:
            intentional = "both"
        normalized = str(intentional).strip().lower()
        if normalized not in {"start", "end", "both"}:
            raise ValueError("intentional_open_end must be false, start, end, or both")
        result.append(("intentional_open_end", normalized))
    return tuple(result)


@dataclass(frozen=True)
class EntityExpectation:
    """Frozen normalized representation of one requested CAD entity."""

    kind: str
    entity_type: str
    layer: str
    fields: tuple[tuple[str, Any], ...]
    semantics: tuple[tuple[str, Any], ...] = ()

    def requested(self) -> dict[str, Any]:
        result = {"type": self.entity_type, "layer": self.layer}
        result.update({key: _thaw(value) for key, value in self.fields})
        if self.semantics:
            result["semantics"] = dict(self.semantics)
        return result

    def semantic_dict(self) -> dict[str, Any]:
        return dict(self.semantics)


def _thaw(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


ENTITY_FIELDS: dict[str, tuple[set[str], set[str]]] = {
    "line": ({"x1", "y1", "x2", "y2"}, set()),
    "circle": ({"cx", "cy", "radius"}, set()),
    "polyline": ({"points"}, {"closed"}),
    "rectangle": ({"x1", "y1", "x2", "y2"}, set()),
    "arc": ({"cx", "cy", "radius", "start_angle", "end_angle"}, set()),
    "ellipse": ({"cx", "cy", "major_x", "major_y", "ratio"}, set()),
    "mtext": ({"x", "y", "width", "text"}, {"height"}),
    "text": ({"x", "y", "text"}, {"height", "rotation"}),
}


def validate_fields(
    kind: str, params: dict[str, Any], *, strict: bool = True
) -> None:
    if kind not in ENTITY_FIELDS:
        raise ValueError(f"Unsupported entity contract: {kind}")
    required, optional = ENTITY_FIELDS[kind]
    keys = set(params)
    missing = sorted(required - keys)
    if missing:
        raise ValueError(f"{kind} is missing required fields: {missing}")
    if strict:
        extra = sorted(keys - required - optional - SEMANTIC_FIELDS)
        if extra:
            raise ValueError(f"{kind} contains unsupported fields in strict mode: {extra}")


def build_entity_expectation(
    kind: str,
    params: dict[str, Any],
    *,
    layer: str | None = None,
    strict: bool = True,
) -> EntityExpectation:
    """Validate one request and return a frozen normalized expectation."""
    kind = str(kind).lower()
    copied = dict(params)
    validate_fields(kind, copied, strict=strict)
    target_layer = str(layer or "0")
    semantics = _semantics(copied)

    if kind in {"line", "rectangle"}:
        x1, y1 = _number(copied["x1"], "x1"), _number(copied["y1"], "y1")
        x2, y2 = _number(copied["x2"], "x2"), _number(copied["y2"], "y2")
        if x1 == x2 and y1 == y2:
            raise ValueError(f"{kind} endpoints must differ")
        if kind == "line":
            entity_type = "LINE"
            fields = (("start", (x1, y1)), ("end", (x2, y2)))
        else:
            if x1 == x2 or y1 == y2:
                raise ValueError("rectangle width and height must be non-zero")
            entity_type = "LWPOLYLINE"
            fields = (
                ("points", ((x1, y1), (x2, y1), (x2, y2), (x1, y2))),
                ("closed", True),
            )
    elif kind == "circle":
        radius = _number(copied["radius"], "radius")
        if radius <= 0:
            raise ValueError("circle radius must be positive")
        entity_type = "CIRCLE"
        fields = (
            ("center", (_number(copied["cx"], "cx"), _number(copied["cy"], "cy"))),
            ("radius", radius),
        )
    elif kind == "polyline":
        raw_points = copied["points"]
        if not isinstance(raw_points, (list, tuple)) or len(raw_points) < 2:
            raise ValueError("polyline requires at least two points")
        points = tuple(_point(point, f"points[{index}]") for index, point in enumerate(raw_points))
        entity_type = "LWPOLYLINE"
        fields = (("points", points), ("closed", bool(copied.get("closed", False))))
    elif kind == "arc":
        radius = _number(copied["radius"], "radius")
        if radius <= 0:
            raise ValueError("arc radius must be positive")
        entity_type = "ARC"
        fields = (
            ("center", (_number(copied["cx"], "cx"), _number(copied["cy"], "cy"))),
            ("radius", radius),
            ("start_angle", _number(copied["start_angle"], "start_angle") % 360),
            ("end_angle", _number(copied["end_angle"], "end_angle") % 360),
        )
    elif kind == "ellipse":
        cx, cy = _number(copied["cx"], "cx"), _number(copied["cy"], "cy")
        major_x, major_y = _number(copied["major_x"], "major_x"), _number(copied["major_y"], "major_y")
        ratio = _number(copied["ratio"], "ratio")
        if major_x == cx and major_y == cy:
            raise ValueError("ellipse major axis must be non-zero")
        if ratio <= 0 or ratio > 1:
            raise ValueError("ellipse ratio must be in (0, 1]")
        entity_type = "ELLIPSE"
        fields = (("center", (cx, cy)), ("major_axis", (major_x - cx, major_y - cy)), ("ratio", ratio))
    elif kind in {"mtext", "text"}:
        text = copied["text"]
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        height = _number(copied.get("height", 2.5), "height")
        if height <= 0:
            raise ValueError("text height must be positive")
        entity_type = "MTEXT" if kind == "mtext" else "TEXT"
        fields_list: list[tuple[str, Any]] = [
            ("insert", (_number(copied["x"], "x"), _number(copied["y"], "y"))),
            ("text", text),
            ("height", height),
        ]
        if kind == "mtext":
            width = _number(copied["width"], "width")
            if width <= 0:
                raise ValueError("mtext width must be positive")
            fields_list.append(("width", width))
        else:
            fields_list.append(("rotation", _number(copied.get("rotation", 0), "rotation") % 360))
        fields = tuple(fields_list)
    else:  # pragma: no cover - validate_fields already guards this
        raise ValueError(f"Unsupported entity contract: {kind}")

    return EntityExpectation(kind, entity_type, target_layer, fields, semantics)


def compare_entity(
    expectation: EntityExpectation,
    actual: dict[str, Any],
    *,
    tolerance: float = 0.000001,
) -> list[dict[str, Any]]:
    """Return stable field-level differences between requested and actual geometry."""
    expected = expectation.requested()
    differences: list[dict[str, Any]] = []
    _compare_value("type", expected["type"], actual.get("type"), tolerance, differences)
    requested_layer = str(expected["layer"])
    actual_layer = actual.get("layer")
    if actual_layer is None or str(actual_layer).casefold() != requested_layer.casefold():
        differences.append({"path": "layer", "requested": requested_layer, "actual": actual_layer})
    for key, value in expectation.fields:
        _compare_value(key, _thaw(value), actual.get(key), tolerance, differences)
    return differences


def _compare_value(
    path: str,
    requested: Any,
    actual: Any,
    tolerance: float,
    differences: list[dict[str, Any]],
) -> None:
    if isinstance(requested, list):
        if not isinstance(actual, (list, tuple)):
            differences.append({"path": path, "requested": requested, "actual": actual})
            return
        if len(actual) < len(requested):
            differences.append({"path": path, "requested": requested, "actual": list(actual)})
            return
        for index, item in enumerate(requested):
            _compare_value(f"{path}[{index}]", item, actual[index], tolerance, differences)
        return
    if isinstance(requested, bool):
        if bool(actual) is not requested:
            differences.append({"path": path, "requested": requested, "actual": actual})
        return
    if isinstance(requested, (int, float)) and not isinstance(requested, bool):
        try:
            actual_number = float(actual)
        except (TypeError, ValueError):
            differences.append({"path": path, "requested": requested, "actual": actual})
            return
        delta = actual_number - float(requested)
        angular = path.endswith("_angle") or path.endswith("rotation")
        if angular:
            delta = (delta + 180) % 360 - 180
        if abs(delta) > tolerance:
            differences.append(
                {
                    "path": path,
                    "requested": requested,
                    "actual": actual_number,
                    "delta": delta,
                    "tolerance": tolerance,
                }
            )
        return
    if requested != actual:
        differences.append({"path": path, "requested": requested, "actual": actual})


def semantic_fields(params: dict[str, Any]) -> dict[str, Any]:
    return dict(_semantics(dict(params)))

