#!/usr/bin/env python3
"""Check revision-bound render sets for camera stability and image integrity."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

try:
    from PIL import Image, ImageChops, ImageStat
except ImportError as exc:  # pragma: no cover - environment failure path
    Image = ImageChops = ImageStat = None
    PIL_ERROR = str(exc)
else:
    PIL_ERROR = None


SOURCE_FIELDS = ("revision", "source_hash", "units")


def indexed(views, label):
    if not isinstance(views, list):
        raise ValueError(f"{label}.views must be a list")
    result = {}
    for view in views:
        if not isinstance(view, dict) or not view.get("id"):
            raise ValueError(f"every {label} view requires id")
        if view["id"] in result:
            raise ValueError(f"duplicate {label} view id: {view['id']}")
        result[view["id"]] = view
    return result


def resolve(path_value, root):
    path = Path(path_value).expanduser()
    return path if path.is_absolute() else root / path


def structures_equal(a, b, tolerance):
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(structures_equal(a[key], b[key], tolerance) for key in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(structures_equal(x, y, tolerance) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=tolerance)
    return a == b


def inspect_image(path, blank_range_threshold):
    if not path.is_file():
        return None, {"code": "IMAGE_MISSING", "path": str(path)}
    try:
        with Image.open(path) as opened:
            image = opened.convert("RGB")
    except Exception as exc:
        return None, {"code": "IMAGE_READ_ERROR", "path": str(path), "error": str(exc)}
    extrema = image.getextrema()
    channel_ranges = [maximum - minimum for minimum, maximum in extrema]
    info = {"path": str(path), "width": image.width, "height": image.height, "channel_ranges": channel_ranges, "image": image}
    if max(channel_ranges) <= blank_range_threshold:
        return info, {"code": "IMAGE_BLANK_OR_UNIFORM", "path": str(path), "channel_ranges": channel_ranges, "threshold": blank_range_threshold}
    return info, None


def validate(data, input_path):
    if PIL_ERROR:
        raise RuntimeError(f"Pillow is required: {PIL_ERROR}")
    baseline = data.get("baseline")
    candidate = data.get("candidate")
    if not isinstance(baseline, dict) or not isinstance(candidate, dict):
        raise ValueError("baseline and candidate objects are required")
    root = Path(data.get("root", input_path.parent)).expanduser()
    if not root.is_absolute():
        root = (input_path.parent / root).resolve()
    required = data.get("required_view_ids")
    if not isinstance(required, list) or not required:
        raise ValueError("required_view_ids must be a non-empty list")
    expect_change = bool(data.get("expect_change", True))
    camera_tolerance = float(data.get("camera_numeric_tolerance", 1e-9))
    blank_threshold = float(data.get("blank_range_threshold", 2.0))
    min_difference = float(data.get("min_mean_abs_difference", 0.25))
    max_unchanged = float(data.get("max_mean_abs_difference", 0.05))
    findings = []

    for label, viewset in (("baseline", baseline), ("candidate", candidate)):
        source = viewset.get("source")
        if not isinstance(source, dict):
            findings.append({"code": "SOURCE_IDENTITY_MISSING", "viewset": label})
            continue
        for field in SOURCE_FIELDS:
            if source.get(field) in (None, ""):
                findings.append({"code": "SOURCE_FIELD_MISSING", "viewset": label, "field": field})

    if expect_change and baseline.get("source", {}).get("source_hash") == candidate.get("source", {}).get("source_hash"):
        findings.append({"code": "SOURCE_HASH_UNCHANGED"})
    if not expect_change and baseline.get("source", {}).get("source_hash") != candidate.get("source", {}).get("source_hash"):
        findings.append({"code": "SOURCE_HASH_CHANGED_UNEXPECTEDLY"})

    base_views = indexed(baseline.get("views"), "baseline")
    cand_views = indexed(candidate.get("views"), "candidate")
    for view_id in required:
        if view_id not in base_views:
            findings.append({"code": "BASELINE_VIEW_MISSING", "view": view_id})
        if view_id not in cand_views:
            findings.append({"code": "CANDIDATE_VIEW_MISSING", "view": view_id})

    results = []
    for view_id in sorted(set(base_views) & set(cand_views)):
        a, b = base_views[view_id], cand_views[view_id]
        if a.get("configuration") != b.get("configuration"):
            findings.append({"code": "CONFIGURATION_MISMATCH", "view": view_id})
        if not structures_equal(a.get("camera"), b.get("camera"), camera_tolerance):
            findings.append({"code": "CAMERA_MISMATCH", "view": view_id})
        if a.get("color_pipeline") != b.get("color_pipeline"):
            findings.append({"code": "COLOR_PIPELINE_MISMATCH", "view": view_id})

        path_a = resolve(a.get("path", ""), root)
        path_b = resolve(b.get("path", ""), root)
        info_a, error_a = inspect_image(path_a, blank_threshold)
        info_b, error_b = inspect_image(path_b, blank_threshold)
        if error_a:
            findings.append({**error_a, "view": view_id, "viewset": "baseline"})
        if error_b:
            findings.append({**error_b, "view": view_id, "viewset": "candidate"})
        if not info_a or not info_b:
            continue
        if (info_a["width"], info_a["height"]) != (info_b["width"], info_b["height"]):
            findings.append({"code": "IMAGE_DIMENSION_MISMATCH", "view": view_id})
            continue
        difference = ImageChops.difference(info_a["image"], info_b["image"])
        mean_difference = sum(ImageStat.Stat(difference).mean) / 3.0
        if expect_change and mean_difference < min_difference:
            findings.append({"code": "EXPECTED_CHANGE_NOT_VISIBLE", "view": view_id, "measured": mean_difference, "threshold": min_difference})
        if not expect_change and mean_difference > max_unchanged:
            findings.append({"code": "UNEXPECTED_VISUAL_CHANGE", "view": view_id, "measured": mean_difference, "threshold": max_unchanged})
        results.append({"view": view_id, "width": info_b["width"], "height": info_b["height"], "mean_abs_difference": mean_difference})

    return {
        "validator": "compare_render_viewset",
        "status": "PASS" if not findings else "FAIL",
        "expect_change": expect_change,
        "results": results,
        "findings": findings,
        "boundary": "Camera and pixel integrity do not prove model correctness, surface quality, lighting taste, or product appeal.",
    }


def main():
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
