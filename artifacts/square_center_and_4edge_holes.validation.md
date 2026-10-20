# DXF acceptance report: square_center_and_4edge_holes.dxf

- Status: **PASS**
- Scope: headless DXF staging only; AutoCAD production and laser hardware were not contacted.
- File: `D:\CHAT GPT WORK\CODEX\AutocadMCP\upstream\beiming183-cloud-AutoCAD-MCP\artifacts\square_center_and_4edge_holes.dxf`
- SHA-256: `cba87db0b383121d70896ac370d363a81b57bc8a0d3c9196abe8ac86033dbce7`
- Independent reader: `dxfgrabber 1.0.1`
- Units: mm (`$INSUNITS = 4`)
- Overall size: 200 x 200 mm; origin at (0, 0)
- Outer contour: closed `LWPOLYLINE` on `CUT_OUTER`
- Hole count: 5
- Duplicate geometry: 0
- Closed contours: 6
- Unsupported entities: 0
- DXF audit errors/fixes: 0/0
- Layers: `CUT_OUTER = 1`, `CUT_INNER = 5`
- Total cut length: 988.495559215 mm

| Hole | Center (mm) | Diameter (mm) | Layer |
|---:|---:|---:|---|
| 1 | (20, 100) | 10 | CUT_INNER |
| 2 | (100, 20) | 10 | CUT_INNER |
| 3 | (100, 100) | 20 | CUT_INNER |
| 4 | (100, 180) | 10 | CUT_INNER |
| 5 | (180, 100) | 10 | CUT_INNER |

All independent checks: **PASS**.
