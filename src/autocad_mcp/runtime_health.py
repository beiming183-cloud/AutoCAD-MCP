"""Preflight checks for the Windows AutoCAD execution environment.

These checks deliberately do not start AutoCAD or connect to COM.  They make
startup failures diagnosable before the drawing backend is allowed to mutate a
document.
"""

from __future__ import annotations

import csv
import datetime as _datetime
import importlib
import importlib.metadata
import io
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path


class RuntimeHealthError(RuntimeError):
    """An environment problem with a stable MCP error code."""

    def __init__(self, message: str, *, error_code: str, details: dict, recommended_action: str):
        super().__init__(message)
        self.error_code = error_code
        self.details = details
        self.recommended_action = recommended_action


def _cer_text(path: Path) -> str:
    """Extract printable UTF-8/Windows-1252 text from a CER protobuf blob."""
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    decoded = raw.decode("utf-8", errors="replace")
    # CER payloads contain length-delimited strings and control bytes.  Keep
    # line breaks and printable characters so regexes remain deterministic.
    return "".join(char if char in "\r\n\t" or ord(char) >= 32 else " " for char in decoded)


def _cer_field(text: str, *patterns: str) -> str | None:
    """Return the first useful field from a CER payload.

    CER ``rawdata-t1.pb`` and ``rawdata-t2.pb`` are protobuf-derived blobs,
    not line-oriented logs.  The printable strings and their length prefixes
    vary between AutoCAD builds, so the parser deliberately tolerates a small
    amount of binary material between a key and its value.
    """
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            value = match.group(1).strip(" \t\r\n\x00")
            if value:
                return value
    return None


def _cer_record(path: Path) -> dict:
    """Parse one CER rawdata file into stable, non-sensitive evidence."""
    text = _cer_text(path)
    kind = path.name.casefold()
    stack = _cer_field(
        text,
        r"Current Stack:\s*(.{0,2400}?)(?:Last Unhandled|Last 3|GDI_Objects|$)",
        r"(?:managed|call)[ _-]?stack\s*[:=]\s*(.{0,2400})",
    )
    exception_code = _cer_field(
        text,
        r"AP/exception/code.{0,160}?(0x[0-9A-Fa-f]{4,})",
        r"(?:exception|error)[ _-]?(?:code|number).{0,100}?(0x[0-9A-Fa-f]{4,})",
        r"\b(0xE0434352)\b",
    )
    exception_address = _cer_field(
        text,
        r"AP/exception/address.{0,160}?(0x[0-9A-Fa-f]{4,})",
        r"exception[ _-]?address.{0,100}?(0x[0-9A-Fa-f]{4,})",
    )
    module = _cer_field(
        text,
        r"AP/exception/module_name.{0,140}?([A-Za-z0-9_.-]+\.dll)",
        r"(?:faulting|exception)[ _-]?module.{0,100}?([A-Za-z0-9_.-]+\.dll)",
    )
    build = _cer_field(
        text,
        r"UPI_BUILD.{0,100}?([0-9]+(?:\.[0-9]+)+)",
        r"(?:AutoCAD|product)[ _-]?(?:build|version).{0,100}?([0-9]+(?:\.[0-9]+)+)",
    )
    theme = _cer_field(
        text,
        r"SP/THEME_TYPE.{0,100}?([A-Za-z]+)",
        r"(?:theme|palette)[ _-]?(?:type|name).{0,100}?([A-Za-z]+)",
    )
    crash_date = _cer_field(
        text,
        r"(?:crash|event|report)[ _-]?date.{0,100}?([0-9]{4}[-/]\d{1,2}[-/]\d{1,2}(?:[ T]\d{1,2}:\d{2}:\d{2})?)",
        r"(?:crash|event|report)[ _-]?date.{0,100}?([0-9]{1,2}/[0-9]{1,2}/[0-9]{4}(?:[ T]\d{1,2}:\d{2}:\d{2})?)",
        r"(?:date|time).{0,80}?([0-9]{4}[-/]\d{1,2}[-/]\d{1,2}(?:[ T]\d{1,2}:\d{2}:\d{2})?)",
        r"(?:date|time).{0,80}?([0-9]{1,2}/[0-9]{1,2}/[0-9]{4}(?:[ T]\d{1,2}:\d{2}:\d{2})?)",
    )
    try:
        modified_at_epoch = path.stat().st_mtime
    except OSError:
        modified_at_epoch = None
    if crash_date is None and modified_at_epoch is not None:
        crash_date = _datetime.datetime.fromtimestamp(
            modified_at_epoch, tz=_datetime.timezone.utc
        ).isoformat(timespec="seconds")
    return {
        "path": str(path),
        "record_kind": kind,
        "modified_at_epoch": modified_at_epoch,
        "crash_date": crash_date,
        "exception_code": exception_code,
        "exception_address": exception_address,
        "module": module,
        "product_build": build,
        "theme_type": theme,
        "stack_excerpt": " ".join((stack or "").split())[:2400] or None,
        "readable": bool(text),
    }


