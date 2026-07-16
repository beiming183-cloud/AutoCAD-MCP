"""Strict creation contracts and semantic postcondition tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from autocad_mcp.audit import audit_geometry
from autocad_mcp.backends.ezdxf_backend import EzdxfBackend
from autocad_mcp.contracts import build_entity_expectation


def test_contract_is_immutable_and_rejects_extra_fields():
    points = [[0, 0], [10, 0]]
    expectation = build_entity_expectation("polyline", {"points": points, "closed": False})
    points[0][0] = 999

    assert expectation.requested()["points"][0] == [0.0, 0.0]
    with pytest.raises(ValueError, match="unsupported fields"):
        build_entity_expectation(
            "line", {"x1": 0, "y1": 0, "x2": 1, "y2": 1, "unexpected": 4}
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_contract_rejects_nonfinite_coordinates(value):
    with pytest.raises(ValueError, match="finite number"):
        build_entity_expectation("line", {"x1": value, "y1": 0, "x2": 1, "y2": 1})


async def test_postcondition_mismatch_deletes_created_entity():
    backend = EzdxfBackend()
    await backend.initialize()
    expectation = build_entity_expectation(
        "line", {"x1": 0, "y1": 0, "x2": 10, "y2": 0}
    )
    created = await backend.create_line(0, 0, 10, 0)
    backend.entity_get = AsyncMock(
        return_value=type(created)(
            ok=True,
            payload={
                "type": "LINE",
                "handle": created.payload["handle"],
                "layer": "0",
                "start": [88, 0],
                "end": [10, 0],
            },
        )
    )

    result = await backend.verify_created_entity(expectation, created)

    assert not result.ok
    assert result.error_code == "E_POSTCONDITION_MISMATCH"
    assert result.payload["deleted"] is True
    assert result.payload["diff"][0]["path"] == "start[0]"
    assert (await backend.entity_count()).payload["count"] == 0


async def test_interleaved_100_round_batch_has_no_coordinate_state_leakage():
    backend = EzdxfBackend()
    await backend.initialize()
    entities = []
    for index in range(100):
        base = index * 20.0
        entities.extend(
            [
                {"type": "line", "x1": base, "y1": 1, "x2": base + 3, "y2": 4},
                {"type": "rectangle", "x1": base + 5, "y1": 2, "x2": base + 9, "y2": 6},
                {
                    "type": "polyline",
                    "points": [[base + 10, 1], [base + 12, 3], [base + 14, 1]],
                    "closed": False,
                },
                {
                    "type": "mtext",
                    "x": base + 15,
                    "y": 2,
                    "width": 4,
                    "height": 2.5,
                    "text": f"R{index}",
                },
            ]
        )

    result = await backend.create_batch(entities, atomic=True, strict=True)

    assert result.ok
    assert result.payload["processed"] == 400
    assert all(entry["payload"]["verified"] for entry in result.payload["results"])
    assert all(entry["payload"]["diff"] == [] for entry in result.payload["results"])


async def test_semantics_classify_intentional_open_ends_and_component_ownership():
    backend = EzdxfBackend()
    await backend.initialize()
    result = await backend.create_batch(
        [
            {
                "type": "line",
                "x1": 0,
                "y1": 0,
                "x2": 10,
                "y2": 0,
                "component_id": "PIPE-1",
                "line_class": "outline",
                "intentional_open_end": "both",
            }
        ],
        atomic=True,
    )
    handle = result.payload["created_handles"][0]
    readback = await backend.entity_get_with_semantics(handle)
    audit = await backend.drawing_audit(rules={"require_component_id": True})

    assert readback.payload["semantics"]["component_id"] == "PIPE-1"
    rules = {rule["rule_id"]: rule for rule in audit.payload["geometry_drc"]["rules"]}
    assert rules["DANGLING_ENDPOINT"]["status"] == "PASS"
    assert rules["UNASSIGNED_ENTITY"]["status"] == "PASS"


def test_unclassified_dangling_and_crossing_are_failures_by_default():
    result = audit_geometry(
        [
            {"type": "LINE", "handle": "A", "layer": "OUTLINE", "start": [0, 0], "end": [10, 10]},
            {"type": "LINE", "handle": "B", "layer": "OUTLINE", "start": [0, 10], "end": [10, 0]},
        ]
    )
    rules = {rule["rule_id"]: rule for rule in result["rules"]}

    assert rules["DANGLING_ENDPOINT"]["status"] == "FAIL"
    assert rules["INTERIOR_CROSSING"]["status"] == "FAIL"
    assert result["status"] == "FAIL"
