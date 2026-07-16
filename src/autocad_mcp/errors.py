"""Stable machine-readable error codes for CAD automation failures."""

from __future__ import annotations


ERROR_ACTIONS = {
    "E_AUTOCAD_NOT_INSTALLED": (False, "configure_autocad_executable"),
    "E_AUTOCAD_NOT_RUNNING": (True, "start_autocad"),
    "E_NO_ACTIVE_DOCUMENT": (True, "create_or_open_drawing"),
    "E_DISPATCHER_NOT_LOADED": (True, "autoload_dispatcher"),
    "E_DISPATCHER_VERSION_MISMATCH": (True, "reload_dispatcher"),
    "E_IPC_TIMEOUT": (True, "recover_and_retry"),
    "E_COMMAND_STATE_BLOCKED": (True, "cancel_active_command"),
    "E_OUTPUT_PATH_REJECTED": (False, "use_managed_output_workspace"),
    "E_VARIABLE_REJECTED": (False, "use_whitelisted_variable_and_value"),
    "E_PARAMETER_REJECTED": (False, "correct_request_fields"),
    "E_POSTCONDITION_MISMATCH": (False, "stop_and_inspect_backend_state"),
    "E_PLOT_SCALE_MISMATCH": (False, "make_declared_and_actual_plot_scale_consistent"),
    "E_VALIDATION_FAILED": (False, "inspect_validation_report"),
    "E_BATCH_FAILED": (True, "inspect_batch_results"),
    "E_BATCH_ROLLED_BACK": (True, "correct_batch_and_retry"),
    "E_TRANSACTION_BEGIN": (True, "recover_and_retry"),
    "E_TRANSACTION_FINALIZE": (False, "inspect_drawing_undo_state"),
    "E_SOLID_OPERATION": (False, "inspect_profile_and_solid_inputs"),
    "E_DXF_EXPORT": (True, "inspect_export_settings_and_retry"),
    "E_OUTPUT_EXISTS": (False, "set_force_true_or_choose_new_path"),
    "E_UNSUPPORTED_OPERATION": (False, "check_backend_capabilities"),
    "E_INTERNAL": (True, "inspect_logs_and_retry"),
}


def infer_error_code(message: str | None) -> str:
    text = (message or "").lower()
    if "not installed" in text or "executable was not found" in text:
        return "E_AUTOCAD_NOT_INSTALLED"
    if "window not found" in text or "no autocad" in text or "not running" in text:
        return "E_AUTOCAD_NOT_RUNNING"
    if "no active document" in text or "no document open" in text:
        return "E_NO_ACTIVE_DOCUMENT"
    if "version mismatch" in text:
        return "E_DISPATCHER_VERSION_MISMATCH"
    if "dispatcher" in text or "mcp_dispatch" in text:
        return "E_DISPATCHER_NOT_LOADED"
    if "timeout" in text:
        return "E_IPC_TIMEOUT"
    if "cmdactive" in text or "command state" in text or "modal dialog" in text:
        return "E_COMMAND_STATE_BLOCKED"
    if "output path" in text or "must remain under" in text:
        return "E_OUTPUT_PATH_REJECTED"
    if "variable" in text and ("not allowed" in text or "out of range" in text):
        return "E_VARIABLE_REJECTED"
    if "validation" in text or "does not match" in text:
        return "E_VALIDATION_FAILED"
    if "plot failed" in text or "can not be set" in text:
        return "E_INTERNAL"
    if "not supported" in text or "unknown" in text:
        return "E_UNSUPPORTED_OPERATION"
    return "E_INTERNAL"


def error_payload(
    message: str | None,
    *,
    code: str | None = None,
    recoverable: bool | None = None,
    recommended_action: str | None = None,
) -> dict:
    resolved_code = code or infer_error_code(message)
    default_recoverable, default_action = ERROR_ACTIONS.get(
        resolved_code, ERROR_ACTIONS["E_INTERNAL"]
    )
    return {
        "code": resolved_code,
        "message": message or "Unknown error",
        "recoverable": default_recoverable if recoverable is None else recoverable,
        "recommended_action": recommended_action or default_action,
    }
