"""Backend detection and environment configuration."""

from __future__ import annotations

import os
import hashlib
import json
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Callable

import structlog

from autocad_mcp.runtime_health import (
    RuntimeHealthError,
    activity_insights_write_preflight,
    list_autocad_processes,
    win32_runtime_health,
)

log = structlog.get_logger()

# Paths
LISP_DIR = Path(__file__).resolve().parent.parent.parent / "lisp-code"
IPC_DIR = Path(os.environ.get("AUTOCAD_MCP_IPC_DIR", "C:/temp"))

# Backend selection
BACKEND_DEFAULT = "auto"  # auto | file_ipc | ezdxf


def _import_bounded_float(name: str, default: float, lower: float, upper: float) -> float:
    """Parse startup timing settings without allowing import-time failure."""
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(lower, min(upper, value))


# IPC timeout (seconds), clamped to [1, 300]
IPC_TIMEOUT = _import_bounded_float("AUTOCAD_MCP_IPC_TIMEOUT", 10.0, 1.0, 300.0)
DOCUMENT_TIMEOUT = _import_bounded_float(
    "AUTOCAD_MCP_DOCUMENT_TIMEOUT", 30.0, 5.0, 120.0
)

# Screenshot
ONLY_TEXT_FEEDBACK = os.environ.get("AUTOCAD_MCP_ONLY_TEXT", "true").lower() in (
    "1",
    "true",
    "yes",
)

# Win32 availability
WIN32_AVAILABLE = sys.platform == "win32"
_LAST_AUTOSTART_RECORD: dict | None = None


class AutoCADStartupError(RuntimeError):
    """A bounded, structured failure while starting AutoCAD."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "E_AUTOCAD_STARTUP_FAILED",
        recoverable: bool = True,
        recommended_action: str = "inspect_startup_evidence_and_retry_after_repair",
        details: dict | None = None,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.recoverable = recoverable
        self.recommended_action = recommended_action
        self.details = details or {}


def last_autostart_record() -> dict | None:
    return dict(_LAST_AUTOSTART_RECORD) if _LAST_AUTOSTART_RECORD else None


def _env_flag(name: str, default: bool = False) -> bool:
    """Read a conventional boolean environment variable."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _bounded_float(name: str, default: float, lower: float, upper: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(lower, min(upper, value))


