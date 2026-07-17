#!/usr/bin/env python3
"""Build a deterministic, revision-bound CAD release artifact manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Iterable


FORMAT = "cad-release-manifest/v1"
GATE_STATUSES = (
    "blocked",
    "draft",
    "candidate-after-human-review",
    "approved-externally",
)


def parse_pair(value: str, label: str) -> tuple[str, str]:
    key, separator, item = value.partition("=")
    if not separator or not key.strip() or not item.strip():
        raise argparse.ArgumentTypeError(f"{label} must use KEY=VALUE syntax: {value!r}")
    return key.strip(), item.strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_file(root: Path, value: str) -> tuple[Path, str]:
    candidate = Path(value)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"artifact is outside release root: {value}") from exc
    if not resolved.is_file():
        raise ValueError(f"artifact is not a regular file: {value}")
    return resolved, relative.as_posix()


def build_artifacts(root: Path, values: Iterable[str], output: Path) -> list[dict[str, object]]:
    artifacts: list[dict[str, object]] = []
    seen_paths: set[str] = set()
    for value in values:
        role, path_value = parse_pair(value, "artifact")
        path, relative = relative_file(root, path_value)
        if path == output:
            raise ValueError("the manifest cannot include itself as an artifact")
        if relative in seen_paths:
            raise ValueError(f"duplicate artifact path: {relative}")
        seen_paths.add(relative)
        artifacts.append(
            {
                "bytes": path.stat().st_size,
                "path": relative,
                "role": role,
                "sha256": sha256_file(path),
            }
        )
    return sorted(artifacts, key=lambda item: (str(item["role"]), str(item["path"])))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Release package root")
    parser.add_argument(
        "--artifact",
        action="append",
        required=True,
        metavar="ROLE=PATH",
        help="Artifact role and file path; repeat for each file",
    )
    parser.add_argument("--output", type=Path, required=True, help="Manifest JSON path")
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--configuration", required=True)
    parser.add_argument("--producer", required=True, help="Producing application/tool and version")
    parser.add_argument("--units", default="")
    parser.add_argument("--schema", default="")
    parser.add_argument("--gate-status", choices=GATE_STATUSES, required=True)
    parser.add_argument(
        "--approval",
        default="",
        help="External approval record ID/person/process; required for approved-externally",
    )
    parser.add_argument(
        "--metadata",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Additional non-secret metadata; repeat as needed",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        raise SystemExit(f"release root is not a directory: {root}")

    output = args.output
    output = (output if output.is_absolute() else root / output).resolve()
    try:
        output.relative_to(root)
    except ValueError as exc:
        raise SystemExit("manifest output must be inside the release root") from exc

    if args.gate_status == "approved-externally" and not args.approval.strip():
        raise SystemExit("--approval is required for gate status approved-externally")

    metadata: dict[str, str] = {}
    for value in args.metadata:
        key, item = parse_pair(value, "metadata")
        if key in metadata:
            raise SystemExit(f"duplicate metadata key: {key}")
        metadata[key] = item

    try:
        artifacts = build_artifacts(root, args.artifact, output)
    except (argparse.ArgumentTypeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    manifest = {
        "approval": args.approval.strip() or None,
        "artifacts": artifacts,
        "configuration": args.configuration,
        "format": FORMAT,
        "gate_status": args.gate_status,
        "metadata": dict(sorted(metadata.items())),
        "producer": args.producer,
        "schema": args.schema,
        "source_revision": args.source_revision,
        "units": args.units,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, output)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
