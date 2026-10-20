"""Shared fixtures for autocad-mcp tests."""

import pytest


@pytest.fixture(autouse=True)
def _isolate_backend(monkeypatch):
    """Reset the backend singleton between tests."""
    # The shipped Ngoc Viet profile is safe-by-default. Legacy upstream unit
    # tests explicitly opt out so their mocked AutoCAD compatibility paths can
    # still be regression-tested without weakening the production entry point.
    monkeypatch.setenv("NGOC_VIET_DXF_SAFE_MODE", "0")
    monkeypatch.setenv(
        "NGOC_VIET_ALLOW_AUTOCAD_COMMAND_TRANSPORT",
        "I_ACCEPT_UNVERIFIED_AUTOCAD_COMMAND_RISK",
    )
    # Force ezdxf backend for tests (not on Windows or no AutoCAD)
    monkeypatch.setenv("AUTOCAD_MCP_BACKEND", "ezdxf")
    # Keep dependency-free tests (COM scheduling, journals, protocol codecs)
    # runnable when the host blocks a native pydantic DLL. Tests that actually
    # exercise ``client`` still surface that environment failure themselves.
    try:
        import autocad_mcp.client as client_mod
    except (ImportError, OSError):
        return
    monkeypatch.setattr(client_mod, "_backend", None)
