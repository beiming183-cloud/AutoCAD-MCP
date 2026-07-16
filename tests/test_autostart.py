"""Tests for the opt-in AutoCAD startup path."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autocad_mcp import config


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
