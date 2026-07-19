"""Run a pytest slice with a hard timeout and durable evidence.

This wrapper is intentionally dependency-free.  It prevents a stalled test,
approval bridge, or native import from holding an MCP/Codex turn forever.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _terminate_process_tree(process: subprocess.Popen) -> dict:
    """Stop a bounded test and any descendants without relying on a shell."""
    pid = int(getattr(process, "pid", 0) or 0)
    result: dict = {"pid": pid, "method": None, "return_code": None}
    if pid <= 0:
        return result

    if os.name == "nt":
        # ``Popen.kill`` only targets the pytest launcher.  pytest plugins and
        # native helpers can survive it, so use the OS process-tree command.
        try:
            completed = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="mbcs",
                errors="replace",
                timeout=5,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            result.update(
                method="taskkill",
                return_code=completed.returncode,
                output=(completed.stdout or completed.stderr or "").strip()[-1000:],
            )
        except (OSError, subprocess.SubprocessError) as exc:
            result.update(method="taskkill_failed", error=f"{type(exc).__name__}: {exc}")
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
            result["method"] = "killpg"
        except (OSError, ProcessLookupError) as exc:
            result.update(method="killpg_failed", error=f"{type(exc).__name__}: {exc}")

    # The process may have exited between taskkill/killpg and this fallback.
    try:
        if process.poll() is None:
            process.kill()
            result["fallback"] = "popen.kill"
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                result["fallback_wait"] = "timeout"
    except (OSError, ProcessLookupError):
        result["fallback"] = "popen.kill_failed"
    result["alive_after"] = process.poll() is None
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument(
        "--output-root",
        default=os.environ.get("AUTOCAD_MCP_OUTPUT_ROOT", r"D:\Codex\AutoCAD-MCP"),
    )
    parser.add_argument("tests", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    requested = list(args.tests)
    if requested and requested[0] == "--":
        requested.pop(0)
    if not requested:
        requested = ["tests"]

    output_root = Path(args.output_root).expanduser().resolve()
    report_dir = output_root / "reports" / "test-runs"
    output_root_error = None
    try:
        report_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # A Codex sandbox may deny the user's D: directory even though the
        # installed MCP can write there outside the sandbox. Keep the run
        # bounded and leave a local record instead of hanging or crashing.
        output_root_error = {"type": type(exc).__name__, "message": str(exc)}
        report_dir = Path(tempfile.gettempdir()) / "AutoCAD-MCP" / "reports" / "test-runs"
        report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = report_dir / f"pytest-{stamp}.log"
    report_path = report_dir / f"pytest-{stamp}.json"
    basetemp = report_dir / f"pytest-tmp-{stamp}"
    try:
        basetemp.mkdir(parents=True, exist_ok=True)
    except OSError:
        basetemp = Path(tempfile.gettempdir()) / "AutoCAD-MCP" / f"pytest-tmp-{stamp}"
        basetemp.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment.setdefault("PYTEST_ADDOPTS", "-p no:cacheprovider")
    source_root = Path.cwd() / "src"
    if source_root.is_dir():
        existing_pythonpath = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = os.pathsep.join(
            [str(source_root), existing_pythonpath] if existing_pythonpath else [str(source_root)]
        )
    command = [
        sys.executable,
        "-B",
        "-m",
        "pytest",
        "--basetemp",
        str(basetemp),
        *requested,
    ]
    started = time.monotonic()
    state = "completed"
    return_code: int | None = None
    timed_out = False
    termination: dict | None = None
    try:
        with log_path.open("w", encoding="utf-8-sig", newline="\n") as stream:
            # pytest writes directly to the inherited file descriptor, so the
            # TextIOWrapper cannot lazily emit the BOM on its behalf.
            stream.write("\ufeff")
            stream.flush()
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if os.name == "nt":
                creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            launch_options = {"creationflags": creationflags}
            if os.name != "nt":
                launch_options = {"start_new_session": True}
            process = subprocess.Popen(
                command,
                cwd=str(Path.cwd()),
                env=environment,
                stdout=stream,
                stderr=subprocess.STDOUT,
                **launch_options,
            )
            try:
                return_code = process.wait(timeout=max(1.0, float(args.timeout)))
            except subprocess.TimeoutExpired:
                timed_out = True
                state = "timeout"
                termination = _terminate_process_tree(process)
                try:
                    return_code = process.wait(timeout=10.0)
                except subprocess.TimeoutExpired:
                    # A broken launcher must not hold the wrapper forever.
                    return_code = -9
    except OSError as exc:
        state = "launcher_error"
        return_code = 127
        log_path.write_text(
            f"{type(exc).__name__}: {exc}\n", encoding="utf-8-sig"
        )

    report = {
        "schema_version": 1,
        "started_at": _now(),
        "finished_at": _now(),
        "state": state,
        "timed_out": timed_out,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "timeout_seconds": float(args.timeout),
        "command": command,
        "cwd": str(Path.cwd()),
        "requested_output_root": str(output_root),
        "output_root_error": output_root_error,
        "return_code": return_code,
        "log_path": str(log_path),
        "basetemp": str(basetemp),
        "termination": termination,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 124 if timed_out else int(return_code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
