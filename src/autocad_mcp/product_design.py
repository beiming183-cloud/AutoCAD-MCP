"""Portable industrial-product feature, motion, review, and preview contracts."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops


MODULE_STATUSES = {"TBD", "supplier_controlled", "measured"}
SOURCE_AUTHORITIES = {
    "GB",
    "supplier_drawing",
    "physical_measurement",
    "concept",
}
REVIEW_NAMES = {
    "appearance_review",
    "ergonomics_review",
    "adapter_clearance_review",
    "cable_management_review",
    "stability_review",
    "mains_rotation_safety_review",
}
REVIEW_STATES = {"PASS", "FAIL", "NOT_EVALUATED"}
MOTION_ROLES = {"motion_axis", "motion_envelope", "motion_overlay"}
FEATURE_KINDS = {
    "rounded_box",
    "recessed_panel",
    "port_cutout_usb_a",
    "port_cutout_usb_c",
    "module_reservation",
    "rotary_layer",
    "annular_gap",
    "detent_ring_placeholder",
}
VIEW_NAMES = {
    "front", "right", "top", "bottom", "iso", "rotated_iso", "section", "exploded"
}


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def point3(value: Any, name: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        raise ValueError(f"{name} must contain x, y, and z")
    return [_finite(value[index], f"{name}[{index}]") for index in range(3)]


def positive_dimensions(value: Any, name: str = "dimensions") -> list[float]:
    result = point3(value, name)
    if min(result) <= 0:
        raise ValueError(f"{name} values must be positive")
    return result


def _positive(value: Any, name: str) -> float:
    result = _finite(value, name)
    if result <= 0:
        raise ValueError(f"{name} must be positive")
    return result


def normalize_feature(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    """Validate one high-level product feature without inventing supplier dimensions."""
    kind = str(kind).strip().lower()
    if kind not in FEATURE_KINDS:
        raise ValueError(f"feature kind must be one of {sorted(FEATURE_KINDS)}")
    feature_id = str(data.get("feature_id", "")).strip()
    component_id = str(data.get("component_id", "")).strip()
    if not feature_id or not component_id:
        raise ValueError("feature_id and component_id are required")
    center = point3(data.get("center", [0, 0, 0]), "center")
    normalized: dict[str, Any] = {
        "feature_id": feature_id,
        "kind": kind,
        "component_id": component_id,
        "design_role": str(data.get("design_role", kind)).strip() or kind,
        "source_authority": str(data.get("source_authority", "concept")).strip(),
        "center": center,
        "layer": data.get("layer"),
    }
    if normalized["source_authority"] not in SOURCE_AUTHORITIES:
        raise ValueError(f"source_authority must be one of {sorted(SOURCE_AUTHORITIES)}")

    if kind in {"rounded_box", "recessed_panel", "module_reservation", "port_cutout_usb_a", "port_cutout_usb_c"}:
        dimensions = positive_dimensions(data.get("dimensions"), "dimensions")
        radius = _finite(data.get("radius", 0.0), "radius")
        if radius < 0 or radius * 2 >= min(dimensions):
            raise ValueError("radius must satisfy 0 <= radius < half the smallest dimension")
        normalized.update(dimensions=dimensions, radius=radius)
    if kind == "recessed_panel":
        normalized["target_id"] = str(data.get("target_id", "")).strip()
        normalized["depth"] = _positive(data.get("depth"), "depth")
        if not normalized["target_id"]:
            raise ValueError("recessed_panel requires target_id")
    if kind in {"module_reservation", "port_cutout_usb_a", "port_cutout_usb_c"}:
        contract = normalize_module_contract(data)
        normalized.update(contract)
        normalized["module_type"] = str(data.get("module_type", kind)).strip()
        normalized["is_manufacturing_aperture"] = False
        if kind.startswith("port_cutout"):
            if not contract["production_dimension_authority"]:
                raise ValueError(
                    "USB cutouts require supplier-controlled or measured dimensions and "
                    "do_not_dimension_apertures=false; use module_reservation for concepts"
                )
            normalized["is_manufacturing_aperture"] = True
            normalized["target_id"] = str(data.get("target_id", "")).strip()
            if not normalized["target_id"]:
                raise ValueError("an authoritative port cutout requires target_id")
    if kind in {"rotary_layer", "annular_gap", "detent_ring_placeholder"}:
        outer_radius = _positive(data.get("outer_radius"), "outer_radius")
        inner_radius = _finite(data.get("inner_radius", 0.0), "inner_radius")
        height = _positive(data.get("height"), "height")
        if inner_radius < 0 or inner_radius >= outer_radius:
            raise ValueError("inner_radius must satisfy 0 <= inner_radius < outer_radius")
        normalized.update(
            outer_radius=outer_radius,
            inner_radius=inner_radius,
            height=height,
        )
        if kind == "detent_ring_placeholder":
            normalized["detent_count"] = int(data.get("detent_count", 0))
            if normalized["detent_count"] < 1:
                raise ValueError("detent_count must be at least 1")
            normalized["placeholder_only"] = True
        if kind == "rotary_layer":
            normalized["motion"] = normalize_motion(
                {**data, "component_id": component_id}
            )
    return normalized


def feature_bounds(feature: dict[str, Any]) -> dict[str, list[float]]:
    center = feature["center"]
    if "dimensions" in feature:
        half = [value / 2 for value in feature["dimensions"]]
    else:
        half = [feature["outer_radius"], feature["outer_radius"], feature["height"] / 2]
    return {
        "min": [center[index] - half[index] for index in range(3)],
        "max": [center[index] + half[index] for index in range(3)],
    }


def aabb_overlap(first: dict[str, Any], second: dict[str, Any], clearance: float = 0.0) -> dict[str, Any]:
    axes = []
    for index, axis in enumerate("xyz"):
        penetration = min(first["max"][index], second["max"][index]) - max(
            first["min"][index], second["min"][index]
        )
        axes.append({"axis": axis, "overlap": penetration})
    intersects = all(axis["overlap"] > -clearance for axis in axes)
    return {
        "intersects": intersects,
        "clearance": clearance,
        "axis_overlaps": axes,
        "method": "broad_phase_aabb",
        "exact_brep_interference": False,
    }


def normalize_module_contract(data: dict[str, Any]) -> dict[str, Any]:
    status = str(data.get("module_status", "TBD")).strip()
    authority = str(data.get("authority", "concept")).strip()
    if status not in MODULE_STATUSES:
        raise ValueError(f"module_status must be one of {sorted(MODULE_STATUSES)}")
    if authority not in SOURCE_AUTHORITIES:
        raise ValueError(f"authority must be one of {sorted(SOURCE_AUTHORITIES)}")
    if status == "measured" and authority != "physical_measurement":
        raise ValueError("measured modules require physical_measurement authority")
    if status == "supplier_controlled" and authority != "supplier_drawing":
        raise ValueError("supplier_controlled modules require supplier_drawing authority")
    do_not_dimension = bool(data.get("do_not_dimension_apertures", True))
    return {
        "module_status": status,
        "authority": authority,
        "do_not_dimension_apertures": do_not_dimension,
        "production_dimension_authority": bool(
            not do_not_dimension
            and status in {"supplier_controlled", "measured"}
            and authority in {"supplier_drawing", "physical_measurement"}
        ),
    }


def normalize_motion(data: dict[str, Any]) -> dict[str, Any]:
    component_id = str(data.get("component_id", "")).strip()
    if not component_id:
        raise ValueError("motion component_id is required")
    axis_point = point3(data.get("motion_axis", {}).get("point", data.get("axis_point")), "axis_point")
    direction = point3(
        data.get("motion_axis", {}).get("direction", data.get("axis_direction")),
        "axis_direction",
    )
    magnitude = math.sqrt(sum(value * value for value in direction))
    if magnitude <= 1e-9:
        raise ValueError("axis_direction must be non-zero")
    direction = [value / magnitude for value in direction]
    limits = data.get("motion_limit", data.get("limits", [-180, 180]))
    if not isinstance(limits, (list, tuple)) or len(limits) != 2:
        raise ValueError("motion_limit must contain minimum and maximum angles")
    minimum, maximum = _finite(limits[0], "motion_limit[0]"), _finite(
        limits[1], "motion_limit[1]"
    )
    if minimum > maximum:
        raise ValueError("motion_limit minimum must not exceed maximum")
    angle = _finite(data.get("rotation_angle", 0.0), "rotation_angle")
    if angle < minimum or angle > maximum:
        raise ValueError("rotation_angle is outside motion_limit")
    clearance = _finite(data.get("clearance", 0.0), "clearance")
    if clearance < 0:
        raise ValueError("clearance must not be negative")
    return {
        "component_id": component_id,
        "motion_axis": {"point": axis_point, "direction": direction},
        "rotation_angle": angle,
        "motion_limit": [minimum, maximum],
        "clearance": clearance,
        "intentional_motion_overlay": bool(data.get("intentional_motion_overlay", True)),
        "analysis_method": str(data.get("analysis_method", "broad_phase_aabb")),
    }


def normalize_review(name: str, data: dict[str, Any]) -> dict[str, Any]:
    if name not in REVIEW_NAMES:
        raise ValueError(f"review name must be one of {sorted(REVIEW_NAMES)}")
    status = str(data.get("status", "NOT_EVALUATED")).strip().upper()
    if status not in REVIEW_STATES:
        raise ValueError(f"review status must be one of {sorted(REVIEW_STATES)}")
    evidence = data.get("evidence", [])
    if not isinstance(evidence, list):
        raise ValueError("review evidence must be a list")
    reason = str(data.get("reason", "")).strip()
    if status == "PASS" and not evidence:
        raise ValueError("PASS requires at least one evidence item")
    if status == "NOT_EVALUATED" and not reason:
        raise ValueError("NOT_EVALUATED requires a reason")
    return {
        "name": name,
        "status": status,
        "evidence": evidence,
        "reason": reason or None,
        "reviewer": data.get("reviewer", "automation-candidate"),
        "revision": data.get("revision"),
        "configuration": data.get("configuration"),
    }


def product_state(backend: Any, doc_id: str) -> dict[str, Any]:
    states = getattr(backend, "_product_states", None)
    if states is None:
        states = {}
        setattr(backend, "_product_states", states)
    return states.setdefault(
        str(doc_id),
        {
            "revision": 0,
            "features": {},
            "motions": {},
            "reviews": {},
            "views": {},
        },
    )


def register_feature(backend: Any, doc_id: str, feature: dict[str, Any]) -> dict[str, Any]:
    state = product_state(backend, doc_id)
    feature_id = str(feature.get("feature_id", "")).strip()
    if not feature_id:
        raise ValueError("feature_id is required")
    state["features"][feature_id] = dict(feature)
    state["revision"] += 1
    return {**feature, "product_state_revision": state["revision"]}


def list_features(backend: Any, doc_id: str) -> dict[str, Any]:
    state = product_state(backend, doc_id)
    return {
        "features": list(state["features"].values()),
        "product_state_revision": state["revision"],
    }


def get_feature(backend: Any, doc_id: str, feature_id: str) -> dict[str, Any] | None:
    return product_state(backend, doc_id)["features"].get(str(feature_id))


def query_edges_by_semantic_role(
    backend: Any, doc_id: str, feature_id: str, role: str | None = None
) -> dict[str, Any]:
    feature = get_feature(backend, doc_id, feature_id)
    if not feature:
        raise ValueError(f"Unknown feature_id: {feature_id}")
    edges = list(feature.get("semantic_edges", []))
    if role:
        edges = [edge for edge in edges if edge.get("role") == role]
    return {
        "feature_id": feature_id,
        "role": role,
        "edges": edges,
        "stable_across_rebuild": True,
        "native_brep_edge_indices_exposed": False,
    }


def measure_registered_feature(
    backend: Any, doc_id: str, feature_id: str, measurement: str
) -> dict[str, Any]:
    feature = get_feature(backend, doc_id, feature_id)
    if not feature:
        raise ValueError(f"Unknown feature_id: {feature_id}")
    if measurement == "fillet_radius" and feature.get("kind") in {"rounded_box", "recessed_panel"}:
        return {
            "feature_id": feature_id,
            "measurement": measurement,
            "value": feature["radius"],
            "units": "drawing_units",
            "authority": "analytic_feature_definition",
        }
    if measurement == "chamfer_distance" and feature.get("chamfer_distance") is not None:
        return {
            "feature_id": feature_id,
            "measurement": measurement,
            "value": feature["chamfer_distance"],
            "units": "drawing_units",
            "authority": "analytic_feature_definition",
        }
    raise ValueError(f"{measurement} is not available for feature {feature_id}")


def interference_sample(
    backend: Any,
    doc_id: str,
    component_ids: list[str] | None = None,
    clearance: float = 0.0,
) -> dict[str, Any]:
    state = product_state(backend, doc_id)
    selected = [
        feature for feature in state["features"].values()
        if not component_ids or feature.get("component_id") in component_ids
    ]
    findings = []
    for index, first in enumerate(selected):
        for second in selected[index + 1:]:
            if first.get("component_id") == second.get("component_id"):
                continue
            result = aabb_overlap(first["bounds"], second["bounds"], clearance)
            if result["intersects"]:
                findings.append({
                    "features": [first["feature_id"], second["feature_id"]],
                    "components": [first["component_id"], second["component_id"]],
                    **result,
                })
    return {
        "status": "WARNING" if findings else "PASS",
        "findings": findings,
        "method": "broad_phase_aabb",
        "exact_brep_interference": False,
        "recommended_action": "run_exact_native_interference_check_for_release",
        "product_state_revision": state["revision"],
    }


def _rotate_point(point: list[float], axis_point: list[float], axis: list[float], angle: float) -> list[float]:
    radians = math.radians(angle)
    cosine, sine = math.cos(radians), math.sin(radians)
    vector = [point[index] - axis_point[index] for index in range(3)]
    cross = [
        axis[1] * vector[2] - axis[2] * vector[1],
        axis[2] * vector[0] - axis[0] * vector[2],
        axis[0] * vector[1] - axis[1] * vector[0],
    ]
    dot = sum(axis[index] * vector[index] for index in range(3))
    rotated = [
        vector[index] * cosine
        + cross[index] * sine
        + axis[index] * dot * (1 - cosine)
        for index in range(3)
    ]
    return [rotated[index] + axis_point[index] for index in range(3)]


def _rotated_bounds(bounds: dict[str, list[float]], motion: dict[str, Any], angle: float) -> dict[str, list[float]]:
    corners = [
        [x, y, z]
        for x in (bounds["min"][0], bounds["max"][0])
        for y in (bounds["min"][1], bounds["max"][1])
        for z in (bounds["min"][2], bounds["max"][2])
    ]
    rotated = [
        _rotate_point(
            corner,
            motion["motion_axis"]["point"],
            motion["motion_axis"]["direction"],
            angle,
        )
        for corner in corners
    ]
    return {
        "min": [min(point[index] for point in rotated) for index in range(3)],
        "max": [max(point[index] for point in rotated) for index in range(3)],
    }


def clearance_sweep(
    backend: Any,
    doc_id: str,
    component_id: str,
    *,
    sample_count: int = 13,
    clearance: float | None = None,
) -> dict[str, Any]:
    state = product_state(backend, doc_id)
    motion = state["motions"].get(str(component_id))
    if not motion:
        raise ValueError(f"No motion is registered for component_id: {component_id}")
    sample_count = int(sample_count)
    if sample_count < 2 or sample_count > 361:
        raise ValueError("sample_count must be between 2 and 361")
    minimum, maximum = motion["motion_limit"]
    clearance_value = motion["clearance"] if clearance is None else _finite(clearance, "clearance")
    if clearance_value < 0:
        raise ValueError("clearance must not be negative")
    moving = [
        feature for feature in state["features"].values()
        if feature.get("component_id") == component_id
    ]
    fixed = [
        feature for feature in state["features"].values()
        if feature.get("component_id") != component_id
    ]
    angles = [
        minimum + (maximum - minimum) * index / (sample_count - 1)
        for index in range(sample_count)
    ]
    findings = []
    for angle in angles:
        for moving_feature in moving:
            moving_bounds = _rotated_bounds(moving_feature["bounds"], motion, angle)
            for fixed_feature in fixed:
                overlap = aabb_overlap(moving_bounds, fixed_feature["bounds"], clearance_value)
                if overlap["intersects"]:
                    findings.append(
                        {
                            "angle": round(angle, 6),
                            "features": [moving_feature["feature_id"], fixed_feature["feature_id"]],
                            "components": [component_id, fixed_feature["component_id"]],
                            **overlap,
                        }
                    )
    return {
        "status": "WARNING" if findings else "PASS",
        "component_id": component_id,
        "sample_count": sample_count,
        "sampled_angles": angles,
        "clearance": clearance_value,
        "findings": findings,
        "method": "sampled_rotated_aabb",
        "exact_brep_interference": False,
        "recommended_action": "run_exact_continuous_brep_sweep_for_release",
        "product_state_revision": state["revision"],
    }


def set_motion(backend: Any, doc_id: str, data: dict[str, Any]) -> dict[str, Any]:
    motion = normalize_motion(data)
    state = product_state(backend, doc_id)
    state["motions"][motion["component_id"]] = motion
    state["revision"] += 1
    return {**motion, "product_state_revision": state["revision"]}


def set_review(
    backend: Any, doc_id: str, name: str, data: dict[str, Any]
) -> dict[str, Any]:
    review = normalize_review(name, data)
    state = product_state(backend, doc_id)
    state["reviews"][name] = review
    state["revision"] += 1
    return {**review, "product_state_revision": state["revision"]}


def review_summary(backend: Any, doc_id: str) -> dict[str, Any]:
    state = product_state(backend, doc_id)
    reviews = {
        name: state["reviews"].get(
            name,
            {
                "name": name,
                "status": "NOT_EVALUATED",
                "evidence": [],
                "reason": "No review evidence was recorded",
            },
        )
        for name in sorted(REVIEW_NAMES)
    }
    statuses = [review["status"] for review in reviews.values()]
    overall = "FAIL" if "FAIL" in statuses else (
        "NOT_EVALUATED" if "NOT_EVALUATED" in statuses else "PASS"
    )
    return {
        "overall": overall,
        "reviews": reviews,
        "product_state_revision": state["revision"],
        "geometry_or_step_validity_is_not_product_approval": True,
    }


def image_content_metrics(path: str | Path, *, background_threshold: int = 248) -> dict[str, Any]:
    output = Path(path).expanduser().resolve()
    with Image.open(output) as source:
        image = source.convert("RGB")
        width, height = image.size
        pixels = image.load()
        xs: list[int] = []
        ys: list[int] = []
        non_background = 0
        for y in range(height):
            for x in range(width):
                red, green, blue = pixels[x, y]
                if min(red, green, blue) < background_threshold:
                    non_background += 1
                    xs.append(x)
                    ys.append(y)
    total = max(1, width * height)
    bbox = [min(xs), min(ys), max(xs) + 1, max(ys) + 1] if xs else None
    margins = None
    clipped = False
    if bbox:
        margins = {
            "left": bbox[0],
            "top": bbox[1],
            "right": width - bbox[2],
            "bottom": height - bbox[3],
        }
        clipped = min(margins.values()) <= 1
    ratio = non_background / total
    if bbox:
        span_ratios = {
            "width": (bbox[2] - bbox[0]) / width,
            "height": (bbox[3] - bbox[1]) / height,
        }
        bbox_ratio = span_ratios["width"] * span_ratios["height"]
    else:
        span_ratios = None
        bbox_ratio = 0.0
    framing_status = (
        "EMPTY" if non_background == 0 else
        "CLIPPED" if clipped else
        "TOO_SMALL" if max(span_ratios.values()) < 0.55 else
        "TOO_DENSE" if min(span_ratios.values()) > 0.95 else
        "PASS"
    )
    return {
        "width": width,
        "height": height,
        "non_background_pixels": non_background,
        "non_background_ratio": round(ratio, 6),
        "content_bbox_pixels": bbox,
        "content_bbox_ratio": round(bbox_ratio, 6),
        "content_span_ratios": (
            {key: round(value, 6) for key, value in span_ratios.items()}
            if span_ratios else None
        ),
        "margins_pixels": margins,
        "clipped": clipped,
        "framing_status": framing_status,
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }


def image_difference(first: str | Path, second: str | Path) -> dict[str, Any]:
    with Image.open(first) as first_image, Image.open(second) as second_image:
        left = first_image.convert("RGB")
        right = second_image.convert("RGB")
        if left.size != right.size:
            right = right.resize(left.size)
        difference = ImageChops.difference(left, right)
        histogram = difference.histogram()
        changed_weight = sum(value * count for value, count in enumerate(histogram))
        maximum = max(1, left.width * left.height * 3 * 255)
        bbox = difference.getbbox()
    return {
        "pixel_difference_ratio": round(changed_weight / maximum, 6),
        "different": bbox is not None,
        "difference_bbox_pixels": list(bbox) if bbox else None,
    }
