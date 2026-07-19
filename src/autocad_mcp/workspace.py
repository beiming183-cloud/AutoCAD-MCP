"""Industrial output workspace and delivery artifact helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_OUTPUT_ROOT = str(Path.home() / "Documents" / "AutoCAD-MCP")
WORKSPACE_FOLDERS = (
    "specs",
    "scripts",
    "models",
    "drawings",
    "dxf",
    "pdf",
    "previews",
    "audits",
    "reports",
    "outputs",
    "jobs",
    "templates",
    "incoming",
    "archive",
    "logs",
)
INVALID_WINDOWS_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def output_root() -> Path:
    return Path(os.environ.get("AUTOCAD_MCP_OUTPUT_ROOT", DEFAULT_OUTPUT_ROOT)).expanduser().resolve()


def sanitize_name(value: str | None, fallback: str = "drawing") -> str:
    name = INVALID_WINDOWS_NAME.sub("_", (value or "").strip()).strip(" .")
    name = re.sub(r"\s+", "-", name)
    if not name:
        name = fallback
    if name.upper() in WINDOWS_RESERVED_NAMES:
        name = f"_{name}"
    return name[:96]


def ensure_workspace() -> dict[str, Path]:
    root = output_root()
    root.mkdir(parents=True, exist_ok=True)
    folders = {name: root / name for name in WORKSPACE_FOLDERS}
    for folder in folders.values():
        folder.mkdir(parents=True, exist_ok=True)
    return {"root": root, **folders}


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class OutputTarget:
    path: Path
    category: str
    requested: str | None = None
    redirected: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "path": str(self.path),
            "category": self.category,
            "redirected": self.redirected,
        }
        if self.requested:
            payload["requested"] = self.requested
        return payload


def resolve_output_target(
    requested: str | None,
    *,
    category: str,
    extension: str,
    default_stem: str = "drawing",
) -> OutputTarget:
    folders = ensure_workspace()
    if category not in folders or category == "root":
        raise ValueError(f"Unknown output category: {category}")

    root = folders["root"]
    category_root = folders[category]
    extension = extension if extension.startswith(".") else f".{extension}"
    redirected = False

    if requested:
        supplied = Path(requested).expanduser()
        if supplied.is_absolute():
            candidate = supplied.resolve()
            if not _is_within(candidate, root) and not _env_flag(
                "AUTOCAD_MCP_ALLOW_EXTERNAL_OUTPUTS"
            ):
                candidate = category_root / sanitize_name(supplied.name, default_stem)
                redirected = True
        else:
            safe_parts = [sanitize_name(part) for part in supplied.parts if part not in (".", "..")]
            candidate = category_root.joinpath(*safe_parts) if safe_parts else category_root / default_stem
    else:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        candidate = category_root / f"{sanitize_name(default_stem)}-{timestamp}"

    if candidate.suffix.lower() != extension.lower():
        candidate = candidate.with_suffix(extension)
    candidate = candidate.resolve()
    if not _is_within(candidate, root) and not _env_flag("AUTOCAD_MCP_ALLOW_EXTERNAL_OUTPUTS"):
        raise ValueError(f"Output path must remain under {root}: {candidate}")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    return OutputTarget(candidate, category, requested=requested, redirected=redirected)


def create_job(name: str | None = None) -> dict[str, Any]:
    folders = ensure_workspace()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = sanitize_name(name, "cad-job")
    job_id = f"{timestamp}-{stem}"
    job_root = folders["jobs"] / job_id
    sequence = 1
    while job_root.exists():
        sequence += 1
        job_id = f"{timestamp}-{stem}-{sequence}"
        job_root = folders["jobs"] / job_id

    outputs = {
        "job_id": job_id,
        "name": name or stem,
        "root": job_root,
        "specs": job_root / "specs",
        "models": job_root / "models",
        "drawings": job_root / "drawings",
        "dxf": job_root / "dxf",
        "pdf": job_root / "pdf",
        "previews": job_root / "previews",
        "audits": job_root / "audits",
        "reports": job_root / "reports",
        "outputs": job_root / "outputs",
        "logs": job_root / "logs",
    }
    for key, folder in outputs.items():
        if isinstance(folder, Path):
            folder.mkdir(parents=True, exist_ok=True)
    return outputs


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def workspace_info() -> dict[str, Any]:
    folders = ensure_workspace()
    return {
        "root": str(folders["root"]),
        "folders": {name: str(path) for name, path in folders.items() if name != "root"},
        "allow_external_outputs": _env_flag("AUTOCAD_MCP_ALLOW_EXTERNAL_OUTPUTS"),
    }


def cleanup_test_job_artifacts(job_id: str) -> dict[str, Any]:
    """Delete managed CAD artifacts while retaining specs, audits, and logs."""
    folders = ensure_workspace()
    jobs_root = folders["jobs"].resolve()
    job_root = (jobs_root / str(job_id)).resolve()
    if job_root == jobs_root or not _is_within(job_root, jobs_root):
        raise ValueError("job_id must resolve to a managed job directory")
    if job_root.name == "_journal" or not job_root.is_dir():
        raise FileNotFoundError(f"Managed job does not exist: {job_id}")

    artifact_categories = ("models", "drawings", "dxf", "pdf", "previews", "outputs")
    deleted = []
    for category in artifact_categories:
        category_root = job_root / category
        if not category_root.is_dir():
            continue
        for path in sorted(item for item in category_root.rglob("*") if item.is_file()):
            record = {
                "path": str(path.relative_to(job_root)),
                "category": category,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            path.unlink()
            record["deleted"] = True
            deleted.append(record)

    report = {
        "schema_version": 1,
        "job_id": str(job_id),
        "cleanup_type": "test-artifacts",
        "cleaned_at": datetime.now().astimezone().isoformat(),
        "job_root": str(job_root),
        "deleted_count": len(deleted),
        "deleted_bytes": sum(item["bytes"] for item in deleted),
        "deleted": deleted,
        "retained_categories": ["specs", "audits", "reports", "logs"],
    }
    report_path = job_root / "reports" / "artifact-cleanup.json"
    write_json_atomic(report_path, report)
    report["report_path"] = str(report_path)
    return report
