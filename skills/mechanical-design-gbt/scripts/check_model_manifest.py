#!/usr/bin/env python3
"""Validate revision, configuration, source hash, and artifact-role consistency."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


IDENTITY_FIELDS = ("revision", "configuration", "units", "source_hash")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate(data: dict, input_path: Path) -> dict:
    source = data.get("source")
    artifacts = data.get("artifacts")
    required_roles = data.get("required_roles")
    if not isinstance(source, dict):
        raise ValueError("source must be an object")
    if not isinstance(artifacts, list):
        raise ValueError("artifacts must be a list")
    if not isinstance(required_roles, list) or not required_roles:
        raise ValueError("required_roles must be a non-empty list")
    root = Path(data.get("root", input_path.parent)).expanduser()
    if not root.is_absolute():
        root = (input_path.parent / root).resolve()

    findings = []
    for field in IDENTITY_FIELDS:
        if source.get(field) in (None, ""):
            findings.append({"code": "SOURCE_IDENTITY_MISSING", "field": field})

    role_counts = {}
    results = []
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            findings.append({"code": "INVALID_ARTIFACT", "index": index})
            continue
        role = artifact.get("role")
        role_counts[role] = role_counts.get(role, 0) + 1
        raw_path = artifact.get("path")
        path = Path(raw_path).expanduser() if raw_path else None
        if path and not path.is_absolute():
            path = root / path
        result = {"index": index, "role": role, "path": str(path) if path else None}
        if not role:
            findings.append({"code": "ARTIFACT_ROLE_MISSING", "index": index})
        for field in IDENTITY_FIELDS:
            expected = str(source.get(field, "")).strip().lower()
            actual = str(artifact.get(field, "")).strip().lower()
            if expected != actual or not expected:
                findings.append({"code": "ARTIFACT_IDENTITY_MISMATCH", "index": index, "role": role, "field": field, "expected": source.get(field), "actual": artifact.get(field)})
        if path is None or not path.is_file():
            findings.append({"code": "ARTIFACT_FILE_MISSING", "index": index, "role": role, "path": str(path) if path else None})
        else:
            result["bytes"] = path.stat().st_size
            if result["bytes"] <= 0:
                findings.append({"code": "ARTIFACT_EMPTY", "index": index, "role": role, "path": str(path)})
            declared_hash = artifact.get("sha256")
            if declared_hash:
                actual_hash = sha256(path)
                result["sha256"] = actual_hash
                if actual_hash.lower() != str(declared_hash).lower():
                    findings.append({"code": "ARTIFACT_HASH_MISMATCH", "index": index, "role": role, "expected": declared_hash, "actual": actual_hash})
        results.append(result)

    for role in required_roles:
        if role_counts.get(role, 0) < 1:
            findings.append({"code": "REQUIRED_ROLE_MISSING", "role": role})

    return {
        "validator": "check_model_manifest",
        "status": "PASS" if not findings else "FAIL",
        "source": source,
        "root": str(root),
        "role_counts": role_counts,
        "artifacts": results,
        "findings": findings,
        "boundary": "Manifest consistency does not prove model quality, export equivalence, or approval.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = validate(json.loads(args.input.read_text(encoding="utf-8")), args.input)
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
