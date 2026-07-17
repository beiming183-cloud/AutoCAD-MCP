#!/usr/bin/env python3
"""Enforce A/B/C/D evidence levels for design-driving dimensions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


LEVEL_BASES = {
    "A": {"authorized_standard", "supplier_controlled_drawing", "supplier_controlled_cad", "project_controlled_drawing"},
    "B": {"official_catalog", "brand_public_parameter", "manufacturer_public_page"},
    "C": {"controlled_measurement"},
    "D": {"concept_assumption", "visual_estimate", "generic_library", "remembered_value"},
}

CRITICAL_CATEGORIES = {
    "ac-opening",
    "usb-cutout",
    "protective-shutter",
    "terminal",
    "installation-hole",
    "rotating-contact",
    "regulated-interface",
    "certification-critical-interface",
}


def missing(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def validate(data: dict) -> dict:
    dimensions = data.get("dimensions")
    if not isinstance(dimensions, list):
        raise ValueError("dimensions must be a list")

    findings = []
    results = []
    seen = set()
    for index, item in enumerate(dimensions):
        if not isinstance(item, dict):
            findings.append({"code": "DIMENSION_INVALID", "index": index})
            continue

        dimension_id = item.get("id")
        level = item.get("authority_level")
        basis = item.get("authority_basis")
        status = item.get("status")
        category = str(item.get("category", "")).strip().lower()
        kind = str(item.get("dimension_kind", "")).strip().lower()
        critical = bool(item.get("critical_detail")) or category in CRITICAL_CATEGORIES or kind == "detail_geometry"

        if missing(dimension_id):
            findings.append({"code": "DIMENSION_ID_MISSING", "index": index})
        elif dimension_id in seen:
            findings.append({"code": "DIMENSION_ID_DUPLICATE", "id": dimension_id})
        else:
            seen.add(dimension_id)

        if level not in LEVEL_BASES:
            findings.append({"code": "AUTHORITY_LEVEL_INVALID", "id": dimension_id, "value": level})
        elif basis not in LEVEL_BASES[level]:
            findings.append({"code": "AUTHORITY_BASIS_MISMATCH", "id": dimension_id, "level": level, "basis": basis})

        if status not in {"CONFIRMED", "TBD"}:
            findings.append({"code": "DIMENSION_STATUS_INVALID", "id": dimension_id, "value": status})
        if missing(item.get("source_id")):
            findings.append({"code": "SOURCE_ID_MISSING", "id": dimension_id})
        if missing(item.get("units")):
            findings.append({"code": "UNITS_MISSING", "id": dimension_id})
        if status == "CONFIRMED" and missing(item.get("value")):
            findings.append({"code": "CONFIRMED_VALUE_MISSING", "id": dimension_id})

        if level == "D" and status != "TBD":
            findings.append({"code": "CONCEPT_ASSUMPTION_MUST_BE_TBD", "id": dimension_id})
        if critical and status == "CONFIRMED" and level not in {"A", "C"}:
            findings.append({"code": "CRITICAL_DETAIL_REQUIRES_A_OR_C", "id": dimension_id, "level": level})
        if critical and level in {"B", "D"} and status != "TBD":
            findings.append({"code": "LOW_AUTHORITY_CRITICAL_DETAIL_MUST_BE_TBD", "id": dimension_id, "level": level})

        results.append(
            {
                "id": dimension_id,
                "category": category,
                "dimension_kind": kind,
                "critical_detail": critical,
                "status": status,
                "authority_level": level,
                "authority_basis": basis,
                "source_id": item.get("source_id"),
            }
        )

    for field in ("document_id", "revision"):
        if missing(data.get(field)):
            findings.append({"code": "IDENTITY_FIELD_MISSING", "field": field})

    return {
        "validator": "check_dimension_authority",
        "status": "PASS" if not findings else "FAIL",
        "document_id": data.get("document_id"),
        "revision": data.get("revision"),
        "dimensions": results,
        "findings": findings,
        "boundary": (
            "A pass proves the declared source class and TBD policy are internally consistent. "
            "It does not authenticate a source, validate a measurement, prove standard applicability, or approve the dimension."
        ),
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
