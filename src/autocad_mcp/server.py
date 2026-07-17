"""AutoCAD MCP Server v3.1 — 8 consolidated tools with operation dispatch.

Tools: drawing, entity, solid, layer, block, annotation, pid, transaction, view, system
"""

from __future__ import annotations

from typing import Any

import structlog
from mcp.server.fastmcp import FastMCP

from autocad_mcp.backends.base import CommandResult
from autocad_mcp.client import (
    _error,
    _json,
    _safe,
    add_screenshot_if_available,
    ensure_backend_ready,
    get_backend,
    tool_error,
)
from autocad_mcp.contracts import build_entity_expectation
from autocad_mcp.delivery import deliver_drawing
from autocad_mcp.drafting import tangent_arc_from_start
from autocad_mcp.offline import audit_dxf_offline
from autocad_mcp.workspace import resolve_output_target, workspace_info

# FastMCP validates return types via Pydantic. Tools that may return
# ImageContent (screenshot) alongside TextContent need a union return type.
ToolResult = Any

log = structlog.get_logger()

mcp = FastMCP("autocad-mcp")


async def _guard_mutation(backend, doc_id, expected_revision) -> CommandResult:
    return await backend.require_document_context(doc_id, expected_revision)


async def _attach_document_context(
    backend, result: CommandResult, *, doc_id: str | None = None, mutated: bool = False
) -> CommandResult:
    if not result.ok:
        return result
    context = (
        await backend.record_document_mutation(doc_id)
        if mutated and doc_id
        else await backend.document_context()
    )
    if not context.ok:
        return context
    payload = result.payload if isinstance(result.payload, dict) else {"result": result.payload}
    payload.update(
        {
            "doc_id": context.payload["doc_id"],
            "active_doc_id": context.payload["active_doc_id"],
            "active_path": context.payload.get("active_path"),
            "revision": context.payload["revision"],
        }
    )
    result.payload = payload
    return result


async def _require_existing_layer(backend, layer_name: str | None) -> CommandResult:
    if not layer_name:
        return CommandResult(ok=True, payload={"exists": True, "name": "0"})
    result = await backend.layer_exists(str(layer_name))
    if not result.ok:
        return result
    if not result.payload.get("exists"):
        return CommandResult(
            ok=False,
            error=f"Layer does not exist: {layer_name}",
            error_code="E_LAYER_NOT_FOUND",
            recoverable=False,
            recommended_action="create_or_select_an_existing_layer",
            payload={"layer": str(layer_name), "entity_created": False},
        )
    return result


# ==========================================================================
# 1. drawing — File/drawing management
# ==========================================================================


