"""Tests for controlled AutoCAD system-variable updates."""

import pytest

from autocad_mcp.backends.ezdxf_backend import EzdxfBackend
from autocad_mcp.variables import mechanical_variable_updates, validate_variable_updates


def test_variable_whitelist_normalizes_names_and_types():
    assert validate_variable_updates({"$insunits": "4", "DIMTXT": "3.5"}) == {
        "INSUNITS": 4,
        "DIMTXT": 3.5,
    }


def test_variable_whitelist_rejects_unknown_and_out_of_range():
    with pytest.raises(ValueError, match="not allowed"):
        validate_variable_updates({"FILEDIA": 0})
    with pytest.raises(ValueError, match="out of range"):
        validate_variable_updates({"LUPREC": 99})


def test_mechanical_defaults_are_millimeter_gbt_values():
    values = mechanical_variable_updates({"units": "mm", "dimension": {"DIMTXT": 4}})
    assert values["INSUNITS"] == 4
    assert values["MEASUREMENT"] == 1
    assert values["DIMTXT"] == 4.0


async def test_ezdxf_set_variables_and_audit_units():
    backend = EzdxfBackend()
    await backend.initialize()

    update = await backend.drawing_set_variables({"INSUNITS": 4, "LUPREC": 3})
    audit = await backend.drawing_audit()

    assert update.ok is True
    assert update.payload["updated"]["INSUNITS"] == 4
    assert audit.payload["units"] == {"code": 4, "name": "millimeters"}
