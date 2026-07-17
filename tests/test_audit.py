"""Tests for structured drawing and DXF audits."""

from __future__ import annotations

import tempfile
from pathlib import Path

import ezdxf

from autocad_mcp.audit import (
    audit_dxf_file,
    audit_geometry,
    build_audit,
    geometry_digest,
    normalize_ezdxf_entity,
)
from autocad_mcp.backends.ezdxf_backend import EzdxfBackend


def test_build_audit_tracks_add_modify_remove():
    original = [{"type": "LINE", "handle": "A", "layer": "0", "start": [0, 0], "end": [1, 0]}]
    first, fingerprints = build_audit(original, revision=1)
    assert first["changes"]["baseline"] is True
    assert first["entity_count"] == 1

    updated = [{"type": "LINE", "handle": "A", "layer": "0", "start": [0, 0], "end": [2, 0]}]
    second, _ = build_audit(updated, previous_fingerprints=fingerprints, revision=2)
    assert second["changes"]["modified"] == ["A"]
    assert second["changes"]["added"] == []

    third, _ = build_audit([], previous_fingerprints=fingerprints, revision=3)
    assert third["changes"]["removed"] == ["A"]


def test_build_audit_limits_entity_payload():
    entities = [
        {"type": "CIRCLE", "handle": str(index), "layer": "HOLES", "center": [index, 0], "radius": 1}
        for index in range(10)
    ]
    payload, _ = build_audit(entities, limit=3)
    assert payload["entity_count"] == 10
    assert payload["returned_entity_count"] == 3
    assert payload["truncated"] is True
    assert len(payload["entities"]) == 3


def test_normalize_ezdxf_line():
    document = ezdxf.new("R2013")
    line = document.modelspace().add_line((1, 2), (3, 4), dxfattribs={"layer": "OUTLINE"})
    normalized = normalize_ezdxf_entity(line)
    assert normalized["type"] == "LINE"
    assert normalized["layer"] == "OUTLINE"
    assert normalized["start"][:2] == [1, 2]
    assert normalized["end"][:2] == [3, 4]


def test_normalize_ezdxf_text_height():
    document = ezdxf.new("R2013")
    text = document.modelspace().add_text("shaft", height=3.5)
    normalized = normalize_ezdxf_entity(text)
    assert normalized["type"] == "TEXT"
    assert normalized["height"] == 3.5


def test_normalize_ezdxf_arc_mtext_and_polyline_parameters():
    document = ezdxf.new("R2013")
    modelspace = document.modelspace()
    arc = normalize_ezdxf_entity(modelspace.add_arc((0, 0), 10, 0, 90))
    mtext = normalize_ezdxf_entity(modelspace.add_mtext("note", dxfattribs={"width": 25}))
    polyline_entity = modelspace.add_lwpolyline([(0, 0, 0.5), (10, 0, 0)], format="xyb")
    polyline = normalize_ezdxf_entity(polyline_entity)

    assert arc["start"][:2] == [10, 0]
    assert arc["end"][:2] == [0, 10]
    assert mtext["width"] == 25
    assert polyline["bulges"] == [0.5, 0]


def test_audit_dxf_file():
    document = ezdxf.new("R2013")
    document.header["$INSUNITS"] = 4
    document.modelspace().add_circle((5, 5), 2, dxfattribs={"layer": "HOLES"})
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "audit.dxf"
        document.saveas(path)
        payload = audit_dxf_file(str(path))
    assert payload["entity_count"] == 1
    assert payload["counts_by_type"] == {"CIRCLE": 1}
    assert payload["counts_by_layer"] == {"HOLES": 1}
    assert payload["units"] == {"code": 4, "name": "millimeters"}


def test_geometry_drc_detects_zero_duplicate_self_intersection_and_duplicate_entity():
    entities = [
        {"type": "LINE", "handle": "L0", "layer": "0", "start": [0, 0], "end": [0, 0]},
        {
            "type": "LWPOLYLINE",
            "handle": "P1",
            "layer": "OUTLINE",
            "points": [[0, 0], [1, 0], [1, 0], [0, 1]],
            "closed": False,
        },
        {
            "type": "LWPOLYLINE",
            "handle": "P2",
            "layer": "OUTLINE",
            "points": [[0, 0], [2, 2], [0, 2], [2, 0]],
            "closed": True,
        },
        {"type": "CIRCLE", "handle": "C1", "layer": "HOLES", "center": [5, 5], "radius": 2},
        {"type": "CIRCLE", "handle": "C2", "layer": "HOLES", "center": [5, 5], "radius": 2},
    ]

    result = audit_geometry(entities)
    rules = {rule["rule_id"]: rule for rule in result["rules"]}

    assert result["status"] == "FAIL"
    assert rules["ZERO_LENGTH_SEGMENT"]["count"] == 2
    assert rules["POLYLINE_DUPLICATE_VERTEX"]["count"] == 1
    assert rules["POLYLINE_SELF_INTERSECTION"]["count"] >= 1
    assert rules["DUPLICATE_ENTITY"]["count"] == 1