@mcp.tool(annotations={"title": "AutoCAD Drawing Operations", "readOnlyHint": False})
@_safe("drawing")
async def drawing(
    operation: str,
    data: dict | None = None,
    include_screenshot: bool = False,
) -> ToolResult:
    """Drawing file management.

    Operations:
      create     — Create a new empty drawing. data: {name?}
      open       — Open an existing drawing. data: {path}
      info       — Get drawing extents, entity count, layers, blocks.
      save       — Save current drawing. data: {path?} (saves to path if given, else QSAVE)
      save_as_dxf — Export as DXF. data: {path}
      plot_pdf   — Plot to PDF. data: {path}
      render_preview — Native deterministic preview. data: {path, paper?, orientation?, plot_style?}
      workspace  — Show the managed output workspace and folder layout.
      deliver    — Build a validated DWG/DXF/PDF job with audits and SHA-256 checksums.
      audit      — Structured drawing audit. data: {limit?, include_entities?, changed_only?, layer?, space?}
      audit_dxf  — Parse an existing DXF into normalized JSON. data: {path, limit?, include_entities?}
      setup_mechanical — Create the seven monochrome GB/T mechanical-drafting layers.
      purge      — Purge unused objects.
      get_variables — Get system variables. data: {names: [...]}
      set_variables — Safely set whitelisted system variables. data: {values: {...}}
      audit_geometry — Run line/polyline geometry DRC and return structured findings.
      undo       — Undo last operation.
      redo       — Redo last undone operation.
    """
    data = data or {}
    if operation == "workspace":
        return _json({"ok": True, "payload": workspace_info()})
    if operation == "audit_dxf":
        return await add_screenshot_if_available(audit_dxf_offline(data), False)

    backend = await get_backend()

    context_required = {
        "save", "save_as_dxf", "plot_pdf", "render_preview", "deliver",
        "setup_mechanical", "purge", "set_variables", "undo", "redo",
    }
    mutation_operations = {
        "setup_mechanical", "purge", "set_variables", "undo", "redo",
    }
    if operation in context_required:
        guard = await _guard_mutation(
            backend, data.get("doc_id"), data.get("expected_revision")
        )
        if not guard.ok:
            return await add_screenshot_if_available(guard, False)

    if operation == "create":
        requested_name = data.get("name")
        if requested_name:
            category = "drawings" if backend.name == "file_ipc" else "dxf"
            extension = ".dwg" if backend.name == "file_ipc" else ".dxf"
            target = resolve_output_target(
                data.get("path"),
                category=category,
                extension=extension,
                default_stem=str(requested_name),
            )
            result = await backend.drawing_create(str(target.path))
        else:
            result = await backend.drawing_create(None)
    elif operation == "info":
        result = await backend.drawing_info()
    elif operation == "context":
        result = await backend.document_context()
    elif operation == "activate":
        result = await backend.drawing_activate(data.get("doc_id"))
    elif operation == "save":
        category = "drawings" if backend.name == "file_ipc" else "dxf"
        extension = ".dwg" if backend.name == "file_ipc" else ".dxf"
        target = resolve_output_target(
            data.get("path"),
            category=category,
            extension=extension,
            default_stem=data.get("name", "drawing"),
        )
        result = await backend.drawing_save(str(target.path))
    elif operation == "save_as_dxf":
        target = resolve_output_target(
            data.get("path"),
            category="dxf",
            extension=".dxf",
            default_stem=data.get("name", "drawing"),
        )
        result = await backend.drawing_save_as_dxf(str(target.path))
    elif operation == "plot_pdf":
        scale_mode = str(data.get("scale_mode", "fit")).lower()
        declared_scale = data.get("declared_scale")
        if declared_scale is not None and scale_mode == "fit" and str(declared_scale).upper() not in {"FIT", "NTS"}:
            return tool_error(
                "A fit-to-extents PDF cannot declare a fixed drawing scale",
                code="E_PLOT_SCALE_MISMATCH",
                recommended_action="use_declared_scale_fit_or_nts",
            )
        target = resolve_output_target(
            data.get("path"),
            category="pdf",
            extension=".pdf",
            default_stem=data.get("name", "drawing"),
        )
        result = await backend.drawing_plot_pdf(
            str(target.path),
            data.get("paper", "A3"),
            data.get("orientation", "landscape"),
            data.get("plot_style", "monochrome.ctb"),
            scale_mode,
            data.get("scale", "1:1"),
            data.get("center", True),
        )
    elif operation == "render_preview":
        target = resolve_output_target(
            data.get("path"),
            category="previews",
            extension=".png",
            default_stem=data.get("name", "preview"),
        )
        result = await backend.drawing_render_preview(
            str(target.path),
            data.get("paper", "A4"),
            data.get("orientation", "auto"),
            data.get("plot_style", "monochrome.ctb"),
            data.get("dpi", 150),
            data.get("force", True),
            data.get("background", "white"),
        )
    elif operation == "deliver":
        result = await deliver_drawing(backend, data)
    elif operation in ("audit", "audit_geometry"):
        result = await backend.drawing_audit(
            data.get("limit", 50),
            data.get("include_entities", True),
            data.get("changed_only", False),
            data.get("layer"),
            data.get("space", "model"),
            data.get("rules"),
        )
    elif operation == "setup_mechanical":
        result = await backend.drawing_setup_mechanical(data)
    elif operation == "purge":
        result = await backend.drawing_purge()
    elif operation == "get_variables":
        result = await backend.drawing_get_variables(data.get("names"))
    elif operation == "set_variables":
        result = await backend.drawing_set_variables(data.get("values") or data)
    elif operation == "open":
        result = await backend.drawing_open(data["path"])
    elif operation == "undo":
        result = await backend.undo()
    elif operation == "redo":
        result = await backend.redo()
    else:
        return tool_error(
            f"Unknown drawing operation: {operation}", code="E_UNSUPPORTED_OPERATION"
        )

    if operation in mutation_operations:
        result = await _attach_document_context(
            backend, result, doc_id=data.get("doc_id"), mutated=True
        )
    elif operation in context_required:
        result = await _attach_document_context(backend, result)
    return await add_screenshot_if_available(result, include_screenshot)


# ==========================================================================
# 2. entity — Entity CRUD + modification
# ==========================================================================


