#!/usr/bin/env python3
"""Compare requested and actual CAD document identities."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


DEFAULT_FIELDS = ["path", "revision", "configuration", "units"]


def normalize(field: str, value):
    if value is None:
        return None
    if field == "path":
        return os.path.normcase(os.path.abspath(os.path.expanduser(str(value))))
    if field in {"source_hash", "sha256"}:
        return str(value).strip().lower()
    return str(value).strip()


def validate(data: dict) -> dict:
    expected = data.get("expected")
    actual = data.get("actual")
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        raise ValueError("input requires expected and actual objects")

    fields = data.get("required_fields", DEFAULT_FIELDS)
    if not isinstance(fields, list) or not fields:
        raise ValueError("required_fields must be a non-empty list")
    for optional in ("document_id", "source_hash", "backend", "kernel_version"):
        if optional in expected and optional not in fields:
            fields.append(optional)

    findings = []
    comparisons = []
    for field in fields:
        exp = normalize(field, expected.get(field))
        act = normalize(field, actual.get(field))
        match = exp is not None and act is not None and exp == act
        comparisons.append({"field": field, "expected": exp, "actual": act, "match": match})
        if exp is None:
            findings.append({"code": "EXPECTED_FIELD_MISSING", "field": field})
        elif act is None:
            findings.append({"code": "ACTUAL_FIELD_MISSING", "field": field})
        elif not match:
            findings.append(
                {"code": "IDENTITY_MISMATCH", "field": field, "expected": exp, "actual": act}
            )

    return {
        "validator": "check_document_identity",
        "status": "PASS" if not findings else "FAIL",
        "comparisons": comparisons,
        "findings": findings,
        "boundary": "Agreement proves only that supplied identity fields match live readback.",
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
