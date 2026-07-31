"""structlog configuration for cdv's process entry points.

Left unconfigured, structlog's default logger factory writes to **stdout**
with no level filtering. That's fatal for two of cdv's entry points:

- the MCP server's stdio transport, where stdout *is* the JSON-RPC stream, and
- any CLI command with a ``--json`` output mode.

``configure_logging`` must be called once at process startup (before any
logger is used) to redirect all structlog output to stderr instead.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import TextIO

import structlog

_DEFAULT_LEVEL = "WARNING"


def configure_logging(level: str | None = None, stream: TextIO | None = None) -> None:
    """Route all structlog output to *stream* (default stderr) at *level*.

    Idempotent and safe to call multiple times (e.g. once from the CLI and
    again from a nested MCP server launch) — later calls simply reconfigure.
    Not called automatically at package import time, so embedding cdv as
    a library never clobbers a host application's own structlog setup.
    """
    resolved_level = (level or os.environ.get("CDV_LOG_LEVEL") or _DEFAULT_LEVEL).upper()
    resolved_stream = stream if stream is not None else sys.stderr
    numeric_level = logging.getLevelName(resolved_level)
    if not isinstance(numeric_level, int):
        numeric_level = logging.WARNING

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=resolved_stream.isatty()),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(file=resolved_stream),
        # Module-level loggers (``logger = structlog.get_logger(__name__)``) are
        # process-wide singletons; caching would permanently bind them to
        # whichever stream/level was active on their *first* log call, so a
        # later configure_logging() call would silently stop taking effect.
        cache_logger_on_first_use=False,
    )