@mcp.tool(annotations={"title": "AutoCAD Entity Operations", "readOnlyHint": False})
@_safe("entity")
async def entity(
    operation: str,
    doc_id: str | None = None,
    expected_revision: int | None = None,
    x1: float | None = None,
    y1: float | None = None,
    x2: float | None = None,
    y2: float | None = None,
    points: list[list[float]] | None = None,
    layer: str | None = None,
    entity_id: str | None = None,
    data: dict | None = None,
    strict: bool = True,
    include_screenshot: bool = False,
) -> ToolResult:
    """Entity creation, querying, and modification.

    Create operations:
      create_line       — x1, y1, x2, y2, layer?
      create_circle     — data: {cx, cy, radius}, layer?
      create_polyline   — points: [[x,y],...], data: {closed?}, layer?
      create_rectangle  — x1, y1, x2, y2, layer?
      create_arc        — data: {cx, cy, radius, start_angle, end_angle}, layer?
      create_ellipse    — data: {cx, cy, major_x, major_y, ratio}, layer?
      create_mtext      — data: {x, y, width, text, height?}, layer?
      create_hatch      — entity_id, data: {pattern?, angle?, scale?, layer?}
      create_batch      — data: {entities: [{type, ...}], continue_on_error?}

    Read operations:
      list              — layer? → list entities
      count             — layer? → count entities
      get               — entity_id → entity details

    Modify operations:
      copy    — entity_id, data: {dx, dy}
      move    — entity_id, data: {dx, dy}
      rotate  — entity_id, data: {cx, cy, angle}
      scale   — entity_id, data: {cx, cy, factor}
      mirror  — entity_id, x1, y1, x2, y2
      offset  — entity_id, data: {distance}
      array   — entity_id, data: {rows, cols, row_dist, col_dist}
      fillet  — data: {id1, id2, radius}
      chamfer — data: {id1, id2, dist1, dist2}
      erase   — entity_id
    """
    data = data or {}
    backend = await get_backend()
    mutating = operation not in {"list", "count", "get"}
    if mutating:
        guard = await _guard_mutation(backend, doc_id, expected_revision)
        if not guard.ok:
            return await add_screenshot_if_available(guard, False)

    expectation = None
    create_kind = operation.removeprefix("create_")
    if operation in {
        "create_line", "create_circle", "create_polyline", "create_rectangle",
        "create_arc", "create_ellipse", "create_mtext", "create_text",
    }:
        layer_check = await _require_existing_layer(backend, layer)
        if not layer_check.ok:
            return await add_screenshot_if_available(layer_check, False)
        params = dict(data)
        if operation in {"create_line", "create_rectangle"}:
            params.update(x1=x1, y1=y1, x2=x2, y2=y2)
        elif operation == "create_polyline":
            params["points"] = points
        try:
            expectation = build_entity_expectation(
                create_kind, params, layer=layer, strict=bool(strict)
            )
        except (TypeError, ValueError) as exc:
            return tool_error(
                str(exc),
                code="E_PARAMETER_REJECTED",
                recommended_action="correct_request_fields",
            )

    # --- Create ---
    if operation == "create_line":
        result = await backend.create_line(x1, y1, x2, y2, layer)
    elif operation == "create_circle":
        result = await backend.create_circle(data["cx"], data["cy"], data["radius"], layer)
    elif operation == "create_polyline":
        result = await backend.create_polyline(points or [], data.get("closed", False), layer)
    elif operation == "create_rectangle":
        result = await backend.create_rectangle(x1, y1, x2, y2, layer)
    elif operation == "create_arc":
        result = await backend.create_arc(data["cx"], data["cy"], data["radius"], data["start_angle"], data["end_angle"], layer)
    elif operation == "create_tangent_arc":
        geometry = tangent_arc_from_start(data["start"], data["end"], data["tangent"])
        result = await backend.create_arc(
            geometry["center"][0],
            geometry["center"][1],
            geometry["radius"],
            geometry["start_angle"],
            geometry["end_angle"],
            layer,
        )
        if result.ok and isinstance(result.payload, dict):
            result.payload["tangent_geometry"] = geometry
    elif operation == "create_ellipse":
        result = await backend.create_ellipse(data["cx"], data["cy"], data["major_x"], data["major_y"], data["ratio"], layer)
    elif operation == "create_mtext":
        result = await backend.create_mtext(data["x"], data["y"], data["width"], data["text"], data.get("height", 2.5), layer)
    elif operation == "create_text":
        result = await backend.create_text(
            data["x"], data["y"], data["text"], data.get("height", 2.5),
            data.get("rotation", 0.0), layer,
        )
    elif operation == "create_hatch":
        result = await backend.create_hatch(
            entity_id,
            data.get("pattern", "ANSI31"),
            data.get("angle", 0.0),
            data.get("scale", 1.0),
            data.get("layer"),
        )
    elif operation == "create_batch":
        result = await backend.create_batch(
            data.get("entities", []),
            data.get("continue_on_error", False),
            data.get("atomic", True),
            data.get("strict", strict),
        )
    # --- Read ---
    elif operation == "list":
        result = await backend.entity_list(layer)
    elif operation == "count":
        result = await backend.entity_count(layer)
    elif operation == "get":
        result = await backend.entity_get_with_semantics(entity_id)
    # --- Modify ---
    elif operation == "copy":
        result = await backend.entity_copy(entity_id, data["dx"], data["dy"])
    elif operation == "move":
        result = await backend.entity_move(entity_id, data["dx"], data["dy"])
    elif operation == "rotate":
        result = await backend.entity_rotate(entity_id, data["cx"], data["cy"], data["angle"])
    elif operation == "scale":
        result = await backend.entity_scale(entity_id, data["cx"], data["cy"], data["factor"])
    elif operation == "mirror":
        result = await backend.entity_mirror(entity_id, x1, y1, x2, y2)
    elif operation == "offset":
        result = await backend.entity_offset(entity_id, data["distance"])
    elif operation == "array":
        result = await backend.entity_array(entity_id, data["rows"], data["cols"], data["row_dist"], data["col_dist"])
    elif operation == "fillet":
        result = await backend.entity_fillet(data["id1"], data["id2"], data["radius"])
    elif operation == "chamfer":
        result = await backend.entity_chamfer(data["id1"], data["id2"], data["dist1"], data["dist2"])
    elif operation == "trim":
        result = await backend.entity_trim(data.get("cutters", []), data.get("targets", []))
    elif operation == "extend":
        result = await backend.entity_extend(data.get("boundaries", []), data.get("targets", []))
    elif operation == "break":
        result = await backend.entity_break(entity_id, data["point1"], data["point2"])
    elif operation == "join":
        result = await backend.entity_join(data.get("entity_ids", []), data.get("tolerance", 0.0))
    elif operation == "constrain":
        result = await backend.entity_constrain(data["constraint"], data.get("entity_ids", []))
    elif operation == "erase":
        result = await backend.entity_erase(entity_id)
    else:
        return tool_error(f"Unknown entity operation: {operation}", code="E_UNSUPPORTED_OPERATION")

    if expectation is not None:
        result = await backend.verify_created_entity(expectation, result)

    if mutating:
        result = await _attach_document_context(
            backend, result, doc_id=doc_id, mutated=True
        )

    return await add_screenshot_if_available(result, include_screenshot)