def _cer_log_record(path: Path) -> dict:
    """Parse the latest selected fields from Autodesk's text CER log.

    Some Windows ACLs allow reading a known ``rawdata-t1.pb`` path but deny
    directory enumeration.  The companion log is a useful fallback and does
    not require opening a minidump or uploading anything.
    """
    text = _cer_text(path)
    blocks = re.split(r"(?=\[[0-9]{2}/[0-9]{2}/[0-9]{2} .*?cer started)", text, flags=re.IGNORECASE)
    block = next((item for item in reversed(blocks) if "cer started" in item.lower()), text)

    def setting(key: str) -> str | None:
        match = re.search(
            rf"Setting key\s+{re.escape(key)}\s*,\s*value\s+(.*?)(?:\s+\(maxCount|\r?$)",
            block,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        return match.group(1).strip() if match else None

    date_match = re.search(r"\[([^\]]+)\]\s+.*?cer started", block, flags=re.IGNORECASE)
    crash_date = setting("AI/CrashDate")
    if crash_date is None and date_match:
        crash_date = date_match.group(1).strip()
    try:
        modified_at_epoch = path.stat().st_mtime
    except OSError:
        modified_at_epoch = None
    return {
        "path": str(path),
        "record_kind": "cer.log",
        "modified_at_epoch": modified_at_epoch,
        "crash_date": crash_date,
        "exception_code": setting("AP/exception/code"),
        "exception_address": setting("AP/exception/address"),
        "module": setting("AP/exception/module_name"),
        "product_build": setting("SP/UPI_BUILD"),
        "theme_type": setting("SP/THEME_TYPE"),
        "stack_excerpt": None,
        "readable": bool(text),
    }


def _cer_log_candidates(root: Path) -> list[Path]:
    configured = os.environ.get("AUTOCAD_MCP_CER_LOG", "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    product_hash = os.environ.get("AUTOCAD_MCP_CER_PRODUCT_HASH", "").strip()
    if product_hash:
        candidates.append(root / product_hash / "cer.log")
    try:
        candidates.extend(root.glob("*/cer.log"))
    except OSError:
        pass
    result: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).casefold()
        if key in seen:
            continue
        try:
            exists = candidate.is_file()
        except OSError:
            exists = False
        if exists:
            result.append(candidate)
            seen.add(key)
    return result


def autocad_cer_snapshot(*, max_files: int = 4) -> dict:
    """Read recent Autodesk CER metadata without opening the dump itself.

    This is intentionally a lightweight evidence reader.  It does not upload
    anything and does not parse the minidump; it only extracts the exception,
    product build, module, and managed stack needed to explain a startup
    failure to an MCP client.
    """
    configured_file = os.environ.get("AUTOCAD_MCP_CER_FILE", "").strip()
    if configured_file:
        path = Path(configured_file).expanduser()
        if path.is_file():
            record = _cer_record(path)
            return {
                "available": bool(record["readable"]),
                "root": str(path.parent),
                "records": [record],
            }
        return {
            "available": False,
            "root": str(path.parent),
            "reason": "configured-cer-file-not-readable",
            "records": [],
        }
    if sys.platform != "win32":
        return {"available": False, "reason": "not-windows", "records": []}
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if not local:
        return {"available": False, "reason": "LOCALAPPDATA-not-set", "records": []}
    root = Path(os.environ.get("AUTOCAD_MCP_CER_ROOT", "").strip() or (Path(local) / "Autodesk" / "CER"))
    try:
        candidates = sorted(
            (
                item
                for pattern in ("*/rawdata-t1.pb", "*/rawdata-t2.pb")
                for item in root.glob(pattern)
                if item.is_file()
            ),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )[: max(1, int(max_files))]
    except (OSError, ValueError):
        return {"available": False, "reason": "cer-directory-unreadable", "records": []}

    records: list[dict] = []
    for path in candidates:
        records.append(_cer_record(path))
    if not records:
        logs = []
        for item in _cer_log_candidates(root):
            try:
                logs.append((item.stat().st_mtime, item))
            except OSError:
                continue
        logs = [
            item
            for _, item in sorted(logs, key=lambda pair: pair[0], reverse=True)
        ][: max(1, int(max_files))]
        records.extend(_cer_log_record(path) for path in logs)
        if records:
            return {
                "available": True,
                "root": str(root),
                "source": "cer_log_fallback",
                "records": records,
            }
    return {"available": bool(records), "root": str(root), "records": records}


def win32_runtime_health() -> dict:
    """Check the pywin32 modules required by the File IPC backend."""
    result = {
        "platform": sys.platform,
        "python": sys.executable,
        "python_version": sys.version.split()[0],
        "pywin32_distribution": None,
        "ok": False,
        "checks": {},
    }
    if sys.platform != "win32":
        result["checks"]["platform"] = {"ok": False, "message": "Windows is required"}
        return result

    try:
        result["pywin32_distribution"] = importlib.metadata.version("pywin32")
    except importlib.metadata.PackageNotFoundError:
        result["pywin32_distribution"] = None

    required = ("pywintypes", "pythoncom", "win32api", "win32gui", "win32process", "win32com.client")
    for module_name in required:
        try:
            module = importlib.import_module(module_name)
            result["checks"][module_name] = {
                "ok": True,
                "file": str(getattr(module, "__file__", "")),
            }
        except Exception as exc:
            result["checks"][module_name] = {
                "ok": False,
                "exception_type": type(exc).__name__,
                "message": str(exc) or type(exc).__name__,
            }
    result["ok"] = all(item.get("ok") for item in result["checks"].values())
    return result


def list_autocad_processes() -> list[dict]:
    """List acad.exe processes without requiring pywin32 or COM."""
    if sys.platform != "win32":
        return []
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq acad.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="mbcs",
            errors="replace",
            timeout=3,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        rows = []
        for row in csv.reader(io.StringIO(completed.stdout)):
            if len(row) < 2 or row[0].strip().lower() != "acad.exe":
                continue
            try:
                process_id = int(row[1])
            except (TypeError, ValueError):
                continue
            rows.append(
                {
                    "image": row[0],
                    "process_id": process_id,
                    "session_name": row[2] if len(row) > 2 else None,
                    "session_number": row[3] if len(row) > 3 else None,
                    "memory": row[4] if len(row) > 4 else None,
                }
            )
        return rows
    except (OSError, subprocess.SubprocessError):
        return []


def activity_insights_path() -> Path:
    configured = os.environ.get("AUTOCAD_MCP_ACTIVITY_INSIGHTS_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
    if local_appdata:
        return (Path(local_appdata) / "Autodesk" / "ActivityInsights" / "CSV").resolve()
    return (Path.home() / "AppData" / "Local" / "Autodesk" / "ActivityInsights" / "CSV").resolve()


def activity_insights_write_preflight() -> dict:
    """Verify the directory AutoCAD's Activity Insights component can write."""
    path = activity_insights_path()
    if os.environ.get("AUTOCAD_MCP_DISABLE_ACTIVITY_INSIGHTS", "").strip().lower() in (
        "1", "true", "yes", "on"
    ):
        return {
            "ok": True,
            "checked": False,
            "disabled_by_config": True,
            "path": str(path),
        }

    probe = path / f".autocad-mcp-write-{os.getpid()}-{uuid.uuid4().hex}"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe.write_text("preflight\n", encoding="ascii")
        probe.unlink(missing_ok=True)
        return {"ok": True, "checked": True, "path": str(path)}
    except Exception as exc:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
        return {
            "ok": False,
            "checked": True,
            "path": str(path),
            "exception_type": type(exc).__name__,
            "message": str(exc) or type(exc).__name__,
        }


def _autocad_registry_root() -> str:
    """Return only the AutoCAD profile branch, never licensing state."""
    default = r"Software\Autodesk\AutoCAD\R25.0\ACAD-8101:804"
    candidate = os.environ.get("AUTOCAD_MCP_PROFILE_REGISTRY_ROOT", default).strip().strip("\\")
    # Refuse arbitrary registry paths.  The helper is intentionally limited to
    # a product Profile branch and must never become a way to reach licensing
    # or activation keys through an environment variable.
    if not re.fullmatch(
        r"Software\\Autodesk\\AutoCAD\\R[0-9.]+\\ACAD-[^\\]+",
        candidate,
        flags=re.IGNORECASE,
    ):
        return default
    return candidate


def _registry_default_profile_name() -> str | None:
    """Read the active product's default profile name, if accessible."""
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _autocad_registry_root() + r"\Profiles"
        ) as profiles:
            value = winreg.QueryValueEx(profiles, "")[0]
        return str(value).strip() or None
    except Exception:
        return None


def _registry_profile_values(profile_name: str) -> dict:
    """Read a small, safe subset of an AutoCAD profile for diagnostics."""
    if not profile_name or "\\" in profile_name or "/" in profile_name or profile_name in {".", ".."}:
        return {"ok": False, "exists": False, "reason": "invalid-profile-name", "profile": profile_name}
    if sys.platform != "win32":
        return {"ok": False, "reason": "not-windows", "profile": profile_name}
    try:
        import winreg

        root = _autocad_registry_root()
        profile_path = root + "\\Profiles\\" + profile_name
        values: dict[str, dict] = {}
        for subkey in ("OptionsDialog_DisplayTab", "Variables", "General"):
            try:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER, profile_path + "\\" + subkey
                ) as key:
                    index = 0
                    while True:
                        try:
                            name, value, value_type = winreg.EnumValue(key, index)
                        except OSError:
                            break
                        if name.upper() in {
                            "COLORSCHEME",
                            "COLORTHEME",
                            "ACTIVITYINSIGHTSSUPPORT",
                            "ACTIVITYINSIGHTSPATH",
                            "GFXDX12",
                            "3DCONFIG",
                            "*GPUTEXT2D",
                            "ACADDRV",
                        }:
                            values.setdefault(subkey, {})[name] = {
                                "value": value,
                                "type": value_type,
                            }
                        index += 1
            except OSError:
                continue
        default_name = _registry_default_profile_name()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, profile_path):
            exists = True
        return {
            "ok": True,
            "exists": exists,
            "profile": profile_name,
            "registry_root": root,
            "default_profile": default_name,
            "values": values,
        }
    except Exception as exc:
        return {
            "ok": False,
            "exists": False,
            "profile": profile_name,
            "registry_root": _autocad_registry_root(),
            "exception_type": type(exc).__name__,
            "message": str(exc) or type(exc).__name__,
        }


