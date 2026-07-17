#!/usr/bin/env python3
"""Validate and append a privacy-bounded cross-window project feedback record."""

from __future__ import annotations

import argparse
import json
import re
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
VALID_ITERATION_STATUS = {"proposed", "rejected", "accepted", "superseded", "rolled-back"}
SUPPORTED_SCHEMA_VERSIONS = {1, 2}


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

    if data.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
        findings.append({"code": "SCHEMA_VERSION_UNSUPPORTED", "value": data.get("schema_version")})

    if data.get("schema_version") == 2:
        iteration = data.get("iteration")
        if not isinstance(iteration, dict):
            findings.append({"code": "ITERATION_OBJECT_REQUIRED"})
        else:
            index = iteration.get("index")
            if not isinstance(index, int) or isinstance(index, bool) or index < 1:
                findings.append({"code": "ITERATION_INDEX_INVALID", "value": index})
            for field in ("branch", "status", "reason", "baseline_revision", "candidate_revision", "change_summary", "design_dna_disposition"):
                if not substantive(iteration.get(field)):
                    findings.append({"code": "ITERATION_FIELD_MISSING", "field": field})
            if iteration.get("status") not in VALID_ITERATION_STATUS:
                findings.append({"code": "ITERATION_STATUS_INVALID", "value": iteration.get("status")})
            if isinstance(index, int) and not isinstance(index, bool) and index > 1 and not substantive(iteration.get("parent_record_id")):
                findings.append({"code": "ITERATION_PARENT_REQUIRED", "index": index})

            outcome = data.get("outcome")
            if isinstance(outcome, dict) and isinstance(outcome.get("accepted"), bool):
                if iteration.get("status") == "accepted" and outcome.get("accepted") is not True:
                    findings.append({"code": "ACCEPTED_STATUS_OUTCOME_MISMATCH"})
                if iteration.get("status") in {"rejected", "rolled-back", "superseded"} and outcome.get("accepted") is not False:
                    findings.append({"code": "NONCURRENT_STATUS_OUTCOME_MISMATCH", "status": iteration.get("status")})

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


def existing_records(store: Path) -> list[dict]:
    if not store.is_file():
        return []
    records = []
    for line_number, line in enumerate(store.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL in {store} at line {line_number}: {exc}") from exc
        if isinstance(item, dict):
            records.append(item)
    return records


def validate_lineage(data: dict, records: list[dict]) -> list[dict]:
    findings = []
    record_id = str(data.get("record_id"))
    if any(str(item.get("record_id")) == record_id for item in records):
        findings.append({"code": "RECORD_ID_DUPLICATE", "record_id": record_id})

    if data.get("schema_version") != 2 or not isinstance(data.get("iteration"), dict):
        return findings

    iteration = data["iteration"]
    key = (str(data.get("project_id")), str(iteration.get("branch")), iteration.get("index"))
    for item in records:
        other = item.get("iteration")
        if not isinstance(other, dict):
            continue
        other_key = (str(item.get("project_id")), str(other.get("branch")), other.get("index"))
        if other_key == key:
            findings.append({"code": "PROJECT_BRANCH_ITERATION_DUPLICATE", "project_id": key[0], "branch": key[1], "index": key[2]})
            break

    index = iteration.get("index")
    parent_id = iteration.get("parent_record_id")
    if isinstance(index, int) and not isinstance(index, bool) and index > 1 and substantive(parent_id):
        parent = next((item for item in records if str(item.get("record_id")) == str(parent_id)), None)
        if parent is None:
            findings.append({"code": "ITERATION_PARENT_NOT_FOUND", "parent_record_id": parent_id})
        else:
            if str(parent.get("project_id")) != str(data.get("project_id")):
                findings.append({"code": "ITERATION_PARENT_PROJECT_MISMATCH", "parent_record_id": parent_id})
            parent_iteration = parent.get("iteration")
            if isinstance(parent_iteration, dict) and isinstance(parent_iteration.get("index"), int) and parent_iteration["index"] >= index:
                findings.append({"code": "ITERATION_PARENT_ORDER_INVALID", "parent_index": parent_iteration["index"], "index": index})
    return findings


def safe_id(value) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value).strip()).strip("-")
    return normalized or "record"


def apply_auto_lineage(data: dict, records: list[dict]) -> None:
    if data.get("schema_version") in (None, "AUTO"):
        data["schema_version"] = 2
    if data.get("schema_version") != 2:
        return

    iteration = data.setdefault("iteration", {})
    if not isinstance(iteration, dict):
        return
    branch = iteration.get("branch")
    if not substantive(branch) or branch == "AUTO":
        branch = "main"
        iteration["branch"] = branch

    same_branch = []
    for item in records:
        other = item.get("iteration")
        if (
            isinstance(other, dict)
            and str(item.get("project_id")) == str(data.get("project_id"))
            and str(other.get("branch")) == str(branch)
            and isinstance(other.get("index"), int)
            and not isinstance(other.get("index"), bool)
        ):
            same_branch.append(item)
    same_branch.sort(key=lambda item: item["iteration"]["index"])

    index = iteration.get("index")
    if not isinstance(index, int) or isinstance(index, bool) or index < 1:
        index = same_branch[-1]["iteration"]["index"] + 1 if same_branch else 1
        iteration["index"] = index

    if index == 1 and iteration.get("parent_record_id") in (None, "", "AUTO"):
        iteration["parent_record_id"] = None
    elif index > 1 and iteration.get("parent_record_id") in (None, "", "AUTO"):
        prior = [item for item in same_branch if item["iteration"]["index"] < index]
        if prior:
            iteration["parent_record_id"] = prior[-1].get("record_id")

    if data.get("record_id") in (None, "", "AUTO"):
        data["record_id"] = "-".join(
            (
                safe_id(data.get("project_id")),
                safe_id(branch),
                f"i{index}",
                safe_id(iteration.get("candidate_revision", data.get("project_revision"))),
            )
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--store", type=Path)
    parser.add_argument("--auto-lineage", action="store_true", help="Assign record ID, branch iteration, and parent from the local store")
    args = parser.parse_args()

    try:
        skill_root = Path(__file__).resolve().parent.parent
        store = args.store or skill_root / "local-learning" / "project-feedback.jsonl"
        store = store.expanduser().resolve()
        records = existing_records(store)
        data = json.loads(args.input.read_text(encoding="utf-8"))
        if args.auto_lineage:
            apply_auto_lineage(data, records)
        findings = validate(data)
        if findings:
            report = {"validator": "record_project_feedback", "status": "FAIL", "findings": findings}
            print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
            return 2

        lineage_findings = validate_lineage(data, records)
        if lineage_findings:
            report = {
                "validator": "record_project_feedback",
                "status": "FAIL",
                "findings": lineage_findings,
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
        "project_id": data.get("project_id"),
        "iteration": data.get("iteration"),
        "store": str(store),
        "boundary": "Storage success does not prove that a lesson is correct, reusable, or authorized for publication.",
    }
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