# ==========================================================================
# 3. layer — Layer management
# ==========================================================================


@mcp.tool(annotations={"title": "AutoCAD Layer Operations", "readOnlyHint": False})
@_safe("layer")
async def layer(
    operation: str,
    doc_id: str | None = None,
    expected_revision: int | None = None,
    data: dict | None = None,
    include_screenshot: bool = False,
) -> ToolResult:
    """Layer creation and management.

    Operations:
      list            — List all layers with properties.
      create          — data: {name, color?, linetype?, lineweight?}
      set_current     — data: {name}
      set_properties  — data: {name, color?, linetype?, lineweight?}
      freeze          — data: {name}
      thaw            — data: {name}
      lock            — data: {name}
      unlock          — data: {name}
    """
    data = data or {}
    backend = await get_backend()
    mutating = operation != "list"
    if mutating:
        guard = await _guard_mutation(backend, doc_id, expected_revision)
        if not guard.ok:
            return await add_screenshot_if_available(guard, False)

    if operation == "list":
        result = await backend.layer_list()
    elif operation == "create":
        result = await backend.layer_create(
            data["name"],
            data.get("color", "white"),
            data.get("linetype", "CONTINUOUS"),
            data.get("lineweight"),
        )
    elif operation == "set_current":
        result = await backend.layer_set_current(data["name"])
    elif operation == "set_properties":
        result = await backend.layer_set_properties(data["name"], data.get("color"), data.get("linetype"), data.get("lineweight"))
    elif operation == "freeze":
        result = await backend.layer_freeze(data["name"])
    elif operation == "thaw":
        result = await backend.layer_thaw(data["name"])
    elif operation == "lock":
        result = await backend.layer_lock(data["name"])
    elif operation == "unlock":
        result = await backend.layer_unlock(data["name"])
    else:
        return tool_error(f"Unknown layer operation: {operation}", code="E_UNSUPPORTED_OPERATION")

    if mutating:
        result = await _attach_document_context(backend, result, doc_id=doc_id, mutated=True)
    return await add_screenshot_if_available(result, include_screenshot)


