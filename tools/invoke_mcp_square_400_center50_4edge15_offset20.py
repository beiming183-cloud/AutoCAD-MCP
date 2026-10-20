"""Invoke the safe MCP tool and independently verify the approved 400 mm part."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import generate_square_center_and_4edge_holes as verifier


verifier.BASE_NAME = "square_400_center50_4edge15_offset20"
verifier.WIDTH_MM = 400.0
verifier.HEIGHT_MM = 400.0
verifier.OUTER_POINTS = [(0.0, 0.0), (400.0, 0.0), (400.0, 400.0), (0.0, 400.0)]
verifier.HOLES = [
    ((200.0, 200.0), 25.0),
    ((200.0, 20.0), 7.5),
    ((200.0, 380.0), 7.5),
    ((20.0, 200.0), 7.5),
    ((380.0, 200.0), 7.5),
]


async def run(output_dir: Path) -> dict[str, object]:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    name, report_path = verifier._available_names(output_dir)
    environment = os.environ.copy()
    environment["NGOC_VIET_DXF_ROOT"] = str(output_dir)
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "autocad_mcp.safe_server"],
        env=environment,
        cwd=str(Path(__file__).parents[1]),
    )
    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            tools = await session.list_tools()
            tool_names = sorted(tool.name for tool in tools.tools)
            response = await session.call_tool(
                "create_square_center_edge_plate",
                {
                    "output_name": name,
                    "side_mm": 400.0,
                    "center_hole_diameter_mm": 50.0,
                    "edge_hole_diameter_mm": 15.0,
                    "edge_offset_mm": 20.0,
                },
            )
    created = json.loads(response.content[0].text)
    path = output_dir / name
    independent = verifier._independent_validation(path)
    markdown = verifier._markdown_report(name, path, independent, created["inspection"])
    with report_path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(markdown)
    return {
        "status": "PASS" if created["valid"] and independent["valid"] else "FAIL",
        "transport": "MCP stdio",
        "tool": "create_square_center_edge_plate",
        "available_tools": tool_names,
        "file": str(path),
        "report": str(report_path),
        "mcp_result": created,
        "independent_result": independent,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts"))
    result = asyncio.run(run(parser.parse_args().output_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
