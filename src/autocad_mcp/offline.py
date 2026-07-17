"""Backend-independent CAD exchange-file operations."""

from __future__ import annotations

from autocad_mcp.audit import audit_dxf_file
from autocad_mcp.backends.base import CommandResult
from autocad_mcp.errors import exception_context


def audit_dxf_offline(data: dict | None = None) -> CommandResult:
    request = dict(data or {})
    path = request.get("path")
    if not path:
        return CommandResult(
            ok=False,
            error="drawing.audit_dxf requires data.path",
            error_code="E_PARAMETER_REJECTED",
            recommended_action="provide_an_existing_dxf_path",
            payload={
                "operation": "drawing.audit_dxf",
                "parameter_fields": sorted(request),
                "backend_required": False,
            },
        )
    try:
        payload = audit_dxf_file(
            path,
            limit=request.get("limit", 50),
            include_entities=request.get("include_entities", True),
        )
        payload["offline"] = True
        payload["backend_required"] = False
        return CommandResult(ok=True, payload=payload)
    except Exception as exc:
        message, details = exception_context(
            exc,
            operation="drawing.audit_dxf",
            parameters=request,
            system_call="ezdxf.readfile",
            file_path=str(path),
        )
        details["backend_required"] = False
        return CommandResult(
            ok=False,
            error=message,
            error_code="E_SYSTEM_CALL_FAILED",
            recommended_action="verify_dxf_path_permissions_and_file_integrity",
            payload=details,
        )