# ==========================================================================
# 4. block — Block operations
# ==========================================================================


@mcp.tool(annotations={"title": "AutoCAD Block Operations", "readOnlyHint": False})
@_safe("block")
async def block(
    operation: str,
    doc_id: str | None = None,
    expected_revision: int | None = None,
    data: dict | None = None,
    include_screenshot: bool = False,
) -> ToolResult:
    """Block definition, insertion, and attribute management.

    Operations:
      list                 — List all block definitions.
      insert               — data: {name, x, y, scale?, rotation?, block_id?}
      insert_with_attributes — data: {name, x, y, scale?, rotation?, attributes: {tag: value}}
      get_attributes       — data: {entity_id}
      update_attribute     — data: {entity_id, tag, value}
      define               — data: {name, entities: [{type, ...}]}
    """
    data = data or {}
    backend = await get_backend()
    mutating = operation not in {"list", "get_attributes"}
    if mutating:
        guard = await _guard_mutation(backend, doc_id, expected_revision)
        if not guard.ok:
            return await add_screenshot_if_available(guard, False)

    if operation == "list":
        result = await backend.block_list()
    elif operation == "insert":
        result = await backend.block_insert(
            data["name"], data["x"], data["y"],
            data.get("scale", 1.0), data.get("rotation", 0.0), data.get("block_id"),
        )
    elif operation == "insert_with_attributes":
        result = await backend.block_insert_with_attributes(
            data["name"], data["x"], data["y"],
            data.get("scale", 1.0), data.get("rotation", 0.0), data.get("attributes"),
        )
    elif operation == "get_attributes":
        result = await backend.block_get_attributes(data["entity_id"])
    elif operation == "update_attribute":
        result = await backend.block_update_attribute(data["entity_id"], data["tag"], data["value"])
    elif operation == "define":
        result = await backend.block_define(data["name"], data.get("entities", []))
    else:
        return tool_error(f"Unknown block operation: {operation}", code="E_UNSUPPORTED_OPERATION")

    if mutating:
        result = await _attach_document_context(backend, result, doc_id=doc_id, mutated=True)
    return await add_screenshot_if_available(result, include_screenshot)


# ==========================================================================
# 5. annotation — Text, dimensions, leaders
# ==========================================================================


@mcp.tool(annotations={"title": "AutoCAD Annotation Operations", "readOnlyHint": False})
@_safe("annotation")
async def annotation(
    operation: str,
    doc_id: str | None = None,
    expected_revision: int | None = None,
    data: dict | None = None,
    include_screenshot: bool = False,
) -> ToolResult:
    """Annotation: text, dimensions, and leaders.

    Operations:
      create_text             — data: {x, y, text, height?, rotation?, layer?}
      create_dimension_linear — data: {x1, y1, x2, y2, dim_x, dim_y}
      create_dimension_aligned — data: {x1, y1, x2, y2, offset}
      create_dimension_angular — data: {cx, cy, x1, y1, x2, y2}
      create_dimension_radius — data: {cx, cy, radius, angle}
      create_leader           — data: {points: [[x,y],...], text}
    """
    data = data or {}
    backend = await get_backend()
    guard = await _guard_mutation(backend, doc_id, expected_revision)
    if not guard.ok:
        return await add_screenshot_if_available(guard, False)
    layer_check = await _require_existing_layer(backend, data.get("layer"))
    if not layer_check.ok:
        return await add_screenshot_if_available(layer_check, False)

    if operation == "create_text":
        result = await backend.create_text(
            data["x"], data["y"], data["text"],
            data.get("height", 2.5), data.get("rotation", 0.0), data.get("layer"),
        )
    elif operation == "create_dimension_linear":
        result = await backend.create_dimension_linear(
            data["x1"], data["y1"], data["x2"], data["y2"], data["dim_x"], data["dim_y"],
        )
    elif operation == "create_dimension_aligned":
        result = await backend.create_dimension_aligned(
            data["x1"], data["y1"], data["x2"], data["y2"], data["offset"],
        )
    elif operation == "create_dimension_angular":
        result = await backend.create_dimension_angular(
            data["cx"], data["cy"], data["x1"], data["y1"], data["x2"], data["y2"],
        )
    elif operation == "create_dimension_radius":
        result = await backend.create_dimension_radius(
            data["cx"], data["cy"], data["radius"], data["angle"],
        )
    elif operation == "create_leader":
        result = await backend.create_leader(data["points"], data["text"])
    else:
        return tool_error(
            f"Unknown annotation operation: {operation}", code="E_UNSUPPORTED_OPERATION"
        )

    result = await _attach_document_context(backend, result, doc_id=doc_id, mutated=True)
    return await add_screenshot_if_available(result, include_screenshot)


