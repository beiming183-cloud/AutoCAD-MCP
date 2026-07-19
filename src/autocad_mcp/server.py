"""AutoCAD MCP Server v4.0 — consolidated tools with operation dispatch.

Tools: drawing, entity, solid, product, layer, block, annotation, pid,
transaction, view, job, and system.
"""

from __future__ import annotations

import copy
import math
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
    request_admission_snapshot,
    reset_backend,
    _screenshot_result,
    tool_error,
)
from autocad_mcp.contracts import build_entity_expectation
from autocad_mcp.delivery import deliver_drawing
from autocad_mcp.drafting import tangent_arc_from_start
from autocad_mcp.journal import OperationJournal
from autocad_mcp.offline import audit_dxf_offline
from autocad_mcp.plot_contract import normalize_plot_scale
from autocad_mcp.product_design import (
    VIEW_NAMES,
    clearance_sweep,
    get_feature,
    image_content_metrics,
    image_difference,
    interference_sample,
    list_features,
    mark_feature_handles_invalid,
    mark_feature_replaced,
    measure_registered_feature,
    product_state,
    query_edges_by_semantic_role,
    register_feature,
    review_summary,
    set_motion,
    set_review,
)
from autocad_mcp.workspace import (
    cleanup_test_job_artifacts,
    resolve_output_target,
    workspace_info,
)
from autocad_mcp.visual_styles import (
    SUPPORTED_VISUAL_STYLES,
    normalize_color_map,
    normalize_visual_style,
    style_readback,
)

# FastMCP validates return types via Pydantic. Tools that may return
# ImageContent (screenshot) alongside TextContent need a union return type.
ToolResult = Any

log = structlog.get_logger()

mcp = FastMCP("autocad-mcp")
_journal: OperationJournal | None = None


def _operation_journal() -> OperationJournal:
    global _journal
    if _journal is None:
        _journal = OperationJournal()
    return _journal


def _result_from_journal(record: dict[str, Any]) -> CommandResult:
    if record.get("state") == "failed":
        stored_failure = record.get("error") or {}
        error = stored_failure.get("error") or {}
        # ``CommandResult.to_dict`` stores failed-operation evidence under
        # ``details`` (the original payload).  Older journal records may use
        # a top-level ``payload`` instead, so replay both shapes without
        # dropping handles/context needed for reconciliation.
        details = stored_failure.get("details")
        if isinstance(details, dict):
            payload = dict(details)
        elif isinstance(stored_failure.get("payload"), dict):
            payload = dict(stored_failure["payload"])
        else:
            payload = {"details": details}
        payload.update(
            idempotent_replay=True,
            journal_state="failed",
            request_hash=record.get("request_hash"),
        )
        return CommandResult(
            ok=False,
            error=error.get("message", "Previously attempted operation failed"),
            error_code=error.get("code", "E_IDEMPOTENT_REPLAY_FAILED"),
            recoverable=error.get("recoverable", False),
            recommended_action=error.get("recommended_action"),
            payload=payload,
        )
    stored = record.get("result") or {}
    if stored.get("ok"):
        payload = stored.get("payload")
        if isinstance(payload, dict):
            payload = {**payload, "idempotent_replay": True}
        return CommandResult(ok=True, payload=payload)
    error = stored.get("error") or {}
    return CommandResult(
        ok=False,
        error=error.get("message", "Previously committed operation failed"),
        error_code=error.get("code", "E_IDEMPOTENT_REPLAY_FAILED"),
        recoverable=error.get("recoverable"),
        recommended_action=error.get("recommended_action"),
        payload={"idempotent_replay": True, "journal": record},
    )


def _begin_journaled_mutation(
    idempotency_key: str | None,
    *,
    operation: str,
    request: dict[str, Any],
    context: dict[str, Any] | None,
) -> CommandResult | None:
    if not idempotency_key:
        return None
    try:
        decision = _operation_journal().begin(
            idempotency_key,
            operation=operation,
            request=request,
            context=context,
        )
    except Exception as exc:
        # A journal write must never escape through the MCP handler and look
        # like an app-server crash.  Refuse the mutation because executing
        # without durable idempotency evidence would make a retry unsafe.
        return CommandResult(
            ok=False,
            error=f"Unable to open the idempotency journal: {exc}",
            error_code="E_IDEMPOTENCY_JOURNAL_UNAVAILABLE",
            recoverable=False,
            recommended_action="repair_the_managed_output_workspace_and_retry_with_the_same_idempotency_key",
            payload={
                "idempotency_key": str(idempotency_key),
                "operation": operation,
                "journal_exception_type": type(exc).__name__,
            },
        )
    if decision.action == "execute":
        return None
    if decision.action == "replay":
        return _result_from_journal(decision.record)
    if decision.action == "conflict":
        return CommandResult(
            ok=False,
            error="The idempotency key was already used for a different request",
            error_code="E_IDEMPOTENCY_CONFLICT",
            recoverable=False,
            payload={"idempotency_key": idempotency_key, "journal": decision.record},
        )
    return CommandResult(
        ok=False,
        error="An operation with this idempotency key is already in progress",
        error_code="E_OPERATION_IN_PROGRESS",
        recoverable=True,
        recommended_action="query_the_operation_journal_before_retrying",
        payload={"idempotency_key": idempotency_key, "journal": decision.record},
    )


