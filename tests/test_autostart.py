"""Tests for the opt-in AutoCAD startup path."""

from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import AsyncMock

import pytest

from autocad_mcp import config
from autocad_mcp.backends.base import CommandResult
from autocad_mcp.backends.file_ipc import FileIPCBackend


def test_autostart_is_opt_in(monkeypatch):
    monkeypatch.delenv("AUTOCAD_MCP_AUTOSTART", raising=False)
    finder = MagicMock(return_value=123)

    assert config._autostart_autocad(finder) is None
    finder.assert_not_called()


def test_autostart_requires_executable(monkeypatch):
    monkeypatch.setattr(config, "WIN32_AVAILABLE", True)
    monkeypatch.setenv("AUTOCAD_MCP_AUTOSTART", "true")
    monkeypatch.delenv("AUTOCAD_MCP_ACAD_EXE", raising=False)

    with pytest.raises(config.AutoCADStartupError) as error:
        config._autostart_autocad(lambda: None)
    assert error.value.error_code == "E_AUTOCAD_EXECUTABLE_NOT_CONFIGURED"


def test_autostart_launches_and_waits(monkeypatch, tmp_path: Path):
    executable = tmp_path / "acad.exe"
    executable.write_bytes(b"test")
    startup_script = tmp_path / "mcp-startup.scr"
    startup_script.write_text("(princ)\n", encoding="ascii")

    monkeypatch.setattr(config, "WIN32_AVAILABLE", True)
    monkeypatch.setenv("AUTOCAD_MCP_AUTOSTART", "true")
    monkeypatch.setenv("AUTOCAD_MCP_ACAD_EXE", str(executable))
    monkeypatch.setenv("AUTOCAD_MCP_ACAD_SCRIPT", str(startup_script))
    monkeypatch.setenv("AUTOCAD_MCP_OUTPUT_ROOT", str(tmp_path / "output"))
    monkeypatch.setenv("AUTOCAD_MCP_ACAD_STARTUP_TIMEOUT", "5")
    monkeypatch.setenv("AUTOCAD_MCP_STARTUP_STABILITY_READS", "2")
    monkeypatch.setenv("AUTOCAD_MCP_STARTUP_SETTLE", "0")
    monkeypatch.setenv("AUTOCAD_MCP_START_MINIMIZED", "false")
    monkeypatch.delenv("AUTOCAD_MCP_ACAD_PROFILE_NAME", raising=False)
    monkeypatch.delenv("AUTOCAD_MCP_ACAD_PROFILE", raising=False)
    monkeypatch.delenv("AUTOCAD_MCP_PROFILE_ARG", raising=False)
    monkeypatch.delenv("AUTOCAD_MCP_EXTRA_ARGS", raising=False)
    monkeypatch.delenv("AUTOCAD_MCP_ACAD_EXTRA_ARGS", raising=False)
    monkeypatch.delenv("AUTOCAD_MCP_ACAD_NOHARDWARE", raising=False)
    popen = MagicMock()
    monkeypatch.setattr(config.subprocess, "Popen", popen)
    monkeypatch.setattr(config.time, "sleep", lambda _: None)
    finder = MagicMock(side_effect=[None, 456, 456, 456])

    assert config._autostart_autocad(finder) == 456
    popen.assert_called_once_with(
        [
            str(executable),
            "/product",
            "ACAD",
            "/language",
            "zh-CN",
            "/nologo",
            "/b",
            str(startup_script),
        ],
        cwd=str(executable.parent),
    )


def test_autostart_fatal_failure_is_recorded_and_then_fused(monkeypatch, tmp_path: Path):
    executable = tmp_path / "acad.exe"
    executable.write_bytes(b"test")
    output = tmp_path / "output"
    monkeypatch.setattr(config, "WIN32_AVAILABLE", True)
    monkeypatch.setenv("AUTOCAD_MCP_AUTOSTART", "true")
    monkeypatch.setenv("AUTOCAD_MCP_ACAD_EXE", str(executable))
    monkeypatch.setenv("AUTOCAD_MCP_OUTPUT_ROOT", str(output))
    monkeypatch.setenv("AUTOCAD_MCP_START_MINIMIZED", "false")
    monkeypatch.setenv("AUTOCAD_MCP_STARTUP_SETTLE", "0")
    monkeypatch.setattr(
        config,
        "activity_insights_write_preflight",
        lambda: {"ok": True, "checked": False, "path": str(output / "activity")},
    )
    popen = MagicMock()
    popen.return_value.pid = 1234
    monkeypatch.setattr(config.subprocess, "Popen", popen)
    crash = {"crashed": True, "reason": "fatal_error_dialog", "dialog": {"title": "AutoCAD Error Aborting"}}

    with pytest.raises(config.AutoCADStartupError) as first:
        config._autostart_autocad(lambda: None, crash_probe=lambda *_: crash)
    assert first.value.error_code == "E_AUTOCAD_CRASHED"
    evidence = output / "reports" / "startup" / "last-autostart.unit-test.json"
    assert evidence.is_file()
    evidence_text = evidence.read_text(encoding="utf-8-sig")
    assert "E_AUTOCAD_CRASHED" in evidence_text
    assert '"evidence_source": "unit_test"' in evidence_text

    with pytest.raises(config.AutoCADStartupError) as second:
        config._autostart_autocad(lambda: None, crash_probe=lambda *_: crash)
    assert second.value.error_code == "E_AUTOCAD_STARTUP_BLOCKED"
    assert popen.call_count == 1


