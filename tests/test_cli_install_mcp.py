"""Tests for the `cdv install-mcp` one-command IDE installer."""
from __future__ import annotations

import json
from pathlib import Path

from cdv.cli import build_parser


def _run(args: list[str]) -> None:
    parsed = build_parser().parse_args(args)
    parsed.func(parsed)


def test_install_mcp_all_writes_cursor_vscode_antigravity(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _run(["install-mcp", "--ide", "all"])

    cursor = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert cursor["mcpServers"]["cdv"]["command"] == "cdv"
    assert "type" not in cursor["mcpServers"]["cdv"]

    vscode = json.loads((tmp_path / ".config" / "Code" / "User" / "mcp.json").read_text())
    assert vscode["servers"]["cdv"]["type"] == "stdio"
    assert vscode["inputs"] == []

    antigravity = json.loads(
        (tmp_path / ".config" / "Antigravity" / "User" / "mcp.json").read_text()
    )
    assert antigravity["servers"]["cdv"]["command"] == "cdv"


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
    assert "cdv" in result["servers"]


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
    assert cursor["mcpServers"]["cdv"]["args"] == ["mcp-server", "--provider", "agent"]


def test_install_mcp_claude_code_writes_project_scoped_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    _run(["install-mcp", "--ide", "claude-code"])

    result = json.loads((project / ".mcp.json").read_text())
    assert result["mcpServers"]["cdv"]["command"] == "cdv"


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
    _run(["install-mcp", "--ide", "cursor", "--name", "cdv-dev", "--model", "gpt-4o"])

    cursor = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert "cdv-dev" in cursor["mcpServers"]
    assert cursor["mcpServers"]["cdv-dev"]["env"]["CDV_MODEL"] == "gpt-4o"


# --- --rules: project-scoped agent instruction files ---


def test_install_mcp_no_rules_by_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    _run(["install-mcp", "--ide", "cursor"])

    assert not (project / ".cursor" / "rules" / "cdv.mdc").exists()


def test_install_mcp_rules_cursor_writes_mdc(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    _run(["install-mcp", "--ide", "cursor", "--rules"])

    rules = (project / ".cursor" / "rules" / "cdv.mdc").read_text()
    assert "alwaysApply: true" in rules
    assert "cdv-agent-rules" in rules
    assert "never your own score" in rules


def test_install_mcp_rules_vscode_writes_instructions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    _run(["install-mcp", "--ide", "vscode", "--rules"])

    rules = (project / ".github" / "instructions" / "cdv.instructions.md").read_text()
    assert 'applyTo: "**"' in rules
    assert "cdv-agent-rules" in rules


def test_install_mcp_rules_claude_code_creates_then_appends(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    _run(["install-mcp", "--ide", "claude-code", "--rules"])
    claude_md = project / "CLAUDE.md"
    assert "cdv-agent-rules" in claude_md.read_text()

    # Existing CLAUDE.md content is preserved, rules appended
    claude_md.write_text("# My project\n\nDo things my way.\n")
    # strip the previous rules block to simulate a pre-existing user file
    _run(["install-mcp", "--ide", "claude-code", "--rules", "--force"])
    text = claude_md.read_text()
    assert text.startswith("# My project")
    assert "cdv-agent-rules" in text


def test_install_mcp_rules_rerun_does_not_duplicate(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    _run(["install-mcp", "--ide", "claude-code", "--rules"])
    _run(["install-mcp", "--ide", "claude-code", "--rules", "--force"])
    out = capsys.readouterr().out
    assert "rules already present" in out
    assert project.joinpath("CLAUDE.md").read_text().count("cdv-agent-rules") == 1


def test_install_mcp_rules_existing_dedicated_file_not_clobbered(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    rules_path = project / ".cursor" / "rules" / "cdv.mdc"
    rules_path.parent.mkdir(parents=True)
    rules_path.write_text("my own custom rules")
    monkeypatch.chdir(project)

    _run(["install-mcp", "--ide", "cursor", "--rules"])
    err = capsys.readouterr().err
    assert "use --force" in err
    assert rules_path.read_text() == "my own custom rules"

    _run(["install-mcp", "--ide", "cursor", "--rules", "--force"])
    assert "alwaysApply: true" in rules_path.read_text()


def test_install_mcp_rules_antigravity_skips_with_note(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    _run(["install-mcp", "--ide", "antigravity", "--rules"])
    out = capsys.readouterr().out
    assert "no rules-file mechanism" in out


def test_install_mcp_rules_written_even_when_config_exists(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    _run(["install-mcp", "--ide", "cursor"])  # config only
    _run(["install-mcp", "--ide", "cursor", "--rules"])  # config skipped, rules still written

    assert (project / ".cursor" / "rules" / "cdv.mdc").exists()
