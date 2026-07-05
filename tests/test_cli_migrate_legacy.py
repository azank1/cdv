"""Tests for the `loopllm migrate-legacy` CLI command and legacy-store detection."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from loopllm.cli import build_parser
from loopllm.episodes import EpisodicStore
from loopllm.project_scope import legacy_store_path
from loopllm.store import SCHEMA_VERSION, LoopStore


def _run(args: list[str]) -> None:
    parsed = build_parser().parse_args(args)
    parsed.func(parsed)


def _make_legacy_store(path: Path, goal: str = "fix flaky auth tests") -> None:
    """Create a store with one recorded episode, as a v0.8/v0.9 user would have."""
    store = LoopStore(db_path=path)
    EpisodicStore(store).record_episode(
        episode_type="agent_loop", goal=goal, task_type="bugfix",
        model_id="m", summary="fixed", score_final=0.9, commit_sha="",
    )
    store.close()


def _make_v5_legacy_store(path: Path, goal: str = "pre-audit-trail work") -> None:
    """Create a genuine schema-v5 store (pre-commit_sha), as shipped in v0.8."""
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE schema_version (version INTEGER NOT NULL);"
        "INSERT INTO schema_version (version) VALUES (5);"
        "CREATE TABLE episodes ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " episode_type TEXT NOT NULL, goal TEXT NOT NULL,"
        " task_type TEXT NOT NULL, model_id TEXT NOT NULL,"
        " summary TEXT NOT NULL, artifact_ref TEXT,"
        " tags TEXT NOT NULL DEFAULT '[]', score_final REAL,"
        " steps_used INTEGER, stop_reason TEXT, recorded_at TEXT NOT NULL);"
    )
    conn.execute(
        "INSERT INTO episodes (episode_type, goal, task_type, model_id, summary,"
        " recorded_at) VALUES ('agent_loop', ?, 'bugfix', 'm', 's',"
        " '2026-01-01T00:00:00')",
        (goal,),
    )
    conn.commit()
    conn.close()


def test_migrate_legacy_imports_into_project_store(tmp_path: Path, capsys) -> None:
    legacy = tmp_path / "legacy" / "store.db"
    legacy.parent.mkdir()
    _make_legacy_store(legacy)
    dest = tmp_path / "projects" / "abc" / "store.db"

    _run(["--db", str(dest), "migrate-legacy", "--from", str(legacy)])

    out = capsys.readouterr().out
    assert "1 episode(s)" in out
    assert legacy.exists(), "legacy file must be left in place"
    store = LoopStore(db_path=dest)
    episodes = store.list_episodes(limit=10)
    store.close()
    assert len(episodes) == 1
    assert episodes[0]["goal"] == "fix flaky auth tests"


def test_migrate_legacy_migrates_v5_schema_to_current(tmp_path: Path, capsys) -> None:
    """A real v0.8-era (schema v5) global store migrates cleanly on import."""
    legacy = tmp_path / "store.db"
    _make_v5_legacy_store(legacy)
    dest = tmp_path / "scoped" / "store.db"

    _run(["--db", str(dest), "migrate-legacy", "--from", str(legacy)])
    assert "1 episode(s)" in capsys.readouterr().out

    store = LoopStore(db_path=dest)
    with store._connection() as conn:
        version = conn.execute("SELECT version FROM schema_version").fetchone()["version"]
        cols = {r[1] for r in conn.execute("PRAGMA table_info(episodes)")}
    episodes = store.list_episodes(limit=10)
    store.close()
    assert version == SCHEMA_VERSION
    assert "commit_sha" in cols
    assert episodes[0]["goal"] == "pre-audit-trail work"
    assert episodes[0]["commit_sha"] is None

    # The legacy source itself must NOT have been migrated or touched.
    conn = sqlite3.connect(legacy)
    legacy_version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    conn.close()
    assert legacy_version == 5


def test_migrate_legacy_missing_source_is_a_noop(tmp_path: Path, capsys) -> None:
    dest = tmp_path / "dest.db"
    _run(["--db", str(dest), "migrate-legacy", "--from", str(tmp_path / "nope.db")])
    assert "nothing to migrate" in capsys.readouterr().out
    assert not dest.exists()


def test_migrate_legacy_refuses_existing_dest_without_force(tmp_path: Path, capsys) -> None:
    legacy = tmp_path / "legacy.db"
    _make_legacy_store(legacy)
    dest = tmp_path / "dest.db"
    LoopStore(db_path=dest).close()

    with pytest.raises(SystemExit):
        _run(["--db", str(dest), "migrate-legacy", "--from", str(legacy)])
    assert "Refusing to overwrite" in capsys.readouterr().err


def test_migrate_legacy_force_overwrites(tmp_path: Path, capsys) -> None:
    legacy = tmp_path / "legacy.db"
    _make_legacy_store(legacy, goal="the real history")
    dest = tmp_path / "dest.db"
    LoopStore(db_path=dest).close()  # pre-existing empty project store

    _run(["--db", str(dest), "migrate-legacy", "--from", str(legacy), "--force"])
    assert "1 episode(s)" in capsys.readouterr().out
    store = LoopStore(db_path=dest)
    episodes = store.list_episodes(limit=10)
    store.close()
    assert episodes[0]["goal"] == "the real history"


def test_migrate_legacy_same_path_is_a_noop(tmp_path: Path, capsys) -> None:
    legacy = tmp_path / "store.db"
    _make_legacy_store(legacy)
    _run(["--db", str(legacy), "migrate-legacy", "--from", str(legacy)])
    assert "already is the legacy store" in capsys.readouterr().out


def test_legacy_store_path_detection(tmp_path: Path) -> None:
    base = tmp_path / ".loopllm"
    assert legacy_store_path(base) is None
    base.mkdir()
    (base / "store.db").write_bytes(b"")
    assert legacy_store_path(base) == base / "store.db"


def test_paths_reports_legacy_store(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("LOOPLLM_PROJECT", raising=False)
    legacy = tmp_path / ".loopllm" / "store.db"
    legacy.parent.mkdir()
    _make_legacy_store(legacy)
    capsys.readouterr()  # discard schema-creation log noise

    _run(["--db", str(tmp_path / "scoped" / "store.db"), "paths"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["legacy_store"] == str(legacy)


def test_paths_legacy_store_null_when_db_is_the_legacy_file(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    legacy = tmp_path / ".loopllm" / "store.db"
    legacy.parent.mkdir()
    _make_legacy_store(legacy)
    capsys.readouterr()

    _run(["--db", str(legacy), "paths"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["legacy_store"] is None
