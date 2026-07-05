"""Tests for the `loopllm install-mcp` one-command IDE installer."""
from __future__ import annotations

import json
from pathlib import Path

from loopllm.cli import build_parser


def _run(args: list[str]) -> None:
    parsed = build_parser().parse_args(args)
    parsed.func(parsed)


def test_install_mcp_all_writes_cursor_vscode_antigravity(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _run(["install-mcp", "--ide", "all"])

    cursor = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert cursor["mcpServers"]["loopllm"]["command"] == "loopllm"
    assert "type" not in cursor["mcpServers"]["loopllm"]

    vscode = json.loads((tmp_path / ".config" / "Code" / "User" / "mcp.json").read_text())
    assert vscode["servers"]["loopllm"]["type"] == "stdio"
    assert vscode["inputs"] == []

    antigravity = json.loads(
        (tmp_path / ".config" / "Antigravity" / "User" / "mcp.json").read_text()
    )
    assert antigravity["servers"]["loopllm"]["command"] == "loopllm"


def test_install_mcp_preserves_other_servers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg_path = tmp_path / ".config" / "Code" / "User" / "mcp.json"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(json.dumps({
        "servers": {"github": {"type": "http", "url": "https://api.githubcopilot.com/mcp/"}},
        "inputs": [],
    }))

    _run(["install-mcp", "--ide", "vscode"])

    result = json.loads(cfg_path.read_text())
    assert "github" in result["servers"]
    assert "loopllm" in result["servers"]


def test_install_mcp_skips_existing_without_force(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _run(["install-mcp", "--ide", "cursor"])
    capsys.readouterr()

    _run(["install-mcp", "--ide", "cursor"])
    out = capsys.readouterr().out
    assert "already configured" in out


def test_install_mcp_force_overwrites(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _run(["install-mcp", "--ide", "cursor", "--provider", "mock"])
    _run(["install-mcp", "--ide", "cursor", "--provider", "agent", "--force"])

    cursor = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert cursor["mcpServers"]["loopllm"]["args"] == ["mcp-server", "--provider", "agent"]


def test_install_mcp_claude_code_writes_project_scoped_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    _run(["install-mcp", "--ide", "claude-code"])

    result = json.loads((project / ".mcp.json").read_text())
    assert result["mcpServers"]["loopllm"]["command"] == "loopllm"


def test_install_mcp_all_excludes_claude_code(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    _run(["install-mcp", "--ide", "all"])

    assert not (project / ".mcp.json").exists()


def test_install_mcp_invalid_json_skips_without_crashing(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    cursor_path = tmp_path / ".cursor" / "mcp.json"
    cursor_path.parent.mkdir(parents=True)
    cursor_path.write_text("not valid json")

    _run(["install-mcp", "--ide", "cursor"])
    err = capsys.readouterr().err
    assert "invalid JSON" in err
    assert cursor_path.read_text() == "not valid json"  # left untouched


def test_install_mcp_custom_name_and_model(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _run(["install-mcp", "--ide", "cursor", "--name", "loopllm-dev", "--model", "gpt-4o"])

    cursor = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert "loopllm-dev" in cursor["mcpServers"]
    assert cursor["mcpServers"]["loopllm-dev"]["env"]["LOOPLLM_MODEL"] == "gpt-4o"
