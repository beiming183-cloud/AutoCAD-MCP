"""UTF-8-BOM file logging for Windows-friendly diagnostics."""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

import structlog

from autocad_mcp.workspace import ensure_workspace


UTF8_BOM = b"\xef\xbb\xbf"


def ensure_utf8_bom(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(UTF8_BOM)
    else:
        content = path.read_bytes()
        if not content.startswith(UTF8_BOM):
            path.write_bytes(UTF8_BOM + content)
    return path


class TeeLogWriter:
    """Mirror structlog text to stderr and one BOM-prefixed UTF-8 file."""

    def __init__(self, stream, path: Path):
        self.stream = stream
        self.path = ensure_utf8_bom(path)
        self._lock = threading.Lock()

    def write(self, value: str) -> int:
        with self._lock:
            self.stream.write(value)
            with self.path.open("a", encoding="utf-8", newline="") as target:
                target.write(value)
        return len(value)

    def flush(self) -> None:
        self.stream.flush()


def configure_logging() -> Path:
    configured = os.environ.get("AUTOCAD_MCP_LOG_PATH", "").strip()
    path = (
        Path(configured).expanduser().resolve()
        if configured
        else ensure_workspace()["logs"] / "autocad-mcp.log"
    )
    ensure_utf8_bom(path)
    stream_handler = logging.StreamHandler(sys.stderr)
    file_handler = logging.FileHandler(path, encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[stream_handler, file_handler],
        force=True,
    )
    tee = TeeLogWriter(sys.stderr, path)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(file=tee),
        cache_logger_on_first_use=True,
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
    )
    return path
