"""Stable machine-readable error codes for CAD automation failures."""

from __future__ import annotations


class LayerNotFoundError(ValueError):
    """Raised before mutation when a requested CAD layer does not exist."""


ERROR_ACTIONS = {
    "E_AUTOCAD_NOT_INSTALLED": (False, "configure_autocad_executable"),
    "E_AUTOCAD_NOT_RUNNING": (True, "start_autocad"),
    "E_AUTOCAD_CRASHED": (True, "close_fatal_dialog_restart_autocad_and_retry"),
    "E_AUTOCAD_GHOST_PROCESS": (True, "terminate_or_close_orphaned_acad_process_then_start_autocad_manually"),
    "E_AUTOCAD_PROFILE_UNWRITABLE": (False, "fix_activity_insights_path_permissions_or_disable_activity_insights"),
    "E_AUTOCAD_PROFILE_NOT_READY": (False, "inspect_only_the_autocad_profiles_registry_branch"),
    "E_AUTOCAD_PROFILE_NOT_FOUND": (False, "create_the_named_profile_or_use_the_default_profile"),
    "E_PYWIN32_BROKEN": (False, "repair_pywin32_in_the_same_python_used_by_the_mcp"),
    "E_AUTOCAD_COM_BUSY": (True, "wait_for_the_other_autocad_mcp_request_then_retry"),
    "E_AUTOCAD_STARTUP_FAILED": (True, "start_autocad_manually_outside_the_mcp_and_retry"),
    "E_NO_ACTIVE_DOCUMENT": (True, "create_or_open_drawing"),
    "E_DOCUMENT_ID_MISMATCH": (False, "activate_the_requested_document_and_retry"),
    "E_DOCUMENT_REVISION_MISMATCH": (False, "read_latest_document_revision_and_retry"),
    "E_LAYER_NOT_FOUND": (False, "create_or_select_an_existing_layer"),
    "E_OUTPUT_LOCKED": (True, "close_the_file_owner_and_retry"),
    "E_DISPATCHER_NOT_LOADED": (True, "autoload_dispatcher"),
    "E_DISPATCHER_VERSION_MISMATCH": (True, "reload_dispatcher"),
    "E_IPC_TIMEOUT": (True, "recover_and_retry"),
    "E_COMMAND_STATE_BLOCKED": (True, "cancel_active_command"),
    "E_OUTPUT_PATH_REJECTED": (False, "use_managed_output_workspace"),
    "E_VARIABLE_REJECTED": (False, "use_whitelisted_variable_and_value"),
    "E_PARAMETER_REJECTED": (False, "correct_request_fields"),
    "E_POSTCONDITION_MISMATCH": (False, "stop_and_inspect_backend_state"),
    "E_PLOT_SCALE_MISMATCH": (False, "make_declared_and_actual_plot_scale_consistent"),
    "E_PLOT_PAGE_MISMATCH": (False, "inspect_plot_device_media_and_retry"),
    "E_SYSTEM_CALL_FAILED": (True, "inspect_path_parameters_and_system_error"),
    "E_VALIDATION_FAILED": (False, "inspect_validation_report"),
    "E_BATCH_FAILED": (True, "inspect_batch_results"),
    "E_BATCH_ROLLED_BACK": (True, "correct_batch_and_retry"),
    "E_TRANSACTION_BEGIN": (True, "recover_and_retry"),
    "E_TRANSACTION_FINALIZE": (False, "inspect_drawing_undo_state"),
    "E_TRANSACTION_NOT_FOUND": (False, "begin_a_new_transaction"),
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
    if "pywin32" in text or "pythoncom" in text or "pywintypes" in text:
        return "E_PYWIN32_BROKEN"
    if "com turn" in text or "mcp process owns" in text or "com busy" in text:
        return "E_AUTOCAD_COM_BUSY"
    if "activity insights" in text or "profile" in text and "write" in text:
        return "E_AUTOCAD_PROFILE_UNWRITABLE"
    if "ghost" in text or "no usable main window" in text:
        return "E_AUTOCAD_GHOST_PROCESS"
    if "fatal error" in text or "致命错误" in text or "autocad crashed" in text:
        return "E_AUTOCAD_CRASHED"
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


def exception_context(
    exc: Exception,
    *,
    operation: str,
    parameters: dict | None = None,
    system_call: str | None = None,
    file_path: str | None = None,
) -> tuple[str, dict]:
    """Describe an exception without reducing it to an opaque ``[Errno N]`` string."""
    errno = getattr(exc, "errno", None)
    winerror = getattr(exc, "winerror", None)
    strerror = getattr(exc, "strerror", None)
    if not strerror:
        strerror = str(exc) or exc.__class__.__name__
    call = system_call or "python-operation"
    location = f" for {file_path}" if file_path else ""
    message = f"{operation} failed during {call}{location}: {strerror}"
    details = {
        "operation": operation,
        "parameter_fields": sorted(str(key) for key in (parameters or {})),
        "system_call": call,
        "file_path": file_path,
        "exception_type": exc.__class__.__name__,
        "errno": errno,
        "winerror": winerror,
        "system_message": strerror,
    }
    return message, {key: value for key, value in details.items() if value is not None}


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
