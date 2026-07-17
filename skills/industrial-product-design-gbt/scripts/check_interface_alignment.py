#!/usr/bin/env python3
"""Compare structured 2D and 3D product-interface definitions."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path


def vector(value, field, interface_id):
    if not isinstance(value, list) or len(value) not in {2, 3}:
        raise ValueError(f"{interface_id}.{field} must be a 2D or 3D numeric list")
    result = [float(item) for item in value]
    while len(result) < 3:
        result.append(0.0)
    return result


def indexed(items, label):
    if not isinstance(items, list):
        raise ValueError(f"{label} must be a list")
    result = {}
    for item in items:
        if not isinstance(item, dict) or not item.get("id"):
            raise ValueError(f"every {label} item requires id")
        if item["id"] in result:
            raise ValueError(f"duplicate {label} id: {item['id']}")
        result[item["id"]] = item
    return result


def angle_delta(a, b):
    return abs((float(a) - float(b) + 180.0) % 360.0 - 180.0)


def validate(data: dict) -> dict:
    tolerance = data.get("tolerance", {})
    pos_tol = float(tolerance.get("position", 0.0))
    size_tol = float(tolerance.get("size", 0.0))
    angle_tol = float(tolerance.get("angle_deg", 0.0))
    if min(pos_tol, size_tol, angle_tol) < 0:
        raise ValueError("tolerances must be non-negative")

    two_d = indexed(data.get("interfaces_2d"), "interfaces_2d")
    three_d = indexed(data.get("interfaces_3d"), "interfaces_3d")
    findings = []
    comparisons = []

    for interface_id in sorted(set(two_d) | set(three_d)):
        if interface_id not in two_d:
            findings.append({"code": "MISSING_2D_INTERFACE", "id": interface_id})
            continue
        if interface_id not in three_d:
            findings.append({"code": "MISSING_3D_INTERFACE", "id": interface_id})
            continue
        a, b = two_d[interface_id], three_d[interface_id]
        center_a, center_b = vector(a.get("center"), "center", interface_id), vector(
            b.get("center"), "center", interface_id
        )
        size_a, size_b = vector(a.get("size"), "size", interface_id), vector(
            b.get("size"), "size", interface_id
        )
        center_delta = math.dist(center_a, center_b)
        size_delta = max(abs(x - y) for x, y in zip(size_a, size_b))
        orientation_delta = angle_delta(a.get("orientation_deg", 0), b.get("orientation_deg", 0))
        type_match = a.get("type") == b.get("type") and a.get("type") is not None
        source_match = (
            a.get("parameter_source") == b.get("parameter_source")
            and a.get("parameter_source") is not None
        )
        comparison = {
            "id": interface_id,
            "type_match": type_match,
            "parameter_source_match": source_match,
            "center_delta": center_delta,
            "size_max_delta": size_delta,
            "orientation_delta_deg": orientation_delta,
        }
        comparisons.append(comparison)
        if not type_match:
            findings.append({"code": "TYPE_MISMATCH", "id": interface_id})
        if not source_match:
            findings.append({"code": "PARAMETER_SOURCE_MISMATCH", "id": interface_id})
        if center_delta > pos_tol:
            findings.append({"code": "CENTER_MISMATCH", "id": interface_id, "measured": center_delta, "threshold": pos_tol})
        if size_delta > size_tol:
            findings.append({"code": "SIZE_MISMATCH", "id": interface_id, "measured": size_delta, "threshold": size_tol})
        if orientation_delta > angle_tol:
            findings.append({"code": "ORIENTATION_MISMATCH", "id": interface_id, "measured": orientation_delta, "threshold": angle_tol})

    return {
        "validator": "check_interface_alignment",
        "status": "PASS" if not findings else "FAIL",
        "units": data.get("units"),
        "tolerance": {"position": pos_tol, "size": size_tol, "angle_deg": angle_tol},
        "comparisons": comparisons,
        "findings": findings,
        "boundary": "Agreement proves table consistency, not purchased-part accuracy, fit, access, or safety.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = validate(json.loads(args.input.read_text(encoding="utf-8")))
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, sort_keys=True))
        return 1
    rendered = json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
