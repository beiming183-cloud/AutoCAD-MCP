#!/usr/bin/env python3
"""Validate structured joints, named motion states, limits, and dispositions."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path


REQUIRED_DISPOSITIONS = ("collision_status", "cable_status", "interface_status")


def indexed(items, label):
    if not isinstance(items, list):
        raise ValueError(f"{label} must be a list")
    result = {}
    for item in items:
        if not isinstance(item, dict) or not item.get("id" if label == "joints" else "name"):
            raise ValueError(f"every {label} item requires an identity")
        key = item["id" if label == "joints" else "name"]
        if key in result:
            raise ValueError(f"duplicate {label} identity: {key}")
        result[key] = item
    return result


def validate(data: dict) -> dict:
    joints = indexed(data.get("joints"), "joints")
    states = indexed(data.get("states"), "states")
    required_states = data.get("required_states")
    if not isinstance(required_states, list) or not required_states:
        raise ValueError("required_states must be a non-empty list")
    findings = []

    for joint_id, joint in joints.items():
        axis = joint.get("axis")
        limits = joint.get("limits")
        if not isinstance(axis, list) or len(axis) != 3 or math.sqrt(sum(float(x) ** 2 for x in axis)) <= 0:
            findings.append({"code": "INVALID_JOINT_AXIS", "joint": joint_id})
        if not isinstance(limits, list) or len(limits) != 2 or float(limits[0]) > float(limits[1]):
            findings.append({"code": "INVALID_JOINT_LIMITS", "joint": joint_id})

    for name in required_states:
        if name not in states:
            findings.append({"code": "REQUIRED_STATE_MISSING", "state": name})

    state_results = []
    for state_name, state in states.items():
        values = state.get("joint_values")
        if not isinstance(values, dict):
            findings.append({"code": "JOINT_VALUES_MISSING", "state": state_name})
            continue
        unknown = sorted(set(values) - set(joints))
        missing = sorted(set(joints) - set(values))
        for joint_id in unknown:
            findings.append({"code": "UNKNOWN_JOINT", "state": state_name, "joint": joint_id})
        for joint_id in missing:
            findings.append({"code": "JOINT_VALUE_MISSING", "state": state_name, "joint": joint_id})
        checks = []
        for joint_id in sorted(set(values) & set(joints)):
            value = float(values[joint_id])
            limits = joints[joint_id].get("limits")
            if not isinstance(limits, list) or len(limits) != 2:
                continue
            inside = float(limits[0]) <= value <= float(limits[1])
            checks.append({"joint": joint_id, "value": value, "limits": limits, "inside": inside})
            if not inside:
                findings.append({"code": "JOINT_LIMIT_VIOLATION", "state": state_name, "joint": joint_id, "value": value, "limits": limits})
        for field in REQUIRED_DISPOSITIONS:
            status = state.get(field)
            if status != "PASS":
                findings.append({"code": "STATE_DISPOSITION_NOT_PASS", "state": state_name, "field": field, "status": status})
        state_results.append({"state": state_name, "joint_checks": checks})

    return {
        "validator": "validate_motion_states",
        "status": "PASS" if not findings else "FAIL",
        "revision": data.get("revision"),
        "configuration_source": data.get("configuration_source"),
        "state_results": state_results,
        "findings": findings,
        "boundary": "Schema and recorded dispositions are checked; CAD collision, cable physics, loads, and safety require independent evidence.",
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
