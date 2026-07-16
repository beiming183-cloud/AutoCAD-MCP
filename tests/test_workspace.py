"""Tests for the industrial D-drive output workspace."""

from __future__ import annotations

import json

from autocad_mcp.workspace import (
    create_job,
    ensure_workspace,
    resolve_output_target,
    sanitize_name,
    sha256_file,
    workspace_info,
    write_json_atomic,
)


def test_workspace_creates_standard_folders(monkeypatch, tmp_path):
    root = tmp_path / "CAD-Automation"
    monkeypatch.setenv("AUTOCAD_MCP_OUTPUT_ROOT", str(root))

    folders = ensure_workspace()

    assert folders["root"] == root.resolve()
    assert folders["drawings"].is_dir()
    assert folders["scripts"].is_dir()
    assert folders["reports"].is_dir()
    assert folders["jobs"].is_dir()
    assert folders["templates"].is_dir()


def test_external_output_is_redirected_into_workspace(monkeypatch, tmp_path):
    root = tmp_path / "workspace"
    external = tmp_path / "outside" / "part.dwg"
    monkeypatch.setenv("AUTOCAD_MCP_OUTPUT_ROOT", str(root))
    monkeypatch.delenv("AUTOCAD_MCP_ALLOW_EXTERNAL_OUTPUTS", raising=False)

    target = resolve_output_target(str(external), category="drawings", extension=".dwg")

    assert target.redirected is True
    assert target.path == (root / "drawings" / "part.dwg").resolve()


def test_relative_output_uses_category_and_extension(monkeypatch, tmp_path):
    root = tmp_path / "workspace"
    monkeypatch.setenv("AUTOCAD_MCP_OUTPUT_ROOT", str(root))

    target = resolve_output_target("project-a/shaft", category="dxf", extension="dxf")

    assert target.path == (root / "dxf" / "project-a" / "shaft.dxf").resolve()
    assert target.redirected is False


def test_job_has_isolated_delivery_folders(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOCAD_MCP_OUTPUT_ROOT", str(tmp_path / "workspace"))

    job = create_job("Gearbox 01")

    assert job["job_id"].endswith("Gearbox-01")
    assert job["drawings"].is_dir()
    assert job["audits"].is_dir()
    assert job["specs"].is_dir()
    assert job["reports"].is_dir()
    assert job["root"].parent.name == "jobs"


def test_atomic_json_and_checksum(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOCAD_MCP_OUTPUT_ROOT", str(tmp_path / "workspace"))
    path = tmp_path / "manifest.json"
    payload = {"status": "complete", "count": 3}

    write_json_atomic(path, payload)

    assert json.loads(path.read_text(encoding="utf-8")) == payload
    assert len(sha256_file(path)) == 64
    assert not path.with_suffix(".json.tmp").exists()


def test_sanitize_name_and_workspace_info(monkeypatch, tmp_path):
    root = tmp_path / "workspace"
    monkeypatch.setenv("AUTOCAD_MCP_OUTPUT_ROOT", str(root))

    assert sanitize_name(' bad<name>:01 ') == "bad_name__01"
    assert workspace_info()["root"] == str(root.resolve())
