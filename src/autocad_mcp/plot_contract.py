"""Plot paper/scale contract shared by the MCP server and offline tests."""

from __future__ import annotations

import math
from typing import Any


def normalize_plot_scale(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize FIT/fixed plot scale into one unambiguous contract."""
    scale_mode = str(data.get("scale_mode", "fit")).lower()
    declared_scale = data.get("declared_scale")
    requested_scale = data.get("scale")
    if scale_mode not in {"fit", "fixed"}:
        return {
            "ok": False,
            "message": "scale_mode must be fit or fixed",
            "recommended_action": "use_fit_or_fixed_scale_mode",
        }
    if scale_mode == "fit":
        if declared_scale is not None and str(declared_scale).upper() not in {"FIT", "NTS"}:
            return {
                "ok": False,
                "message": "A fit-to-extents PDF cannot use a numeric scale declaration",
                "recommended_action": "use_declared_scale_fit_or_nts",
            }
        if requested_scale is not None and str(requested_scale).upper() not in {"FIT", "NTS"}:
            return {
                "ok": False,
                "message": "A fit-to-extents PDF cannot carry a numeric scale",
                "recommended_action": "remove_scale_or_use_fixed_scale_mode",
            }
        return {
            "ok": True,
            "scale_mode": "fit",
            "effective_scale": "fit",
            "declared_scale": str(declared_scale) if declared_scale is not None else "FIT",
        }
    effective_scale = requested_scale or declared_scale
    if not effective_scale:
        return {
            "ok": False,
            "message": "fixed scale_mode requires scale=paper:drawing",
            "recommended_action": "provide_scale_or_use_fit",
        }
    try:
        paper_units, drawing_units = [
            float(item.strip()) for item in str(effective_scale).split(":", 1)
        ]
    except (TypeError, ValueError) as exc:
        return {
            "ok": False,
            "message": "fixed scale must use paper:drawing form, for example 1:2",
            "recommended_action": "provide_a_positive_paper_to_drawing_ratio",
        }
    if not all(
        math.isfinite(value) and value > 0 for value in (paper_units, drawing_units)
    ):
        return {
            "ok": False,
            "message": "fixed scale values must be finite and positive",
            "recommended_action": "provide_a_positive_paper_to_drawing_ratio",
        }
    canonical_scale = f"{paper_units:g}:{drawing_units:g}"
    if declared_scale is not None:
        try:
            declared_paper, declared_drawing = [
                float(item.strip()) for item in str(declared_scale).split(":", 1)
            ]
            canonical_declared = f"{declared_paper:g}:{declared_drawing:g}"
        except (TypeError, ValueError):
            canonical_declared = str(declared_scale).strip()
    else:
        canonical_declared = canonical_scale
    if canonical_declared != canonical_scale:
        return {
            "ok": False,
            "message": "declared_scale must match the fixed plot scale",
            "recommended_action": "make_declared_scale_and_scale_identical",
        }
    return {
        "ok": True,
        "scale_mode": "fixed",
        "effective_scale": canonical_scale,
        "declared_scale": canonical_declared,
    }
