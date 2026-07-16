"""Abstract base class for AutoCAD backends + CommandResult envelope."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CommandResult:
    """Structured result envelope from backend operations."""

    ok: bool
    payload: Any = None
    error: str | None = None

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"ok": self.ok}
        if self.ok:
            d["payload"] = self.payload
        else:
            d["error"] = self.error
        return d


@dataclass
class BackendCapabilities:
    """Declares what a backend supports."""

    can_read_drawing: bool = False
    can_modify_entities: bool = False
    can_create_entities: bool = True
    can_screenshot: bool = False
    can_save: bool = False
    can_plot_pdf: bool = False
    can_zoom: bool = False
    can_query_entities: bool = False
    can_file_operations: bool = False
    can_undo: bool = False


class AutoCADBackend(ABC):
    """Abstract interface for AutoCAD operation backends."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Backend identifier: 'file_ipc' or 'ezdxf'."""

    @property
    @abstractmethod
    def capabilities(self) -> BackendCapabilities:
        """Declare supported operations."""

    @abstractmethod
    async def initialize(self) -> CommandResult:
        """Initialize the backend. Called once at startup."""

    @abstractmethod
    async def status(self) -> CommandResult:
        """Return backend health/status info."""

    # --- Drawing management ---

    async def drawing_info(self) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def drawing_save(self, path: str | None = None) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def drawing_save_as_dxf(self, path: str) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def drawing_create(self, name: str | None = None) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def drawing_purge(self) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def drawing_plot_pdf(self, path: str) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def drawing_render_preview(
        self,
        path: str,
        paper: str = "A4",
        orientation: str = "auto",
        plot_style: str = "monochrome.ctb",
    ) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def drawing_audit(
        self,
        limit: int = 50,
        include_entities: bool = True,
        changed_only: bool = False,
        layer: str | None = None,
        space: str = "model",
    ) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def drawing_audit_dxf(
        self, path: str, limit: int = 50, include_entities: bool = True
    ) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def drawing_get_variables(self, names: list[str] | None = None) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def drawing_open(self, path: str) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def drawing_setup_mechanical(self) -> CommandResult:
        """Create the standard monochrome mechanical-drafting layers."""
        from autocad_mcp.drafting import MECHANICAL_LAYERS

        results = []
        for layer in MECHANICAL_LAYERS:
            result = await self.layer_create(**layer)
            results.append(result.to_dict())
            if not result.ok:
                return CommandResult(ok=False, payload={"layers": results}, error=result.error)
        return CommandResult(ok=True, payload={"profile": "mechanical-gbt", "layers": results})

    async def recover(self) -> CommandResult:
        return CommandResult(ok=False, error="Recovery is not supported on this backend")

    # --- Undo / Redo ---

    async def undo(self) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def redo(self) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    # --- Freehand LISP execution ---

    async def execute_lisp(self, code: str) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    # --- Entity operations ---

    async def create_line(self, x1: float, y1: float, x2: float, y2: float, layer: str | None = None) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def create_circle(self, cx: float, cy: float, radius: float, layer: str | None = None) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def create_polyline(self, points: list[list[float]], closed: bool = False, layer: str | None = None) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def create_rectangle(self, x1: float, y1: float, x2: float, y2: float, layer: str | None = None) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def create_arc(self, cx: float, cy: float, radius: float, start_angle: float, end_angle: float, layer: str | None = None) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def create_ellipse(self, cx: float, cy: float, major_x: float, major_y: float, ratio: float, layer: str | None = None) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def create_mtext(self, x: float, y: float, width: float, text: str, height: float = 2.5, layer: str | None = None) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def create_hatch(
        self,
        entity_id: str,
        pattern: str = "ANSI31",
        angle: float = 0.0,
        scale: float = 1.0,
        layer: str | None = None,
    ) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def create_batch(
        self, entities: list[dict[str, Any]], continue_on_error: bool = False
    ) -> CommandResult:
        """Create a bounded structured entity batch without arbitrary code execution."""
        if len(entities) > 500:
            return CommandResult(ok=False, error="A batch is limited to 500 entities")

        results: list[dict[str, Any]] = []
        last_handle: str | None = None
        failures = 0
        for index, item in enumerate(entities):
            kind = str(item.get("type", "")).lower()
            layer = item.get("layer")
            try:
                if kind == "line":
                    result = await self.create_line(
                        item["x1"], item["y1"], item["x2"], item["y2"], layer
                    )
                elif kind == "circle":
                    result = await self.create_circle(
                        item["cx"], item["cy"], item["radius"], layer
                    )
                elif kind == "polyline":
                    result = await self.create_polyline(
                        item["points"], item.get("closed", False), layer
                    )
                elif kind == "rectangle":
                    result = await self.create_rectangle(
                        item["x1"], item["y1"], item["x2"], item["y2"], layer
                    )
                elif kind == "arc":
                    result = await self.create_arc(
                        item["cx"],
                        item["cy"],
                        item["radius"],
                        item["start_angle"],
                        item["end_angle"],
                        layer,
                    )
                elif kind == "ellipse":
                    result = await self.create_ellipse(
                        item["cx"],
                        item["cy"],
                        item["major_x"],
                        item["major_y"],
                        item["ratio"],
                        layer,
                    )
                elif kind == "text":
                    result = await self.create_text(
                        item["x"],
                        item["y"],
                        item["text"],
                        item.get("height", 2.5),
                        item.get("rotation", 0.0),
                        layer,
                    )
                elif kind == "mtext":
                    result = await self.create_mtext(
                        item["x"],
                        item["y"],
                        item["width"],
                        item["text"],
                        item.get("height", 2.5),
                        layer,
                    )
                elif kind == "hatch":
                    entity_id = item.get("entity_id")
                    if entity_id in (None, "last", "$last"):
                        entity_id = last_handle or "last"
                    result = await self.create_hatch(
                        entity_id,
                        item.get("pattern", "ANSI31"),
                        item.get("angle", 0.0),
                        item.get("scale", 1.0),
                        layer,
                    )
                else:
                    result = CommandResult(ok=False, error=f"Unsupported batch type: {kind}")
            except (KeyError, TypeError, ValueError) as exc:
                result = CommandResult(ok=False, error=f"Invalid {kind or 'entity'}: {exc}")

            entry = {"index": index, "type": kind, **result.to_dict()}
            results.append(entry)
            if result.ok and isinstance(result.payload, dict):
                last_handle = result.payload.get("handle", last_handle)
            if not result.ok:
                failures += 1
                if not continue_on_error:
                    break

        return CommandResult(
            ok=True,
            payload={
                "batch_ok": failures == 0,
                "requested": len(entities),
                "processed": len(results),
                "failures": failures,
                "results": results,
            },
        )

    async def entity_list(self, layer: str | None = None) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def entity_count(self, layer: str | None = None) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def entity_get(self, entity_id: str) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def entity_erase(self, entity_id: str) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def entity_copy(self, entity_id: str, dx: float, dy: float) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def entity_move(self, entity_id: str, dx: float, dy: float) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def entity_rotate(self, entity_id: str, cx: float, cy: float, angle: float) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def entity_scale(self, entity_id: str, cx: float, cy: float, factor: float) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def entity_mirror(self, entity_id: str, x1: float, y1: float, x2: float, y2: float) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def entity_offset(self, entity_id: str, distance: float) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def entity_array(self, entity_id: str, rows: int, cols: int, row_dist: float, col_dist: float) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def entity_fillet(self, entity_id1: str, entity_id2: str, radius: float) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def entity_chamfer(self, entity_id1: str, entity_id2: str, dist1: float, dist2: float) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    # --- Layer operations ---

    async def layer_list(self) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def layer_create(
        self,
        name: str,
        color: str | int = "white",
        linetype: str = "CONTINUOUS",
        lineweight: str | float | int | None = None,
    ) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def layer_set_current(self, name: str) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def layer_set_properties(self, name: str, color: str | int | None = None, linetype: str | None = None, lineweight: str | None = None) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def layer_freeze(self, name: str) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def layer_thaw(self, name: str) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def layer_lock(self, name: str) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def layer_unlock(self, name: str) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    # --- Block operations ---

    async def block_list(self) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def block_insert(self, name: str, x: float, y: float, scale: float = 1.0, rotation: float = 0.0, block_id: str | None = None) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def block_insert_with_attributes(self, name: str, x: float, y: float, scale: float = 1.0, rotation: float = 0.0, attributes: dict[str, str] | None = None) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def block_get_attributes(self, entity_id: str) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def block_update_attribute(self, entity_id: str, tag: str, value: str) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def block_define(self, name: str, entities: list[dict]) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    # --- Annotation ---

    async def create_text(self, x: float, y: float, text: str, height: float = 2.5, rotation: float = 0.0, layer: str | None = None) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def create_dimension_linear(self, x1: float, y1: float, x2: float, y2: float, dim_x: float, dim_y: float) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def create_dimension_aligned(self, x1: float, y1: float, x2: float, y2: float, offset: float) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def create_dimension_angular(self, cx: float, cy: float, x1: float, y1: float, x2: float, y2: float) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def create_dimension_radius(self, cx: float, cy: float, radius: float, angle: float) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def create_leader(self, points: list[list[float]], text: str) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    # --- P&ID ---

    async def pid_setup_layers(self) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def pid_insert_symbol(self, category: str, symbol: str, x: float, y: float, scale: float = 1.0, rotation: float = 0.0) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def pid_list_symbols(self, category: str) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def pid_draw_process_line(self, x1: float, y1: float, x2: float, y2: float) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def pid_connect_equipment(self, x1: float, y1: float, x2: float, y2: float) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def pid_add_flow_arrow(self, x: float, y: float, rotation: float = 0.0) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def pid_add_equipment_tag(self, x: float, y: float, tag: str, description: str = "") -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def pid_add_line_number(self, x: float, y: float, line_num: str, spec: str) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def pid_insert_valve(self, x: float, y: float, valve_type: str, rotation: float = 0.0, attributes: dict[str, str] | None = None) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def pid_insert_instrument(self, x: float, y: float, instrument_type: str, rotation: float = 0.0, tag_id: str = "", range_value: str = "") -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def pid_insert_pump(self, x: float, y: float, pump_type: str, rotation: float = 0.0, attributes: dict[str, str] | None = None) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def pid_insert_tank(self, x: float, y: float, tank_type: str, scale: float = 1.0, attributes: dict[str, str] | None = None) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    # --- View ---

    async def zoom_extents(self) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def zoom_window(self, x1: float, y1: float, x2: float, y2: float) -> CommandResult:
        return CommandResult(ok=False, error="Not supported on this backend")

    async def get_screenshot(self) -> CommandResult:
        """Return base64 PNG in payload."""
        return CommandResult(ok=False, error="Not supported on this backend")
