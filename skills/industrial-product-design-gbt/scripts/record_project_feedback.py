#!/usr/bin/env python3
"""Validate and append a privacy-bounded cross-window project feedback record."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REQUIRED_TOP_LEVEL = (
    "schema_version",
    "record_id",
    "project_id",
    "project_revision",
    "timestamp",
    "task_summary",
    "maturity",
    "design_dna",
    "feedback",
    "outcome",
    "evidence",
    "learning",
    "privacy",
)

REQUIRED_FEEDBACK = (
    "verbatim",
    "category",
    "target",
    "expected",
    "observed",
    "severity",
    "user_disposition",
)

FORBIDDEN_KEY_PARTS = (
    "password",
    "passwd",
    "token",
    "secret",
    "authorization",
    "api_key",
    "apikey",
    "private_key",
    "credential",
)

VALID_SCOPES = {"project-only", "tentative", "cross-project", "backend-specific", "gate-change"}


def substantive(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def scan_forbidden_keys(value, path="$"):
    findings = []
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if any(part in normalized for part in FORBIDDEN_KEY_PARTS):
                findings.append({"code": "SENSITIVE_KEY_FORBIDDEN", "path": f"{path}.{key}"})
            findings.extend(scan_forbidden_keys(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(scan_forbidden_keys(child, f"{path}[{index}]"))
    return findings


def validate(data: dict) -> list[dict]:
    if not isinstance(data, dict):
        raise ValueError("record must be an object")
    findings = []
    for field in REQUIRED_TOP_LEVEL:
        if field not in data:
            findings.append({"code": "FIELD_MISSING", "field": field})
        elif not substantive(data[field]):
            findings.append({"code": "FIELD_EMPTY", "field": field})

    if data.get("schema_version") != 1:
        findings.append({"code": "SCHEMA_VERSION_UNSUPPORTED", "value": data.get("schema_version")})

    feedback = data.get("feedback")
    if isinstance(feedback, list):
        for index, item in enumerate(feedback):
            if not isinstance(item, dict):
                findings.append({"code": "FEEDBACK_INVALID", "index": index})
                continue
            for field in REQUIRED_FEEDBACK:
                if not substantive(item.get(field)):
                    findings.append({"code": "FEEDBACK_FIELD_MISSING", "index": index, "field": field})

    learning = data.get("learning")
    if isinstance(learning, dict) and learning.get("reuse_scope") not in VALID_SCOPES:
        findings.append({"code": "REUSE_SCOPE_INVALID", "value": learning.get("reuse_scope")})

    privacy = data.get("privacy")
    if not isinstance(privacy, dict):
        findings.append({"code": "PRIVACY_OBJECT_REQUIRED"})
    else:
        if privacy.get("may_store_locally") is not True:
            findings.append({"code": "LOCAL_STORAGE_NOT_AUTHORIZED"})
        if "may_publish" not in privacy:
            findings.append({"code": "PUBLISH_PERMISSION_MISSING"})
        if "contains_private_assets" not in privacy:
            findings.append({"code": "PRIVATE_ASSET_FLAG_MISSING"})

    findings.extend(scan_forbidden_keys(data))
    return findings


def existing_ids(store: Path) -> set[str]:
    if not store.is_file():
        return set()
    ids = set()
    for line_number, line in enumerate(store.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL in {store} at line {line_number}: {exc}") from exc
        if isinstance(item, dict) and item.get("record_id"):
            ids.add(str(item["record_id"]))
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--store", type=Path)
    args = parser.parse_args()

    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        findings = validate(data)
        if findings:
            report = {"validator": "record_project_feedback", "status": "FAIL", "findings": findings}
            print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
            return 2

        skill_root = Path(__file__).resolve().parent.parent
        store = args.store or skill_root / "local-learning" / "project-feedback.jsonl"
        store = store.expanduser().resolve()
        if str(data["record_id"]) in existing_ids(store):
            report = {
                "validator": "record_project_feedback",
                "status": "FAIL",
                "findings": [{"code": "RECORD_ID_DUPLICATE", "record_id": data["record_id"]}],
            }
            print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
            return 2

        store.parent.mkdir(parents=True, exist_ok=True)
        with store.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, sort_keys=True))
        return 1

    report = {
        "validator": "record_project_feedback",
        "status": "PASS",
        "record_id": data["record_id"],
        "store": str(store),
        "boundary": "Storage success does not prove that a lesson is correct, reusable, or authorized for publication.",
    }
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
