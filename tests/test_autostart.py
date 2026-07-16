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

    with pytest.raises(RuntimeError, match="AUTOCAD_MCP_ACAD_EXE"):
        config._autostart_autocad(lambda: None)


def test_autostart_launches_and_waits(monkeypatch, tmp_path: Path):
    executable = tmp_path / "acad.exe"
    executable.write_bytes(b"test")
    startup_script = tmp_path / "mcp-startup.scr"
    startup_script.write_text("(princ)\n", encoding="ascii")

    monkeypatch.setattr(config, "WIN32_AVAILABLE", True)
    monkeypatch.setenv("AUTOCAD_MCP_AUTOSTART", "true")
    monkeypatch.setenv("AUTOCAD_MCP_ACAD_EXE", str(executable))
    monkeypatch.setenv("AUTOCAD_MCP_ACAD_SCRIPT", str(startup_script))
    monkeypatch.setenv("AUTOCAD_MCP_ACAD_STARTUP_TIMEOUT", "5")
    popen = MagicMock()
    monkeypatch.setattr(config.subprocess, "Popen", popen)
    monkeypatch.setattr(config.time, "sleep", lambda _: None)
    finder = MagicMock(side_effect=[None, 456])

    assert config._autostart_autocad(finder) == 456
    popen.assert_called_once_with(
        [str(executable), "/nologo", "/b", str(startup_script)],
        cwd=str(executable.parent),
    )


async def test_file_ipc_ensure_ready_loads_and_version_checks_dispatcher(monkeypatch, tmp_path):
    import autocad_mcp.backends.file_ipc as file_ipc

    backend = FileIPCBackend()
    backend._ipc_dir = tmp_path / "ipc"
    monkeypatch.setattr(file_ipc, "find_autocad_window", lambda: 123)
    monkeypatch.setattr(backend, "_ensure_autocad_visible", lambda: {"shown": True})
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
            return_value=CommandResult(
                    ok=True, payload={"pong": True, "dispatcher_version": "3.8.0"}
            )
        ),
    )
    monkeypatch.delenv("AUTOCAD_MCP_LISP_PATH", raising=False)

    result = await backend.ensure_ready()

    assert result.ok is True
    assert result.payload["ready"] is True
    assert result.payload["autocad"]["product"] == "AutoCAD 2025"
    assert result.payload["dispatcher"]["version"] == "3.8.0"
    assert typed and "mcp_dispatch.lsp" in typed[0]
