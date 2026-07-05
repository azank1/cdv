"""Tests for `loopllm audit --export` and `loopllm audit-gate`."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from loopllm.cli import build_parser
from loopllm.episodes import EpisodicStore
from loopllm.store import LoopStore


def _run(args: list[str]) -> int:
    """Parse and invoke a CLI command, returning its exit code (0 unless it sys.exit()s)."""
    parsed = build_parser().parse_args(args)
    try:
        parsed.func(parsed)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _commit(repo: Path, message: str) -> str:
    (repo / "f.txt").write_text(message)
    _git(["add", "."], repo)
    _git(["-c", "user.email=t@example.com", "-c", "user.name=Test", "commit", "-m", message], repo)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _init_repo(tmp_path: Path, name: str = "repo") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(["init", "-b", "main"], repo)
    return repo


def test_audit_export_writes_portable_artifact(tmp_path: Path, capsys) -> None:
    db = tmp_path / "store.db"
    store = LoopStore(db_path=db)
    episodic = EpisodicStore(store)
    episodic.record_episode(
        episode_type="agent_loop", goal="export me", task_type="bugfix",
        model_id="m", summary="s", score_final=0.8, commit_sha="cafebabe",
    )
    store.close()
    capsys.readouterr()

    export_path = tmp_path / ".loopllm" / "audit.json"
    _run(["--db", str(db), "audit", "--export", str(export_path)])

    artifact = json.loads(export_path.read_text())
    assert artifact["version"] == 1
    assert len(artifact["episodes"]) == 1
    assert artifact["episodes"][0]["commit_sha"] == "cafebabe"
    assert artifact["episodes"][0]["goal"] == "export me"


def test_audit_export_respects_since_filter(tmp_path: Path, capsys, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    sha_before = _commit(repo, "before")
    sha_after = _commit(repo, "after")

    db = repo / "store.db"
    store = LoopStore(db_path=db)
    episodic = EpisodicStore(store)
    episodic.record_episode(
        episode_type="agent_loop", goal="old", task_type="bugfix",
        model_id="m", summary="s", score_final=0.5, commit_sha=sha_before,
    )
    episodic.record_episode(
        episode_type="agent_loop", goal="new", task_type="bugfix",
        model_id="m", summary="s", score_final=0.9, commit_sha=sha_after,
    )
    store.close()
    capsys.readouterr()

    monkeypatch.chdir(repo)
    export_path = repo / ".loopllm" / "audit.json"
    _run(["--db", str(db), "audit", "--since", sha_before, "--export", str(export_path)])

    artifact = json.loads(export_path.read_text())
    assert [e["goal"] for e in artifact["episodes"]] == ["new"]
    assert artifact["since"] == sha_before


def test_gate_report_only_always_exits_zero(tmp_path: Path, capsys, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, "base")
    _commit(repo, "unverified work")

    monkeypatch.chdir(repo)
    code = _run(["audit-gate", "--since", base_sha, "--artifact", str(repo / "missing.json")])
    out = capsys.readouterr().out
    assert code == 0
    assert "NOT VERIFIED" in out
    assert "PASS" in out


def test_gate_require_verified_fails_on_missing_record(tmp_path: Path, capsys, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, "base")
    _commit(repo, "unverified work")

    monkeypatch.chdir(repo)
    code = _run([
        "audit-gate", "--since", base_sha,
        "--artifact", str(repo / "missing.json"), "--require-verified",
    ])
    out = capsys.readouterr().out
    assert code == 1
    assert "FAIL" in out
    assert "no verification record" in out


def test_gate_passes_when_all_commits_verified(tmp_path: Path, capsys, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, "base")
    verified_sha = _commit(repo, "verified work")

    db = repo / "store.db"
    store = LoopStore(db_path=db)
    episodic = EpisodicStore(store)
    episodic.record_episode(
        episode_type="agent_loop", goal="verified work", task_type="bugfix",
        model_id="m", summary="s", score_final=0.95, commit_sha=verified_sha,
    )
    store.close()
    capsys.readouterr()

    artifact_path = repo / ".loopllm" / "audit.json"
    monkeypatch.chdir(repo)
    _run(["--db", str(db), "audit", "--since", base_sha, "--export", str(artifact_path)])
    capsys.readouterr()

    code = _run([
        "audit-gate", "--since", base_sha,
        "--artifact", str(artifact_path), "--require-verified",
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "PASS" in out
    assert "NOT VERIFIED" not in out


def test_gate_min_score_fails_below_threshold(tmp_path: Path, capsys, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, "base")
    low_score_sha = _commit(repo, "weak work")

    db = repo / "store.db"
    store = LoopStore(db_path=db)
    episodic = EpisodicStore(store)
    episodic.record_episode(
        episode_type="agent_loop", goal="weak work", task_type="bugfix",
        model_id="m", summary="s", score_final=0.3, commit_sha=low_score_sha,
    )
    store.close()
    capsys.readouterr()

    artifact_path = repo / ".loopllm" / "audit.json"
    monkeypatch.chdir(repo)
    _run(["--db", str(db), "audit", "--since", base_sha, "--export", str(artifact_path)])
    capsys.readouterr()

    code = _run([
        "audit-gate", "--since", base_sha,
        "--artifact", str(artifact_path), "--min-score", "0.7",
    ])
    out = capsys.readouterr().out
    assert code == 1
    assert "below --min-score" in out


def test_gate_excludes_merge_commits(tmp_path: Path, capsys, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, "base")
    _git(["checkout", "-b", "feature"], repo)
    feature_sha = _commit(repo, "feature work")
    _git(["checkout", "main"], repo)
    _git(
        ["-c", "user.email=t@example.com", "-c", "user.name=Test", "merge", "--no-ff", "feature", "-m", "merge feature"],
        repo,
    )

    monkeypatch.chdir(repo)
    code = _run(["audit-gate", "--since", base_sha, "--artifact", str(repo / "missing.json")])
    out = capsys.readouterr().out
    assert code == 0
    # Only the non-merge commit should appear as a gated entry.
    assert feature_sha[:7] in out
    assert "2 commit(s) checked" not in out
    assert "1 commit(s) checked" in out


def test_gate_empty_range_reports_nothing_to_gate(tmp_path: Path, capsys, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    sha = _commit(repo, "only commit")

    monkeypatch.chdir(repo)
    code = _run(["audit-gate", "--since", sha])
    out = capsys.readouterr().out
    assert code == 0
    assert "nothing to gate" in out


def test_gate_bad_ref_warns_and_does_not_fail(tmp_path: Path, capsys, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "only commit")

    monkeypatch.chdir(repo)
    code = _run(["audit-gate", "--since", "not-a-real-ref"])
    captured = capsys.readouterr()
    assert code == 0
    assert "could not resolve commit range" in captured.err
