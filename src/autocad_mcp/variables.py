"""Whitelist and validation for safe AutoCAD system-variable updates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class VariableRule:
    value_type: type
    minimum: float
    maximum: float
    allowed: frozenset[int] | None = None


VARIABLE_RULES = {
    "INSUNITS": VariableRule(int, 0, 24),
    "LUNITS": VariableRule(int, 1, 5),
    "LUPREC": VariableRule(int, 0, 8),
    "AUNITS": VariableRule(int, 0, 4),
    "AUPREC": VariableRule(int, 0, 8),
    "DIMTXT": VariableRule(float, 0.01, 1000.0),
    "DIMASZ": VariableRule(float, 0.01, 1000.0),
    "DIMSCALE": VariableRule(float, 0.000001, 1000000.0),
    "LTSCALE": VariableRule(float, 0.000001, 1000000.0),
    "PSLTSCALE": VariableRule(int, 0, 1, frozenset({0, 1})),
    "MSLTSCALE": VariableRule(int, 0, 1, frozenset({0, 1})),
    "MEASUREMENT": VariableRule(int, 0, 1, frozenset({0, 1})),
    "MIRRTEXT": VariableRule(int, 0, 1, frozenset({0, 1})),
}


MECHANICAL_DEFAULTS = {
    "INSUNITS": 4,
    "MEASUREMENT": 1,
    "LUNITS": 2,
    "LUPREC": 2,
    "DIMTXT": 3.5,
    "DIMASZ": 3.0,
    "DIMSCALE": 1.0,
    "LTSCALE": 1.0,
    "PSLTSCALE": 1,
    "MSLTSCALE": 1,
}


def validate_variable_updates(values: dict[str, Any]) -> dict[str, int | float]:
    if not isinstance(values, dict) or not values:
        raise ValueError("At least one system variable is required")
    normalized: dict[str, int | float] = {}
    for supplied_name, supplied_value in values.items():
        name = str(supplied_name).lstrip("$").upper()
        rule = VARIABLE_RULES.get(name)
        if not rule:
            raise ValueError(f"System variable {name} is not allowed")
        if isinstance(supplied_value, bool):
            raise ValueError(f"System variable {name} requires a numeric value")
        try:
            value = rule.value_type(supplied_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"System variable {name} has an invalid value") from exc
        if not rule.minimum <= value <= rule.maximum:
            raise ValueError(
                f"System variable {name} is out of range [{rule.minimum}, {rule.maximum}]"
            )
        if rule.allowed is not None and value not in rule.allowed:
            raise ValueError(f"System variable {name} must be one of {sorted(rule.allowed)}")
        normalized[name] = value
    return normalized


def mechanical_variable_updates(config: dict[str, Any] | None) -> dict[str, int | float]:
    options = dict(config or {})
    updates = dict(MECHANICAL_DEFAULTS)
    units = str(options.get("units", "mm")).lower()
    if units not in ("mm", "millimeter", "millimeters"):
        raise ValueError("Mechanical setup currently supports units=mm only")
    dimension = options.get("dimension") or {}
    updates.update(dimension)
    return validate_variable_updates(updates)
