"""Seven-round AutoCAD auto-start and drawing regression campaign.

This is an intentional live-CAD test.  It starts AutoCAD only when the caller
enables ``AUTOCAD_MCP_AUTOSTART`` and records every startup/document/window
decision.  Generated CAD artifacts are removed at the end; specs, hashes,
audits, logs, and reflections remain in the managed job directory.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _hash_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


async def _timed(label: str, operation: Awaitable[Any]) -> tuple[dict[str, Any], Any | None]:
    timeout_seconds = _operation_timeout()
    started = time.monotonic()
    try:
        result = await asyncio.wait_for(operation, timeout=timeout_seconds)
        elapsed = time.monotonic() - started
        serialized = result.to_dict() if hasattr(result, "to_dict") else result
        return {
            "label": label,
            "elapsed_seconds": round(elapsed, 3),
            "timeout_seconds": timeout_seconds,
            "ok": bool(getattr(result, "ok", True)),
            "result": serialized,
        }, result
    except asyncio.TimeoutError:
        return {
            "label": label,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "timeout_seconds": timeout_seconds,
            "ok": False,
            "timeout": True,
            "error_code": "E_TEST_OPERATION_TIMEOUT",
            "error": "The campaign operation exceeded its hard timeout",
        }, None
    except Exception as exc:
        return {
            "label": label,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "timeout_seconds": timeout_seconds,
            "ok": False,
            "exception": {
                "type": type(exc).__name__,
                "message": str(exc),
            },
        }, None


async def _call(
    records: list[dict[str, Any]],
    label: str,
    operation: Awaitable[Any],
) -> Any | None:
    record, result = await _timed(label, operation)
    records.append(record)
    return result


def _operation_timeout() -> float:
    """Return a bounded per-operation timeout for live-CAD campaigns."""
    try:
        value = float(os.environ.get("AUTOCAD_MCP_CAMPAIGN_OPERATION_TIMEOUT", "120"))
    except (TypeError, ValueError):
        value = 120.0
    return max(5.0, min(300.0, value))


def _campaign_timeout() -> float:
    try:
        value = float(os.environ.get("AUTOCAD_MCP_CAMPAIGN_TIMEOUT", "900"))
    except (TypeError, ValueError):
        value = 900.0
    return max(30.0, min(3600.0, value))


async def run_campaign_bounded(
    output_root: Path,
    *,
    keep_artifacts: bool = False,
    allow_existing: bool = False,
) -> dict[str, Any]:
    """Run the live campaign without allowing a stalled await to own a turn."""
    timeout_seconds = _campaign_timeout()
    try:
        result = await asyncio.wait_for(
            run_campaign(
                output_root,
                keep_artifacts=keep_artifacts,
                allow_existing=allow_existing,
            ),
            timeout=timeout_seconds,
        )
        if isinstance(result, dict):
            result.setdefault("execution_limits", {})
            result["execution_limits"].update(
                campaign_timeout_seconds=timeout_seconds,
                operation_timeout_seconds=_operation_timeout(),
            )
        return result
    except asyncio.TimeoutError:
        return {
            "schema_version": 1,
            "status": "TIMEOUT",
            "error_code": "E_CAMPAIGN_TIMEOUT",
            "error": "The live AutoCAD campaign exceeded its hard timeout and was cancelled",
            "timeout_seconds": timeout_seconds,
            "output_root": str(Path(output_root).resolve()),
        }
    except Exception as exc:
        return {
            "schema_version": 1,
            "status": "FAILED",
            "error_code": "E_CAMPAIGN_FAILED",
            "error": str(exc) or type(exc).__name__,
            "exception_type": type(exc).__name__,
            "output_root": str(Path(output_root).resolve()),
        }


async def _draw_batch(backend, entities: list[dict[str, Any]], records: list[dict[str, Any]], label: str):
    return await _call(
        records,
        label,
        backend.create_batch(entities, atomic=True, strict=True),
    )


async def _round_flange(backend, records: list[dict[str, Any]]) -> dict[str, Any]:
    entities = [
        {"type": "rectangle", "x1": -50, "y1": -30, "x2": 50, "y2": 30, "layer": "OUTLINE"},
        {"type": "circle", "cx": 0, "cy": 0, "radius": 18, "layer": "OUTLINE"},
        *[
            {
                "type": "circle",
                "cx": x,
                "cy": y,
                "radius": 5,
                "layer": "OUTLINE",
                "component_id": "FLANGE-HOLE-PATTERN",
                "line_class": "outline",
            }
            for x, y in ((-35, -18), (-35, 18), (35, -18), (35, 18))
        ],
        {"type": "line", "x1": -60, "y1": 0, "x2": 60, "y2": 0, "layer": "CENTER", "component_id": "FLANGE-DATUM", "line_class": "center", "intentional_open_end": "both"},
        {"type": "line", "x1": 0, "y1": -40, "x2": 0, "y2": 40, "layer": "CENTER", "component_id": "FLANGE-DATUM", "line_class": "center", "intentional_open_end": "both"},
    ]
    result = await _draw_batch(backend, entities, records, "flange.atomic_batch")
    return {"kind": "2d_flange", "entity_request_count": len(entities), "batch_ok": bool(result and result.ok)}


async def _round_gear(backend, records: list[dict[str, Any]]) -> dict[str, Any]:
    import math

    teeth = 20
    points = []
    for index in range(teeth * 2):
        radius = 42 if index % 2 == 0 else 35
        angle = math.pi * index / teeth
        points.append([radius * math.cos(angle), radius * math.sin(angle)])
    entities = [
        {"type": "polyline", "points": points, "closed": True, "layer": "OUTLINE", "component_id": "GEAR-OUTER", "line_class": "outline"},
        {"type": "circle", "cx": 0, "cy": 0, "radius": 14, "layer": "OUTLINE", "component_id": "GEAR-BORE", "line_class": "outline"},
        {"type": "circle", "cx": 0, "cy": 0, "radius": 25, "layer": "HIDDEN", "component_id": "GEAR-PITCH", "line_class": "hidden"},
    ]
    result = await _draw_batch(backend, entities, records, "gear.closed_profile_batch")
    return {"kind": "2d_gear_profile", "tooth_count": teeth, "batch_ok": bool(result and result.ok)}


async def _round_pid(backend, records: list[dict[str, Any]]) -> dict[str, Any]:
    operations: list[tuple[str, Callable[..., Awaitable[Any]], tuple[Any, ...]]] = [
        ("pid.setup_layers", backend.pid_setup_layers, ()),
        ("pid.insert_pump", backend.pid_insert_pump, (0, 0, "centrifugal", 0.0, {"tag": "P-101"})),
        ("pid.insert_tank", backend.pid_insert_tank, (100, 0, "vertical", 1.0, {"tag": "TK-101"})),
        ("pid.connect", backend.pid_connect_equipment, (15, 0, 85, 0)),
        ("pid.flow_arrow", backend.pid_add_flow_arrow, (50, 0, 0.0)),
        ("pid.line_number", backend.pid_add_line_number, (50, -8, "L-101", "DN25")),
    ]
    successes = 0
    for label, function, args in operations:
        result = await _call(records, label, function(*args))
        successes += int(bool(result and result.ok))
    return {"kind": "pid_process_layout", "operation_count": len(operations), "success_count": successes}


async def _round_bracket(backend, records: list[dict[str, Any]]) -> dict[str, Any]:
    base = await _call(records, "bracket.create_base", backend.solid_create_box([0, 0, 8], 110, 70, 16, "OUTLINE"))
    bore = await _call(records, "bracket.create_bore", backend.solid_create_cylinder([0, 0, -1], 14, 20, "OUTLINE"))
    boolean = None
    if base and base.ok and bore and bore.ok:
        boolean = await _call(records, "bracket.subtract_bore", backend.solid_boolean(base.payload["handle"], bore.payload["handle"], "subtract"))
    return {
        "kind": "3d_bracket_boolean",
        "base_ok": bool(base and base.ok),
        "bore_ok": bool(bore and bore.ok),
        "boolean_ok": bool(boolean and boolean.ok),
    }


async def _round_enclosure(backend, records: list[dict[str, Any]]) -> dict[str, Any]:
    feature = await _call(
        records,
        "enclosure.rounded_box",
        backend.product_create_feature(
            "rounded_box",
            {
                "feature_id": "ENCLOSURE-BODY",
                "component_id": "ENCLOSURE",
                "center": [0, 0, 12],
                "dimensions": [120, 78, 24],
                "radius": 8,
                "source_authority": "concept",
                "layer": "OUTLINE",
            },
        ),
    )
    module = await _call(
        records,
        "enclosure.module_reservation",
        backend.product_create_feature(
            "module_reservation",
            {
                "feature_id": "USB-MODULE-TBD",
                "component_id": "USB-MODULE",
                "center": [0, 0, 28],
                "dimensions": [24, 12, 8],
                "radius": 2,
                "module_status": "TBD",
                "authority": "concept",
                "do_not_dimension_apertures": True,
                "source_authority": "concept",
                "layer": "OUTLINE",
            },
        ),
    )
    return {"kind": "consumer_enclosure", "rounded_box_ok": bool(feature and feature.ok), "module_placeholder_ok": bool(module and module.ok)}


async def _round_rotary(backend, records: list[dict[str, Any]]) -> dict[str, Any]:
    rotary = await _call(
        records,
        "rotary.create_layer",
        backend.product_create_feature(
            "rotary_layer",
            {
                "feature_id": "ROTARY-LAYER",
                "component_id": "ROTARY-INTERFACE",
                "center": [0, 0, 24],
                "outer_radius": 32,
                "inner_radius": 10,
                "height": 8,
                "axis_point": [0, 0, 24],
                "axis_direction": [0, 0, 1],
                "rotation_angle": 0,
                "motion_limit": [-90, 90],
                "clearance": 2,
                "source_authority": "concept",
                "layer": "OUTLINE",
            },
        ),
    )
    ring = await _call(
        records,
        "rotary.annular_gap",
        backend.product_create_feature(
            "annular_gap",
            {
                "feature_id": "ROTARY-GAP",
                "component_id": "ROTARY-INTERFACE",
                "center": [0, 0, 34],
                "outer_radius": 36,
                "inner_radius": 32,
                "height": 2,
                "source_authority": "concept",
                "layer": "HIDDEN",
            },
        ),
    )
    return {"kind": "rotary_product", "rotary_ok": bool(rotary and rotary.ok), "gap_ok": bool(ring and ring.ok)}


async def _round_recovery(backend, records: list[dict[str, Any]]) -> dict[str, Any]:
    before = await _call(records, "recovery.count_before", backend.entity_count())
    failed = await _call(
        records,
        "recovery.intentional_atomic_failure",
        backend.create_batch(
            [
                {"type": "line", "x1": -20, "y1": 90, "x2": 20, "y2": 90, "layer": "OUTLINE"},
                {"type": "unsupported", "layer": "OUTLINE"},
            ],
            atomic=True,
            strict=True,
        ),
    )
    after = await _call(records, "recovery.count_after", backend.entity_count())
    recovered = await _call(records, "recovery.backend_recover", backend.recover())
    before_count = before.payload.get("count") if before and before.ok else None
    after_count = after.payload.get("count") if after and after.ok else None
    return {
        "kind": "atomic_failure_recovery",
        "failure_reported": bool(failed and not failed.ok),
        "count_unchanged": before_count == after_count,
        "recovery_ok": bool(recovered and recovered.ok),
        "before_count": before_count,
        "after_count": after_count,
    }


ROUND_BUILDERS = (
    _round_flange,
    _round_gear,
    _round_pid,
    _round_bracket,
    _round_enclosure,
    _round_rotary,
    _round_recovery,
)


def _reflection(
    number: int,
    startup: dict[str, Any],
    drawing: dict[str, Any],
    fit: dict[str, Any] | None,
    audit: dict[str, Any] | None,
    prior: dict[str, Any] | None,
) -> dict[str, Any]:
    issues: list[str] = []
    improvements: list[str] = []
    startup_result = startup.get("result") or {}
    startup_payload = startup_result.get("payload") if isinstance(startup_result, dict) else {}
    startup_elapsed = float(startup.get("elapsed_seconds", 0))
    if startup_elapsed > 15:
        issues.append("startup_or_document_handshake_took_longer_than_15_seconds")
        improvements.append("keep_one_supervisor-owned_AutoCAD_process_and_read_native_worker_descriptor_before_COM")
    if not startup.get("ok"):
        issues.append("round_startup_failed_before_mutation")
        improvements.append("stop_the_round_after_startup_failure_and_capture_process_window_dialog_evidence")
    if startup_payload and startup_payload.get("created_first_document"):
        improvements.append("retain_the_first-document-creation_path_as_a_regression_gate")
    if fit and not fit.get("ok"):
        issues.append("automatic_center_fit_failed_or_was_not_reported")
        improvements.append("use_planned_bounds_then_one_final_fit_and_return_actual_view_state")
    if audit:
        audit_payload = audit.get("payload") if isinstance(audit, dict) else None
        drc = audit_payload.get("geometry_drc") if isinstance(audit_payload, dict) else None
        if isinstance(drc, dict) and drc.get("status") != "PASS":
            issues.append(f"geometry_drc_{drc.get('status', 'unknown').lower()}")
            improvements.append("classify_each_dangling_endpoint_or_crossing_before_the_next_round")
    if drawing.get("batch_ok") is False or drawing.get("success_count", 1) == 0:
        issues.append("drawing_operations_did_not_complete")
        improvements.append("reduce_batch_size_and_read_back_each_semantic_stage")
    if prior and (prior.get("issues") or prior.get("evidence_based_issues")):
        improvements.append("carry_forward_prior_round_failures_instead_of_repeating_the_same_backend_path")
    if not issues:
        issues.append("no_automatic_failure_detected;_visual_and_engineering_review_still_required")
    return {
        "round": number,
        "evidence_based_issues": issues,
        "next_round_improvements": list(dict.fromkeys(improvements)),
        "confidence": "instrumented_runtime_observation_only",
    }


async def run_campaign(
    output_root: Path,
    *,
    keep_artifacts: bool = False,
    allow_existing: bool = False,
) -> dict[str, Any]:
    from autocad_mcp.config import last_autostart_record
    from autocad_mcp.runtime_health import environment_preflight, list_autocad_processes
    from autocad_mcp.workspace import cleanup_test_job_artifacts, create_job
    from autocad_mcp.backends.file_ipc import FileIPCBackend

    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    before_preflight = environment_preflight()
    before_processes = list_autocad_processes()
    if before_processes and not allow_existing:
        return {
            "status": "BLOCKED_USER_AUTOCAD_ALREADY_RUNNING",
            "started_at": _utc_now(),
            "preflight": before_preflight,
            "autocad_processes": before_processes,
            "recommendation": "rerun_with_allow_existing_only_after_confirming_the_active_document_is_safe",
        }

    job = create_job("autocad-autostart-rounds")
    job_root = Path(job["root"])
    specs_root = Path(job["specs"])
    reports_root = Path(job["reports"])
    records: list[dict[str, Any]] = []
    backend = FileIPCBackend()
    run_id = f"autostart-rounds-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    campaign: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "started_at": _utc_now(),
        "preflight_before": before_preflight,
        "processes_before": before_processes,
        "rounds": [],
        "backend": "file_ipc",
        "execution_limits": {
            "campaign_timeout_seconds": _campaign_timeout(),
            "operation_timeout_seconds": _operation_timeout(),
        },
        "auto_start_record_before": last_autostart_record(),
    }
    prior_reflection = None
    owned_document_ids: list[str] = []
    startup_gate_failure: dict[str, Any] | None = None
    cleanup_done = False
    try:
        for index, builder in enumerate(ROUND_BUILDERS, start=1):
            round_root = specs_root / f"round-{index:02d}"
            round_root.mkdir(parents=True, exist_ok=True)
            _json_write(round_root / "request.json", {"round": index, "kind": builder.__name__, "prior_reflection": prior_reflection})
            round_report: dict[str, Any] = {
                "round": index,
                "started_at": _utc_now(),
                "startup": None,
                "operations": [],
                "drawing": None,
                "reflection": None,
            }

            # Once startup has produced a fatal signature, do not call
            # ensure_ready again for the remaining drawing types.  Record the
            # skipped gates so the campaign still has seven explicit rows,
            # while guaranteeing that a bad AutoCAD profile is never hammered
            # seven times in one run.
            if startup_gate_failure is not None:
                round_report["status"] = "not_run_startup_gate"
                round_report["startup"] = {
                    "label": f"round-{index:02d}.ensure_ready",
                    "ok": False,
                    "result": startup_gate_failure.get("result"),
                    "skipped_after_round": startup_gate_failure.get("round"),
                }
                round_report["reflection"] = {
                    "round": index,
                    "evidence_based_issues": [
                        "not_run_after_prior_startup_failure",
                        "no_repeat_autocad_launch_attempt_was_allowed",
                    ],
                    "next_round_improvements": [
                        "repair_the_startup_profile_before_resuming_drawing_rounds",
                    ],
                    "confidence": "startup_gate_containment",
                }
                _json_write(reports_root / f"round-{index:02d}.json", round_report)
                campaign["rounds"].append(round_report)
                prior_reflection = round_report["reflection"]
                continue

            startup, ready = await _timed(f"round-{index:02d}.ensure_ready", backend.ensure_ready())
            round_report["startup"] = startup
            if not startup["ok"] or not ready or not ready.ok:
                round_report["status"] = "startup_failed"
                round_report["reflection"] = _reflection(index, startup, {}, None, None, prior_reflection)
                _json_write(reports_root / f"round-{index:02d}.json", round_report)
                campaign["rounds"].append(round_report)
                prior_reflection = round_report["reflection"]
                startup_gate_failure = {"round": index, "result": startup.get("result")}
                continue

            round_report["status"] = "drawing_started"

            create = await _call(
                round_report["operations"],
                f"round-{index:02d}.create_blank_document",
                backend.drawing_create(None, idempotency_key=f"{run_id}:round-{index:02d}:create"),
            )
            if not create or not create.ok:
                round_report["reflection"] = _reflection(index, startup, {}, None, None, prior_reflection)
                campaign["rounds"].append(round_report)
                prior_reflection = round_report["reflection"]
                await backend.recover()
                continue

            context = await _call(round_report["operations"], f"round-{index:02d}.document_context", backend.document_context())
            if context and context.ok:
                doc_id = context.payload.get("active_doc_id")
                if doc_id:
                    owned_document_ids.append(str(doc_id))
            await _call(round_report["operations"], f"round-{index:02d}.setup_layers", backend.drawing_setup_mechanical({"sheet": "A3", "orientation": "landscape", "projection": "first-angle"}))

            drawing = await builder(backend, round_report["operations"])
            round_report["drawing"] = drawing
            fit_result = await _call(round_report["operations"], f"round-{index:02d}.fit_center", backend.zoom_extents())
            fit = fit_result.to_dict() if fit_result and hasattr(fit_result, "to_dict") else None
            audit_result = await _call(
                round_report["operations"],
                f"round-{index:02d}.audit",
                backend.drawing_audit(
                    rules={"connection_tolerance": 0.05, "near_miss_tolerance": 0.5, "topology_layers": ["OUTLINE"]}
                ),
            )
            audit = audit_result.to_dict() if audit_result and hasattr(audit_result, "to_dict") else None
            artifact_paths: dict[str, str] = {}
            save_path = Path(job["drawings"]) / f"round-{index:02d}.dwg"
            saved = await _call(round_report["operations"], f"round-{index:02d}.save_temporary_dwg", backend.drawing_save(str(save_path)))
            if saved and saved.ok:
                artifact_paths["dwg"] = str(save_path)
            preview_path = Path(job["previews"]) / f"round-{index:02d}.png"
            preview = await _call(
                round_report["operations"],
                f"round-{index:02d}.preview",
                backend.drawing_render_preview(str(preview_path), paper="A3", orientation="landscape", dpi=120, force=True),
            )
            if preview and preview.ok:
                artifact_paths["preview"] = str(preview_path)
            round_report["artifacts"] = {
                **artifact_paths,
                "sha256_before_cleanup": {key: _hash_file(Path(value)) for key, value in artifact_paths.items()},
            }
            round_report["reflection"] = _reflection(index, startup, drawing, fit, audit, prior_reflection)
            round_report["finished_at"] = _utc_now()
            _json_write(reports_root / f"round-{index:02d}.json", round_report)
            campaign["rounds"].append(round_report)
            prior_reflection = round_report["reflection"]
            close = await _call(round_report["operations"], f"round-{index:02d}.close_test_document", backend.drawing_close(False))
            if close and not close.ok:
                await backend.recover()

        campaign["finished_at"] = _utc_now()
        campaign["auto_start_record_after"] = last_autostart_record()
        campaign["processes_after"] = list_autocad_processes()
        campaign["autocad_pid_used"] = getattr(backend, "_acad_process_id", None)
        campaign["hwnd_used"] = getattr(backend, "_hwnd", None)
        campaign["startup_gate_failure"] = startup_gate_failure
        campaign["global_reflection"] = {
            "round_count": len(campaign["rounds"]),
            "successful_startups": sum(1 for item in campaign["rounds"] if item.get("startup", {}).get("ok")),
            "successful_drawing_rounds": sum(1 for item in campaign["rounds"] if (item.get("drawing") or {}).get("kind")),
            "skipped_after_startup_failure": sum(
                1 for item in campaign["rounds"] if item.get("status") == "not_run_startup_gate"
            ),
            "pid_stability": "one_process_expected_and_recorded",
            "focus_policy": "quiet_minimized_no_activation_requested",
            "startup_containment": (
                "stopped_after_first_failure"
                if startup_gate_failure
                else "no_startup_failure_observed"
            ),
            "startup_lessons": [
                "preflight must run before autostart",
                "first-document creation must be observable and revision-bound",
                "subsequent rounds must reuse the same PID/HWND instead of launching another AutoCAD",
                "a failed round must recover before the next round",
                "test CAD files are disposable; evidence files are retained",
            ],
            "next_campaign_improvements": [
                "install_and_test_the_signed_native_worker_then_compare_native_pipe_startup_to_COM_startup",
                "record fatal-dialog titles and window-state transitions at 250ms cadence during startup",
                "add a supervisor-owned launch mode and compare it with direct MCP autostart",
                "add stable camera readback to the center-fit gate",
            ],
        }
        _json_write(reports_root / "autostart-rounds-final.json", campaign)
        if not keep_artifacts:
            campaign["cleanup"] = cleanup_test_job_artifacts(job["job_id"])
            cleanup_done = True
            _json_write(reports_root / "autostart-rounds-final.json", campaign)
        return campaign
    finally:
        if not keep_artifacts and not cleanup_done:
            # Cancellation can happen while an AutoCAD/COM await is pending.
            # Keep the evidence tree, but remove only artifacts owned by this
            # managed job before the wrapper returns its timeout result.
            try:
                cleanup = cleanup_test_job_artifacts(job["job_id"])
                _json_write(
                    reports_root / "campaign-aborted-cleanup.json",
                    {
                        "schema_version": 1,
                        "status": "cancelled_or_failed",
                        "job_id": job["job_id"],
                        "cleanup": cleanup,
                        "finished_at": _utc_now(),
                    },
                )
            except Exception as cleanup_exc:
                try:
                    _json_write(
                        reports_root / "campaign-aborted-cleanup.json",
                        {
                            "schema_version": 1,
                            "status": "cleanup_failed",
                            "job_id": job["job_id"],
                            "error": str(cleanup_exc) or type(cleanup_exc).__name__,
                            "exception_type": type(cleanup_exc).__name__,
                            "finished_at": _utc_now(),
                        },
                    )
                except Exception:
                    pass
        # Do not kill AutoCAD here: leaving the owned process alive and
        # minimized makes the startup result inspectable without stealing focus.
        try:
            backend._com_executor.close()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Run seven managed AutoCAD startup/drawing rounds")
    parser.add_argument("--output-root", default=os.environ.get("AUTOCAD_MCP_OUTPUT_ROOT", r"D:\Codex\AutoCAD-MCP"))
    parser.add_argument("--acad-exe", default=os.environ.get("AUTOCAD_MCP_ACAD_EXE", r"D:\cad\AutoCAD 2025\acad.exe"))
    parser.add_argument("--keep-artifacts", action="store_true")
    parser.add_argument("--allow-existing", action="store_true")
    parser.add_argument("--no-autostart", action="store_true")
    args = parser.parse_args()
    os.environ["AUTOCAD_MCP_OUTPUT_ROOT"] = str(Path(args.output_root).expanduser())
    os.environ["AUTOCAD_MCP_ACTIVITY_INSIGHTS_PATH"] = str(Path(args.output_root).expanduser() / "activity-insights")
    os.environ["AUTOCAD_MCP_ACAD_EXE"] = str(Path(args.acad_exe).expanduser())
    os.environ["AUTOCAD_MCP_AUTOSTART"] = "false" if args.no_autostart else "true"
    os.environ.setdefault("AUTOCAD_MCP_VISIBLE", "true")
    os.environ.setdefault("AUTOCAD_MCP_WINDOW_MODE", "quiet_minimized")
    os.environ.setdefault("AUTOCAD_MCP_ACTIVATE_ON_DRAW", "false")
    result = asyncio.run(
        run_campaign_bounded(
            Path(args.output_root),
            keep_artifacts=args.keep_artifacts,
            allow_existing=args.allow_existing,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 124 if result.get("status") == "TIMEOUT" else (1 if result.get("status") == "FAILED" else 0)


if __name__ == "__main__":
    raise SystemExit(main())
