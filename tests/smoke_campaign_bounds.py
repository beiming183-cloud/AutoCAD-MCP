"""Exercise campaign timeout and managed-artifact cleanup without AutoCAD."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _load_rounds():
    path = Path(__file__).with_name("run_autostart_rounds.py")
    spec = importlib.util.spec_from_file_location("autocad_mcp_rounds", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load campaign runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _slow_campaign(*args, **kwargs):
    await asyncio.sleep(1.0)


async def _run() -> dict:
    rounds = _load_rounds()
    old_operation_timeout = rounds._operation_timeout
    old_campaign_timeout = rounds._campaign_timeout
    old_campaign = rounds.run_campaign
    try:
        rounds._operation_timeout = lambda: 0.05
        operation_record, operation_result = await rounds._timed("slow-operation", asyncio.sleep(1.0))
        assert operation_result is None
        assert operation_record["error_code"] == "E_TEST_OPERATION_TIMEOUT"

        rounds._campaign_timeout = lambda: 0.05
        rounds.run_campaign = _slow_campaign
        bounded = await rounds.run_campaign_bounded(Path(tempfile.gettempdir()) / "autocad-mcp-smoke")
        assert bounded["status"] == "TIMEOUT"
        assert bounded["error_code"] == "E_CAMPAIGN_TIMEOUT"

        from autocad_mcp.workspace import cleanup_test_job_artifacts, create_job

        with tempfile.TemporaryDirectory(prefix="autocad-mcp-cleanup-") as raw:
            os.environ["AUTOCAD_MCP_OUTPUT_ROOT"] = raw
            job = create_job("smoke-cleanup")
            drawing = job["drawings"] / "temporary.dwg"
            preview = job["previews"] / "temporary.png"
            log = job["logs"] / "kept.log"
            drawing.write_bytes(b"DWG")
            preview.write_bytes(b"PNG")
            log.write_text("retain evidence", encoding="utf-8")
            cleanup = cleanup_test_job_artifacts(job["job_id"])
            assert cleanup["deleted_count"] == 2
            assert not drawing.exists() and not preview.exists()
            assert log.read_text(encoding="utf-8") == "retain evidence"

        return {
            "status": "passed",
            "operation_error": operation_record["error_code"],
            "campaign_error": bounded["error_code"],
            "artifact_cleanup": "drawings_deleted_records_kept",
        }
    finally:
        rounds._operation_timeout = old_operation_timeout
        rounds._campaign_timeout = old_campaign_timeout
        rounds.run_campaign = old_campaign


if __name__ == "__main__":
    print(json.dumps(asyncio.run(_run()), ensure_ascii=False))
