#!/usr/bin/env python3
"""Check that the design-specific brief exists before detailed CAD."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REQUIRED_FIELDS = (
    "user_scenarios",
    "interface_inventory",
    "adapter_and_mating_envelopes",
    "cable_directions",
    "motion_actions",
    "footprint_and_context",
    "stability_and_tip_risks",
    "product_character",
    "design_dna",
)


def substantive(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set)):
        return bool(value) and all(substantive(item) for item in value)
    if isinstance(value, dict):
        if value.get("status") == "NOT_APPLICABLE":
            return substantive(value.get("reason"))
        return bool(value) and any(substantive(item) for item in value.values())
    return True


def validate(data: dict) -> dict:
    brief = data.get("brief")
    if not isinstance(brief, dict):
        raise ValueError("brief must be an object")

    findings = []
    for field in REQUIRED_FIELDS:
        if field not in brief:
            findings.append({"code": "BRIEF_FIELD_MISSING", "field": field})
        elif not substantive(brief[field]):
            findings.append({"code": "BRIEF_FIELD_EMPTY", "field": field})

    interfaces = brief.get("interface_inventory")
    if isinstance(interfaces, list):
        seen = set()
        for index, item in enumerate(interfaces):
            if not isinstance(item, dict):
                findings.append({"code": "INTERFACE_INVALID", "index": index})
                continue
            interface_id = item.get("id")
            if not substantive(interface_id):
                findings.append({"code": "INTERFACE_ID_MISSING", "index": index})
            elif interface_id in seen:
                findings.append({"code": "INTERFACE_ID_DUPLICATE", "id": interface_id})
            else:
                seen.add(interface_id)
            if not substantive(item.get("type")):
                findings.append({"code": "INTERFACE_TYPE_MISSING", "index": index})
            count = item.get("count")
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                findings.append({"code": "INTERFACE_COUNT_INVALID", "index": index, "value": count})

    dna = brief.get("design_dna")
    if isinstance(dna, dict):
        if not substantive(dna.get("immutable")):
            findings.append({"code": "DESIGN_DNA_IMMUTABLE_MISSING"})
        for field in ("adjustable", "prohibited", "authority", "regression_views"):
            if field not in dna:
                findings.append({"code": "DESIGN_DNA_FIELD_MISSING", "field": field})

    for field in ("product_id", "revision"):
        if not substantive(data.get(field)):
            findings.append({"code": "IDENTITY_FIELD_MISSING", "field": field})

    return {
        "validator": "check_pre_cad_brief",
        "status": "PASS" if not findings else "FAIL",
        "product_id": data.get("product_id"),
        "revision": data.get("revision"),
        "required_fields": list(REQUIRED_FIELDS),
        "findings": findings,
        "boundary": (
            "A pass proves required brief fields are present and minimally structured. "
            "It does not prove the assumptions, design DNA, ergonomics, stability, safety, or concept quality are correct."
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
