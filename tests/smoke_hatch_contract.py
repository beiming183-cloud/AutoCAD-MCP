"""Verify HATCH requested/actual readback without AutoCAD or pytest."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# The shared CAD-only Python environment intentionally omits the MCP logging
# dependency.  Keep this standalone geometry smoke dependency-free while the
# normal package and pytest suite continue to use real structlog.
if importlib.util.find_spec("structlog") is None:
    logger = SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )
    sys.modules["structlog"] = SimpleNamespace(get_logger=lambda: logger)

from autocad_mcp.backends.ezdxf_backend import EzdxfBackend


async def _run() -> dict:
    backend = EzdxfBackend()
    initialized = await backend.initialize()
    assert initialized.ok
    boundary = await backend.create_polyline(
        [[0, 0], [20, 0], [20, 10], [0, 10]], closed=True
    )
    hatch = await backend.create_hatch(
        boundary.payload["handle"], pattern="ANSI31", angle=15.0, scale=2.0
    )
    verified = await backend.verify_created_hatch(
        hatch,
        entity_id=boundary.payload["handle"],
        pattern="ANSI31",
        angle=15.0,
        scale=2.0,
    )
    assert verified.ok, verified.to_dict()
    assert verified.payload["diff"] == []
    return {
        "status": "passed",
        "handle": verified.payload["handle"],
        "requested": verified.payload["requested"],
        "actual": verified.payload["actual"],
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(_run()), ensure_ascii=False, default=str))
