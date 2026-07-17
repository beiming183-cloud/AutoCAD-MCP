"""Tests for non-mutating Windows runtime and profile preflight helpers."""

from __future__ import annotations

from autocad_mcp.runtime_health import (
    activity_insights_write_preflight,
    environment_preflight,
)


def test_activity_insights_preflight_can_be_explicitly_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOCAD_MCP_DISABLE_ACTIVITY_INSIGHTS", "true")
    monkeypatch.setenv("AUTOCAD_MCP_ACTIVITY_INSIGHTS_PATH", str(tmp_path / "activity"))

    result = activity_insights_write_preflight()

    assert result == {
        "ok": True,
        "checked": False,
        "disabled_by_config": True,
        "path": str((tmp_path / "activity").resolve()),
    }


def test_environment_preflight_has_process_and_activity_sections(monkeypatch):
    monkeypatch.setattr(
        "autocad_mcp.runtime_health.win32_runtime_health",
        lambda: {"ok": True, "checks": {}, "python": "test"},
    )
    monkeypatch.setattr(
        "autocad_mcp.runtime_health.activity_insights_write_preflight",
        lambda: {"ok": True, "checked": True, "path": "D:/CAD-Automation/activity-insights"},
    )
    monkeypatch.setattr("autocad_mcp.runtime_health.list_autocad_processes", lambda: [])

    result = environment_preflight()

    assert result["ok"] is True
    assert result["autocad_processes"] == []
    assert result["activity_insights"]["ok"] is True
