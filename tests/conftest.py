"""Shared fixtures for autocad-mcp tests."""

import pytest


@pytest.fixture(autouse=True)
def _isolate_backend(monkeypatch):
    """Reset the backend singleton between tests."""
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