# ==========================================================================
# 6. pid — P&ID operations (CTO library)
# ==========================================================================


@mcp.tool(annotations={"title": "AutoCAD Native 3D Solids", "readOnlyHint": False})
@_safe("solid")
async def solid(
    operation: str,
    doc_id: str | None = None,
    expected_revision: int | None = None,
    data: dict | None = None,
    include_screenshot: bool = False,
) -> ToolResult:
    """Create and combine native AutoCAD 3D solids through the safe COM API.

    Operations:
      create_box      - {center: [x,y,z], length, width, height, layer?}
      create_cylinder - {base_center: [x,y,z], radius, height, layer?}
      extrude         - {profile_id, height, taper_angle?, erase_profile?, layer?}
      revolve         - {profile_id, axis_point, axis_direction, angle?, erase_profile?, layer?}
      sweep           - {profile_id, path_id, erase_profile?, layer?}
      boolean         - {primary_id, tool_id, operation: union|intersection|subtract}

    Edge fillets/chamfers and projected drawing views are intentionally not advertised
    until their native AutoCAD workflows can be made deterministic.
    """
    data = data or {}
    backend = await get_backend()
    guard = await _guard_mutation(backend, doc_id, expected_revision)
    if not guard.ok:
        return await add_screenshot_if_available(guard, False)
    layer_check = await _require_existing_layer(backend, data.get("layer"))
    if not layer_check.ok:
        return await add_screenshot_if_available(layer_check, False)

    if operation == "create_box":
        result = await backend.solid_create_box(
            data.get("center", data.get("origin", [0, 0, 0])),
            data["length"], data["width"], data["height"], data.get("layer")
        )
    elif operation == "create_cylinder":
        result = await backend.solid_create_cylinder(
            data.get("base_center", data.get("center", [0, 0, 0])),
            data["radius"], data["height"], data.get("layer")
        )
    elif operation == "extrude":
        result = await backend.solid_extrude(
            data["profile_id"], data["height"], data.get("taper_angle", 0.0),
            data.get("erase_profile", False), data.get("layer"),
        )
    elif operation == "revolve":
        result = await backend.solid_revolve(
            data["profile_id"], data["axis_point"], data["axis_direction"], data.get("angle", 360.0),
            data.get("erase_profile", False), data.get("layer"),
        )
    elif operation == "sweep":
        result = await backend.solid_sweep(
            data["profile_id"], data["path_id"], data.get("erase_profile", False), data.get("layer")
        )
    elif operation == "boolean":
        result = await backend.solid_boolean(data["primary_id"], data["tool_id"], data["operation"])
    else:
        return tool_error(f"Unknown solid operation: {operation}", code="E_UNSUPPORTED_OPERATION")

    result = await _attach_document_context(backend, result, doc_id=doc_id, mutated=True)
    return await add_screenshot_if_available(result, include_screenshot)


