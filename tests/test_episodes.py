"""Tests for episodic memory (schema v5)."""
from __future__ import annotations

import sqlite3

from loopllm.episodes import EpisodicStore, extract_tags, summarize_artifacts, tokenize_for_recall
from loopllm.store import LoopStore, SCHEMA_VERSION


def _git(args: list[str], cwd) -> None:
    import subprocess

    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_migration_v4_to_current(tmp_path) -> None:
    """A pre-existing v4 database gains v5 episodic tables, v6 commit_sha, v7 FTS index."""
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE schema_version (version INTEGER NOT NULL);"
        "INSERT INTO schema_version (version) VALUES (4);"
    )
    conn.commit()
    conn.close()

    store = LoopStore(db_path=db)
    with store._connection() as c:
        version = c.execute("SELECT version FROM schema_version").fetchone()["version"]
        tables = {
            r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        episode_cols = {r[1] for r in c.execute("PRAGMA table_info(episodes)")}
    assert version == SCHEMA_VERSION == 7
    assert {"episodes", "active_runs"} <= tables
    assert "commit_sha" in episode_cols
    # FTS5 recall index is built when the SQLite build supports it.
    if store._fts5:
        assert "episodes_fts" in tables


def test_recall_ranks_relevant_first_with_tag_boost(store: LoopStore) -> None:
    episodic = EpisodicStore(store)
    episodic.record_episode(
        episode_type="agent_loop", goal="fix flaky auth tests", task_type="bugfix",
        model_id="m", summary="auth pytest flaky resolved", score_final=0.9,
    )
    episodic.record_episode(
        episode_type="agent_loop", goal="update billing docs", task_type="docs",
        model_id="m", summary="documentation refresh", score_final=0.8,
    )
    hits = episodic.recall("flaky auth tests", k=5)
    assert hits and "auth" in hits[0]["goal"].lower()


def test_recall_ignores_stopwords(store: LoopStore) -> None:
    episodic = EpisodicStore(store)
    episodic.record_episode(
        episode_type="agent_loop", goal="refactor the api client", task_type="refactor",
        model_id="m", summary="api refactor done",
    )
    # Query is all stopwords/short tokens except 'api' -> still finds the episode.
    hits = episodic.recall("how do the api", k=5)
    assert hits and "api" in hits[0]["goal"].lower()
    assert tokenize_for_recall("how do the api") == ["api"]


def test_schema_v5_migration(store: LoopStore) -> None:
    with store._connection() as conn:
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        assert row["version"] == SCHEMA_VERSION
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "episodes" in tables
    assert "active_runs" in tables


def test_record_and_recall_episode(store: LoopStore) -> None:
    episodic = EpisodicStore(store)
    episodic.record_episode(
        episode_type="agent_loop",
        goal="fix flaky auth pytest",
        task_type="bugfix",
        model_id="demo",
        summary="Stopped at step 2 with tests passing",
        score_final=0.9,
        steps_used=2,
        stop_reason="goal reached",
    )
    episodic.record_episode(
        episode_type="agent_loop",
        goal="write readme docs",
        task_type="docs",
        model_id="demo",
        summary="Documentation update",
        score_final=0.85,
        steps_used=1,
    )
    hits = episodic.recall("flaky auth pytest", task_type="bugfix", k=3)
    assert len(hits) >= 1
    assert "auth" in hits[0]["goal"].lower() or "pytest" in hits[0]["summary"].lower()


def test_active_run_lifecycle(store: LoopStore, tmp_path) -> None:
    mirror = tmp_path / "active_run.json"
    episodic = EpisodicStore(store, mirror_path=mirror)
    episodic.upsert_active_run("sess1", "agent_loop", {"goal": "test"})
    assert episodic.get_active_run("sess1") is not None
    assert mirror.exists()
    episodic.clear_active_run("sess1")
    assert episodic.get_active_run("sess1") is None


def test_active_run_mirror_dir_supports_concurrent_runs(store: LoopStore, tmp_path) -> None:
    """mirror_dir writes one file per run_id, unlike the single-file mirror_path."""
    mirror_dir = tmp_path / "active_runs"
    episodic = EpisodicStore(store, mirror_dir=mirror_dir)

    episodic.upsert_active_run("sess1", "agent_loop", {"goal": "fix auth"})
    episodic.upsert_active_run("sess2", "dag", {"goal": "refactor module"})

    assert (mirror_dir / "sess1.json").exists()
    assert (mirror_dir / "sess2.json").exists()

    import json

    sess2_payload = json.loads((mirror_dir / "sess2.json").read_text())
    assert sess2_payload["run_type"] == "dag"
    assert sess2_payload["state"]["goal"] == "refactor module"

    episodic.clear_active_run("sess1")
    assert not (mirror_dir / "sess1.json").exists()
    assert (mirror_dir / "sess2.json").exists()


def test_record_episode_auto_captures_commit_sha(store: LoopStore, tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init"], repo)
    (repo / "f.txt").write_text("hi")
    _git(["add", "."], repo)
    _git(["-c", "user.email=t@example.com", "-c", "user.name=Test", "commit", "-m", "c1"], repo)

    monkeypatch.chdir(repo)
    episodic = EpisodicStore(store)
    ep_id = episodic.record_episode(
        episode_type="agent_loop", goal="fix bug", task_type="bugfix",
        model_id="m", summary="fixed", score_final=0.9,
    )
    episodes = store.list_episodes()
    recorded = next(e for e in episodes if e["id"] == ep_id)
    assert recorded["commit_sha"] is not None
    assert len(recorded["commit_sha"]) == 40


def test_record_episode_explicit_commit_sha_overrides_auto(store: LoopStore) -> None:
    episodic = EpisodicStore(store)
    ep_id = episodic.record_episode(
        episode_type="agent_loop", goal="fix bug", task_type="bugfix",
        model_id="m", summary="fixed", score_final=0.9, commit_sha="deadbeef",
    )
    episodes = store.list_episodes()
    recorded = next(e for e in episodes if e["id"] == ep_id)
    assert recorded["commit_sha"] == "deadbeef"


def test_summarize_artifacts() -> None:
    text = summarize_artifacts(
        "fix tests",
        step_outputs=["pytest: 12 passed"],
        stop_reason="goal reached",
        score_final=0.95,
    )
    assert "fix tests" in text
    assert "0.95" in text


def test_extract_tags() -> None:
    tags = extract_tags("refactor auth module and run pytest", "bugfix")
    assert "bugfix" in tags
    assert "pytest" in tags
    assert "auth" in tags
