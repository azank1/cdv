"""Tests for FTS5-backed episodic recall (schema v7) and its keyword fallback."""
from __future__ import annotations

import pytest

from loopllm.episodes import EpisodicStore
from loopllm.store import LoopStore

pytestmark = pytest.mark.skipif(
    not LoopStore._probe_fts5(), reason="SQLite build has no FTS5 support"
)


def _seed(store: LoopStore) -> EpisodicStore:
    ep = EpisodicStore(store)
    ep.record_episode(
        episode_type="agent_loop", goal="fix flaky authentication tests",
        task_type="bugfix", model_id="m",
        summary="resolved intermittent login token failures", score_final=0.9,
    )
    ep.record_episode(
        episode_type="agent_loop", goal="add pagination to the REST API",
        task_type="feature", model_id="m",
        summary="cursor-based paging over results", score_final=0.8,
    )
    ep.record_episode(
        episode_type="agent_loop", goal="write a docker compose file",
        task_type="devops", model_id="m",
        summary="container orchestration for local dev", score_final=0.7,
    )
    return ep


def test_fts_ranks_relevant_episode_first(store: LoopStore) -> None:
    ep = _seed(store)
    assert store._fts5 is True
    hits = ep.recall("authentication login token", k=2)
    assert hits
    assert "authentication" in hits[0]["goal"].lower()


def test_fts_matches_across_goal_and_summary(store: LoopStore) -> None:
    ep = _seed(store)
    # 'orchestration' appears only in the summary, not the goal.
    hits = ep.recall("container orchestration", k=1)
    assert hits and "docker" in hits[0]["goal"].lower()


def test_fts_and_keyword_agree_on_top_hit(store: LoopStore) -> None:
    """The FTS path and the deterministic fallback pick the same best episode."""
    ep = _seed(store)
    fts_hits = ep.recall("pagination api", k=1)

    store._fts5 = False  # force the keyword fallback
    kw_hits = ep.recall("pagination api", k=1)

    assert fts_hits and kw_hits
    assert fts_hits[0]["id"] == kw_hits[0]["id"]


def test_fts_backfills_preexisting_episodes(store: LoopStore, tmp_path) -> None:
    """Episodes written before the FTS index exists are found after a reopen."""
    db = tmp_path / "store.db"
    s1 = LoopStore(db_path=db)
    ep = EpisodicStore(s1)
    ep.record_episode(
        episode_type="agent_loop", goal="optimize the sql query planner",
        task_type="perf", model_id="m", summary="index tuning", score_final=0.9,
    )
    # Simulate a store whose FTS index is missing, then reopen to trigger rebuild.
    with s1._connection() as c:
        c.executescript("DROP TABLE IF EXISTS episodes_fts;")
        c.commit()
    s1.close()

    s2 = LoopStore(db_path=db)
    hits = EpisodicStore(s2).recall("sql query planner", k=1)
    s2.close()
    assert hits and "sql" in hits[0]["goal"].lower()


def test_v6_store_with_episodes_upgrades_and_recalls(tmp_path) -> None:
    """A v6 DB (episodes, no FTS index) migrates to v7 and its rows become searchable."""
    import sqlite3

    db = tmp_path / "store.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE schema_version (version INTEGER NOT NULL);"
        "INSERT INTO schema_version (version) VALUES (6);"
        "CREATE TABLE episodes ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, episode_type TEXT NOT NULL,"
        " goal TEXT NOT NULL, task_type TEXT NOT NULL, model_id TEXT NOT NULL,"
        " summary TEXT NOT NULL, artifact_ref TEXT, tags TEXT NOT NULL DEFAULT '[]',"
        " score_final REAL, steps_used INTEGER, stop_reason TEXT,"
        " recorded_at TEXT NOT NULL, commit_sha TEXT);"
    )
    conn.execute(
        "INSERT INTO episodes (episode_type, goal, task_type, model_id, summary,"
        " recorded_at) VALUES ('agent_loop', 'debug the websocket reconnect loop',"
        " 'bugfix', 'm', 'handshake retry backoff', '2026-01-01T00:00:00')"
    )
    conn.commit()
    conn.close()

    store = LoopStore(db_path=db)  # migrates 6 -> 7, backfills FTS
    hits = EpisodicStore(store).recall("websocket reconnect", k=1)
    store.close()
    assert hits and "websocket" in hits[0]["goal"].lower()


def test_fts_task_type_filter(store: LoopStore) -> None:
    ep = _seed(store)
    # 'api' also appears in the bugfix summary path; restrict to feature.
    hits = ep.recall("api", task_type="feature", k=5)
    assert hits
    assert all(h["task_type"] == "feature" for h in hits)