@mcp.tool(annotations={"title": "P&ID Operations (CTO Library)", "readOnlyHint": False})
@_safe("pid")
async def pid(
    operation: str,
    doc_id: str | None = None,
    expected_revision: int | None = None,
    data: dict | None = None,
    include_screenshot: bool = False,
) -> ToolResult:
    """P&ID drawing with CTO symbol library.

    Operations:
      setup_layers     — Create standard P&ID layers.
      insert_symbol    — data: {category, symbol, x, y, scale?, rotation?}
      list_symbols     — data: {category}
      draw_process_line — data: {x1, y1, x2, y2}
      connect_equipment — data: {x1, y1, x2, y2}
      add_flow_arrow   — data: {x, y, rotation?}
      add_equipment_tag — data: {x, y, tag, description?}
      add_line_number  — data: {x, y, line_num, spec}
      insert_valve     — data: {x, y, valve_type, rotation?, attributes?}
      insert_instrument — data: {x, y, instrument_type, rotation?, tag_id?, range_value?}
      insert_pump      — data: {x, y, pump_type, rotation?, attributes?}
      insert_tank      — data: {x, y, tank_type, scale?, attributes?}
    """
    data = data or {}
    backend = await get_backend()
    mutating = operation != "list_symbols"
    if mutating:
        guard = await _guard_mutation(backend, doc_id, expected_revision)
        if not guard.ok:
            return await add_screenshot_if_available(guard, False)

    if operation == "setup_layers":
        result = await backend.pid_setup_layers()
    elif operation == "insert_symbol":
        result = await backend.pid_insert_symbol(
            data["category"], data["symbol"], data["x"], data["y"],
            data.get("scale", 1.0), data.get("rotation", 0.0),
        )
    elif operation == "list_symbols":
        result = await backend.pid_list_symbols(data["category"])
    elif operation == "draw_process_line":
        result = await backend.pid_draw_process_line(data["x1"], data["y1"], data["x2"], data["y2"])
    elif operation == "connect_equipment":
        result = await backend.pid_connect_equipment(data["x1"], data["y1"], data["x2"], data["y2"])
    elif operation == "add_flow_arrow":
        result = await backend.pid_add_flow_arrow(data["x"], data["y"], data.get("rotation", 0.0))
    elif operation == "add_equipment_tag":
        result = await backend.pid_add_equipment_tag(data["x"], data["y"], data["tag"], data.get("description", ""))
    elif operation == "add_line_number":
        result = await backend.pid_add_line_number(data["x"], data["y"], data["line_num"], data["spec"])
    elif operation == "insert_valve":
        result = await backend.pid_insert_valve(
            data["x"], data["y"], data["valve_type"],
            data.get("rotation", 0.0), data.get("attributes"),
        )
    elif operation == "insert_instrument":
        result = await backend.pid_insert_instrument(
            data["x"], data["y"], data["instrument_type"],
            data.get("rotation", 0.0), data.get("tag_id", ""), data.get("range_value", ""),
        )
    elif operation == "insert_pump":
        result = await backend.pid_insert_pump(
            data["x"], data["y"], data["pump_type"],
            data.get("rotation", 0.0), data.get("attributes"),
        )
    elif operation == "insert_tank":
        result = await backend.pid_insert_tank(
            data["x"], data["y"], data["tank_type"],
            data.get("scale", 1.0), data.get("attributes"),
        )
    else:
        return tool_error(f"Unknown pid operation: {operation}", code="E_UNSUPPORTED_OPERATION")

    if mutating:
        result = await _attach_document_context(backend, result, doc_id=doc_id, mutated=True)
    return await add_screenshot_if_available(result, include_screenshot)


# ==========================================================================
# 7. view — Viewport and screenshot
# ==========================================================================


@mcp.tool(annotations={"title": "AutoCAD Transactions", "readOnlyHint": False})
@_safe("transaction")
async def transaction(
    operation: str,
    doc_id: str,
    expected_revision: int,
    transaction_id: str | None = None,
) -> ToolResult:
    """Begin, commit, or roll back a document-scoped AutoCAD undo transaction."""
    backend = await get_backend()
    if operation == "begin":
        result = await backend.transaction_begin(doc_id, expected_revision)
    elif operation == "commit":
        result = await backend.transaction_commit(
            transaction_id, doc_id, expected_revision
        )
    elif operation == "rollback":
        result = await backend.transaction_rollback(
            transaction_id, doc_id, expected_revision
        )
    else:
        return tool_error(
            f"Unknown transaction operation: {operation}",
            code="E_UNSUPPORTED_OPERATION",
        )
    return await add_screenshot_if_available(result, False)


@mcp.tool(annotations={"title": "AutoCAD View Operations", "readOnlyHint": True})
@_safe("view")
async def view(
    operation: str,
    x1: float | None = None,
    y1: float | None = None,
    x2: float | None = None,
    y2: float | None = None,
) -> ToolResult:
    """Viewport control and diagnostic window capture.

    Operations:
      zoom_extents   — Zoom to show all entities.
      fit_drawing    — Center and fit all drawing geometry in the viewport.
      zoom_window    — Zoom to window: x1, y1, x2, y2
      show_window    — Restore and activate the AutoCAD window.
      get_screenshot — Diagnostic-only window capture. Prefer drawing.render_preview.
    """
    backend = await get_backend()

    if operation in ("zoom_extents", "fit_drawing"):
        result = await backend.zoom_extents()
        return _json(result.to_dict())
    elif operation == "zoom_window":
        result = await backend.zoom_window(x1, y1, x2, y2)
        return _json(result.to_dict())
    elif operation == "show_window":
        result = await backend.show_window(activate=True)
        return _json(result.to_dict())
    elif operation == "minimize_window":
        result = await backend.minimize_window()
        return _json(result.to_dict())
    elif operation == "get_screenshot":
        result = await backend.get_screenshot()
        if result.ok and result.payload:
            from mcp.types import ImageContent, TextContent

            return [
                TextContent(type="text", text=_json({"ok": True, "screenshot": "attached"})),
                ImageContent(type="image", data=result.payload, mimeType="image/png"),
            ]
        return _json(result.to_dict())
    else:
        return tool_error(f"Unknown view operation: {operation}", code="E_UNSUPPORTED_OPERATION")


