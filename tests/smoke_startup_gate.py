"""Simulate a fatal AutoCAD launch and verify the cooldown circuit breaker."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autocad_mcp import config


def main() -> int:
    env_names = (
        "AUTOCAD_MCP_AUTOSTART",
        "AUTOCAD_MCP_ACAD_EXE",
        "AUTOCAD_MCP_OUTPUT_ROOT",
        "AUTOCAD_MCP_START_MINIMIZED",
        "AUTOCAD_MCP_STARTUP_SETTLE",
        "AUTOCAD_MCP_PROFILE_MODE",
        "AUTOCAD_MCP_ACAD_PROFILE",
        "AUTOCAD_MCP_PROFILE_ARG",
        "AUTOCAD_MCP_ACAD_EXTRA_ARGS",
        "AUTOCAD_MCP_AUTOSTART_FORCE_RETRY",
    )
    old_env = {name: os.environ.get(name) for name in env_names}
    old_win32 = config.WIN32_AVAILABLE
    old_preflight = config.activity_insights_write_preflight
    old_popen = config.subprocess.Popen
    try:
        with tempfile.TemporaryDirectory(prefix="autocad-mcp-startup-") as raw:
            root = Path(raw)
            executable = root / "acad.exe"
            executable.write_bytes(b"test-only")
            output = root / "output"
            os.environ.update(
                {
                    "AUTOCAD_MCP_AUTOSTART": "true",
                    "AUTOCAD_MCP_ACAD_EXE": str(executable),
                    "AUTOCAD_MCP_OUTPUT_ROOT": str(output),
                    "AUTOCAD_MCP_START_MINIMIZED": "false",
                    "AUTOCAD_MCP_STARTUP_SETTLE": "0",
                    "AUTOCAD_MCP_PROFILE_MODE": "existing",
                }
            )
            for name in ("AUTOCAD_MCP_ACAD_PROFILE", "AUTOCAD_MCP_PROFILE_ARG", "AUTOCAD_MCP_ACAD_EXTRA_ARGS"):
                os.environ.pop(name, None)
            config.WIN32_AVAILABLE = True
            config.activity_insights_write_preflight = lambda: {
                "ok": True,
                "checked": False,
                "path": str(output / "activity"),
            }
            calls = {"popen": 0}

            def fake_popen(*args, **kwargs):
                calls["popen"] += 1
                return SimpleNamespace(pid=1234)

            config.subprocess.Popen = fake_popen
            crash = {
                "crashed": True,
                "reason": "fatal_error_dialog",
                "dialog": {"title": "AutoCAD Error Aborting"},
            }
            try:
                config._autostart_autocad(lambda: None, crash_probe=lambda *_: crash)
            except config.AutoCADStartupError as first:
                assert first.error_code == "E_AUTOCAD_CRASHED"
            else:
                raise AssertionError("fatal startup was not classified")

            evidence = output / "reports" / "startup" / "last-autostart.json"
            assert evidence.is_file()
            payload = json.loads(evidence.read_text(encoding="utf-8-sig"))
            assert payload["error_code"] == "E_AUTOCAD_CRASHED"

            try:
                config._autostart_autocad(lambda: None, crash_probe=lambda *_: crash)
            except config.AutoCADStartupError as second:
                assert second.error_code == "E_AUTOCAD_STARTUP_BLOCKED"
            else:
                raise AssertionError("identical fatal startup was retried")
            assert calls["popen"] == 1
            print(
                json.dumps(
                    {
                        "status": "passed",
                        "first_error": "E_AUTOCAD_CRASHED",
                        "second_error": "E_AUTOCAD_STARTUP_BLOCKED",
                        "launch_count": calls["popen"],
                        "evidence": str(evidence),
                    },
                    ensure_ascii=False,
                )
            )
            return 0
    finally:
        config.WIN32_AVAILABLE = old_win32
        config.activity_insights_write_preflight = old_preflight
        config.subprocess.Popen = old_popen
        config._LAST_AUTOSTART_RECORD = None
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


if __name__ == "__main__":
    raise SystemExit(main())
