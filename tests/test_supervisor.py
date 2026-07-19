from __future__ import annotations

import codecs
import json
from pathlib import Path
from types import SimpleNamespace

from autocad_mcp.supervisor import (
    DesktopSupervisor,
    SupervisorConfig,
    _append_utf8_bom_log,
    read_supervisor_state,
)


def test_supervisor_log_is_utf8_bom_and_preserves_chinese(tmp_path):
    path = tmp_path / "supervisor.log"

    _append_utf8_bom_log(path, "状态", message="可见但最小化")

    assert path.read_bytes().startswith(codecs.BOM_UTF8)
    assert "可见但最小化" in path.read_text(encoding="utf-8-sig")


def test_supervisor_launch_owns_pid_and_exports_desktop_contract(tmp_path):
    executable = tmp_path / "acad.exe"
    executable.write_bytes(b"stub")
    output = tmp_path / "D-drive-output"
    activity = output / "activity-insights"
    observed = {}

    def launcher(command, **kwargs):
        observed.update(command=command, kwargs=kwargs)
        return SimpleNamespace(pid=4321)

    supervisor = DesktopSupervisor(
        SupervisorConfig(
            acad_exe=str(executable),
            output_root=str(output),
            activity_insights_path=str(activity),
        ),
        root=tmp_path / "state",
        launcher=launcher,
        process_probe=lambda pid: pid == 4321,
    )

    assert supervisor.launch() == 4321
    assert observed["command"][0] == str(executable.resolve())
    assert "/nologo" in observed["command"]
    assert "/product" in observed["command"]
    assert "ACAD" in observed["command"]
    assert observed["kwargs"]["env"]["AUTOCAD_MCP_WINDOW_MODE"] == "quiet_minimized"
    assert observed["kwargs"]["env"]["AUTOCAD_MCP_OUTPUT_ROOT"] == str(output)
    assert output.is_dir() and activity.is_dir()


def test_supervisor_state_declares_ownership_without_terminating_autocad(tmp_path):
    supervisor = DesktopSupervisor(
        SupervisorConfig(acad_exe=None, attach_pid=7654),
        root=tmp_path,
        process_probe=lambda pid: pid == 7654,
    )
    supervisor.acad_pid = 7654

    state = supervisor._publish("PROCESS_READY", ready=True)
    stored = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))

    assert state["owned"] is False
    assert stored["acad_pid"] == 7654
    assert stored["config"]["window_mode"] == "quiet_minimized"
    inspected = read_supervisor_state(tmp_path)
    assert inspected["acad_pid"] == 7654


def test_native_bundle_manifest_loads_at_startup_and_keeps_secureload_policy():
    root = Path(__file__).resolve().parents[1]
    manifest = (root / "native" / "AutoCADMcp.bundle" / "PackageContents.xml").read_text(
        encoding="utf-8"
    )
    build_script = (root / "native" / "scripts" / "build-plugin.ps1").read_text(
        encoding="utf-8"
    )

    assert 'LoadOnAutoCADStartup="True"' in manifest
    assert "SECURELOAD" in build_script
    assert "SECURELOAD=0" not in build_script.replace(" ", "").upper()
    assert "SETVAR SECURELOAD" not in build_script.upper()
    assert '"Contents\\Windows"' in build_script
    assert '"AutoCADMcp.Plugin.dll"' in build_script