def autocad_profile_preflight(profile_name: str | None = None) -> dict:
    """Check the configured profile without creating or changing anything."""
    name = (
        profile_name
        or os.environ.get("AUTOCAD_MCP_ACAD_PROFILE_NAME", "").strip()
        or _registry_default_profile_name()
        or "MCP-Minimal"
    )
    snapshot = _registry_profile_values(name)
    if not snapshot.get("ok"):
        return snapshot
    values = snapshot.get("values", {})
    variables = values.get("Variables", {})
    display = values.get("OptionsDialog_DisplayTab", {})
    required = {
        "profile_exists": bool(snapshot.get("exists")),
        "light_theme": display.get("COLORSCHEME", {}).get("value") == 1
        or variables.get("COLORTHEME", {}).get("value") in ("1", 1),
        "activity_insights_disabled": variables.get("ACTIVITYINSIGHTSSUPPORT", {}).get("value")
        in ("0", 0),
        "directx12_disabled": variables.get("GFXDX12", {}).get("value") in ("0", 0),
        "software_3d_config": variables.get("3DCONFIG", {}).get("value") in ("0", 0),
    }
    return {
        **snapshot,
        "checks": required,
        "ready": all(required.values()),
        "license_scope_untouched": True,
    }


def _delete_registry_tree(winreg, root, path: str) -> None:
    """Delete only a profile key created by this module."""
    try:
        with winreg.OpenKey(
            root, path, 0, winreg.KEY_READ | winreg.KEY_WRITE
        ) as key:
            subkeys = []
            index = 0
            while True:
                try:
                    subkeys.append(winreg.EnumKey(key, index))
                except OSError:
                    break
                index += 1
        for child in subkeys:
            _delete_registry_tree(winreg, root, path + "\\" + child)
        winreg.DeleteKey(root, path)
    except OSError:
        pass


