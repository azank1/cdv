"""Tests for the `loopllm paths` and `loopllm audit` CLI commands."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from loopllm.cli import build_parser
from loopllm.episodes import EpisodicStore
from loopllm.store import LoopStore


def _run(args: list[str]) -> None:
    parsed = build_parser().parse_args(args)
    parsed.func(parsed)


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _commit(repo: Path, message: str) -> str:
    (repo / "f.txt").write_text(message)
    _git(["add", "."], repo)
    _git(["-c", "user.email=t@example.com", "-c", "user.name=Test", "commit", "-m", message], repo)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_paths_command_reads_top_level_db_flag(tmp_path: Path, capsys) -> None:
    # --db is defined on the top-level parser, so it must precede the
    # subcommand name (same convention as `loopllm --db X score ...`).
    db = tmp_path / "store.db"
    _run(["--db", str(db), "paths"])
    result = json.loads(capsys.readouterr().out)

    assert result["db"] == str(db)
    assert result["active_runs_dir"] == str(db.parent / "active_runs")


def test_audit_reports_recorded_episodes(tmp_path: Path, capsys) -> None:
    db = tmp_path / "store.db"
    store = LoopStore(db_path=db)
    episodic = EpisodicStore(store)
    episodic.record_episode(
        episode_type="agent_loop", goal="fix flaky auth tests", task_type="bugfix",
        model_id="m", summary="fixed", score_final=0.92, steps_used=2,
        stop_reason="goal_reached", commit_sha="abc1234",
    )
    store.close()

    _run(["--db", str(db), "audit"])
    out = capsys.readouterr().out
    assert "fix flaky auth tests" in out
    assert "1 episode(s)" in out
    assert "abc1234"[:7] in out


def test_audit_json_output(tmp_path: Path, capsys) -> None:
    db = tmp_path / "store.db"
    store = LoopStore(db_path=db)
    episodic = EpisodicStore(store)
    episodic.record_episode(
        episode_type="dag", goal="merged dag run", task_type="refactor",
        model_id="m", summary="merged", score_final=1.0, commit_sha="deadbeef",
    )
    store.close()
    capsys.readouterr()  # discard schema-creation log noise from setup above

    _run(["--db", str(db), "audit", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == 1
    assert payload[0]["goal"] == "merged dag run"
    assert payload[0]["commit_sha"] == "deadbeef"


def test_audit_no_episodes_message(tmp_path: Path, capsys) -> None:
    db = tmp_path / "store.db"
    LoopStore(db_path=db).close()
    _run(["--db", str(db), "audit"])
    assert "No verification episodes recorded" in capsys.readouterr().out


def test_audit_since_filters_by_commit_range(tmp_path: Path, capsys, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init"], repo)
    sha_before = _commit(repo, "before")
    sha_after = _commit(repo, "after")

    db = repo / "store.db"
    store = LoopStore(db_path=db)
    episodic = EpisodicStore(store)
    episodic.record_episode(
        episode_type="agent_loop", goal="old work", task_type="bugfix",
        model_id="m", summary="s", score_final=0.5, commit_sha=sha_before,
    )
    episodic.record_episode(
        episode_type="agent_loop", goal="new work", task_type="bugfix",
        model_id="m", summary="s", score_final=0.9, commit_sha=sha_after,
    )
    store.close()

    monkeypatch.chdir(repo)
    _run(["--db", str(db), "audit", "--since", sha_before])
    out = capsys.readouterr().out
    assert "new work" in out
    assert "old work" not in out


def test_audit_since_bad_ref_warns_and_shows_all(tmp_path: Path, capsys) -> None:
    db = tmp_path / "store.db"
    store = LoopStore(db_path=db)
    episodic = EpisodicStore(store)
    episodic.record_episode(
        episode_type="agent_loop", goal="some work", task_type="bugfix",
        model_id="m", summary="s", score_final=0.9, commit_sha="abc",
    )
    store.close()

    _run(["--db", str(db), "audit", "--since", "not-a-real-ref"])
    captured = capsys.readouterr()
    assert "could not resolve commit range" in captured.err
    assert "some work" in captured.out
