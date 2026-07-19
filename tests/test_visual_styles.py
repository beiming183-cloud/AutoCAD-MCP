"""Offline contract tests for the safe visual-style operation."""

import pytest

from autocad_mcp.visual_styles import (
    SUPPORTED_VISUAL_STYLES,
    autocad_visual_style_name,
    normalize_color_map,
    normalize_visual_style,
    style_readback,
)


@pytest.mark.parametrize(
    ("requested", "canonical"),
    [
        ("Conceptual", "Conceptual"),
        ("realistic", "Realistic"),
        ("shaded-with-edges", "ShadedWithEdges"),
        ("2D Wireframe", "2DWireframe"),
        ("x-ray", "XRay"),
    ],
)
def test_visual_style_aliases_are_canonicalized(requested, canonical):
    assert normalize_visual_style(requested) == canonical


def test_visual_style_rejects_arbitrary_command_text():
    with pytest.raises(ValueError, match="Unsupported AutoCAD visual style"):
        normalize_visual_style("_.VSCURRENT _Conceptual; (command \"_erase\")")


def test_color_map_is_strict_and_does_not_clamp_values():
    assert normalize_color_map({"A1": [0, 128, 255]}) == {"A1": [0, 128, 255]}
    with pytest.raises(ValueError, match="0..255"):
        normalize_color_map({"A1": [256, 0, 0]})
    with pytest.raises(ValueError, match="exactly three"):
        normalize_color_map({"A1": [1, 2]})
    with pytest.raises(ValueError, match="non-empty"):
        normalize_color_map({"": [1, 2, 3]})


def test_style_readback_reports_verified_requested_and_actual_values():
    result = style_readback(
        "ShadedWithEdges",
        {"A1": [10, 20, 30]},
        {
            # AutoCAD may return the spaced display spelling; it is equivalent.
            "visual_style": "Shaded with Edges",
            "colored_handles": ["A1"],
            "color_count": 1,
            "actual_colors": {"A1": [10, 20, 30]},
        },
    )
    assert result["verified"] is True
    assert result["diff"] == []
    assert result["requested"]["visual_style"] == "ShadedWithEdges"
    assert result["actual"]["visual_style"] == "Shaded with Edges"


def test_style_readback_exposes_postcondition_mismatch():
    result = style_readback(
        "Conceptual",
        {"A1": [10, 20, 30]},
        {
            "visual_style": "Realistic",
            "colored_handles": [],
            "color_count": 0,
            "actual_colors": {},
        },
    )
    assert result["verified"] is False
    fields = {item["field"] for item in result["diff"]}
    assert {"visual_style", "colored_handles", "colors"} <= fields


def test_allowlist_is_nonempty_and_contains_presentation_styles():
    assert "Conceptual" in SUPPORTED_VISUAL_STYLES
    assert "Realistic" in SUPPORTED_VISUAL_STYLES
    assert "ShadedWithEdges" in SUPPORTED_VISUAL_STYLES


def test_com_boundary_uses_autocad_ui_spelling():
    assert autocad_visual_style_name("ShadedWithEdges") == "Shaded with Edges"
    assert autocad_visual_style_name("Conceptual") == "Conceptual"