def ensure_minimal_autocad_profile(
    *, profile_name: str = "MCP-Minimal", source_profile: str | None = None
) -> dict:
    """Create a minimal UI profile in the AutoCAD profile branch.

    This is opt-in and copies only benign profile settings.  It never
    traverses or edits Autodesk licensing, activation, or crack-related keys.
    Existing target profiles are left untouched and merely inspected.
    """
    if sys.platform != "win32":
        return {"ok": False, "reason": "not-windows", "profile": profile_name}
    profile_name = profile_name.strip()
    if not profile_name or "\\" in profile_name or "/" in profile_name:
        return {"ok": False, "reason": "invalid-profile-name", "profile": profile_name}
    try:
        import winreg

        root = _autocad_registry_root()
        profiles_path = root + r"\Profiles"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, profiles_path) as profiles:
            default_name = winreg.QueryValueEx(profiles, "")[0]
        source = (
            source_profile
            or os.environ.get("AUTOCAD_MCP_PROFILE_SOURCE_NAME", "").strip()
            or default_name
        )
        if (
            not source
            or source == profile_name
            or "\\" in str(source)
            or "/" in str(source)
            or str(source) in {".", ".."}
        ):
            return {
                "ok": False,
                "reason": "source-profile-not-configured",
                "profile": profile_name,
                "source_profile": source,
                "registry_root": root,
            }
        target_path = profiles_path + "\\" + profile_name
        source_path = profiles_path + "\\" + str(source)
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, target_path):
                return {
                    **autocad_profile_preflight(profile_name),
                    "created": False,
                    "source_profile": source,
                    "license_scope_untouched": True,
                }
        except OSError:
            pass

        allowed_subkeys = ("General", "Variables", "OptionsDialog_DisplayTab")
        target = winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, target_path, 0, winreg.KEY_WRITE
        )
        try:
            winreg.SetValueEx(target, "", 0, winreg.REG_SZ, "AutoCAD MCP Minimal")
            target.Close()
            for subkey in allowed_subkeys:
                try:
                    with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER, source_path + "\\" + subkey
                    ) as src:
                        dst = winreg.CreateKeyEx(
                            winreg.HKEY_CURRENT_USER,
                            target_path + "\\" + subkey,
                            0,
                            winreg.KEY_WRITE,
                        )
                        try:
                            index = 0
                            while True:
                                try:
                                    name, value, value_type = winreg.EnumValue(src, index)
                                except OSError:
                                    break
                                winreg.SetValueEx(dst, name, 0, value_type, value)
                                index += 1
                        finally:
                            dst.Close()
                except OSError:
                    continue

            with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER,
                target_path + r"\OptionsDialog_DisplayTab",
                0,
                winreg.KEY_WRITE,
            ) as display:
                winreg.SetValueEx(display, "COLORSCHEME", 0, winreg.REG_DWORD, 1)
            with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER,
                target_path + r"\Variables",
                0,
                winreg.KEY_WRITE,
            ) as variables:
                for name, value in {
                    "ACTIVITYINSIGHTSSUPPORT": "0",
                    "ACTIVITYINSIGHTSPATH": r"D:\Codex\AutoCAD-MCP\runtime\activity-insights",
                    "COLORTHEME": "1",
                    "GFXDX12": "0",
                    "3DCONFIG": "0",
                    "*GPUTEXT2D": "0",
                }.items():
                    winreg.SetValueEx(variables, name, 0, winreg.REG_SZ, value)
        except Exception:
            _delete_registry_tree(winreg, winreg.HKEY_CURRENT_USER, target_path)
            raise
        snapshot = autocad_profile_preflight(profile_name)
        snapshot.update(
            {
                "created": True,
                "source_profile": source,
                "license_scope_untouched": True,
            }
        )
        return snapshot
    except Exception as exc:
        return {
            "ok": False,
            "created": False,
            "profile": profile_name,
            "registry_root": _autocad_registry_root(),
            "exception_type": type(exc).__name__,
            "message": str(exc) or type(exc).__name__,
            "license_scope_untouched": True,
        }


