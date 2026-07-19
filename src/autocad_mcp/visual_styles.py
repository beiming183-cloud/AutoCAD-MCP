"""Validation helpers for safe AutoCAD visual-style changes.

AutoCAD accepts a surprisingly broad set of strings through ``VSCURRENT``.
Keeping the accepted set here makes the MCP operation deterministic and keeps
untrusted input away from the command/COM layer.  The canonical spellings are
the compact names accepted by AutoCAD's system variable; aliases are only a
convenience for callers.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from numbers import Real
from typing import Any


# These are built-in AutoCAD visual styles.  Do not pass arbitrary strings to
# VSCURRENT: a typo otherwise becomes a modal command/error in the UI.
VISUAL_STYLE_ALIASES: dict[str, str] = {
    "2d wireframe": "2DWireframe",
    "2dwireframe": "2DWireframe",
    # AutoCAD calls its 3D line-only built-in style simply ``Wireframe``.
    # Accepting the descriptive alias avoids making callers version-aware.
    "3d wireframe": "Wireframe",
    "3dwireframe": "Wireframe",
    "wireframe": "Wireframe",
    "hidden": "Hidden",
    "conceptual": "Conceptual",
    "realistic": "Realistic",
    "realistic with edges": "RealisticWithEdges",
    "realisticwithedges": "RealisticWithEdges",
    "shaded": "Shaded",
    "shaded with edges": "ShadedWithEdges",
    "shadedwithedges": "ShadedWithEdges",
    "shades of gray": "ShadesOfGray",
    "shadesofgray": "ShadesOfGray",
    "flat with edges": "FlatWithEdges",
    "flatwithedges": "FlatWithEdges",
    "gouraud with edges": "GouraudWithEdges",
    "gouraudwithedges": "GouraudWithEdges",
    "x ray": "XRay",
    "xray": "XRay",
    "sketchy": "Sketchy",
}

SUPPORTED_VISUAL_STYLES: tuple[str, ...] = tuple(
    dict.fromkeys(VISUAL_STYLE_ALIASES.values())
)

# ``VSCURRENT`` is more reliable with the display spelling used by the
# AutoCAD UI.  The MCP contract keeps compact canonical names for stable JSON
# values, then converts only at the COM boundary.
AUTOCAD_VISUAL_STYLE_NAMES: dict[str, str] = {
    "2DWireframe": "2D Wireframe",
    "3DWireframe": "3D Wireframe",
    "RealisticWithEdges": "Realistic with Edges",
    "ShadedWithEdges": "Shaded with Edges",
    "ShadesOfGray": "Shades of Gray",
    "FlatWithEdges": "Flat with Edges",
    "GouraudWithEdges": "Gouraud with Edges",
    "XRay": "X-ray",
}


def autocad_visual_style_name(value: str) -> str:
    """Return the UI spelling accepted by ``Document.SetVariable``."""

    return AUTOCAD_VISUAL_STYLE_NAMES.get(value, value)


def _style_key(value: str) -> str:
    """Normalize user punctuation without changing the canonical value."""

    return " ".join(
        value.strip().lower().replace("_", " ").replace("-", " ").split()
    )


def normalize_visual_style(value: Any) -> str:
    """Return a canonical built-in style or raise ``ValueError``.

    The exception text is intentionally suitable for an MCP parameter error;
    callers should attach ``SUPPORTED_VISUAL_STYLES`` as structured details.
    """

    if not isinstance(value, str) or not value.strip():
        raise ValueError("visual_style must be a non-empty string")
    key = _style_key(value)
    # Compact aliases (e.g. ``shaded-with-edges``) are accepted as well.
    canonical = VISUAL_STYLE_ALIASES.get(key)
    if canonical is None:
        compact = key.replace(" ", "")
        canonical = VISUAL_STYLE_ALIASES.get(compact)
    if canonical is None:
        allowed = ", ".join(SUPPORTED_VISUAL_STYLES)
        raise ValueError(f"Unsupported AutoCAD visual style {value!r}; allowed: {allowed}")
    return canonical


def normalize_color_map(value: Any, *, max_items: int = 500) -> dict[str, list[int]]:
    """Validate an optional ``handle -> [R, G, B]`` map.

    Values are rejected, rather than clamped, so a successful response can
    unambiguously mean that the caller's requested colors were applied.
    """

    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("colors must be an object mapping entity handles to RGB triples")
    if len(value) > max_items:
        raise ValueError(f"colors may contain at most {max_items} handles")

    normalized: dict[str, list[int]] = {}
    for raw_handle, raw_rgb in value.items():
        if not isinstance(raw_handle, str) or not raw_handle.strip():
            raise ValueError("each colors key must be a non-empty entity handle string")
        if not isinstance(raw_rgb, (list, tuple)) or len(raw_rgb) != 3:
            raise ValueError(f"colors[{raw_handle!r}] must contain exactly three channels")
        channels: list[int] = []
        for channel in raw_rgb:
            # bool is a Real, but accepting True as 1 is almost certainly a
            # caller bug and makes postcondition reports misleading.
            if isinstance(channel, bool) or not isinstance(channel, Real):
                raise ValueError(f"colors[{raw_handle!r}] channels must be numeric")
            number = float(channel)
            if not math.isfinite(number) or not number.is_integer():
                raise ValueError(
                    f"colors[{raw_handle!r}] channels must be finite integers in the range 0..255"
                )
            integer = int(number)
            if integer < 0 or integer > 255:
                raise ValueError(
                    f"colors[{raw_handle!r}] channels must be in the range 0..255"
                )
            channels.append(integer)
        normalized[raw_handle.strip()] = channels
    return normalized


def style_readback(
    requested_style: str,
    requested_colors: Mapping[str, list[int]],
    backend_payload: Any,
) -> dict[str, Any]:
    """Build a stable requested/actual/diff envelope from backend output."""

    payload = backend_payload if isinstance(backend_payload, Mapping) else {}
    actual_style = payload.get("visual_style")
    actual_handles = [str(handle) for handle in (payload.get("colored_handles") or [])]
    requested_handles = [str(handle) for handle in requested_colors]

    diff: list[dict[str, Any]] = []
    if not isinstance(actual_style, str) or not actual_style.strip():
        diff.append(
            {
                "field": "visual_style",
                "requested": requested_style,
                "actual": actual_style,
                "reason": "missing_readback",
            }
        )
    else:
        try:
            actual_canonical = normalize_visual_style(actual_style)
        except ValueError:
            actual_canonical = actual_style.strip()
        if actual_canonical != requested_style:
            diff.append(
                {
                    "field": "visual_style",
                    "requested": requested_style,
                    "actual": actual_style,
                    "reason": "value_mismatch",
                }
            )

    # Handle ordering is not semantically meaningful, but duplicates are: a
    # duplicate readback still fails because the sorted lists retain counts.
    if sorted(actual_handles) != sorted(requested_handles):
        diff.append(
            {
                "field": "colored_handles",
                "requested": requested_handles,
                "actual": actual_handles,
                "reason": "handle_set_mismatch",
            }
        )

    if requested_colors:
        actual_colors = payload.get("actual_colors")
        if not isinstance(actual_colors, Mapping):
            diff.append(
                {
                    "field": "actual_colors",
                    "requested": dict(requested_colors),
                    "actual": actual_colors,
                    "reason": "missing_readback",
                }
            )
        else:
            for handle, requested_rgb in requested_colors.items():
                actual_rgb = actual_colors.get(handle)
                if isinstance(actual_rgb, (list, tuple)):
                    comparable_actual = list(actual_rgb)
                else:
                    comparable_actual = actual_rgb
                if comparable_actual != list(requested_rgb):
                    diff.append(
                        {
                            "field": "colors",
                            "handle": handle,
                            "requested": list(requested_rgb),
                            "actual": actual_rgb,
                            "reason": "value_mismatch",
                        }
                    )

    return {
        "requested": {
            "visual_style": requested_style,
            "colors": dict(requested_colors),
            "colored_handles": requested_handles,
        },
        "actual": {
            "visual_style": actual_style,
            "colored_handles": actual_handles,
            "color_count": payload.get("color_count", len(actual_handles)),
            "colors": payload.get("actual_colors"),
            "color_readback_errors": payload.get("color_readback_errors", []),
        },
        "diff": diff,
        "verified": not diff,
    }
