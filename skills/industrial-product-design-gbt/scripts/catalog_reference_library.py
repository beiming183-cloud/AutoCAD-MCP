#!/usr/bin/env python3
"""Create a deterministic, read-only manifest for a local CAD/image library."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from datetime import datetime, timezone
from pathlib import Path


DWG_VERSIONS = {
    "AC1012": "R13",
    "AC1014": "R14",
    "AC1015": "2000-2002",
    "AC1018": "2004-2006",
    "AC1021": "2007-2009",
    "AC1024": "2010-2012",
    "AC1027": "2013-2017",
    "AC1032": "2018+",
}

DEFAULT_EXTENSIONS = {".dwg", ".dxf", ".jpg", ".jpeg", ".png"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dwg_header(path: Path) -> dict[str, str | None]:
    with path.open("rb") as handle:
        signature = handle.read(6).decode("ascii", errors="replace")
    return {
        "signature": signature,
        "version_family": DWG_VERSIONS.get(signature),
    }


def png_size(path: Path) -> tuple[int, int] | None:
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) >= 24 and header[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", header[16:24])
    return None


def jpeg_size(path: Path) -> tuple[int, int] | None:
    sof_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    with path.open("rb") as handle:
        if handle.read(2) != b"\xff\xd8":
            return None
        while True:
            byte = handle.read(1)
            if not byte:
                return None
            if byte != b"\xff":
                continue
            while byte == b"\xff":
                byte = handle.read(1)
            if not byte:
                return None
            marker = byte[0]
            if marker in {0xD8, 0xD9}:
                continue
            length_raw = handle.read(2)
            if len(length_raw) != 2:
                return None
            length = struct.unpack(">H", length_raw)[0]
            if length < 2:
                return None
            if marker in sof_markers:
                payload = handle.read(5)
                if len(payload) != 5:
                    return None
                height, width = struct.unpack(">HH", payload[1:5])
                return width, height
            handle.seek(length - 2, 1)


def image_size(path: Path) -> tuple[int, int] | None:
    suffix = path.suffix.lower()
    if suffix == ".png":
        return png_size(path)
    if suffix in {".jpg", ".jpeg"}:
        return jpeg_size(path)
    return None


def parse_extensions(raw: str) -> set[str]:
    result = set()
    for item in raw.split(","):
        item = item.strip().lower()
        if not item:
            continue
        result.add(item if item.startswith(".") else f".{item}")
    return result


def build_manifest(root: Path, extensions: set[str]) -> dict:
    files = sorted(
        (path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in extensions),
        key=lambda path: path.relative_to(root).as_posix().casefold(),
    )
    entries = []
    for path in files:
        stat = path.stat()
        digest = sha256(path)
        entry = {
            "id": f"LIB-{digest[:16]}",
            "relative_path": path.relative_to(root).as_posix(),
            "extension": path.suffix.lower(),
            "bytes": stat.st_size,
            "modified_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "sha256": digest,
        }
        if path.suffix.lower() == ".dwg":
            entry["dwg"] = dwg_header(path)
        size = image_size(path)
        if size:
            entry["image"] = {"width": size[0], "height": size[1]}
        entries.append(entry)

    counts = {}
    for entry in entries:
        counts[entry["extension"]] = counts.get(entry["extension"], 0) + 1

    return {
        "schema_version": 1,
        "snapshot_latest_modified_utc": max(
            (entry["modified_utc"] for entry in entries), default=None
        ),
        "root": str(root.resolve()),
        "file_count": len(entries),
        "total_bytes": sum(entry["bytes"] for entry in entries),
        "counts_by_extension": dict(sorted(counts.items())),
        "files": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Library root to scan read-only")
    parser.add_argument("--output", type=Path, required=True, help="Manifest JSON path")
    parser.add_argument(
        "--extensions",
        default=",".join(sorted(DEFAULT_EXTENSIONS)),
        help="Comma-separated extensions to include",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    if not root.is_dir():
        parser.error(f"library root is not a directory: {root}")
    extensions = parse_extensions(args.extensions)
    if not extensions:
        parser.error("at least one extension is required")

    manifest = build_manifest(root, extensions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(args.output.resolve()),
                "file_count": manifest["file_count"],
                "total_bytes": manifest["total_bytes"],
                "counts_by_extension": manifest["counts_by_extension"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
