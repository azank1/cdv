"""Tests for per-project state scoping (~/.loopllm/projects/<id>/)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from loopllm.project_scope import (
    commits_since,
    current_commit_sha,
    project_state_dir,
    resolve_db_path,
    resolve_project_id,
)


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _commit(repo: Path, message: str) -> str:
    (repo / "file.txt").write_text(message)
    _git(["add", "."], repo)
    _git(["-c", "user.email=t@example.com", "-c", "user.name=Test", "commit", "-m", message], repo)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _init_repo(tmp_path: Path, name: str, remote: str | None = None) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(["init"], repo)
    if remote:
        _git(["remote", "add", "origin", remote], repo)
    return repo


def test_env_override_wins_regardless_of_cwd(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOOPLLM_PROJECT", "my-explicit-project")
    repo_a = _init_repo(tmp_path, "repo_a", remote="https://github.com/acme/widgets.git")
    repo_b = _init_repo(tmp_path, "repo_b", remote="https://github.com/acme/gadgets.git")
    # Two unrelated repos resolve to the *same* id while the override is set.
    assert resolve_project_id(repo_a) == resolve_project_id(repo_b)

    monkeypatch.delenv("LOOPLLM_PROJECT")
    # Once the override is gone, the repos diverge based on their own remotes.
    assert resolve_project_id(repo_a) != resolve_project_id(repo_b)


def test_same_remote_same_id_different_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("LOOPLLM_PROJECT", raising=False)
    remote = "https://github.com/acme/widgets.git"
    repo_a = _init_repo(tmp_path, "clone_a", remote=remote)
    repo_b = _init_repo(tmp_path, "clone_b", remote=remote)
    assert resolve_project_id(repo_a) == resolve_project_id(repo_b)


def test_different_remotes_different_ids(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("LOOPLLM_PROJECT", raising=False)
    repo_a = _init_repo(tmp_path, "a", remote="https://github.com/acme/widgets.git")
    repo_b = _init_repo(tmp_path, "b", remote="https://github.com/acme/gadgets.git")
    assert resolve_project_id(repo_a) != resolve_project_id(repo_b)


def test_git_repo_without_remote_uses_repo_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("LOOPLLM_PROJECT", raising=False)
    repo = _init_repo(tmp_path, "no_remote")
    # Same repo root resolved from a subdirectory still matches the root id.
    subdir = repo / "sub"
    subdir.mkdir()
    assert resolve_project_id(repo) == resolve_project_id(subdir)


def test_non_git_directory_falls_back_to_cwd_hash(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("LOOPLLM_PROJECT", raising=False)
    plain_a = tmp_path / "plain_a"
    plain_b = tmp_path / "plain_b"
    plain_a.mkdir()
    plain_b.mkdir()
    assert resolve_project_id(plain_a) != resolve_project_id(plain_b)
    assert resolve_project_id(plain_a) == resolve_project_id(plain_a)


def test_project_id_is_stable_short_hash(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("LOOPLLM_PROJECT", raising=False)
    pid = resolve_project_id(tmp_path)
    assert len(pid) == 12
    assert all(c in "0123456789abcdef" for c in pid)


def test_project_state_dir_layout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOOPLLM_PROJECT", "acme-widgets")
    base = tmp_path / ".loopllm"
    state_dir = project_state_dir(base=base)
    assert state_dir.parent.name == "projects"
    assert state_dir.parent.parent == base


def test_resolve_db_path_explicit_wins(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOOPLLM_PROJECT", "acme-widgets")
    explicit = tmp_path / "custom" / "store.db"
    assert resolve_db_path(str(explicit)) == explicit


def test_resolve_db_path_scoped_when_no_explicit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOOPLLM_PROJECT", "acme-widgets")
    base = tmp_path / ".loopllm"
    db_path = resolve_db_path(None, base=base)
    assert db_path.name == "store.db"
    assert db_path.parent.parent.name == "projects"
    assert db_path.parent.parent.parent == base


@pytest.mark.parametrize("explicit", [None, ""])
def test_resolve_db_path_empty_string_treated_as_unset(
    explicit: str | None, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LOOPLLM_PROJECT", "acme-widgets")
    base = tmp_path / ".loopllm"
    db_path = resolve_db_path(explicit, base=base)
    assert db_path.parent.parent.name == "projects"


def test_current_commit_sha_matches_head(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path, "repo")
    sha = _commit(repo, "first commit")
    assert current_commit_sha(repo) == sha


def test_current_commit_sha_none_outside_git(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert current_commit_sha(plain) is None


def test_commits_since_returns_range(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path, "repo")
    sha1 = _commit(repo, "first")
    sha2 = _commit(repo, "second")
    sha3 = _commit(repo, "third")

    since_first = commits_since(sha1, repo)
    assert since_first == {sha2, sha3}


def test_commits_since_empty_range_when_ref_is_head(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path, "repo")
    sha = _commit(repo, "only commit")
    assert commits_since(sha, repo) == set()


def test_commits_since_none_for_bad_ref(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path, "repo")
    _commit(repo, "only commit")
    assert commits_since("not-a-real-ref", repo) is None
