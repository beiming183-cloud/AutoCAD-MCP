from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from autocad_mcp.audit import audit_geometry
from autocad_mcp.product_design import (
    clearance_sweep,
    image_content_metrics,
    image_difference,
    interference_sample,
    normalize_feature,
    query_edges_by_semantic_role,
    register_feature,
    review_summary,
    set_motion,
    set_review,
)


class ProductBackend:
    pass


def rounded_feature(feature_id="body", component_id="base", center=(0, 0, 0)):
    feature = normalize_feature(
        "rounded_box",
        {
            "feature_id": feature_id,
            "component_id": component_id,
            "center": center,
            "dimensions": [100, 60, 20],
            "radius": 5,
            "source_authority": "concept",
        },
    )
    feature.update(
        bounds={
            "min": [center[0] - 50, center[1] - 30, center[2] - 10],
            "max": [center[0] + 50, center[1] + 30, center[2] + 10],
        },
        semantic_edges=[
            {
                "semantic_edge_id": f"{feature_id}:x:-1:-1",
                "role": "rounded_edge_x",
                "fillet_radius": 5,
            }
        ],
    )
    return feature


def test_rounded_box_contract_and_semantic_edge_query():
    backend = ProductBackend()
    feature = rounded_feature()
    register_feature(backend, "doc", feature)

    result = query_edges_by_semantic_role(
        backend, "doc", "body", "rounded_edge_x"
    )

    assert result["stable_across_rebuild"] is True
    assert result["native_brep_edge_indices_exposed"] is False
    assert result["edges"][0]["fillet_radius"] == 5


def test_usb_cutout_rejects_concept_dimensions():
    with pytest.raises(ValueError, match="supplier-controlled or measured"):
        normalize_feature(
            "port_cutout_usb_c",
            {
                "feature_id": "usb-c",
                "component_id": "io-module",
                "center": [0, 0, 0],
                "dimensions": [9, 4, 3],
                "radius": 1,
                "module_status": "TBD",
                "authority": "concept",
                "source_authority": "concept",
                "target_id": "AB",
            },
        )


def test_usb_cutout_accepts_supplier_authority_only_when_dimensioning_enabled():
    result = normalize_feature(
        "port_cutout_usb_a",
        {
            "feature_id": "usb-a",
            "component_id": "io-module",
            "center": [0, 0, 0],
            "dimensions": [14, 7, 5],
            "radius": 0.5,
            "module_status": "supplier_controlled",
            "authority": "supplier_drawing",
            "source_authority": "supplier_drawing",
            "do_not_dimension_apertures": False,
            "target_id": "AB",
        },
    )

    assert result["production_dimension_authority"] is True
    assert result["is_manufacturing_aperture"] is True


def test_product_reviews_never_inherit_geometry_pass():
    backend = ProductBackend()
    set_review(
        backend,
        "doc",
        "appearance_review",
        {"status": "PASS", "evidence": [{"view": "iso", "revision": 3}]},
    )
    summary = review_summary(backend, "doc")

    assert summary["overall"] == "NOT_EVALUATED"
    assert summary["geometry_or_step_validity_is_not_product_approval"] is True
    assert summary["reviews"]["ergonomics_review"]["status"] == "NOT_EVALUATED"


def test_static_and_motion_aabb_screening_are_explicitly_not_exact():
    backend = ProductBackend()
    register_feature(backend, "doc", rounded_feature("moving", "rotor", (0, 0, 0)))
    register_feature(backend, "doc", rounded_feature("fixed", "base", (120, 0, 0)))
    set_motion(
        backend,
        "doc",
        {
            "component_id": "rotor",
            "axis_point": [0, 0, 0],
            "axis_direction": [0, 0, 1],
            "motion_limit": [0, 90],
            "rotation_angle": 0,
            "clearance": 1,
        },
    )

    static = interference_sample(backend, "doc")
    sweep = clearance_sweep(backend, "doc", "rotor", sample_count=5)

    assert static["exact_brep_interference"] is False
    assert sweep["method"] == "sampled_rotated_aabb"
    assert sweep["sample_count"] == 5
    assert sweep["exact_brep_interference"] is False


def test_semantic_drc_ignores_motion_overlay_and_honors_permitted_crossing():
    result = audit_geometry(
        [
            {
                "type": "LINE",
                "handle": "A",
                "layer": "OUTLINE",
                "start": [0, 0],
                "end": [10, 10],
                "semantics": {
                    "component_id": "body",
                    "design_role": "geometry",
                    "line_class": "outline",
                    "intentional_open_end": "both",
                    "permitted_crossing": True,
                },
            },
            {
                "type": "LINE",
                "handle": "B",
                "layer": "OUTLINE",
                "start": [0, 10],
                "end": [10, 0],
                "semantics": {
                    "component_id": "body",
                    "design_role": "geometry",
                    "line_class": "outline",
                    "intentional_open_end": "both",
                },
            },
            {
                "type": "LINE",
                "handle": "M",
                "layer": "OUTLINE",
                "start": [5, -10],
                "end": [5, 20],
                "semantics": {
                    "component_id": "rotor",
                    "design_role": "motion_overlay",
                    "line_class": "outline",
                },
            },
        ],
        rules={"required_semantic_fields": ["component_id", "design_role"]},
    )
    rules = {rule["rule_id"]: rule for rule in result["rules"]}

    assert rules["INTERIOR_CROSSING"]["status"] == "PASS"
    assert rules["MISSING_SEMANTIC_FIELD"]["status"] == "PASS"


def test_image_metrics_and_difference(tmp_path: Path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    image = Image.new("RGB", (200, 120), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 30, 160, 90), fill="black")
    image.save(first)
    draw.rectangle((80, 45, 120, 75), fill="red")
    image.save(second)

    metrics = image_content_metrics(first)
    difference = image_difference(first, second)

    assert metrics["framing_status"] == "PASS"
    assert metrics["clipped"] is False
    assert difference["different"] is True
    assert difference["pixel_difference_ratio"] > 0


def test_native_plot_framing_normalization_centers_and_scales(tmp_path: Path):
    from autocad_mcp.backends.file_ipc import FileIPCBackend

    path = tmp_path / "off-center.png"
    image = Image.new("RGB", (400, 240), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 5, 90, 45), outline="black", width=2)
    image.save(path)

    result = FileIPCBackend._normalize_png_content_framing(path, 0.8)
    metrics = image_content_metrics(path)

    assert result["desktop_capture_used"] is False
    assert result["scale"] > 1
    assert metrics["framing_status"] == "PASS"
    margins = metrics["margins_pixels"]
    assert abs(margins["left"] - margins["right"]) <= 2
    assert abs(margins["top"] - margins["bottom"]) <= 2