def _finish_journaled_mutation(
    idempotency_key: str | None, result: CommandResult
) -> CommandResult:
    if not idempotency_key:
        return result
    # ``CommandResult.to_dict`` intentionally keeps the payload object by
    # reference.  Keep a deep snapshot before adding reconciliation metadata;
    # otherwise the error path would mutate the original result while claiming
    # to preserve it.
    original = copy.deepcopy(result.to_dict())
    journal: OperationJournal | None = None
    try:
        # Capture one journal instance for both finalization and recovery.  A
        # singleton lookup can itself be affected by a workspace/permission
        # failure; calling it again in the recovery path could target a
        # different journal (or raise a second exception).
        journal = _operation_journal()
        if result.ok:
            record = journal.commit(idempotency_key, original)
        else:
            record = journal.fail(
                idempotency_key,
                original,
                retryable=bool(result.recoverable),
            )
    except Exception as exc:
        # Do not let a permissions/locking failure in the journal bubble out
        # after CAD has already mutated the document.  Return explicit
        # reconciliation evidence; callers must not blindly retry because the
        # native mutation may have committed even though its journal state is
        # unknown.
        #
        # ``commit``/``fail`` can fail after ``begin`` has durably written the
        # accepted record (for example, an atomic replace is denied while the
        # output directory is briefly locked).  Make one conditional recovery
        # attempt so a retry is not needlessly held behind
        # ``E_OPERATION_IN_PROGRESS`` for the full accepted timeout.  The
        # journal helper only changes a record that is still accepted under its
        # own lock, so a concurrent commit/failure wins and is never
        # overwritten.
        journal_recovery: dict[str, Any] = {
            "attempted": True,
            "state": "unknown",
        }
        try:
            recovery_error = {
                "ok": False,
                "error": {
                    "message": "Idempotency finalization failed",
                    "code": "E_IDEMPOTENCY_JOURNAL_FAILED",
                    "recoverable": False,
                },
                "details": {
                    "exception_type": type(exc).__name__,
                    "exception": str(exc) or type(exc).__name__,
                    "mutation_committed": bool(result.ok),
                },
            }
            if journal is not None:
                recovered = journal.fail_if_accepted(
                    idempotency_key,
                    recovery_error,
                    retryable=False if result.ok else bool(result.recoverable),
                )
                if recovered is None:
                    journal_recovery["state"] = "missing"
                else:
                    journal_recovery["state"] = recovered.get("state", "unknown")
                    journal_recovery["abandoned"] = bool(recovered.get("abandoned", False))
        except Exception as recovery_exc:
            journal_recovery.update(
                {
                    "state": "unknown",
                    "error": {
                        "type": type(recovery_exc).__name__,
                        "message": str(recovery_exc) or type(recovery_exc).__name__,
                    },
                }
            )
        # Never mutate ``result.payload`` here: callers may still hold the
        # backend result for reconciliation, and ``original_result`` must stay
        # an exact snapshot of what the CAD operation returned.
        payload = (
            copy.deepcopy(result.payload)
            if isinstance(result.payload, dict)
            else {"result": copy.deepcopy(result.payload)}
        )
        payload.update(
            {
                "idempotency_key": str(idempotency_key),
                "journal_state": journal_recovery.get("state", "unknown"),
                "journal_recovery": journal_recovery,
                "journal_error": {
                    "type": type(exc).__name__,
                    "message": str(exc) or type(exc).__name__,
                },
                "mutation_committed": bool(result.ok),
            }
        )
        return CommandResult(
            ok=False,
            error=(
                "CAD mutation completed but the idempotency journal could not be committed"
                if result.ok
                else "CAD operation failed and its idempotency journal could not be updated"
            ),
            error_code="E_IDEMPOTENCY_JOURNAL_FAILED",
            recoverable=False,
            recommended_action="inspect_document_context_and_journal_permissions_before_retrying",
            payload={"original_result": original, **payload},
        )
    payload = (
        copy.deepcopy(result.payload)
        if isinstance(result.payload, dict)
        else {"result": copy.deepcopy(result.payload)}
    )
    payload.update(
        idempotency_key=idempotency_key,
        journal_state=record["state"],
        request_hash=record["request_hash"],
    )
    result.payload = payload
    return result


