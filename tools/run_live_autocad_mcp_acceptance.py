"""Run the explicitly approved AI -> MCP -> AutoCAD acceptance workflow."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from autocad_mcp.live_autocad import LIVE_APPROVAL_ENV, LIVE_APPROVAL_VALUE


CREATE_NAME = "live_square_200_center20_4edge10.dxf"
REVISION_NAME = "live_square_200_center24_4edge10_rev2.dxf"


def _tool_json(response: Any) -> dict[str, Any]:
    return json.loads(response.content[0].text)


async def run(output_dir: Path, phase: str) -> dict[str, Any]:
    if os.environ.get(LIVE_APPROVAL_ENV) != LIVE_APPROVAL_VALUE:
        raise PermissionError("explicit live AutoCAD approval environment is missing")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = output_dir / f"live_autocad_mcp_{phase}.json"
    if phase == "create":
        outputs = (output_dir / CREATE_NAME, transcript_path)
    elif phase == "revision":
        outputs = (output_dir / REVISION_NAME, transcript_path)
    else:
        outputs = (transcript_path,)
    for path in outputs:
        if path.exists():
            raise FileExistsError(f"refusing to overwrite acceptance output: {path.name}")
    if phase in {"inspect", "revision"} and not (output_dir / CREATE_NAME).is_file():
        raise FileNotFoundError(f"approved source drawing is missing: {CREATE_NAME}")

    environment = os.environ.copy()
    environment["NGOC_VIET_AUTOCAD_ROOT"] = str(output_dir)
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "autocad_mcp.live_autocad_server"],
        env=environment,
        cwd=str(Path(__file__).parents[1]),
    )
    calls: list[dict[str, Any]] = []
    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            listed = await session.list_tools()
            tool_names = sorted(tool.name for tool in listed.tools)

            status = _tool_json(await session.call_tool("autocad_status", {}))
            calls.append({"tool": "autocad_status", "arguments": {}, "result": status})

            if phase == "create":
                create_args = {
                    "output_name": CREATE_NAME,
                    "side_mm": 200.0,
                    "center_hole_diameter_mm": 20.0,
                    "edge_hole_diameter_mm": 10.0,
                    "edge_offset_mm": 20.0,
                }
                created = _tool_json(await session.call_tool("autocad_create_test_plate", create_args))
                calls.append({"tool": "autocad_create_test_plate", "arguments": create_args, "result": created})

                inspect_args = {"name": CREATE_NAME}
                inspected = _tool_json(await session.call_tool("autocad_inspect_test_drawing", inspect_args))
                calls.append({"tool": "autocad_inspect_test_drawing", "arguments": inspect_args, "result": inspected})
            else:
                inspect_args = {"name": CREATE_NAME}
                inspected = _tool_json(await session.call_tool("autocad_inspect_test_drawing", inspect_args))
                calls.append({"tool": "autocad_inspect_test_drawing", "arguments": inspect_args, "result": inspected})

            if phase == "revision":
                modify_args = {
                    "source_name": CREATE_NAME,
                    "output_name": REVISION_NAME,
                    "center_x_mm": 100.0,
                    "center_y_mm": 100.0,
                    "new_diameter_mm": 24.0,
                }
                revised = _tool_json(await session.call_tool("autocad_modify_hole_revision", modify_args))
                calls.append({"tool": "autocad_modify_hole_revision", "arguments": modify_args, "result": revised})

    transcript = {
        "status": "PASS",
        "phase": phase,
        "scope": "fixed allowlisted AutoCAD ActiveX operations; no SendCommand",
        "available_tools": tool_names,
        "calls": calls,
    }
    with transcript_path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(transcript, handle, ensure_ascii=False, indent=2)
    return transcript


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/autocad-live"))
    parser.add_argument("--phase", choices=("create", "inspect", "revision"), required=True)
    arguments = parser.parse_args()
    result = asyncio.run(run(arguments.output_dir, arguments.phase))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
