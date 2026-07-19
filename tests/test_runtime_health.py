"""Tests for non-mutating Windows runtime and profile preflight helpers."""

from __future__ import annotations

from autocad_mcp.runtime_health import (
    activity_insights_write_preflight,
    autocad_cer_snapshot,
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


def test_cer_snapshot_extracts_startup_theme_stack(monkeypatch, tmp_path):
    cer = tmp_path / "rawdata-t2.pb"
    cer.write_bytes(
        b"AP/exception/code\x0c0xE0434352\n"
        b"AP/exception/module_name\x10KERNELBASE.dll\n"
        b"SP/THEME_TYPE\x06Blue\n"
        b"Current Stack:\n"
        b"   at Autodesk.Windows.Themes.OverridePaletteTheme.get_Dark()\n"
        b"   at AdUiMgdPaletteTheme.ctor\n"
        b"Last Unhandled Exception:\n"
    )
    monkeypatch.setenv("AUTOCAD_MCP_CER_FILE", str(cer))

    result = autocad_cer_snapshot()

    assert result["available"] is True
    record = result["records"][0]
    assert record["exception_code"] == "0xE0434352"
    assert "OverridePaletteTheme" in record["stack_excerpt"]


def test_cer_snapshot_extracts_t1_crash_fields(monkeypatch, tmp_path):
    cer = tmp_path / "rawdata-t1.pb"
    cer.write_bytes(
        b" AP/exception/code\n0xE0434352\n"
        b" AP/exception/address\n0x00007FFDCDA01B6A\n"
        b" AP/exception/module_name\nKERNELBASE.dll\n"
        b" SP/UPI_BUILD \n25.0.58.0.0\n"
        b" SP/THEME_TYPE \nBlue\n"
        b" AI/CrashDate \n07/18/2026\n"
    )
    monkeypatch.setenv("AUTOCAD_MCP_CER_FILE", str(cer))

    record = autocad_cer_snapshot()["records"][0]

    assert record["record_kind"] == "rawdata-t1.pb"
    assert record["exception_code"] == "0xE0434352"
    assert record["exception_address"] == "0x00007FFDCDA01B6A"
    assert record["module"] == "KERNELBASE.dll"
    assert record["product_build"] == "25.0.58.0.0"
    assert record["theme_type"] == "Blue"
    assert record["crash_date"] == "07/18/2026"


def test_cer_log_is_used_when_rawdata_is_not_enumerable(monkeypatch, tmp_path):
    log = tmp_path / "cer.log"
    log.write_text(
        "[07/18/26 15:38:44] info ================= cer started =================\n"
        "[07/18/26 15:38:44] info Setting key SP/UPI_BUILD, value 25.0.58.0.0 (maxCount 1)\n"
        "[07/18/26 15:38:44] info Setting key SP/THEME_TYPE, value Blue (maxCount 1)\n"
        "[07/18/26 15:38:44] info Setting key AP/exception/code, value 0xE0434352 (maxCount 1)\n"
        "[07/18/26 15:38:44] info Setting key AP/exception/module_name, value KERNELBASE.dll (maxCount 1)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AUTOCAD_MCP_CER_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTOCAD_MCP_CER_LOG", str(log))

    result = autocad_cer_snapshot()

    assert result["source"] == "cer_log_fallback"
    record = result["records"][0]
    assert record["record_kind"] == "cer.log"
    assert record["exception_code"] == "0xE0434352"
    assert record["module"] == "KERNELBASE.dll"
