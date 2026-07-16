"""Shared drafting profiles and AutoCAD-safe text helpers."""

from __future__ import annotations

import math
from typing import Any


MECHANICAL_LAYERS: tuple[dict[str, Any], ...] = (
    {"name": "OUTLINE", "color": 7, "linetype": "CONTINUOUS", "lineweight": "0.50"},
    {"name": "THIN", "color": 7, "linetype": "CONTINUOUS", "lineweight": "0.20"},
    {"name": "CENTER", "color": 7, "linetype": "CENTER", "lineweight": "0.20"},
    {"name": "HIDDEN", "color": 7, "linetype": "HIDDEN", "lineweight": "0.20"},
    {"name": "HATCH", "color": 7, "linetype": "CONTINUOUS", "lineweight": "0.20"},
    {"name": "DIM", "color": 7, "linetype": "CONTINUOUS", "lineweight": "0.20"},
    {"name": "TEXT", "color": 7, "linetype": "CONTINUOUS", "lineweight": "0.20"},
)


def encode_autocad_text(value: str) -> str:
    """Encode non-ASCII characters with AutoCAD's portable ``\\U+XXXX`` form."""
    encoded: list[str] = []
    for character in value:
        codepoint = ord(character)
        if codepoint < 128:
            encoded.append(character)
        elif codepoint <= 0xFFFF:
            encoded.append(f"\\U+{codepoint:04X}")
        else:
            codepoint -= 0x10000
            high = 0xD800 + (codepoint >> 10)
            low = 0xDC00 + (codepoint & 0x3FF)
            encoded.extend((f"\\U+{high:04X}", f"\\U+{low:04X}"))
    return "".join(encoded)


def lineweight_hundredths(value: str | int | float | None, default: int = -3) -> int:
    """Convert millimetres to AutoCAD/DXF hundredths, preserving enum values."""
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number < 0:
        return int(number)
    return int(round(number * 100))


def tangent_arc_from_start(
    start: list[float], end: list[float], tangent: list[float], tolerance: float = 0.000001
) -> dict[str, Any]:
    """Solve the circle through two points with a prescribed tangent at the start."""
    if len(start) < 2 or len(end) < 2 or len(tangent) < 2:
        raise ValueError("start, end, and tangent require two coordinates")
    sx, sy = float(start[0]), float(start[1])
    ex, ey = float(end[0]), float(end[1])
    tx, ty = float(tangent[0]), float(tangent[1])
    tangent_length = math.hypot(tx, ty)
    chord_x, chord_y = ex - sx, ey - sy
    chord_squared = chord_x * chord_x + chord_y * chord_y
    if tangent_length <= tolerance or chord_squared <= tolerance * tolerance:
        raise ValueError("tangent and chord must have non-zero length")
    tx, ty = tx / tangent_length, ty / tangent_length
    normal_x, normal_y = -ty, tx
    denominator = 2 * (chord_x * normal_x + chord_y * normal_y)
    if abs(denominator) <= tolerance:
        raise ValueError("the requested tangent produces a straight line, not a finite arc")
    signed_radius = chord_squared / denominator
    center = [sx + normal_x * signed_radius, sy + normal_y * signed_radius]
    radius = abs(signed_radius)
    start_angle = math.degrees(math.atan2(sy - center[1], sx - center[0])) % 360
    end_angle = math.degrees(math.atan2(ey - center[1], ex - center[0])) % 360
    ccw_tangent = [-(sy - center[1]), sx - center[0]]
    counterclockwise = ccw_tangent[0] * tx + ccw_tangent[1] * ty >= 0
    return {
        "center": center,
        "radius": radius,
        "start_angle": start_angle if counterclockwise else end_angle,
        "end_angle": end_angle if counterclockwise else start_angle,
        "requested_start": [sx, sy],
        "requested_end": [ex, ey],
        "direction": "counterclockwise" if counterclockwise else "clockwise",
    }