# ==========================================================================
# 8. system — Server management
# ==========================================================================


@mcp.tool(annotations={"title": "AutoCAD MCP System", "readOnlyHint": True})
@_safe("system")
async def system(
    operation: str,
    data: dict | None = None,
    include_screenshot: bool = False,
) -> ToolResult:
    """Server status and management.

    Operations:
      status        — Backend info, capabilities, health check.
      ensure_ready  — Discover/start AutoCAD, open a document, load/version-check dispatcher, ping IPC.
      health        — Quick health check (ping backend).
      get_backend   — Return current backend name and capabilities.
      runtime       — Return process/runtime details for spawn diagnostics.
      init          — Re-initialize the backend.
      execute_lisp  — Execute arbitrary AutoLISP code (File IPC only). data: {code}
      recover       — Cancel a stuck AutoCAD command and clear stale IPC state.
    """
    data = data or {}

    if operation == "status":
        from autocad_mcp import client

        if client._backend is None:
            import os

            return _json(
                {
                    "ok": True,
                    "payload": {
                        "initialized": False,
                        "ready": False,
                        "backend_env": os.environ.get("AUTOCAD_MCP_BACKEND", "auto"),
                        "recommended_action": "system.ensure_ready",
                    },
                }
            )
        result = await client._backend.status()
        return await add_screenshot_if_available(result, include_screenshot)
    elif operation == "get_backend":
        backend = await get_backend()
        result = await backend.status()
        return await add_screenshot_if_available(result, include_screenshot)
    elif operation == "ensure_ready":
        result = await ensure_backend_ready()
        return await add_screenshot_if_available(result, include_screenshot)
    elif operation == "health":
        from autocad_mcp import client

        if client._backend is None:
            return tool_error(
                "Backend is not initialized",
                code="E_AUTOCAD_NOT_RUNNING",
                recommended_action="system.ensure_ready",
            )
        result = await client._backend.status()
        return await add_screenshot_if_available(result, False)
    elif operation == "runtime":
        import os
        import sys

        return _json(
            {
                "ok": True,
                "platform": sys.platform,
                "python": sys.executable,
                "cwd": os.getcwd(),
                "backend_env": os.environ.get("AUTOCAD_MCP_BACKEND", "auto"),
                "wsl_interop": bool(os.environ.get("WSL_INTEROP")),
            }
        )
    elif operation == "init":
        # Force re-initialization
        from autocad_mcp import client
        client._backend = None
        result = await ensure_backend_ready()
        return await add_screenshot_if_available(result, include_screenshot)
    elif operation == "recover":
        backend = await get_backend()
        result = await backend.recover()
        return _json(result.to_dict())
    elif operation == "execute_lisp":
        import os

        if os.environ.get("AUTOCAD_MCP_ALLOW_ARBITRARY_LISP", "").lower() not in (
            "1",
            "true",
            "yes",
        ):
            return tool_error(
                "Arbitrary AutoLISP execution is disabled. Set "
                "AUTOCAD_MCP_ALLOW_ARBITRARY_LISP=true to enable it.",
                code="E_UNSUPPORTED_OPERATION",
            )
        backend = await get_backend()
        if not data.get("code"):
            return tool_error("data.code is required", code="E_UNSUPPORTED_OPERATION")
        result = await backend.execute_lisp(data["code"])
        return await add_screenshot_if_available(result, include_screenshot)
    else:
        return tool_error(f"Unknown system operation: {operation}", code="E_UNSUPPORTED_OPERATION")


# ==========================================================================
# Main entry point
# ==========================================================================


def main():
    """Run the MCP server on stdio transport."""
    # Load NumPy-backed ezdxf modules before AnyIO starts worker threads.
    # Late native-module imports can stall on some Windows Python runtimes.
    import ezdxf  # noqa: F401

    from autocad_mcp.logging_setup import configure_logging

    log_path = configure_logging()

    from autocad_mcp import __version__

    log.info("autocad_mcp_starting", version=__version__, log_path=str(log_path))
    mcp.run(transport="stdio")
