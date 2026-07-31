"""structlog must never leak onto stdout — it corrupts the MCP stdio
JSON-RPC stream and any CLI --json output."""
from __future__ import annotations

import io

import pytest
import structlog

from cdv.logging_config import configure_logging
from cdv.store import LoopStore


@pytest.fixture(autouse=True)
def _reset_structlog() -> None:
    yield
    structlog.reset_defaults()


def test_debug_log_goes_to_stderr_not_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    buffer = io.StringIO()
    configure_logging(level="DEBUG", stream=buffer)

    LoopStore(db_path=":memory:")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "store_schema_created" in buffer.getvalue()


def test_default_level_filters_debug_logs(capsys: pytest.CaptureFixture[str]) -> None:
    buffer = io.StringIO()
    configure_logging(stream=buffer)

    LoopStore(db_path=":memory:")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert buffer.getvalue() == ""


def test_configure_logging_is_idempotent() -> None:
    buffer = io.StringIO()
    configure_logging(level="DEBUG", stream=buffer)
    configure_logging(level="DEBUG", stream=buffer)

    LoopStore(db_path=":memory:")

    assert "store_schema_created" in buffer.getvalue()


def test_log_level_env_override(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CDV_LOG_LEVEL", "DEBUG")
    buffer = io.StringIO()
    configure_logging(stream=buffer)

    LoopStore(db_path=":memory:")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "store_schema_created" in buffer.getvalue()
