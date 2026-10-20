"""Deployment safety policy for the Ngoc Viet CNC branch."""

from __future__ import annotations

import os


def dxf_safe_mode_enabled() -> bool:
    """Default to the headless DXF-only backend unless tests opt out."""
    return os.environ.get("NGOC_VIET_DXF_SAFE_MODE", "1").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }


def autocad_command_transport_allowed() -> bool:
    """Require a separate explicit break-glass flag for legacy compatibility."""
    return (
        not dxf_safe_mode_enabled()
        and os.environ.get("NGOC_VIET_ALLOW_AUTOCAD_COMMAND_TRANSPORT", "").strip()
        == "I_ACCEPT_UNVERIFIED_AUTOCAD_COMMAND_RISK"
    )
