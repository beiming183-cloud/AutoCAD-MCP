"""User-owned desktop supervisor for a stable AutoCAD worker session.

Run this process from the user's normal desktop session, independently of an
MCP client.  It owns the AutoCAD PID/HWND/profile contract, keeps the window
minimized without hiding it, publishes health state, and never opens outputs or
terminates AutoCAD unless explicitly asked by the user.
"""

from __future__ import annotations

import argparse
import codecs
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from autocad_mcp.native_pipe import NativePipeError, discover_native_worker
from autocad_mcp.config import build_autocad_startup_command


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def supervisor_root() -> Path:
    configured = os.environ.get("AUTOCAD_MCP_SUPERVISOR_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if not local:
        local = str(Path.home() / "AppData" / "Local")
    return (Path(local) / "AutoCAD-MCP" / "supervisor").resolve()


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)


def _append_utf8_bom_log(path: Path, event: str, **data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.stat().st_size == 0:
        path.write_bytes(codecs.BOM_UTF8)
    record = {"timestamp": _utc_now(), "event": event, **data}
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _process_alive(process_id: int | None) -> bool:
    if not process_id or process_id <= 0:
        return False
    try:
        os.kill(process_id, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _windows_for_process(process_id: int) -> list[dict[str, Any]]:
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    windows: list[dict[str, Any]] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def callback(hwnd, _):
        found_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(found_pid))
        if int(found_pid.value) != int(process_id):
            return True
        length = int(user32.GetWindowTextLengthW(hwnd))
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        windows.append(
            {
                "hwnd": int(hwnd),
                "title": buffer.value,
                "visible": bool(user32.IsWindowVisible(hwnd)),
                "minimized": bool(user32.IsIconic(hwnd)),
            }
        )
        return True

    user32.EnumWindows(callback, 0)
    return windows


def _main_window(process_id: int) -> int | None:
    windows = _windows_for_process(process_id)
    fatal_tokens = (
        "fatal error",
        "error abort",
        "unhandled exception",
        "\u81f4\u547d\u9519\u8bef",
        "\u9519\u8bef\u4e2d\u65ad",
        "\u65e0\u6cd5\u7ee7\u7eed",
        "\u9519\u8bef\u4e2d\u65ad",
    )
    visible = [
        item
        for item in windows
        if item["visible"] and not any(token in item["title"].casefold() for token in fatal_tokens)
    ]
    if visible:
        return int(visible[0]["hwnd"])
    nonfatal = [
        item for item in windows if not any(token in item["title"].casefold() for token in fatal_tokens)
    ]
    # A hidden startup frame is still a valid identity anchor.  The caller
    # records its visibility separately and applies SW_SHOWMINNOACTIVE; it is
    # never treated as permission to steal the foreground window.
    return int(nonfatal[0]["hwnd"]) if nonfatal else None


def _fatal_window_titles(process_id: int) -> list[str]:
    tokens = (
        "fatal error",
        "error abort",
        "unhandled exception",
        "\u81f4\u547d\u9519\u8bef",
        "\u9519\u8bef\u4e2d\u65ad",
        "\u65e0\u6cd5\u7ee7\u7eed",
        "\u9519\u8bef\u4e2d\u65ad",
    )
    return [
        item["title"]
        for item in _windows_for_process(process_id)
        if any(marker in item["title"].casefold() for marker in tokens)
    ]


def _apply_window_mode(hwnd: int, mode: str) -> dict[str, Any]:
    if sys.platform != "win32":
        return {"applied": False, "reason": "not-windows"}
    import ctypes

    user32 = ctypes.windll.user32
    if not user32.IsWindow(int(hwnd)):
        return {"applied": False, "reason": "invalid-hwnd", "hwnd": hwnd}
    normalized = "quiet_minimized" if mode not in {"quiet_minimized", "visible"} else mode
    command = 7 if normalized == "quiet_minimized" else 4  # no activation
    foreground_before = int(user32.GetForegroundWindow() or 0)
    user32.ShowWindow(int(hwnd), command)
    return {
        "applied": True,
        "hwnd": int(hwnd),
        "mode": normalized,
        "visible": bool(user32.IsWindowVisible(int(hwnd))),
        "minimized": bool(user32.IsIconic(int(hwnd))),
        "foreground_before": foreground_before or None,
        "foreground_after": int(user32.GetForegroundWindow() or 0) or None,
    }


@dataclass(frozen=True)
class SupervisorConfig:
    acad_exe: str | None
    attach_pid: int | None = None
    window_mode: str = "quiet_minimized"
    output_root: str = r"D:\Codex\AutoCAD-MCP"
    activity_insights_path: str = r"D:\Codex\AutoCAD-MCP\activity-insights"
    poll_interval: float = 1.0
    startup_timeout: float = 120.0
    startup_script: str | None = None


class DesktopSupervisor:
    def __init__(
        self,
        config: SupervisorConfig,
        *,
        root: Path | None = None,
        launcher: Callable[..., subprocess.Popen] = subprocess.Popen,
        window_finder: Callable[[int], int | None] = _main_window,
        process_probe: Callable[[int | None], bool] = _process_alive,
    ):
        self.config = config
        self.root = Path(root or supervisor_root()).resolve()
        self.state_path = self.root / "state.json"
        self.log_path = self.root / "supervisor.log"
        self.stop_path = self.root / "stop.request"
        self.launcher = launcher
        self.window_finder = window_finder
        self.process_probe = process_probe
        self.process: subprocess.Popen | None = None
        self.acad_pid = config.attach_pid
        self.hwnd: int | None = None
        self.window_policy_applied = False
        self.launch_command: list[str] | None = None
        self.launch_provenance: dict[str, Any] = {}

    def _publish(self, lifecycle: str, **details: Any) -> dict[str, Any]:
        state = {
            "schema_version": 1,
            "lifecycle": lifecycle,
            "supervisor_pid": os.getpid(),
            "acad_pid": self.acad_pid,
            "hwnd": self.hwnd,
            "owned": self.process is not None,
            "heartbeat": _utc_now(),
            "config": asdict(self.config),
            "launch_command": self.launch_command,
            "launch_provenance": self.launch_provenance,
            **details,
        }
        _write_json_atomic(self.state_path, state)
        return state

    def launch(self) -> int:
        if self.acad_pid:
            if not self.process_probe(self.acad_pid):
                raise RuntimeError(f"Attached AutoCAD process is not alive: {self.acad_pid}")
            return self.acad_pid
        if not self.config.acad_exe:
            raise RuntimeError("acad_exe is required when attach_pid is not supplied")
        executable = Path(self.config.acad_exe).expanduser().resolve()
        if not executable.is_file():
            raise FileNotFoundError(f"AutoCAD executable was not found: {executable}")
        environment = os.environ.copy()
        environment.update(
            AUTOCAD_MCP_OUTPUT_ROOT=self.config.output_root,
            AUTOCAD_MCP_ACTIVITY_INSIGHTS_PATH=self.config.activity_insights_path,
            AUTOCAD_MCP_WINDOW_MODE=self.config.window_mode,
            AUTOCAD_MCP_VISIBLE="true",
        )
        Path(self.config.output_root).mkdir(parents=True, exist_ok=True)
        Path(self.config.activity_insights_path).mkdir(parents=True, exist_ok=True)
        startup_path = Path(self.config.startup_script).expanduser() if self.config.startup_script else None
        if startup_path and not startup_path.is_file():
            raise FileNotFoundError(f"AutoCAD startup script was not found: {startup_path}")
        self.launch_command, self.launch_provenance = build_autocad_startup_command(
            executable, startup_path
        )
        launch_kwargs: dict[str, Any] = {
            "cwd": str(executable.parent),
            "env": environment,
        }
        if os.environ.get("AUTOCAD_MCP_START_MINIMIZED", "true").lower() in (
            "1", "true", "yes", "on"
        ) and hasattr(subprocess, "STARTUPINFO"):
            try:
                startup_info = subprocess.STARTUPINFO()
                startup_info.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
                startup_info.wShowWindow = getattr(subprocess, "SW_MINIMIZE", 6)
                launch_kwargs["startupinfo"] = startup_info
            except Exception:
                pass
        self.process = self.launcher(self.launch_command, **launch_kwargs)
        self.acad_pid = int(self.process.pid)
        _append_utf8_bom_log(
            self.log_path,
            "autocad_launched",
            acad_pid=self.acad_pid,
            command=self.launch_command,
            command_provenance=self.launch_provenance,
        )
        return self.acad_pid

    def run(self) -> int:
        self.root.mkdir(parents=True, exist_ok=True)
        self.stop_path.unlink(missing_ok=True)
        try:
            self.launch()
            self._publish("PROCESS_STARTING")
            deadline = time.monotonic() + max(5.0, self.config.startup_timeout)
            stable_hwnd: int | None = None
            stable_reads = 0
            try:
                stability_required = max(
                    1,
                    min(10, int(os.environ.get("AUTOCAD_MCP_STARTUP_STABILITY_READS", "3"))),
                )
            except ValueError:
                stability_required = 3
            while not self.stop_path.exists():
                if not self.process_probe(self.acad_pid):
                    self._publish("PROCESS_EXITED", ready=False)
                    _append_utf8_bom_log(self.log_path, "autocad_exited", acad_pid=self.acad_pid)
                    return 1
                fatal_before_window = _fatal_window_titles(int(self.acad_pid))
                if fatal_before_window:
                    self._publish(
                        "PROCESS_CRASHED",
                        ready=False,
                        fatal_windows=fatal_before_window,
                        launch_command=self.launch_command,
                    )
                    _append_utf8_bom_log(
                        self.log_path,
                        "fatal_window_detected",
                        titles=fatal_before_window,
                    )
                    return 2
                candidate = self.window_finder(int(self.acad_pid))
                if candidate:
                    candidate = int(candidate)
                    if candidate == stable_hwnd:
                        stable_reads += 1
                    else:
                        stable_hwnd = candidate
                        stable_reads = 1
                    if stable_reads >= stability_required:
                        self.hwnd = candidate
                policy = None
                if self.hwnd and not self.window_policy_applied:
                    policy = _apply_window_mode(self.hwnd, self.config.window_mode)
                    self.window_policy_applied = bool(policy.get("applied"))
                native = None
                native_error = None
                try:
                    descriptor = discover_native_worker(preferred_process_id=int(self.acad_pid))
                    native = descriptor.to_dict() if descriptor else None
                except NativePipeError as exc:
                    native_error = {"code": exc.error_code, "message": str(exc), "details": exc.details}

                titles = [item["title"] for item in _windows_for_process(int(self.acad_pid))]
                fatal = [
                    title for title in titles
                    if any(marker in title.lower() for marker in ("fatal error", "error abort", "错误中断"))
                ]
                if fatal:
                    self._publish("PROCESS_CRASHED", ready=False, fatal_windows=fatal)
                    _append_utf8_bom_log(self.log_path, "fatal_window_detected", titles=fatal)
                    return 2
                ready = bool(self.hwnd and native)
                lifecycle = "PROCESS_READY" if ready else "PROCESS_STARTING"
                self._publish(
                    lifecycle,
                    ready=ready,
                    native_worker=native,
                    native_error=native_error,
                    window_policy=policy,
                    window_stability={
                        "candidate_hwnd": candidate,
                        "stable_reads": stable_reads,
                        "required": stability_required,
                    },
                )
                if not ready and time.monotonic() >= deadline:
                    self._publish(
                        "PROCESS_STARTUP_TIMEOUT",
                        ready=False,
                        native_worker=native,
                        native_error=native_error,
                    )
                    return 3
                time.sleep(max(0.1, self.config.poll_interval))
            self._publish("SUPERVISOR_STOPPED", ready=False, autocad_left_running=True)
            return 0
        except Exception as exc:
            self._publish(
                "SUPERVISOR_FAILED",
                ready=False,
                error={"type": type(exc).__name__, "message": str(exc)},
            )
            _append_utf8_bom_log(
                self.log_path,
                "supervisor_failed",
                exception_type=type(exc).__name__,
                message=str(exc),
            )
            return 4
        finally:
            self.stop_path.unlink(missing_ok=True)


def read_supervisor_state(root: Path | None = None) -> dict[str, Any]:
    path = Path(root or supervisor_root()) / "state.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {"lifecycle": "SUPERVISOR_ABSENT", "ready": False, "path": str(path)}
    state["supervisor_alive"] = _process_alive(state.get("supervisor_pid"))
    state["autocad_alive"] = _process_alive(state.get("acad_pid"))
    state["path"] = str(path)
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AutoCAD-MCP desktop supervisor")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="Run the supervisor in this desktop session")
    run.add_argument("--acad-exe")
    run.add_argument("--attach-pid", type=int)
    run.add_argument("--window-mode", choices=("quiet_minimized", "visible"), default="quiet_minimized")
    run.add_argument("--output-root", default=os.environ.get("AUTOCAD_MCP_OUTPUT_ROOT", r"D:\Codex\AutoCAD-MCP"))
    run.add_argument("--activity-insights-path", default=os.environ.get("AUTOCAD_MCP_ACTIVITY_INSIGHTS_PATH", r"D:\Codex\AutoCAD-MCP\activity-insights"))
    run.add_argument("--startup-script", default=os.environ.get("AUTOCAD_MCP_ACAD_SCRIPT"))
    subparsers.add_parser("status", help="Read supervisor and AutoCAD health")
    subparsers.add_parser("stop", help="Stop supervision while leaving AutoCAD running")
    args = parser.parse_args(argv)
    if args.command == "status":
        print(json.dumps(read_supervisor_state(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "stop":
        root = supervisor_root()
        root.mkdir(parents=True, exist_ok=True)
        (root / "stop.request").write_text(_utc_now(), encoding="utf-8")
        print(json.dumps({"ok": True, "autocad_left_running": True}, ensure_ascii=False))
        return 0
    config = SupervisorConfig(
        acad_exe=args.acad_exe,
        attach_pid=args.attach_pid,
        window_mode=args.window_mode,
        output_root=args.output_root,
        activity_insights_path=args.activity_insights_path,
        startup_script=args.startup_script,
    )
    return DesktopSupervisor(config).run()


if __name__ == "__main__":
    raise SystemExit(main())