async def _guard_mutation(
    backend,
    doc_id,
    expected_revision,
    lease_token=None,
    worker_generation=None,
) -> CommandResult:
    return await backend.require_document_context(
        doc_id,
        expected_revision,
        lease_token=lease_token,
        worker_generation=worker_generation,
    )


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
        if not mutated:
            return context
        original_payload = (
            result.payload if isinstance(result.payload, dict) else {"result": result.payload}
        )
        return CommandResult(
            ok=False,
            error="The CAD mutation completed but document context readback failed",
            error_code="E_MUTATION_CONTEXT_UNAVAILABLE",
            recoverable=False,
            recommended_action="inspect_the_returned_handles_and_reconcile_document_context_before_new_writes",
            payload={
                **original_payload,
                "mutation_committed": True,
                "context_readback": context.to_dict(),
            },
        )
    payload = result.payload if isinstance(result.payload, dict) else {"result": result.payload}
    payload.update(
        {
            "session_id": context.payload.get("session_id"),
            "worker_generation": context.payload.get("worker_generation"),
            "doc_id": context.payload["doc_id"],
            "active_doc_id": context.payload["active_doc_id"],
            "active_path": context.payload.get("active_path"),
            "revision": context.payload["revision"],
            "lease_token": context.payload.get("lease_token"),
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


async def _entity_handle_snapshot(backend) -> tuple[set[str] | None, dict[str, Any]]:
    """Return the active document's native handles for compensation logic."""
    listing = await backend.entity_list()
    if not listing.ok or not isinstance(listing.payload, dict):
        return None, {
            "ok": False,
            "error": listing.error or "entity listing failed",
            "error_code": listing.error_code or "E_ENTITY_LIST_FAILED",
        }
    handles = {
        str(entity.get("handle")).strip()
        for entity in listing.payload.get("entities", [])
        if isinstance(entity, dict) and entity.get("handle")
    }
    return handles, {"ok": True, "entity_count": len(handles)}


async def _compensate_product_failure(
    backend,
    doc_id: str,
    before_handles: set[str] | None,
) -> dict[str, Any]:
    """Erase entities leaked by a failed product feature call."""
    if before_handles is None:
        return {
            "attempted": False,
            "complete": False,
            "reason": "pre_call_entity_snapshot_unavailable",
            "erased_handles": [],
            "failed_handles": [],
        }
    context = await backend.document_context()
    if not context.ok:
        return {
            "attempted": False,
            "complete": False,
            "reason": "post_failure_document_context_unavailable",
            "context_error": context.error,
            "erased_handles": [],
            "failed_handles": [],
        }
    if str(context.payload.get("active_doc_id")) != str(doc_id):
        return {
            "attempted": False,
            "complete": False,
            "reason": "E_DOCUMENT_ID_MISMATCH",
            "active_doc_id": context.payload.get("active_doc_id"),
            "erased_handles": [],
            "failed_handles": [],
        }
    after_handles, snapshot = await _entity_handle_snapshot(backend)
    if after_handles is None:
        return {
            "attempted": False,
            "complete": False,
            "reason": "post_failure_entity_snapshot_unavailable",
            "snapshot": snapshot,
            "erased_handles": [],
            "failed_handles": [],
        }
    leaked = sorted(after_handles - before_handles)
    missing_preexisting = sorted(before_handles - after_handles)
    erased: list[str] = []
    failed: list[dict[str, Any]] = []
    for handle in leaked:
        deletion = await backend.entity_erase(handle)
        if deletion.ok:
            erased.append(handle)
        else:
            failed.append(
                {
                    "handle": handle,
                    "error": deletion.error,
                    "error_code": deletion.error_code,
                }
            )
    registry = mark_feature_handles_invalid(
        backend,
        doc_id,
        erased,
        reason="product_create_failure_compensation",
    )
    return {
        "attempted": True,
        "complete": not failed and not missing_preexisting,
        "leaked_handles": leaked,
        "missing_preexisting_handles": missing_preexisting,
        "erased_handles": erased,
        "failed_handles": failed,
        "registry": registry,
    }


async def _attach_failure_context(
    backend,
    result: CommandResult,
    *,
    doc_id: str,
    mutated: bool,
) -> CommandResult:
    """Attach the post-compensation revision to a failed response."""
    context = (
        await backend.record_document_mutation(doc_id)
        if mutated
        else await backend.document_context()
    )
    payload = result.payload if isinstance(result.payload, dict) else {"result": result.payload}
    if context.ok:
        payload.update(
            {
                "session_id": context.payload.get("session_id"),
                "worker_generation": context.payload.get("worker_generation"),
                "doc_id": context.payload.get("doc_id"),
                "active_doc_id": context.payload.get("active_doc_id"),
                "active_path": context.payload.get("active_path"),
                "revision": context.payload.get("revision"),
                "lease_token": context.payload.get("lease_token"),
            }
        )
    else:
        payload["post_failure_context_error"] = context.to_dict()
    result.payload = payload
    return result


async def _guard_document_read(backend, doc_id: str | None) -> CommandResult:
    context = await backend.document_context()
    if not context.ok:
        return context
    if doc_id and str(doc_id) != str(context.payload.get("active_doc_id")):
        return CommandResult(
            ok=False,
            error="Requested document is not the active document",
            error_code="E_DOCUMENT_ID_MISMATCH",
            recoverable=False,
            recommended_action="activate_the_requested_document_and_retry",
            payload={"requested_doc_id": doc_id, "actual": context.payload},
        )
    return context


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
      activate   - Activate a known document without changing window focus.
                   data: {doc_id, expected_revision, lease_token?, worker_generation?}
      create     — Create a new empty drawing. data: {name?}
      open       — Open an existing drawing. data: {path}
      info       — Get drawing extents, entity count, layers, blocks.
      save       — Save current drawing. data: {path?} (saves to path if given, else QSAVE)
      save_as_dxf — Export as DXF. data: {path}
      plot_pdf   — Plot to PDF. data: {path}
      render_preview — Native deterministic preview. data: {path, paper?, orientation?, plot_style?}
                       Optional visual_style: Conceptual, Realistic, Shaded, or
                       ShadedWithEdges. The style is applied only for the render
                       and restored by default.
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
    known_operations = {
        "create", "open", "info", "context", "activate", "save", "save_as_dxf",
        "plot_pdf", "render_preview", "workspace", "deliver", "audit", "audit_dxf",
        "setup_mechanical", "purge", "get_variables", "set_variables", "audit_geometry",
        "undo", "redo",
    }
    if operation not in known_operations:
        return tool_error(
            f"Unknown drawing operation: {operation}", code="E_UNSUPPORTED_OPERATION"
        )
    if operation == "workspace":
        return _json({"ok": True, "payload": workspace_info()})
    if operation == "audit_dxf":
        return await add_screenshot_if_available(audit_dxf_offline(data), False)

    # Activation is fenced before backend initialization.  This prevents a
    # malformed request from implicitly starting AutoCAD just to discover that
    # its document identity fields were missing.  Backends repeat the check
    # for direct integrations that bypass this MCP handler.
    activation_revision: int | None = None
    if operation == "activate":
        if not isinstance(data, dict):
            return tool_error(
                "drawing.activate requires data.doc_id and data.expected_revision",
                code="E_PARAMETER_REJECTED",
                recommended_action="read_document_context_and_retry",
            )
        if not isinstance(data.get("doc_id"), str) or not data.get("doc_id", "").strip():
            return tool_error(
                "drawing.activate requires data.doc_id",
                code="E_PARAMETER_REJECTED",
                recommended_action="read_document_context_and_retry",
            )
        raw_revision = data.get("expected_revision")
        if raw_revision is None or isinstance(raw_revision, bool):
            return tool_error(
                "drawing.activate requires data.expected_revision",
                code="E_PARAMETER_REJECTED",
                recommended_action="read_document_context_and_retry",
            )
        try:
            activation_revision = int(raw_revision)
        except (TypeError, ValueError, OverflowError):
            return tool_error(
                "drawing.activate data.expected_revision must be an integer",
                code="E_PARAMETER_REJECTED",
                recommended_action="read_document_context_and_retry",
            )
        if isinstance(raw_revision, float) and not raw_revision.is_integer():
            return tool_error(
                "drawing.activate data.expected_revision must be an integer",
                code="E_PARAMETER_REJECTED",
                recommended_action="read_document_context_and_retry",
            )
        if activation_revision < 0:
            return tool_error(
                "drawing.activate data.expected_revision must be non-negative",
                code="E_PARAMETER_REJECTED",
                recommended_action="read_document_context_and_retry",
            )

    backend = await get_backend()

    if operation == "open" and not str(data.get("path", "")).strip():
        return tool_error(
            "drawing.open requires data.path",
            code="E_PARAMETER_REJECTED",
            recommended_action="provide_an_existing_drawing_path",
        )
    if operation == "save_as_dxf" and not str(data.get("path", "")).strip():
        return tool_error(
            "drawing.save_as_dxf requires data.path",
            code="E_PARAMETER_REJECTED",
            recommended_action="provide_a_dxf_output_path",
        )

    context_required = {
        "save", "save_as_dxf", "plot_pdf", "render_preview", "deliver",
        "setup_mechanical", "purge", "set_variables", "undo", "redo",
    }
    mutation_operations = {
        "setup_mechanical", "purge", "set_variables", "undo", "redo",
    }
    journaled_operations = {
        "create", "open", "save", "save_as_dxf", "plot_pdf", "render_preview",
        "setup_mechanical", "purge", "set_variables", "undo", "redo",
    }
    guard = None
    if operation in context_required:
        guard = await _guard_mutation(
            backend,
            data.get("doc_id"),
            data.get("expected_revision"),
            data.get("lease_token"),
            data.get("worker_generation"),
        )
        if not guard.ok:
            return await add_screenshot_if_available(guard, False)
    idempotency_key = data.get("idempotency_key")
    scale_contract_prevalidated = None
    if operation == "plot_pdf":
        scale_contract_prevalidated = normalize_plot_scale(data)
        if not scale_contract_prevalidated["ok"]:
            return tool_error(
                scale_contract_prevalidated["message"],
                code="E_PLOT_SCALE_MISMATCH",
                recommended_action=scale_contract_prevalidated["recommended_action"],
            )
    if operation in journaled_operations:
        replay = _begin_journaled_mutation(
            idempotency_key,
            operation=f"drawing.{operation}",
            request={"operation": operation, "data": data},
            context=guard.payload if guard is not None else None,
        )
        if replay is not None:
            return await add_screenshot_if_available(replay, False)

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
            result = await backend.drawing_create(
                str(target.path), idempotency_key=idempotency_key
            )
        else:
            result = await backend.drawing_create(
                None, idempotency_key=idempotency_key
            )
    elif operation == "info":
        result = await backend.drawing_info()
    elif operation == "context":
        result = await backend.document_context()
    elif operation == "activate":
        activation_kwargs = {
            key: data[key]
            for key in ("lease_token", "worker_generation")
            if data.get(key) is not None
        }
        result = await backend.drawing_activate(
            data["doc_id"], activation_revision, **activation_kwargs
        )
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
        scale_contract = scale_contract_prevalidated or normalize_plot_scale(data)
        if not scale_contract["ok"]:
            return tool_error(
                scale_contract["message"],
                code="E_PLOT_SCALE_MISMATCH",
                recommended_action=scale_contract["recommended_action"],
            )
        scale_mode = scale_contract["scale_mode"]
        effective_scale = scale_contract["effective_scale"]
        declared_scale = scale_contract["declared_scale"]
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
            effective_scale,
            data.get("center", True),
        )
        if isinstance(result.payload, dict):
            result.payload.setdefault("requested", {})
            result.payload["requested"]["declared_scale"] = str(declared_scale)
            result.payload["requested"]["scale"] = str(effective_scale)
            result.payload.setdefault("actual", {})
            result.payload["actual"].setdefault("declared_scale", str(declared_scale))
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
            visual_style=data.get("visual_style"),
            preserve_visual_style=bool(data.get("preserve_visual_style", True)),
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

    presentation_mutation = (
        operation == "render_preview"
        and data.get("visual_style")
        and not bool(data.get("preserve_visual_style", True))
    )
    if operation in mutation_operations or presentation_mutation:
        result = await _attach_document_context(
            backend, result, doc_id=data.get("doc_id"), mutated=True
        )
    elif operation in context_required:
        result = await _attach_document_context(backend, result)
    if operation in journaled_operations:
        result = _finish_journaled_mutation(idempotency_key, result)
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
    lease_token: str | None = None,
    worker_generation: int | None = None,
    idempotency_key: str | None = None,
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
    known_operations = {
        "create_line", "create_circle", "create_polyline", "create_rectangle",
        "create_arc", "create_tangent_arc", "create_ellipse", "create_mtext",
        "create_text", "create_hatch", "create_batch", "list", "count", "get",
        "copy", "move", "rotate", "scale", "mirror", "offset", "array", "fillet",
        "chamfer", "trim", "extend", "break", "join", "constrain", "erase",
    }
    if operation not in known_operations:
        return tool_error(f"Unknown entity operation: {operation}", code="E_UNSUPPORTED_OPERATION")
    mutating = operation not in {"list", "count", "get"}
    if mutating:
        guard = await _guard_mutation(
            backend, doc_id, expected_revision, lease_token, worker_generation
        )
        if not guard.ok:
            return await add_screenshot_if_available(guard, False)

    expectation = None
    hatch_contract = None
    tangent_geometry = None
    create_kind = operation.removeprefix("create_")
    if operation in {
        "create_line", "create_circle", "create_polyline", "create_rectangle",
        "create_arc", "create_tangent_arc", "create_ellipse", "create_mtext", "create_text",
    }:
        layer_check = await _require_existing_layer(backend, layer)
        if not layer_check.ok:
            return await add_screenshot_if_available(layer_check, False)
        params = dict(data)
        if operation in {"create_line", "create_rectangle"}:
            params.update(x1=x1, y1=y1, x2=x2, y2=y2)
        elif operation == "create_polyline":
            params["points"] = points
        elif operation == "create_tangent_arc":
            try:
                tangent_geometry = tangent_arc_from_start(
                    data["start"], data["end"], data["tangent"]
                )
                params.update(
                    cx=tangent_geometry["center"][0],
                    cy=tangent_geometry["center"][1],
                    radius=tangent_geometry["radius"],
                    start_angle=tangent_geometry["start_angle"],
                    end_angle=tangent_geometry["end_angle"],
                )
                # The tangent construction inputs are intentional source
                # fields in addition to the resulting ARC contract.
                expectation = build_entity_expectation(
                    "arc", params, layer=layer, strict=False
                )
            except (KeyError, TypeError, ValueError) as exc:
                return tool_error(
                    str(exc),
                    code="E_PARAMETER_REJECTED",
                    recommended_action="provide_valid_tangent_arc_points_and_direction",
                )
        try:
            if expectation is None:
                expectation = build_entity_expectation(
                    create_kind, params, layer=layer, strict=bool(strict)
                )
        except (TypeError, ValueError) as exc:
            return tool_error(
                str(exc),
                code="E_PARAMETER_REJECTED",
                recommended_action="correct_request_fields",
            )

    if mutating:
        replay = _begin_journaled_mutation(
            idempotency_key,
            operation=f"entity.{operation}",
            request={
                "operation": operation,
                "doc_id": doc_id,
                "expected_revision": expected_revision,
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "points": points,
                "layer": layer,
                "entity_id": entity_id,
                "data": data,
                "strict": strict,
            },
            context=guard.payload,
        )
        if replay is not None:
            return await add_screenshot_if_available(replay, False)

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
        geometry = tangent_geometry or tangent_arc_from_start(
            data["start"], data["end"], data["tangent"]
        )
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
        if not entity_id:
            return tool_error(
                "create_hatch requires entity_id",
                code="E_PARAMETER_REJECTED",
                recommended_action="provide_an_existing_closed_boundary_handle",
            )
        hatch_layer = data.get("layer")
        layer_check = await _require_existing_layer(backend, hatch_layer)
        if not layer_check.ok:
            return await add_screenshot_if_available(layer_check, False)
        try:
            hatch_contract = {
                "entity_id": str(entity_id),
                "pattern": str(data.get("pattern", "ANSI31")),
                "angle": float(data.get("angle", 0.0)),
                "scale": float(data.get("scale", 1.0)),
                "layer": hatch_layer,
            }
            if not hatch_contract["pattern"].strip():
                raise ValueError("pattern must not be empty")
            if not math.isfinite(hatch_contract["angle"]):
                raise ValueError("angle must be finite")
            if not math.isfinite(hatch_contract["scale"]) or hatch_contract["scale"] <= 0:
                raise ValueError("scale must be positive")
        except (TypeError, ValueError) as exc:
            return tool_error(
                str(exc),
                code="E_PARAMETER_REJECTED",
                recommended_action="provide_a_nonempty_pattern_and_positive_scale",
            )
        result = await backend.create_hatch(
            entity_id,
            hatch_contract["pattern"],
            hatch_contract["angle"],
            hatch_contract["scale"],
            hatch_contract["layer"],
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
        if result.ok and doc_id and entity_id:
            registry = mark_feature_handles_invalid(
                backend,
                str(doc_id),
                [str(entity_id)],
                reason="native_entity_erased",
            )
            payload = result.payload if isinstance(result.payload, dict) else {}
            payload["registry_sync"] = registry
            result.payload = payload
    if hatch_contract is not None:
        result = await backend.verify_created_hatch(result, **hatch_contract)
    if expectation is not None:
        result = await backend.verify_created_entity(expectation, result)

    if mutating:
        result = await _attach_document_context(
            backend, result, doc_id=doc_id, mutated=True
        )
        result = _finish_journaled_mutation(idempotency_key, result)

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
    lease_token: str | None = None,
    worker_generation: int | None = None,
    idempotency_key: str | None = None,
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
    known_operations = {"list", "create", "set_current", "set_properties", "freeze", "thaw", "lock", "unlock"}
    if operation not in known_operations:
        return tool_error(f"Unknown layer operation: {operation}", code="E_UNSUPPORTED_OPERATION")
    mutating = operation != "list"
    if mutating:
        guard = await _guard_mutation(
            backend, doc_id, expected_revision, lease_token, worker_generation
        )
        if not guard.ok:
            return await add_screenshot_if_available(guard, False)
        replay = _begin_journaled_mutation(
            idempotency_key,
            operation=f"layer.{operation}",
            request={"operation": operation, "doc_id": doc_id, "data": data},
            context=guard.payload,
        )
        if replay is not None:
            return await add_screenshot_if_available(replay, False)

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
    if mutating:
        result = await _attach_document_context(backend, result, doc_id=doc_id, mutated=True)
        result = _finish_journaled_mutation(idempotency_key, result)
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
    lease_token: str | None = None,
    worker_generation: int | None = None,
    idempotency_key: str | None = None,
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
    known_operations = {"list", "insert", "insert_with_attributes", "get_attributes", "update_attribute", "define"}
    if operation not in known_operations:
        return tool_error(f"Unknown block operation: {operation}", code="E_UNSUPPORTED_OPERATION")
    mutating = operation not in {"list", "get_attributes"}
    if mutating:
        guard = await _guard_mutation(
            backend, doc_id, expected_revision, lease_token, worker_generation
        )
        if not guard.ok:
            return await add_screenshot_if_available(guard, False)
        replay = _begin_journaled_mutation(
            idempotency_key,
            operation=f"block.{operation}",
            request={"operation": operation, "doc_id": doc_id, "data": data},
            context=guard.payload,
        )
        if replay is not None:
            return await add_screenshot_if_available(replay, False)

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
    if mutating:
        result = await _attach_document_context(backend, result, doc_id=doc_id, mutated=True)
        result = _finish_journaled_mutation(idempotency_key, result)
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
    lease_token: str | None = None,
    worker_generation: int | None = None,
    idempotency_key: str | None = None,
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
    known_operations = {
        "create_text", "create_dimension_linear", "create_dimension_aligned",
        "create_dimension_angular", "create_dimension_radius", "create_leader",
    }
    if operation not in known_operations:
        return tool_error(f"Unknown annotation operation: {operation}", code="E_UNSUPPORTED_OPERATION")
    guard = await _guard_mutation(
        backend, doc_id, expected_revision, lease_token, worker_generation
    )
    if not guard.ok:
        return await add_screenshot_if_available(guard, False)
    # Validate all deterministic preconditions before opening the journal.
    # Otherwise a missing layer exits early with the record stuck in
    # ``accepted`` and every retry is incorrectly reported as in progress.
    layer_check = await _require_existing_layer(backend, data.get("layer"))
    if not layer_check.ok:
        return await add_screenshot_if_available(layer_check, False)
    replay = _begin_journaled_mutation(
        idempotency_key,
        operation=f"annotation.{operation}",
        request={"operation": operation, "doc_id": doc_id, "data": data},
        context=guard.payload,
    )
    if replay is not None:
        return await add_screenshot_if_available(replay, False)

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
    result = await _attach_document_context(backend, result, doc_id=doc_id, mutated=True)
    result = _finish_journaled_mutation(idempotency_key, result)
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
    lease_token: str | None = None,
    worker_generation: int | None = None,
    idempotency_key: str | None = None,
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
      fillet_edges    - returns capability error until stable semantic edge selection exists
      chamfer_edges   - returns capability error until stable semantic edge selection exists

    General native edge edits never accept volatile edge indices. Use product.rounded_box
    for analytic radius geometry or a future stable native feature plugin.
    """
    data = data or {}
    backend = await get_backend()
    known_operations = {"create_box", "create_cylinder", "extrude", "revolve", "sweep", "boolean", "fillet_edges", "chamfer_edges"}
    if operation not in known_operations:
        return tool_error(f"Unknown solid operation: {operation}", code="E_UNSUPPORTED_OPERATION")
    guard = await _guard_mutation(
        backend, doc_id, expected_revision, lease_token, worker_generation
    )
    if not guard.ok:
        return await add_screenshot_if_available(guard, False)
    layer_check = await _require_existing_layer(backend, data.get("layer"))
    if not layer_check.ok:
        return await add_screenshot_if_available(layer_check, False)
    replay = _begin_journaled_mutation(
        idempotency_key,
        operation=f"solid.{operation}",
        request={"operation": operation, "doc_id": doc_id, "data": data},
        context=guard.payload,
    )
    if replay is not None:
        return await add_screenshot_if_available(replay, False)

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
    elif operation == "fillet_edges":
        result = await backend.solid_fillet_edges(
            data["entity_id"], data.get("semantic_roles", []), data["radius"]
        )
    elif operation == "chamfer_edges":
        result = await backend.solid_chamfer_edges(
            data["entity_id"], data.get("semantic_roles", []), data["distance"]
        )
    result = await _attach_document_context(backend, result, doc_id=doc_id, mutated=True)
    result = _finish_journaled_mutation(idempotency_key, result)
    return await add_screenshot_if_available(result, include_screenshot)


@mcp.tool(annotations={"title": "Industrial Product Design", "readOnlyHint": False})
@_safe("product")
async def product(
    operation: str,
    doc_id: str | None = None,
    expected_revision: int | None = None,
    lease_token: str | None = None,
    worker_generation: int | None = None,
    idempotency_key: str | None = None,
    data: dict | None = None,
) -> ToolResult:
    """Parametric consumer-product features, motion screening, views, and reviews.

    Operations:
      capabilities              - honest verified/unsupported capability matrix
      create_feature            - data: {kind, feature_id, component_id, ...}
      list_features / get_feature
      query_edges_by_semantic_role
      measure_fillet_radius / measure_chamfer_distance
      fillet_edges / chamfer_edges - structured capability error without stable selection
      set_motion                - axis, angle, limits, clearance
      interference_sample       - static broad-phase AABB screening
      clearance_sweep           - sampled rotated-AABB motion screening
      render_view               - front/right/top/bottom/iso/rotated_iso/section/exploded;
                                   data.visual_style requests an allow-listed AutoCAD
                                   display style; material rendering is reported only
                                   when independently verified by the backend
      set_review / review_summary

    Product review verdicts are independent from geometry and STEP validity. USB cutouts
    require supplier-controlled or physically measured authority; concept dimensions must
    use module_reservation instead.
    """
    data = dict(data or {})
    backend = await get_backend()
    if operation == "capabilities":
        status = await backend.status()
        return await add_screenshot_if_available(status, False)

    write_operations = {"create_feature", "set_motion", "set_review", "fillet_edges", "chamfer_edges"}
    if operation in write_operations:
        guard = await _guard_mutation(
            backend, doc_id, expected_revision, lease_token, worker_generation
        )
    else:
        guard = await _guard_document_read(backend, doc_id)
    if not guard.ok:
        return await add_screenshot_if_available(guard, False)
    if operation in write_operations:
        replay = _begin_journaled_mutation(
            idempotency_key,
            operation=f"product.{operation}",
            request={"operation": operation, "doc_id": doc_id, "data": data},
            context=guard.payload,
        )
        if replay is not None:
            return await add_screenshot_if_available(replay, False)
    active_doc_id = str(guard.payload["active_doc_id"])

    try:
        if operation == "create_feature":
            layer_check = await _require_existing_layer(backend, data.get("layer"))
            if not layer_check.ok:
                layer_check = _finish_journaled_mutation(idempotency_key, layer_check)
                return await add_screenshot_if_available(layer_check, False)
            feature_data = dict(data)
            kind = str(feature_data.pop("kind", ""))
            before_handles, before_snapshot = await _entity_handle_snapshot(backend)
            result = await backend.product_create_feature(kind, feature_data)
            if not result.ok:
                cleanup = await _compensate_product_failure(
                    backend, active_doc_id, before_handles
                )
                result.payload = result.payload if isinstance(result.payload, dict) else {}
                result.payload["compensation"] = cleanup
                result.payload["pre_call_snapshot"] = before_snapshot
                if cleanup.get("attempted") and cleanup.get("erased_handles"):
                    result = await _attach_failure_context(
                        backend,
                        result,
                        doc_id=active_doc_id,
                        mutated=True,
                    )
                if not cleanup.get("complete", False):
                    result.error_code = "E_PRODUCT_ROLLBACK_INCOMPLETE"
                    result.recoverable = False
                    result.recommended_action = "stop_and_reconcile_native_handles_before_retry"
            else:
                try:
                    replacement = mark_feature_replaced(
                        backend,
                        active_doc_id,
                        result.payload.get("replaced_target_handle")
                        if isinstance(result.payload, dict)
                        else None,
                        result.payload.get("handle") if isinstance(result.payload, dict) else None,
                        replacement_feature_id=(
                            result.payload.get("feature_id")
                            if isinstance(result.payload, dict)
                            else None
                        ),
                    )
                    registered = register_feature(
                        backend, active_doc_id, result.payload
                    )
                    if registered.get("motion"):
                        registered["motion"] = set_motion(
                            backend, active_doc_id, registered["motion"]
                        )
                        registered["product_state_revision"] = registered["motion"][
                            "product_state_revision"
                        ]
                    registered["replacement_registry"] = replacement
                    result.payload = registered
                    result = await _attach_document_context(
                        backend, result, doc_id=active_doc_id, mutated=True
                    )
                except Exception as registration_error:
                    cleanup = await _compensate_product_failure(
                        backend, active_doc_id, before_handles
                    )
                    result = CommandResult(
                        ok=False,
                        error=f"Product feature registry commit failed: {registration_error}",
                        error_code=(
                            "E_PRODUCT_ROLLBACK_INCOMPLETE"
                            if not cleanup.get("complete", False)
                            else "E_PRODUCT_REGISTRY_COMMIT_FAILED"
                        ),
                        recoverable=False,
                        recommended_action="stop_and_reconcile_native_handles_before_retry",
                        payload={
                            "operation": f"product.create_feature.{kind}",
                            "compensation": cleanup,
                            "pre_call_snapshot": before_snapshot,
                        },
                    )
                    if cleanup.get("attempted") and cleanup.get("erased_handles"):
                        result = await _attach_failure_context(
                            backend,
                            result,
                            doc_id=active_doc_id,
                            mutated=True,
                        )
        elif operation == "list_features":
            result = CommandResult(ok=True, payload=list_features(backend, active_doc_id))
        elif operation == "get_feature":
            feature = get_feature(backend, active_doc_id, data.get("feature_id", ""))
            result = (
                CommandResult(ok=True, payload=feature)
                if feature
                else CommandResult(
                    ok=False,
                    error=f"Unknown feature_id: {data.get('feature_id')}",
                    error_code="E_FEATURE_NOT_FOUND",
                )
            )
        elif operation == "query_edges_by_semantic_role":
            result = CommandResult(
                ok=True,
                payload=query_edges_by_semantic_role(
                    backend, active_doc_id, data["feature_id"], data.get("role")
                ),
            )
        elif operation in {"measure_fillet_radius", "measure_chamfer_distance"}:
            measurement = (
                "fillet_radius" if operation == "measure_fillet_radius" else "chamfer_distance"
            )
            result = CommandResult(
                ok=True,
                payload=measure_registered_feature(
                    backend, active_doc_id, data["feature_id"], measurement
                ),
            )
        elif operation == "fillet_edges":
            result = await backend.solid_fillet_edges(
                data["entity_id"], data.get("semantic_roles", []), data["radius"]
            )
        elif operation == "chamfer_edges":
            result = await backend.solid_chamfer_edges(
                data["entity_id"], data.get("semantic_roles", []), data["distance"]
            )
        elif operation == "set_motion":
            result = CommandResult(ok=True, payload=set_motion(backend, active_doc_id, data))
        elif operation == "interference_sample":
            result = CommandResult(
                ok=True,
                payload=interference_sample(
                    backend,
                    active_doc_id,
                    data.get("component_ids"),
                    float(data.get("clearance", 0.0)),
                ),
            )
        elif operation == "clearance_sweep":
            result = CommandResult(
                ok=True,
                payload=clearance_sweep(
                    backend,
                    active_doc_id,
                    data["component_id"],
                    sample_count=int(data.get("sample_count", 13)),
                    clearance=data.get("clearance"),
                ),
            )
        elif operation == "render_view":
            if data.get("visual_style") and data.get("preserve_visual_style") is False:
                result = CommandResult(
                    ok=False,
                    error="product.render_view must restore the user's visual style",
                    error_code="E_PARAMETER_REJECTED",
                    recoverable=False,
                    recommended_action="use_view_set_visual_style_for_a_persistent_change",
                )
                return await add_screenshot_if_available(result, False)
            view_name = str(data.get("view", "iso")).lower()
            if view_name not in VIEW_NAMES:
                raise ValueError(f"view must be one of {sorted(VIEW_NAMES)}")
            target = resolve_output_target(
                data.get("path"),
                category="previews",
                extension=".png",
                default_stem=data.get("name", f"product-{view_name}"),
            )
            result = await backend.product_render_view(view_name, str(target.path), data)
            if result.ok:
                metrics = image_content_metrics(target.path)
                comparison = None
                if data.get("compare_to"):
                    comparison = image_difference(data["compare_to"], target.path)
                result.payload.update(
                    image_metrics=metrics,
                    comparison=comparison,
                    framing_pass=metrics["framing_status"] == "PASS",
                )
                state = product_state(backend, active_doc_id)
                state["views"][view_name] = dict(result.payload)
                state["revision"] += 1
                result.payload["product_state_revision"] = state["revision"]
        elif operation == "set_review":
            result = CommandResult(
                ok=True,
                payload=set_review(backend, active_doc_id, data["name"], data),
            )
        elif operation == "review_summary":
            result = CommandResult(
                ok=True, payload=review_summary(backend, active_doc_id)
            )
        else:
            result = CommandResult(
                ok=False,
                error=f"Unknown product operation: {operation}",
                error_code="E_UNSUPPORTED_OPERATION",
            )
    except (KeyError, TypeError, ValueError) as exc:
        result = CommandResult(
            ok=False,
            error=str(exc),
            error_code="E_PARAMETER_REJECTED",
            recoverable=False,
            recommended_action="correct_the_product_contract_and_retry",
            payload={"operation": operation, "data": data},
        )
    if result.ok and operation in {
        "set_motion", "set_review", "fillet_edges", "chamfer_edges"
    }:
        result = await _attach_document_context(
            backend,
            result,
            doc_id=doc_id,
            mutated=operation in {"fillet_edges", "chamfer_edges"},
        )
    if operation in write_operations:
        result = _finish_journaled_mutation(idempotency_key, result)
    return await add_screenshot_if_available(result, False)


@mcp.tool(annotations={"title": "P&ID Operations (CTO Library)", "readOnlyHint": False})
@_safe("pid")
async def pid(
    operation: str,
    doc_id: str | None = None,
    expected_revision: int | None = None,
    lease_token: str | None = None,
    worker_generation: int | None = None,
    idempotency_key: str | None = None,
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
    known_operations = {
        "setup_layers", "insert_symbol", "list_symbols", "draw_process_line",
        "connect_equipment", "add_flow_arrow", "add_equipment_tag", "add_line_number",
        "insert_valve", "insert_instrument", "insert_pump", "insert_tank",
    }
    if operation not in known_operations:
        return tool_error(f"Unknown pid operation: {operation}", code="E_UNSUPPORTED_OPERATION")
    mutating = operation != "list_symbols"
    if mutating:
        guard = await _guard_mutation(
            backend, doc_id, expected_revision, lease_token, worker_generation
        )
        if not guard.ok:
            return await add_screenshot_if_available(guard, False)
        replay = _begin_journaled_mutation(
            idempotency_key,
            operation=f"pid.{operation}",
            request={"operation": operation, "doc_id": doc_id, "data": data},
            context=guard.payload,
        )
        if replay is not None:
            return await add_screenshot_if_available(replay, False)

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
    if mutating:
        result = await _attach_document_context(backend, result, doc_id=doc_id, mutated=True)
        result = _finish_journaled_mutation(idempotency_key, result)
    return await add_screenshot_if_available(result, include_screenshot)


# ==========================================================================
# 7. view — Viewport and screenshot
# ==========================================================================


@mcp.tool(annotations={"title": "AutoCAD Transactions", "readOnlyHint": False})
@_safe("transaction")
async def transaction(
    operation: str,
    doc_id: str | None = None,
    expected_revision: int | None = None,
    transaction_id: str | None = None,
    idempotency_key: str | None = None,
    data: dict | None = None,
) -> ToolResult:
    """Document identity and atomic native AutoCAD transactions.

    Operations:
      context  - Read canonical native document/session/revision identity.
      create   - Create a document. data: {name?}; requires idempotency_key.
      execute  - Atomically apply data.operations through the native worker.
                 Requires doc_id, expected_revision, and idempotency_key.
      begin/commit/rollback - Compatibility undo transaction operations.
    """
    data = data or {}
    backend = await get_backend()
    if operation == "context":
        result = await backend.document_context()
    elif operation == "create":
        if not idempotency_key:
            return tool_error(
                "transaction.create requires idempotency_key",
                code="E_PARAMETER_REJECTED",
            )
        replay = _begin_journaled_mutation(
            idempotency_key,
            operation="transaction.create",
            request={"operation": operation, "data": data},
            context=None,
        )
        if replay is not None:
            return await add_screenshot_if_available(replay, False)
        result = await backend.drawing_create(
            data.get("path") or data.get("name"),
            idempotency_key=idempotency_key,
        )
        result = _finish_journaled_mutation(idempotency_key, result)
    elif operation == "execute":
        if not doc_id or expected_revision is None or not idempotency_key:
            return tool_error(
                "transaction.execute requires doc_id, expected_revision, and idempotency_key",
                code="E_PARAMETER_REJECTED",
            )
        operations = data.get("operations")
        if not isinstance(operations, list) or not operations:
            return tool_error(
                "transaction.execute requires a non-empty data.operations array",
                code="E_PARAMETER_REJECTED",
            )
        replay = _begin_journaled_mutation(
            idempotency_key,
            operation="transaction.execute",
            request={
                "operation": operation,
                "doc_id": doc_id,
                "expected_revision": expected_revision,
                "operations": operations,
            },
            context={"doc_id": doc_id, "revision": expected_revision},
        )
        if replay is not None:
            return await add_screenshot_if_available(replay, False)
        result = await backend.native_transaction_execute(
            doc_id,
            expected_revision,
            idempotency_key,
            operations,
            session_id=data.get("session_id"),
        )
        result = _finish_journaled_mutation(idempotency_key, result)
    elif operation == "begin":
        if not doc_id or expected_revision is None:
            return tool_error(
                "transaction.begin requires doc_id and expected_revision",
                code="E_PARAMETER_REJECTED",
            )
        result = await backend.transaction_begin(doc_id, expected_revision)
    elif operation == "commit":
        if not transaction_id or not doc_id or expected_revision is None:
            return tool_error(
                "transaction.commit requires transaction_id, doc_id, and expected_revision",
                code="E_PARAMETER_REJECTED",
            )
        result = await backend.transaction_commit(
            transaction_id, doc_id, expected_revision
        )
    elif operation == "rollback":
        if not transaction_id or not doc_id or expected_revision is None:
            return tool_error(
                "transaction.rollback requires transaction_id, doc_id, and expected_revision",
                code="E_PARAMETER_REJECTED",
            )
        result = await backend.transaction_rollback(
            transaction_id, doc_id, expected_revision
        )
    else:
        return tool_error(
            f"Unknown transaction operation: {operation}",
            code="E_UNSUPPORTED_OPERATION",
        )
    return await add_screenshot_if_available(result, False)


@mcp.tool(annotations={"title": "AutoCAD View Operations", "readOnlyHint": False})
@_safe("view")
async def view(
    operation: str,
    x1: float | None = None,
    y1: float | None = None,
    x2: float | None = None,
    y2: float | None = None,
    data: dict | None = None,
) -> ToolResult:
    """Viewport control and diagnostic window capture.

    Operations:
      zoom_extents   — Zoom to show all entities.
      fit_drawing    — Center and fit all drawing geometry in the viewport.
      zoom_window    — Zoom to window: x1, y1, x2, y2
      set_visual_style — Apply a built-in visual style and optional entity
                         colors. data: {visual_style|style, colors?, doc_id,
                         expected_revision, lease_token?, worker_generation?}
      show_window    — Restore and activate the AutoCAD window.
      get_screenshot — Diagnostic-only window capture. Prefer drawing.render_preview.
    """
    data = data or {}
    backend = await get_backend()

    if operation in ("zoom_extents", "fit_drawing"):
        result = await backend.zoom_extents()
        return _json(result.to_dict())
    elif operation == "zoom_window":
        result = await backend.zoom_window(x1, y1, x2, y2)
        return _json(result.to_dict())
    elif operation == "prepare_recording":
        result = await backend.prepare_recording_view(
            data["bounds"],
            data.get("view", "iso"),
            data.get("margin_scale", 0.82),
            data.get("output_aspect", 16 / 9),
        )
        return _json(result.to_dict())
    elif operation == "set_visual_style":
        # Validate caller-controlled values before invoking COM.  Passing an
        # arbitrary VSCURRENT string can open a modal AutoCAD prompt and wedge
        # the IPC worker, so only built-in styles are accepted.
        requested_style = data.get("visual_style", data.get("style"))
        try:
            canonical_style = normalize_visual_style(requested_style)
            colors = normalize_color_map(data.get("colors"))
        except ValueError as exc:
            return tool_error(
                str(exc),
                code="E_VISUAL_STYLE_NOT_ALLOWED",
                recoverable=False,
                recommended_action="choose_a_builtin_visual_style_and_valid_rgb_values",
                details={"allowed_visual_styles": list(SUPPORTED_VISUAL_STYLES)},
            )

        # Style changes alter document presentation and follow the same
        # document identity/revision contract as other mutations.
        guard = await _guard_mutation(
            backend,
            data.get("doc_id"),
            data.get("expected_revision"),
            data.get("lease_token"),
            data.get("worker_generation"),
        )
        if not guard.ok:
            return _json(guard.to_dict())

        result = await backend.apply_presentation_style(colors, canonical_style)
        if result.ok:
            readback = style_readback(canonical_style, colors, result.payload)
            # Preserve backend diagnostics while exposing a stable
            # requested/actual/diff envelope to every MCP client.
            backend_payload = result.payload if isinstance(result.payload, dict) else {}
            result.payload = {**backend_payload, **readback}
            # The COM call may have changed the document even when readback
            # disagrees, so advance the revision before returning either
            # state.  _attach_document_context intentionally skips failed
            # results, hence this happens before converting a mismatch into
            # an error envelope.
            result = await _attach_document_context(
                backend, result, doc_id=data.get("doc_id"), mutated=True
            )
            if not result.ok:
                return _json(result.to_dict())
            # A successful COM call with stale readback is not a verified
            # success. Return the evidence instead of claiming it was applied.
            if not readback["verified"]:
                result.ok = False
                result.error = "AutoCAD visual-style postcondition did not match the request"
                result.error_code = "E_POSTCONDITION_MISMATCH"
                result.recoverable = True
                result.recommended_action = "read_the_current_visual_style_and_retry"
        return _json(result.to_dict())
    elif operation == "show_window":
        result = await backend.show_window(activate=True)
        return _json(result.to_dict())
    elif operation == "minimize_window":
        result = await backend.minimize_window()
        return _json(result.to_dict())
    elif operation == "get_screenshot":
        result = await backend.get_screenshot()
        return _screenshot_result(
            result,
            include_image=bool(data.get("include_image", False)),
            stem="view-capture",
        )
    else:
        return tool_error(f"Unknown view operation: {operation}", code="E_UNSUPPORTED_OPERATION")


# ==========================================================================
# 8. system — Server management
# ==========================================================================


@mcp.tool(annotations={"title": "AutoCAD Job Operations", "readOnlyHint": False})
@_safe("job")
async def job(operation: str, data: dict | None = None) -> ToolResult:
    """Inspect idempotent operations or clean managed test artifacts.

    Operations:
      journal_status         - data: {idempotency_key}
      cleanup_test_artifacts - data: {job_id, confirm: true}
    """
    data = data or {}
    if operation == "journal_status":
        key = str(data.get("idempotency_key", "")).strip()
        if not key:
            return tool_error("data.idempotency_key is required", code="E_PARAMETER_REJECTED")
        record = _operation_journal().read(key)
        if record is None:
            return tool_error(
                f"Unknown idempotency key: {key}",
                code="E_OPERATION_NOT_FOUND",
            )
        return _json({"ok": True, "payload": record})
    if operation == "cleanup_test_artifacts":
        if data.get("confirm") is not True:
            return tool_error(
                "data.confirm must be true before deleting managed test artifacts",
                code="E_CONFIRMATION_REQUIRED",
            )
        try:
            report = cleanup_test_job_artifacts(str(data.get("job_id", "")))
        except (FileNotFoundError, ValueError) as exc:
            return tool_error(str(exc), code="E_PARAMETER_REJECTED")
        return _json({"ok": True, "payload": report})
    return tool_error(f"Unknown job operation: {operation}", code="E_UNSUPPORTED_OPERATION")


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
      preflight     — Check Python/pywin32, acad.exe processes, and Activity Insights without starting AutoCAD.
      ensure_ready  — Discover/start AutoCAD, open a document, load/version-check dispatcher, ping IPC.
      health        — Quick health check (ping backend).
      get_backend   — Return current backend name and capabilities.
      runtime       — Return process/runtime details for spawn diagnostics.
      supervisor_status — Read the external desktop supervisor heartbeat without starting AutoCAD.
      init          — Re-initialize the backend.
      execute_lisp  — Execute arbitrary AutoLISP code (File IPC only). data: {code}
      recover       — Cancel a stuck AutoCAD command and clear stale IPC state.
    """
    data = data or {}

    if operation == "preflight":
        from autocad_mcp.runtime_health import environment_preflight

        return _json({"ok": True, "payload": environment_preflight()})
    elif operation == "status":
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
                "request_admission": request_admission_snapshot(),
            }
        )
    elif operation == "supervisor_status":
        from autocad_mcp.supervisor import read_supervisor_state

        return _json({"ok": True, "payload": read_supervisor_state()})
    elif operation == "init":
        # Force re-initialization
        await reset_backend()
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