def test_geometry_digest_is_order_and_handle_independent():
    first = [
        {"type": "LINE", "handle": "1", "layer": "0", "start": [0, 0], "end": [1, 0]},
        {"type": "CIRCLE", "handle": "2", "layer": "0", "center": [2, 2], "radius": 1},
    ]
    second = [dict(first[1], handle="A"), dict(first[0], handle="B")]
    assert geometry_digest(first) == geometry_digest(second)


def test_topology_drc_detects_near_miss_dangling_and_interior_crossing():
    entities = [
        {"type": "LINE", "handle": "A", "layer": "OUTLINE", "start": [0, 0], "end": [10, 0]},
        {"type": "LINE", "handle": "B", "layer": "OUTLINE", "start": [10.2, 0], "end": [20, 0]},
        {"type": "LINE", "handle": "C", "layer": "OUTLINE", "start": [5, -5], "end": [5, 5]},
        {"type": "LINE", "handle": "AXIS", "layer": "CENTER", "start": [-100, 0], "end": [100, 0]},
    ]
    result = audit_geometry(
        entities,
        rules={
            "connection_tolerance": 0.05,
            "near_miss_tolerance": 0.5,
            "near_miss_policy": "fail",
            "intersection_policy": "fail",
        },
    )
    rules = {rule["rule_id"]: rule for rule in result["rules"]}

    assert result["status"] == "FAIL"
    assert rules["NEAR_MISS_ENDPOINT"]["count"] >= 1
    assert rules["INTERIOR_CROSSING"]["count"] == 1
    assert all(item["entity"] != "AXIS" for item in rules["DANGLING_ENDPOINT"]["findings"])
    assert result["topology_graph"]["node_count"] == 6


def test_explicit_equal_radius_projection_and_tangency_rules():
    entities = [
        {"type": "CIRCLE", "handle": "C1", "layer": "OUTLINE", "center": [0, 0], "radius": 10},
        {"type": "CIRCLE", "handle": "C2", "layer": "OUTLINE", "center": [0, 20], "radius": 10.1},
        {"type": "LINE", "handle": "L1", "layer": "OUTLINE", "start": [10, 0], "end": [10, 20]},
        {"type": "CIRCLE", "handle": "V1", "layer": "OUTLINE", "center": [100, 0], "radius": 2},
        {"type": "CIRCLE", "handle": "V2", "layer": "OUTLINE", "center": [100.2, 20], "radius": 2},
    ]
    result = audit_geometry(
        entities,
        rules={
            "equal_radius_groups": [
                {"name": "pulleys", "handles": ["C1", "C2"], "tolerance": 0.01}
            ],
            "projection_checks": [
                {
                    "name": "view-centers",
                    "source_handles": ["C1"],
                    "target_handles": ["V1"],
                    "axis": "x",
                    "offset": 100,
                    "tolerance": 0.05,
                }
            ],
            "tangent_pairs": [{"name": "belt", "line": "L1", "curve": "C1"}],
        },
    )
    named = {(rule["rule_id"], rule.get("name")): rule for rule in result["rules"]}

    assert named[("EQUAL_RADIUS_GROUP", "pulleys")]["status"] == "FAIL"
    assert named[("PROJECTION_ALIGNMENT", "view-centers")]["status"] == "PASS"
    assert named[("TANGENCY", "belt")]["status"] == "PASS"


async def test_ezdxf_backend_incremental_audit():
    backend = EzdxfBackend()
    await backend.initialize()
    await backend.layer_create("OUTLINE")
    await backend.layer_create("HOLES")
    await backend.create_line(0, 0, 10, 0, "OUTLINE")
    first = await backend.drawing_audit()
    assert first.ok
    assert first.payload["entity_count"] == 1

    await backend.create_circle(5, 5, 2, "HOLES")
    second = await backend.drawing_audit(changed_only=True)
    assert second.ok
    assert len(second.payload["changes"]["added"]) == 1
    assert second.payload["returned_entity_count"] == 1
