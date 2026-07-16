"""Shared drafting profiles and AutoCAD-safe text helpers."""

from __future__ import annotations

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
