"""Allowlisted AutoCAD ActiveX adapter for an isolated Ngoc Viet staging root.

This module never calls SendCommand, loads LISP, launches a process, or accepts
arbitrary AutoCAD commands.  The MCP-facing operations are fixed geometry
transactions and all files are new DXFs below one managed root.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable


LIVE_ROOT_ENV = "NGOC_VIET_AUTOCAD_ROOT"
LIVE_APPROVAL_ENV = "NGOC_VIET_LIVE_AUTOCAD_APPROVAL"
LIVE_APPROVAL_VALUE = "I_APPROVE_FIXED_AUTOCAD_AUTOMATION"
SAFE_DXF_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}\.dxf$", re.IGNORECASE)
OUTER_LAYER = "CUT_OUTER"
INNER_LAYER = "CUT_INNER"
DXF_2013 = 61


@dataclass(frozen=True)
class LivePlateSpec:
    side_mm: float = 200.0
    center_hole_diameter_mm: float = 20.0
    edge_hole_diameter_mm: float = 10.0
    edge_offset_mm: float = 20.0

    def validated(self) -> "LivePlateSpec":
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not math.isfinite(float(value)) or float(value) <= 0:
                raise ValueError(f"{name} must be a finite positive number")
        if self.edge_offset_mm <= self.edge_hole_diameter_mm / 2.0:
            raise ValueError("edge_offset_mm must be greater than edge-hole radius")
        if self.side_mm <= 2.0 * self.edge_offset_mm:
            raise ValueError("side_mm must exceed twice edge_offset_mm")
        if self.center_hole_diameter_mm >= self.side_mm:
            raise ValueError("center hole must fit inside plate")
        return self

    @property
    def holes(self) -> list[tuple[tuple[float, float], float]]:
        middle = self.side_mm / 2.0
        offset = self.edge_offset_mm
        return [
            ((middle, middle), self.center_hole_diameter_mm / 2.0),
            ((middle, offset), self.edge_hole_diameter_mm / 2.0),
            ((middle, self.side_mm - offset), self.edge_hole_diameter_mm / 2.0),
            ((offset, middle), self.edge_hole_diameter_mm / 2.0),
            ((self.side_mm - offset, middle), self.edge_hole_diameter_mm / 2.0),
        ]


def live_root() -> Path:
    default = Path.cwd() / "artifacts" / "autocad-live"
    root = Path(os.environ.get(LIVE_ROOT_ENV, default)).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def live_automation_approved() -> bool:
    return os.environ.get(LIVE_APPROVAL_ENV, "") == LIVE_APPROVAL_VALUE


def resolve_live_dxf(name: str, *, must_exist: bool = False) -> Path:
    if not isinstance(name, str) or not SAFE_DXF_NAME.fullmatch(name) or Path(name).name != name:
        raise ValueError("name must be a simple .dxf basename")
    root = live_root()
    target = (root / name).resolve()
    target.relative_to(root)
    if target.is_symlink():
        raise ValueError("symlink DXF paths are forbidden")
    if must_exist and not target.is_file():
        raise FileNotFoundError(f"managed live DXF does not exist: {name}")
    return target


class LiveAutoCADAdapter:
    def __init__(
        self,
        *,
        application: Any | None = None,
        variant_factory: Callable[[list[float]], Any] | None = None,
        require_approval: bool = True,
    ) -> None:
        self._application = application
        self._variant_factory = variant_factory
        self._require_approval = require_approval

    def _approved(self) -> None:
        if self._require_approval and not live_automation_approved():
            raise PermissionError(
                f"live AutoCAD automation is disabled; set {LIVE_APPROVAL_ENV} only after explicit approval"
            )

    def connect(self) -> Any:
        self._approved()
        if self._application is None:
            if os.name != "nt":
                raise RuntimeError("live AutoCAD automation requires Windows")
            import win32com.client  # type: ignore[import-not-found]

            self._application = win32com.client.GetActiveObject("AutoCAD.Application")
        return self._application

    def _variant(self, values: list[float]) -> Any:
        if self._variant_factory is not None:
            return self._variant_factory(values)
        import pythoncom  # type: ignore[import-not-found]
        from win32com.client import VARIANT  # type: ignore[import-not-found]

        return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, values)

    def _log(self, operation: str, status: str, **details: Any) -> None:
        log_dir = live_root() / "logs"
        log_dir.mkdir(exist_ok=True)
        record = {
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "operation": operation,
            "status": status,
            **details,
        }
        with (log_dir / "live_autocad_mcp.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    @staticmethod
    def _ensure_layer(document: Any, name: str) -> None:
        try:
            document.Layers.Item(name)
        except Exception:
            document.Layers.Add(name)

    @staticmethod
    def _find_open_document(application: Any, path: Path) -> Any | None:
        """Return an already-open managed document, preferring a writable copy."""
        matches = []
        for document in application.Documents:
            full_name = str(getattr(document, "FullName", ""))
            if full_name and Path(full_name).resolve() == path:
                matches.append(document)
        if not matches:
            return None
        matches.sort(key=lambda item: bool(getattr(item, "ReadOnly", False)))
        return matches[0]

    def status(self) -> dict[str, Any]:
        application = self.connect()
        active = getattr(application, "ActiveDocument", None)
        return {
            "connected": True,
            "application": str(getattr(application, "Name", "AutoCAD")),
            "version": str(getattr(application, "Version", "unknown")),
            "active_document": str(getattr(active, "Name", "")) if active is not None else "",
            "root": str(live_root()),
            "transport": "ActiveX object model; SendCommand forbidden",
        }

    def create_plate(self, output_name: str, spec: LivePlateSpec | None = None) -> dict[str, Any]:
        spec = (spec or LivePlateSpec()).validated()
        target = resolve_live_dxf(output_name)
        if target.exists():
            raise FileExistsError(f"refusing to overwrite existing DXF: {output_name}")
        application = self.connect()
        document = application.Documents.Add()
        document.SetVariable("INSUNITS", 4)
        self._ensure_layer(document, OUTER_LAYER)
        self._ensure_layer(document, INNER_LAYER)
        side = spec.side_mm
        polyline = document.ModelSpace.AddLightWeightPolyline(
            self._variant([0.0, 0.0, side, 0.0, side, side, 0.0, side])
        )
        polyline.Closed = True
        polyline.Layer = OUTER_LAYER
        for (x, y), radius in spec.holes:
            circle = document.ModelSpace.AddCircle(self._variant([x, y, 0.0]), radius)
            circle.Layer = INNER_LAYER
        document.SaveAs(str(target), DXF_2013)
        application.ZoomExtents()
        report = self.inspect_document(document)
        report.update({"created": True, "file": output_name, "spec": asdict(spec)})
        self._log("create_plate", "PASS", file=output_name, entity_count=report["entity_count"])
        return report

    def inspect_document(self, document: Any | None = None) -> dict[str, Any]:
        application = self.connect()
        document = document or application.ActiveDocument
        entities: list[dict[str, Any]] = []
        for entity in document.ModelSpace:
            object_name = str(entity.ObjectName)
            if object_name == "AcDbPolyline":
                coordinates = [float(value) for value in entity.Coordinates]
                entities.append(
                    {
                        "type": "LWPOLYLINE",
                        "layer": str(entity.Layer),
                        "closed": bool(entity.Closed),
                        "points_mm": [coordinates[index : index + 2] for index in range(0, len(coordinates), 2)],
                    }
                )
            elif object_name == "AcDbCircle":
                entities.append(
                    {
                        "type": "CIRCLE",
                        "layer": str(entity.Layer),
                        "center_mm": [float(entity.Center[0]), float(entity.Center[1])],
                        "radius_mm": float(entity.Radius),
                    }
                )
            else:
                entities.append({"type": object_name, "layer": str(getattr(entity, "Layer", ""))})
        counts: dict[str, int] = {}
        for entity in entities:
            counts[entity["type"]] = counts.get(entity["type"], 0) + 1
        return {
            "document": str(document.Name),
            "entity_count": len(entities),
            "entity_counts": counts,
            "entities": entities,
        }

    def inspect_managed(self, name: str) -> dict[str, Any]:
        path = resolve_live_dxf(name, must_exist=True)
        application = self.connect()
        document = self._find_open_document(application, path)
        if document is None:
            document = application.Documents.Open(str(path), True)
        document.Activate()
        application.ZoomExtents()
        report = self.inspect_document(document)
        self._log("inspect_managed", "PASS", file=name, entity_count=report["entity_count"])
        return report

    def modify_hole_revision(
        self,
        source_name: str,
        output_name: str,
        center_x_mm: float,
        center_y_mm: float,
        new_diameter_mm: float,
    ) -> dict[str, Any]:
        source = resolve_live_dxf(source_name, must_exist=True)
        target = resolve_live_dxf(output_name)
        if source == target or target.exists():
            raise FileExistsError("revision output must be a new DXF name")
        for name, value in {
            "center_x_mm": center_x_mm,
            "center_y_mm": center_y_mm,
            "new_diameter_mm": new_diameter_mm,
        }.items():
            if isinstance(value, bool) or not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        if new_diameter_mm <= 0:
            raise ValueError("new_diameter_mm must be positive")
        application = self.connect()
        document = self._find_open_document(application, source)
        if document is None:
            document = application.Documents.Open(str(source), False)
        if bool(getattr(document, "ReadOnly", False)):
            raise PermissionError("managed source is open read-only; refusing to modify it")
        document.Activate()
        matches = []
        for entity in document.ModelSpace:
            if str(entity.ObjectName) != "AcDbCircle":
                continue
            center = entity.Center
            if math.dist((float(center[0]), float(center[1])), (center_x_mm, center_y_mm)) <= 1e-6:
                matches.append(entity)
        if len(matches) != 1:
            raise ValueError(f"expected exactly one circle at requested center; found {len(matches)}")
        matches[0].Radius = new_diameter_mm / 2.0
        document.SaveAs(str(target), DXF_2013)
        application.ZoomExtents()
        report = self.inspect_document(document)
        report.update({"modified": True, "source": source_name, "file": output_name})
        self._log("modify_hole_revision", "PASS", source=source_name, file=output_name)
        return report