def test_profile_mode_requires_explicit_arg(monkeypatch):
    monkeypatch.setenv("AUTOCAD_MCP_PROFILE_MODE", "isolated")
    monkeypatch.delenv("AUTOCAD_MCP_ACAD_PROFILE", raising=False)
    monkeypatch.delenv("AUTOCAD_MCP_PROFILE_ARG", raising=False)

    with pytest.raises(config.AutoCADStartupError) as error:
        config.build_autocad_startup_command(Path("acad.exe"))
    assert error.value.error_code == "E_AUTOCAD_PROFILE_NOT_CONFIGURED"


def test_named_profile_and_userdata_cache_are_recorded(monkeypatch, tmp_path: Path):
    executable = tmp_path / "acad.exe"
    executable.write_bytes(b"test")
    userdata_cache = executable.parent / "UserDataCache"
    userdata_cache.mkdir()
    monkeypatch.setattr(config, "WIN32_AVAILABLE", True)
    monkeypatch.setenv("AUTOCAD_MCP_ACAD_PROFILE_NAME", "MCP-Clean")
    monkeypatch.setenv("AUTOCAD_MCP_PROFILE_MODE", "explicit")
    monkeypatch.delenv("AUTOCAD_MCP_ACAD_PROFILE", raising=False)
    monkeypatch.delenv("AUTOCAD_MCP_PROFILE_ARG", raising=False)

    command, provenance = config.build_autocad_startup_command(executable)

    assert command == [
        str(executable),
        "/product",
        "ACAD",
        "/language",
        "zh-CN",
        "/nologo",
        "/p",
        "MCP-Clean",
    ]
    assert provenance["profile"]["kind"] == "profile_name"
    assert provenance["working_directory"] == str(userdata_cache)


def test_fatal_window_ownership_filter_rejects_stale_cer_window():
    assert config._startup_fatal_window_is_relevant(
        window_process_id=9876,
        managed_process_id=1234,
        executable_name="acad.exe",
    ) is False
    assert config._startup_fatal_window_is_relevant(
        window_process_id=1234,
        managed_process_id=1234,
        executable_name="cer_dialog.exe",
    ) is True
    assert config._startup_fatal_window_is_relevant(
        window_process_id=9876,
        managed_process_id=None,
        executable_name="cer_dialog.exe",
    ) is False


async def test_file_ipc_ensure_ready_loads_and_version_checks_dispatcher(monkeypatch, tmp_path):
    import autocad_mcp.backends.file_ipc as file_ipc

    backend = FileIPCBackend()
    backend._ipc_dir = tmp_path / "ipc"
    monkeypatch.setattr(file_ipc, "win32_runtime_health", lambda: {"ok": True, "checks": {}})
    monkeypatch.setattr(file_ipc, "list_autocad_processes", lambda: [])
    monkeypatch.setattr(file_ipc, "find_autocad_window", lambda: 123)
    monkeypatch.setattr(file_ipc, "_window_process_id", lambda hwnd: 456)
    monkeypatch.setattr(
        file_ipc,
        "detect_autocad_crash_state",
        lambda hwnd=None, process_id=None: {"crashed": False, "process_id": process_id},
    )
    monkeypatch.setattr(backend, "_ensure_autocad_visible", lambda: {"shown": True})
    # The test uses a mocked dispatcher; never let it call a real AutoCAD COM
    # server that may be open in the user's desktop session.
    monkeypatch.setattr(backend, "_wait_for_autocad_idle", lambda timeout=2.0: True)
    monkeypatch.setattr(backend, "_ensure_active_document", lambda: {"ready": True, "name": "Drawing1.dwg"})
    monkeypatch.setattr(
        backend,
        "_discover_product",
        lambda: {"installed": True, "product": "AutoCAD 2025", "version": "25.0", "exe": "acad.exe"},
    )
    monkeypatch.setattr(backend, "_find_command_line_hwnd", lambda: None)
    monkeypatch.setattr(backend, "_cleanup_stale_files", lambda: None)
    typed = []
    monkeypatch.setattr(backend, "_type_command", typed.append)
    monkeypatch.setattr(
        backend,
        "_dispatch",
        AsyncMock(
            side_effect=[
                CommandResult(ok=False, error="AutoCAD COM is still registering"),
                CommandResult(
                    ok=True, payload={"pong": True, "dispatcher_version": "4.0.0"}
                ),
            ]
        ),
    )
    monkeypatch.delenv("AUTOCAD_MCP_LISP_PATH", raising=False)

    result = await backend.ensure_ready()

    assert result.ok is True
    assert result.payload["ready"] is True
    assert result.payload["autocad"]["product"] == "AutoCAD 2025"
    assert result.payload["dispatcher"]["version"] == "4.0.0"
    assert result.payload["dispatcher"]["load_attempts"] == 2
    assert len(typed) == 2
    assert typed and "mcp_dispatch.lsp" in typed[0]


def test_existing_autocad_activity_policy_is_deferred_without_explicit_opt_in(monkeypatch):
    from autocad_mcp.backends.file_ipc import FileIPCBackend

    monkeypatch.setenv("AUTOCAD_MCP_DISABLE_ACTIVITY_INSIGHTS", "true")
    monkeypatch.setenv("AUTOCAD_MCP_ACTIVITY_INSIGHTS_PATH", "D:/Codex/activity-insights")
    monkeypatch.delenv("AUTOCAD_MCP_APPLY_ACTIVITY_POLICY", raising=False)

    result = FileIPCBackend._apply_activity_insights_policy.__wrapped__(
        object(), allow_mutation=False
    )

    assert result["configured"] is True
    assert result["mutation_allowed"] is False
    assert result["deferred"] is True
    assert result["applied"] == {}