def _bounded_int(name: str, default: int, lower: int, upper: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(lower, min(upper, value))


def _autostart_evidence_source() -> str:
    """Classify evidence so test doubles cannot masquerade as real AutoCAD."""
    configured = os.environ.get("AUTOCAD_MCP_EVIDENCE_SOURCE", "").strip().lower()
    if configured in {"real_autocad", "unit_test", "simulation"}:
        return configured
    # Pytest sets this for every test invocation.  Keeping the fallback here
    # makes old tests safe without requiring them to know the evidence schema.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return "unit_test"
    return "real_autocad"


def _autostart_evidence_path(*, source: str | None = None) -> Path:
    root = os.environ.get("AUTOCAD_MCP_OUTPUT_ROOT", r"D:\Codex\AutoCAD-MCP").strip()
    source = source or _autostart_evidence_source()
    suffix = ".unit-test" if source == "unit_test" else ".simulation" if source == "simulation" else ""
    return Path(root).expanduser() / "reports" / "startup" / f"last-autostart{suffix}.json"


def _write_autostart_evidence(payload: dict) -> str | None:
    """Best-effort durable startup evidence; never masks the real failure."""
    payload = dict(payload)
    payload.setdefault("evidence_source", _autostart_evidence_source())
    path = _autostart_evidence_path(source=payload["evidence_source"])
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(
            b"\xef\xbb\xbf"
            + json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        )
        temporary.replace(path)
        return str(path)
    except (OSError, TypeError, ValueError):
        return None


def _startup_signature(executable: Path, command: list[str]) -> str:
    material = json.dumps(
        {"executable": str(executable), "command": command},
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _read_recent_startup_failure(signature: str) -> dict | None:
    """Return a matching failure inside the cooldown window, if any."""
    if _env_flag("AUTOCAD_MCP_AUTOSTART_FORCE_RETRY"):
        return None
    # A test/simulation may exercise its own isolated cooldown record, but it
    # must never be read by a real launch.
    source = _autostart_evidence_source()
    path = _autostart_evidence_path(source=source)
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if (
        payload.get("state") != "failed"
        or payload.get("signature") != signature
        or payload.get("evidence_source", "real_autocad") != source
    ):
        return None
    try:
        age = max(0.0, time.time() - float(payload.get("finished_at_epoch", 0)))
    except (TypeError, ValueError):
        return None
    cooldown = _bounded_float("AUTOCAD_MCP_AUTOSTART_FAILURE_COOLDOWN", 900.0, 0.0, 86400.0)
    return payload if age <= cooldown else None


def _profile_switch() -> tuple[list[str], dict]:
    """Resolve an explicit AutoCAD profile/ARG without changing user settings.

    ``/p`` accepts either a profile name or an ARG exported by AutoCAD.  A
    registry export that merely has an ``.arg`` suffix is not enough evidence
    that AutoCAD can import it; callers should prefer a named profile when one
    has already been created in the active product registry.
    """
    value = (
        os.environ.get("AUTOCAD_MCP_ACAD_PROFILE_NAME", "").strip()
        or os.environ.get("AUTOCAD_MCP_ACAD_PROFILE", "").strip()
        or os.environ.get("AUTOCAD_MCP_PROFILE_ARG", "").strip()
    )
    mode = os.environ.get("AUTOCAD_MCP_PROFILE_MODE", "existing").strip().lower()
    if mode not in {"existing", "explicit", "isolated", "required"}:
        mode = "existing"
    if mode in {"isolated", "required"} and not value:
        raise AutoCADStartupError(
            "An isolated AutoCAD profile was requested but no profile name or .arg path was supplied",
            error_code="E_AUTOCAD_PROFILE_NOT_CONFIGURED",
            recoverable=False,
            recommended_action="export_or_reset_a_clean_autocad_profile_then_set_AUTOCAD_MCP_ACAD_PROFILE",
            details={
                "profile_mode": mode,
                "variables": ["AUTOCAD_MCP_ACAD_PROFILE", "AUTOCAD_MCP_PROFILE_ARG"],
            },
        )
    if not value:
        return [], {"mode": mode, "source": "autocad_default_profile", "value": None}
    profile_path = Path(value).expanduser()
    if profile_path.suffix.lower() == ".arg" and not profile_path.is_file():
        raise AutoCADStartupError(
            f"Configured AutoCAD profile ARG was not found: {profile_path}",
            error_code="E_AUTOCAD_PROFILE_NOT_FOUND",
            recoverable=False,
            recommended_action="export_a_valid_autocad_ARG_profile_and_update_AUTOCAD_MCP_ACAD_PROFILE",
            details={"profile_path": str(profile_path), "profile_mode": mode},
        )
    if profile_path.suffix.lower() != ".arg" and mode in {"isolated", "required"}:
        try:
            from autocad_mcp.runtime_health import autocad_profile_preflight

            profile_state = autocad_profile_preflight(value)
        except Exception as exc:
            raise AutoCADStartupError(
                f"Unable to verify configured AutoCAD profile: {value}",
                error_code="E_AUTOCAD_PROFILE_NOT_READY",
                recoverable=False,
                recommended_action="inspect_only_the_autocad_profiles_registry_branch",
                details={
                    "profile_name": value,
                    "exception_type": type(exc).__name__,
                    "message": str(exc) or type(exc).__name__,
                },
            ) from exc
        if not profile_state.get("exists"):
            raise AutoCADStartupError(
                f"Configured AutoCAD profile does not exist: {value}",
                error_code="E_AUTOCAD_PROFILE_NOT_FOUND",
                recoverable=False,
                recommended_action="create_the_named_profile_or_use_the_default_profile",
                details={"profile_name": value, "profile": profile_state},
            )
    return ["/p", value], {
        "mode": mode,
        "source": "explicit_environment",
        "value": value,
        "path_exists": profile_path.is_file() if profile_path.suffix.lower() == ".arg" else None,
        "kind": "arg_path" if profile_path.suffix.lower() == ".arg" else "profile_name",
    }


def _extra_startup_args() -> list[str]:
    raw = os.environ.get("AUTOCAD_MCP_ACAD_EXTRA_ARGS", "").strip()
    if not raw:
        return []
    try:
        # ``posix=False`` retains Windows paths; remove only quote pairs that
        # shlex intentionally preserves in that mode.
        tokens = shlex.split(raw, posix=False)
    except ValueError as exc:
        raise AutoCADStartupError(
            f"Invalid AUTOCAD_MCP_ACAD_EXTRA_ARGS: {exc}",
            error_code="E_AUTOCAD_STARTUP_ARGUMENTS",
            recoverable=False,
            recommended_action="fix_AUTOCAD_MCP_ACAD_EXTRA_ARGS_and_retry",
            details={"raw": raw},
        ) from exc
    return [
        token[1:-1]
        if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'"
        else token
        for token in tokens
    ]


def _official_startup_context() -> tuple[list[str], dict]:
    """Return the product/language switches used by the installed shortcut.

    AutoCAD installations can contain several verticals and language packs.
    Starting only ``acad.exe`` leaves product selection and relative user-data
    resolution to whichever registry state was last written.  The official
    shortcut is deterministic, so mirror it unless a caller explicitly opts
    out for a compatibility test.
    """
    if not WIN32_AVAILABLE or not _env_flag("AUTOCAD_MCP_USE_OFFICIAL_STARTUP_CONTEXT", True):
        return [], {"enabled": False, "product": None, "language": None}
    product = os.environ.get("AUTOCAD_MCP_ACAD_PRODUCT", "ACAD").strip() or "ACAD"
    language = os.environ.get("AUTOCAD_MCP_ACAD_LANGUAGE", "zh-CN").strip() or "zh-CN"
    return ["/product", product, "/language", language], {
        "enabled": True,
        "product": product,
        "language": language,
    }


def autocad_working_directory(executable: Path) -> Path:
    """Resolve the same Start In directory as the installed AutoCAD shortcut."""
    configured = os.environ.get("AUTOCAD_MCP_ACAD_WORKDIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    userdata_cache = executable.parent / "UserDataCache"
    if userdata_cache.is_dir():
        return userdata_cache
    return executable.parent


def build_autocad_startup_command(
    executable: Path, startup_path: Path | None = None
) -> tuple[list[str], dict]:
    """Build a shell-free AutoCAD command and return its provenance."""
    profile_args, profile = _profile_switch()
    context_args, context = _official_startup_context()
    extra_args = _extra_startup_args()
    if _env_flag("AUTOCAD_MCP_ACAD_NOHARDWARE", False):
        extra_args = ["/nohardware", *extra_args]
    command = [str(executable), *context_args, "/nologo", *profile_args, *extra_args]
    if startup_path is not None:
        command.extend(["/b", str(startup_path)])
    return command, {
        "profile": profile,
        "startup_context": context,
        "working_directory": str(autocad_working_directory(executable)),
        "extra_args": extra_args,
    }


def _startup_window_process_id(hwnd: int | None) -> int | None:
    """Return a window owner PID without making startup depend on COM."""
    if not hwnd or not WIN32_AVAILABLE:
        return None
    try:
        import win32process

        return int(win32process.GetWindowThreadProcessId(int(hwnd))[1])
    except Exception:
        return None


def _startup_process_name(process_id: int | None) -> str | None:
    """Return a process image name when the host permits process inspection."""
    if not process_id or not WIN32_AVAILABLE:
        return None
    handle = None
    try:
        import win32api
        import win32con
        import win32process

        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
            False,
            int(process_id),
        )
        return Path(win32process.GetModuleFileNameEx(handle, 0)).name.casefold()
    except Exception:
        return None
    finally:
        if handle is not None:
            try:
                win32api.CloseHandle(handle)
            except Exception:
                pass


def _startup_process_is_alive(process_id: int | None) -> bool | None:
    """Return ``True``/``False`` when Windows can query the launch PID."""
    if not process_id or not WIN32_AVAILABLE:
        return None
    try:
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(process_id))
        if not handle:
            # Access restrictions are not proof that the process exited. The
            # launcher loop has a separate Popen.poll() check for true exits.
            return None
        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return None
            return exit_code.value == 259  # STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        return None


def _startup_fatal_window_is_relevant(
    *, window_process_id: int | None, managed_process_id: int | None, executable_name: str | None
) -> bool:
    """Keep stale CER/WebView windows out of the launcher's crash evidence."""
    if managed_process_id is not None:
        return window_process_id == int(managed_process_id)
    return (executable_name or "").casefold() == "acad.exe"


def _apply_quiet_startup_window_policy(
    hwnd: int | None, foreground_before: int | None
) -> dict:
    """Minimize a backend-owned CAD window without activating it."""
    if not _env_flag("AUTOCAD_MCP_START_MINIMIZED", True):
        return {"applied": False, "reason": "disabled"}
    mode = os.environ.get("AUTOCAD_MCP_WINDOW_MODE", "quiet_minimized").strip().lower()
    if mode in {"foreground", "recording", "preserve", "user", "unchanged"}:
        return {"applied": False, "reason": "foreground_mode", "mode": mode}
    if not hwnd or not WIN32_AVAILABLE:
        return {"applied": False, "reason": "no_window"}
    try:
        import win32con
        import win32gui

        current = int(win32gui.GetForegroundWindow() or 0)
        win32gui.ShowWindow(int(hwnd), win32con.SW_SHOWMINNOACTIVE)
        if foreground_before and current == int(hwnd) and current != int(foreground_before):
            try:
                win32gui.SetForegroundWindow(int(foreground_before))
            except Exception:
                pass
        return {
            "applied": True,
            "mode": "minimized",
            "hwnd": int(hwnd),
            "foreground_before": foreground_before,
            "foreground_after": int(win32gui.GetForegroundWindow() or 0) or None,
        }
    except Exception as exc:
        return {
            "applied": False,
            "reason": "window_policy_failed",
            "hwnd": int(hwnd),
            "exception_type": type(exc).__name__,
            "message": str(exc) or type(exc).__name__,
        }


def _default_startup_crash_probe(
    hwnd: int | None = None, process_id: int | None = None
) -> dict:
    """Probe fatal AutoCAD dialogs without importing the backend module."""
    if not WIN32_AVAILABLE:
        return {"crashed": False}
    if process_id and _startup_process_is_alive(process_id) is False:
        return {
            "crashed": True,
            "reason": "process_exited",
            "process_id": int(process_id),
        }
    try:
        import win32gui

        tokens = (
            "fatal error",
            "error abort",
            "unhandled exception",
            "致命错误",
            "错误中断",
            "无法继续",
            "\u9519\u8bef\u4e2d\u65ad",
        )
        tokens = tokens + (
            "\u81f4\u547d\u9519\u8bef",
            "\u9519\u8bef\u4e2d\u65ad",
            "\u65e0\u6cd5\u7ee7\u7eed",
        )
        found: list[dict] = []

        def callback(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return True
            window_pid = _startup_window_process_id(hwnd)
            title = str(win32gui.GetWindowText(hwnd) or "")
            child_text: list[str] = []

            def child_callback(child, __):
                value = str(win32gui.GetWindowText(child) or "")
                if value:
                    child_text.append(value)
                return True

            try:
                win32gui.EnumChildWindows(hwnd, child_callback, None)
            except Exception:
                pass
            combined = " ".join([title, *child_text]).casefold()
            if any(token in combined for token in tokens):
                executable_name = _startup_process_name(window_pid)
                if _startup_fatal_window_is_relevant(
                    window_process_id=window_pid,
                    managed_process_id=process_id,
                    executable_name=executable_name,
                ):
                    found.append(
                        {
                            "hwnd": int(hwnd),
                            "title": title,
                            "process_id": window_pid,
                            "message": " | ".join(child_text[:8]),
                        }
                    )
            return True

        win32gui.EnumWindows(callback, None)
        if found:
            return {
                "crashed": True,
                "reason": "fatal_error_dialog",
                "process_id": process_id,
                "dialog": found[0],
            }
    except Exception:
        pass
    return {"crashed": False, "process_id": process_id}


def _autostart_autocad(
    find_window: Callable[[], int | None],
    *,
    crash_probe: Callable[..., dict] | None = None,
) -> int | None:
    """Start AutoCAD and wait for a stable, non-fatal main window.

    A visible HWND is only a candidate.  AutoCAD can expose a fatal-error
    dialog with an ``acad.exe`` owner before the real application is ready;
    the old implementation returned that HWND immediately and every campaign
    round retried the same doomed launch.  This function now performs bounded
    stability reads, persists evidence, and trips a cooldown circuit breaker
    after a reproducible startup failure.
    """
    if not WIN32_AVAILABLE or not _env_flag("AUTOCAD_MCP_AUTOSTART"):
        return None

    executable = os.environ.get("AUTOCAD_MCP_ACAD_EXE", "").strip()
    if not executable:
        raise AutoCADStartupError(
            "AUTOCAD_MCP_AUTOSTART is enabled but AUTOCAD_MCP_ACAD_EXE is not set",
            error_code="E_AUTOCAD_EXECUTABLE_NOT_CONFIGURED",
            recoverable=False,
            recommended_action="set_AUTOCAD_MCP_ACAD_EXE_to_the_installed_acad_exe",
            details={"variable": "AUTOCAD_MCP_ACAD_EXE"},
        )

    executable_path = Path(executable).expanduser()
    if not executable_path.is_file():
        raise AutoCADStartupError(
            f"Configured AutoCAD executable was not found: {executable_path}",
            error_code="E_AUTOCAD_EXECUTABLE_NOT_FOUND",
            recoverable=False,
            recommended_action="verify_AUTOCAD_MCP_ACAD_EXE_and_the_AutoCAD_installation",
            details={"executable": str(executable_path)},
        )

    # Registry profile creation is deliberately opt-in.  When enabled it is
    # confined to AutoCAD's HKCU Profiles branch and never touches activation
    # or licensing state.
    if _env_flag("AUTOCAD_MCP_CREATE_MINIMAL_PROFILE", False):
        from autocad_mcp.runtime_health import ensure_minimal_autocad_profile

        profile_name = (
            os.environ.get("AUTOCAD_MCP_ACAD_PROFILE_NAME", "MCP-Minimal").strip()
            or "MCP-Minimal"
        )
        profile_result = ensure_minimal_autocad_profile(profile_name=profile_name)
        if not profile_result.get("ok") or not profile_result.get("ready"):
            raise AutoCADStartupError(
                "The requested minimal AutoCAD profile is not ready",
                error_code="E_AUTOCAD_PROFILE_NOT_READY",
                recoverable=False,
                recommended_action="inspect_profile_preflight_and_fix_only_the_autocad_profile_branch",
                details={"profile": profile_result},
            )

    apply_activity_policy = _env_flag("AUTOCAD_MCP_APPLY_ACTIVITY_POLICY", False)
    disable_activity_insights = _env_flag("AUTOCAD_MCP_DISABLE_ACTIVITY_INSIGHTS", False)
    configured_activity_path = os.environ.get("AUTOCAD_MCP_ACTIVITY_INSIGHTS_PATH", "").strip()
    # Disabling the component is itself an explicit opt-in and does not
    # require the component's directory to be writable.  A custom path does
    # require the broader apply flag because it mutates the user's profile.
    policy_requested = apply_activity_policy
    activity_profile = None
    if policy_requested:
        activity_profile = activity_insights_write_preflight()
        if not activity_profile["ok"]:
            raise RuntimeHealthError(
                "AutoCAD Activity Insights directory is not writable before startup",
                error_code="E_AUTOCAD_PROFILE_UNWRITABLE",
                details=activity_profile,
                recommended_action=(
                    "Fix the directory ACL, set AUTOCAD_MCP_ACTIVITY_INSIGHTS_PATH to a writable D: path, "
                    "or remove the Activity Insights policy flags and preserve the user's profile."
                ),
            )

    startup_script = os.environ.get("AUTOCAD_MCP_ACAD_SCRIPT", "").strip()
    startup_path = Path(startup_script).expanduser() if startup_script else None
    if startup_path and not startup_path.is_file():
        raise AutoCADStartupError(
            f"Configured AutoCAD startup script was not found: {startup_path}",
            error_code="E_AUTOCAD_STARTUP_SCRIPT_NOT_FOUND",
            recoverable=False,
            recommended_action="remove_or_fix_AUTOCAD_MCP_ACAD_SCRIPT",
            details={"startup_script": str(startup_path)},
        )

    generated_script = None
    policy_lines = []
    if disable_activity_insights:
        policy_lines.extend(["_.SETVAR", "ACTIVITYINSIGHTSSUPPORT", "0"])
    if apply_activity_policy and configured_activity_path:
        activity_value = configured_activity_path.replace("\\", "/")
        if any(character.isspace() for character in activity_value):
            activity_value = f'"{activity_value}"'
        policy_lines.extend(
            ["_.SETVAR", "ACTIVITYINSIGHTSPATH", activity_value]
        )
    if policy_lines:
        ipc_dir = Path(os.environ.get("AUTOCAD_MCP_IPC_DIR", "C:/temp"))
        ipc_dir.mkdir(parents=True, exist_ok=True)
        generated_script = ipc_dir / f"autocad_mcp_startup_{os.getpid()}.scr"
        existing = startup_path.read_text(encoding="utf-8") if startup_path else ""
        generated_script.write_text("\n".join(policy_lines) + "\n" + existing, encoding="utf-8")
        startup_path = generated_script
    elif configured_activity_path and not apply_activity_policy:
        log.info(
            "activity_insights_path_ignored_without_policy_flag",
            configured_path=configured_activity_path,
            recommended_flag="AUTOCAD_MCP_APPLY_ACTIVITY_POLICY=true",
        )

    command, command_provenance = build_autocad_startup_command(executable_path, startup_path)
    signature = _startup_signature(executable_path, command)
    prior_failure = _read_recent_startup_failure(signature)
    if prior_failure is not None:
        raise AutoCADStartupError(
            "AutoCAD startup is blocked after a recent identical fatal failure; refusing to loop",
            error_code="E_AUTOCAD_STARTUP_BLOCKED",
            recoverable=True,
            recommended_action=(
                "repair_or_reset_the_autocad_profile_or_installation_then_set_AUTOCAD_MCP_AUTOSTART_FORCE_RETRY=true_once"
            ),
            details={
                "signature": signature,
                "previous_failure": prior_failure,
                "command": command,
                "command_provenance": command_provenance,
            },
        )

    global _LAST_AUTOSTART_RECORD
    launch_token = f"launch-{uuid.uuid4().hex}"
    started_at_epoch = time.time()
    log.info(
        "autocad_autostart",
        executable=str(executable_path),
        launch_token=launch_token,
        command=command,
        command_provenance=command_provenance,
    )
    try:
        foreground_before = None
        if WIN32_AVAILABLE:
            try:
                import win32gui

                foreground_before = int(win32gui.GetForegroundWindow() or 0) or None
            except Exception:
                pass
        popen_kwargs = {"cwd": str(autocad_working_directory(executable_path))}
        # AutoCAD remains a real, taskbar-visible process, but the first frame
        # should not jump in front of the user's other work.  SW_MINIMIZE is a
        # launch hint only; the backend reapplies the no-activate policy after
        # the actual main HWND is known.
        if _env_flag("AUTOCAD_MCP_START_MINIMIZED", True) and hasattr(
            subprocess, "STARTUPINFO"
        ):
            try:
                startup_info = subprocess.STARTUPINFO()
                startup_info.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
                startup_info.wShowWindow = getattr(subprocess, "SW_MINIMIZE", 6)
                popen_kwargs["startupinfo"] = startup_info
            except Exception:
                # Some test doubles and non-Windows shims expose Popen but not
                # a usable STARTUPINFO implementation.
                pass
        process = subprocess.Popen(command, **popen_kwargs)
        launcher_pid = getattr(process, "pid", None)
        if not isinstance(launcher_pid, int):
            launcher_pid = None
        _LAST_AUTOSTART_RECORD = {
            "launch_token": launch_token,
            "launcher_pid": launcher_pid,
            "executable": str(executable_path),
            "started_at": time.time(),
            "started_at_epoch": started_at_epoch,
            "hwnd": None,
            "state": "starting",
            "phase": "launch",
            "signature": signature,
            "command": command,
            "command_provenance": command_provenance,
            "stability_reads_required": _bounded_int(
                "AUTOCAD_MCP_STARTUP_STABILITY_READS", 3, 1, 10
            ),
        }

        timeout = _bounded_float("AUTOCAD_MCP_ACAD_STARTUP_TIMEOUT", 75.0, 5.0, 180.0)
        stability_reads_required = int(_LAST_AUTOSTART_RECORD["stability_reads_required"])
        stability_interval = _bounded_float(
            "AUTOCAD_MCP_STARTUP_STABILITY_INTERVAL", 0.25, 0.05, 5.0
        )
        launcher_exit_grace = _bounded_float(
            "AUTOCAD_MCP_STARTUP_EXIT_GRACE", 2.0, 0.1, 15.0
        )
        deadline = time.monotonic() + timeout
        last_hwnd = None
        stable_reads = 0
        launcher_exit_at = None

        def locate_window() -> int | None:
            """Prefer the HWND owned by this launch, with old finder compatibility."""
            if launcher_pid is None:
                return find_window()
            try:
                return find_window(preferred_process_id=launcher_pid)  # type: ignore[call-arg]
            except TypeError:
                # Third-party/test finders may still expose the original
                # zero-argument callback contract.
                return find_window()

        def probe_crash() -> dict:
            if crash_probe is None:
                return _default_startup_crash_probe(process_id=launcher_pid)
            try:
                return crash_probe(None, launcher_pid)
            except TypeError:
                return crash_probe()

        while time.monotonic() < deadline:
            _LAST_AUTOSTART_RECORD["phase"] = "window_discovery"
            crash = probe_crash() or {}
            if crash.get("crashed"):
                raise AutoCADStartupError(
                    "AutoCAD entered a fatal startup state",
                    error_code="E_AUTOCAD_CRASHED",
                    recoverable=True,
                    recommended_action=(
                        "reset_or_repair_the_autocad_profile_or_installation_before_retrying"
                    ),
                    details={"crash": crash, "command": command},
                )

            hwnd = locate_window()
            if hwnd:
                hwnd = int(hwnd)
                window_policy = _apply_quiet_startup_window_policy(
                    hwnd, foreground_before
                )
                if hwnd == last_hwnd:
                    stable_reads += 1
                else:
                    last_hwnd = hwnd
                    stable_reads = 1
                _LAST_AUTOSTART_RECORD.update(
                    hwnd=hwnd,
                    state="window-stabilizing" if stable_reads < stability_reads_required else "window-ready",
                    stability_reads=stable_reads,
                    window_policy=window_policy,
                )
                if stable_reads >= stability_reads_required:
                    _LAST_AUTOSTART_RECORD["phase"] = "settle"
                    settle = _bounded_float("AUTOCAD_MCP_STARTUP_SETTLE", 0.75, 0.0, 10.0)
                    if settle:
                        time.sleep(settle)
                    post_settle_crash = probe_crash() or {}
                    confirmed = locate_window()
                    if post_settle_crash.get("crashed"):
                        raise AutoCADStartupError(
                            "AutoCAD failed during startup settling",
                            error_code="E_AUTOCAD_CRASHED",
                            recoverable=True,
                            recommended_action="reset_or_repair_the_autocad_profile_or_installation_before_retrying",
                            details={"crash": post_settle_crash, "command": command},
                        )
                    if confirmed and int(confirmed) == hwnd:
                        _LAST_AUTOSTART_RECORD.update(
                            state="window-stable",
                            phase="ready",
                            finished_at_epoch=time.time(),
                        )
                        log.info("autocad_autostart_ready", hwnd=hwnd, stability_reads=stable_reads)
                        _write_autostart_evidence(
                            {
                                **_LAST_AUTOSTART_RECORD,
                                "state": "succeeded",
                                "finished_at_epoch": time.time(),
                            }
                        )
                        return hwnd
                    stable_reads = 0
                    last_hwnd = None

            # Popen can represent a short-lived launcher while acad.exe is a
            # child, so only treat an exit as fatal after a small grace period
            # and when no usable HWND has appeared.
            poll = getattr(process, "poll", None)
            if callable(poll):
                try:
                    exit_code = poll()
                except Exception:
                    exit_code = None
                if isinstance(exit_code, int):
                    if launcher_exit_at is None:
                        launcher_exit_at = time.monotonic()
                    elif time.monotonic() - launcher_exit_at >= launcher_exit_grace and not hwnd:
                        raise AutoCADStartupError(
                            f"AutoCAD launcher exited before exposing a usable main window (exit code {exit_code})",
                            error_code="E_AUTOCAD_STARTUP_EXITED",
                            recoverable=True,
                            recommended_action="inspect_startup_evidence_and_restart_autocad_manually_after_repair",
                            details={"exit_code": exit_code, "command": command},
                        )
            time.sleep(stability_interval)

        raise AutoCADStartupError(
            f"AutoCAD did not expose a stable usable main window within {timeout:g} seconds",
            error_code="E_AUTOCAD_STARTUP_TIMEOUT",
            recoverable=True,
            recommended_action="inspect_startup_evidence_and_verify_autocad_profile_and_installation",
            details={"timeout_seconds": timeout, "command": command, "last_record": _LAST_AUTOSTART_RECORD},
        )
    except AutoCADStartupError as exc:
        finished = time.time()
        details = dict(exc.details)
        details.setdefault("command", command)
        evidence = {
            "schema_version": 1,
            "state": "failed",
            "finished_at_epoch": finished,
            "launch_token": launch_token,
            "executable": str(executable_path),
            "signature": signature,
            "command": command,
            "command_provenance": command_provenance,
            "error_code": exc.error_code,
            "message": str(exc),
            "details": details,
        }
        evidence_path = _write_autostart_evidence(evidence)
        details["evidence_path"] = evidence_path
        _LAST_AUTOSTART_RECORD = {
            **(_LAST_AUTOSTART_RECORD or {}),
            "state": "failed",
            "phase": "failed",
            "finished_at_epoch": finished,
            "error_code": exc.error_code,
            "evidence_path": evidence_path,
        }
        raise AutoCADStartupError(
            str(exc),
            error_code=exc.error_code,
            recoverable=exc.recoverable,
            recommended_action=exc.recommended_action,
            details=details,
        ) from exc
    except Exception as exc:
        if _LAST_AUTOSTART_RECORD is not None:
            _LAST_AUTOSTART_RECORD.update(state="failed", phase="failed")
        details = {"exception_type": type(exc).__name__, "command": command}
        evidence = {
            "schema_version": 1,
            "state": "failed",
            "finished_at_epoch": time.time(),
            "launch_token": launch_token,
            "executable": str(executable_path),
            "signature": signature,
            "command": command,
            "command_provenance": command_provenance,
            "error_code": "E_AUTOCAD_STARTUP_FAILED",
            "message": str(exc) or type(exc).__name__,
            "details": details,
        }
        details["evidence_path"] = _write_autostart_evidence(evidence)
        raise AutoCADStartupError(
            str(exc) or type(exc).__name__,
            error_code="E_AUTOCAD_STARTUP_FAILED",
            recoverable=True,
            recommended_action="inspect_startup_evidence_and_retry_after_repair",
            details=details,
        ) from exc
    finally:
        if generated_script is not None:
            try:
                generated_script.unlink(missing_ok=True)
            except OSError:
                pass


def _current_backend_env() -> str:
    """Read backend selection from env with normalization."""
    return os.environ.get("AUTOCAD_MCP_BACKEND", BACKEND_DEFAULT).strip().lower()


def _is_wsl() -> bool:
    """Detect WSL Linux runtime."""
    if os.environ.get("WSL_INTEROP"):
        return True
    try:
        return "microsoft" in os.uname().release.lower()
    except AttributeError:
        return False


def _write_debug_snapshot(backend_env: str):
    """Optionally write backend detection debug information.

    Set AUTOCAD_MCP_DEBUG_DETECT_FILE to enable.
    """
    debug_file = os.environ.get("AUTOCAD_MCP_DEBUG_DETECT_FILE", "").strip()
    if not debug_file:
        return

    try:
        debug_path = Path(debug_file)
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        with debug_path.open("w", encoding="utf-8") as f:
            f.write(f"sys.platform={sys.platform}\n")
            f.write(f"WIN32_AVAILABLE={WIN32_AVAILABLE}\n")
            f.write(f"BACKEND_ENV={backend_env}\n")
            f.write(f"python={sys.executable}\n")
    except Exception:
        # Best-effort only; never fail backend detection due debug writes.
        pass


def detect_backend() -> str:
    """Return the backend name to use: 'file_ipc' or 'ezdxf'.

    Raises RuntimeError with actionable message if explicit backend fails.
    """
    backend_env = _current_backend_env()
    _write_debug_snapshot(backend_env)

    if backend_env == "ezdxf":
        return "ezdxf"

    if backend_env in ("auto", "file_ipc"):
        if WIN32_AVAILABLE:
            native_mode = os.environ.get("AUTOCAD_MCP_NATIVE_PLUGIN", "auto").strip().lower()
            if native_mode != "off":
                from autocad_mcp.native_pipe import NativePipeError, discover_native_worker

                try:
                    native_worker = discover_native_worker()
                except NativePipeError:
                    # Ambiguity and protocol mismatch must fail closed instead
                    # of silently selecting COM or an unrelated AutoCAD process.
                    raise
                if native_worker is not None:
                    log.info(
                        "autocad_native_worker_found",
                        process_id=native_worker.process_id,
                        session_id=native_worker.session_id,
                    )
                    return "file_ipc"
                if native_mode == "required":
                    return "file_ipc"
            runtime = win32_runtime_health()
            processes = list_autocad_processes()
            if not runtime["ok"]:
                if backend_env == "file_ipc" or processes:
                    raise RuntimeHealthError(
                        "The pywin32 runtime required for AutoCAD COM is unhealthy",
                        error_code="E_PYWIN32_BROKEN",
                        details={"runtime": runtime, "autocad_processes": processes},
                        recommended_action="repair_pywin32_in_the_same_python_used_by_the_mcp",
                    )
                log.info("win32_runtime_unhealthy_fallback_ezdxf", runtime=runtime)
                return "ezdxf"
            try:
                from autocad_mcp.backends.file_ipc import find_autocad_window

                hwnd = find_autocad_window()
                if not hwnd and processes:
                    raise RuntimeHealthError(
                        "acad.exe is alive but exposes no usable main window",
                        error_code="E_AUTOCAD_GHOST_PROCESS",
                        details={"autocad_processes": processes},
                        recommended_action="terminate_or_close_orphaned_acad_process_then_start_autocad_manually",
                    )
                if not hwnd:
                    hwnd = _autostart_autocad(find_autocad_window)
                if hwnd:
                    log.info("autocad_window_found", hwnd=hwnd)
                    return "file_ipc"
                elif backend_env == "file_ipc":
                    raise RuntimeError(
                        "AUTOCAD_MCP_BACKEND=file_ipc but no AutoCAD window found. "
                        "Start AutoCAD and open or create a drawing."
                    )
            except ImportError:
                if backend_env == "file_ipc":
                    raise RuntimeError(
                        "AUTOCAD_MCP_BACKEND=file_ipc requires pywin32. "
                        "Install with: pip install pywin32"
                    )
                log.info("win32_deps_missing_fallback_ezdxf")
        elif backend_env == "file_ipc":
            raise RuntimeError(
                "AUTOCAD_MCP_BACKEND=file_ipc requires Windows. "
                "Use AUTOCAD_MCP_BACKEND=ezdxf for headless mode."
            )
        elif _is_wsl():
            log.info(
                "wsl_linux_python_fallback_ezdxf",
                platform=sys.platform,
                python=sys.executable,
                hint="Launch MCP with Windows python.exe for File IPC backend.",
            )

    log.info("using_ezdxf_backend")
    return "ezdxf"