def environment_preflight() -> dict:
    """Return a non-mutating snapshot suitable for ``system.preflight``."""
    runtime = win32_runtime_health()
    activity = activity_insights_write_preflight()
    processes = list_autocad_processes()
    cer = autocad_cer_snapshot()
    profile = autocad_profile_preflight()
    profile_required = os.environ.get("AUTOCAD_MCP_PROFILE_MODE", "existing").strip().lower() in {
        "isolated",
        "required",
    }
    profile_ok = bool(profile.get("ready")) if profile_required else True
    recent_crash = any(
        record.get("exception_code")
        for record in cer.get("records", [])
        if isinstance(record, dict)
    )
    return {
        "ok": bool(runtime["ok"] and activity["ok"] and profile_ok),
        "runtime": runtime,
        "autocad_processes": processes,
        "activity_insights": activity,
        "cer": cer,
        "recent_autocad_crash": recent_crash,
        "profile": profile,
        "autostart_enabled": os.environ.get("AUTOCAD_MCP_AUTOSTART", "false").lower()
        in ("1", "true", "yes", "on"),
        "recommended_action": (
            "repair_pywin32_and_restart_mcp"
            if not runtime["ok"]
            else "fix_activity_insights_path_permissions_or_disable_activity_insights"
            if not activity["ok"]
            else "start_autocad_manually_then_call_system.ensure_ready"
            if processes
            else "create_or_select_the_minimal_autocad_profile"
            if profile_required and not profile_ok
            else "inspect_the_latest_autocad_CER_record_before_starting"
            if recent_crash and not processes
            else "environment_ready"
        ),
    }


def runtime_health_error(result: dict) -> RuntimeHealthError | None:
    if not result["ok"]:
        if not result["runtime"]["ok"]:
            error_code = "E_PYWIN32_BROKEN"
        elif not result["activity_insights"]["ok"]:
            error_code = "E_AUTOCAD_PROFILE_UNWRITABLE"
        elif result.get("profile", {}).get("ready") is False:
            error_code = "E_AUTOCAD_PROFILE_NOT_READY"
        else:
            error_code = "E_AUTOCAD_RUNTIME_UNHEALTHY"
        return RuntimeHealthError(
            "The Windows AutoCAD runtime preflight failed",
            error_code=error_code,
            details=result,
            recommended_action=result["recommended_action"],
        )
    return None
