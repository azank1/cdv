"""Tests for opt-in cross-project recall (global scope)."""
from __future__ import annotations

import json
from pathlib import Path

from loopllm.cli import build_parser
from loopllm.episodes import EpisodicStore, global_recall
from loopllm.project_scope import list_project_dirs
from loopllm.store import LoopStore


def _seed_project(base: Path, project_id: str, goal: str, task_type: str = "bugfix") -> None:
    proj = base / "projects" / project_id
    proj.mkdir(parents=True)
    store = LoopStore(db_path=proj / "store.db")
    EpisodicStore(store).record_episode(
        episode_type="agent_loop", goal=goal, task_type=task_type,
        model_id="m", summary=goal, score_final=0.9, commit_sha="",
    )
    store.close()


def test_list_project_dirs_empty_when_no_projects(tmp_path: Path) -> None:
    assert list_project_dirs(tmp_path) == []


def test_global_recall_spans_projects_and_tags_them(tmp_path: Path) -> None:
    _seed_project(tmp_path, "proj_alpha", "fix flaky auth tests in service A")
    _seed_project(tmp_path, "proj_beta", "fix flaky auth tests in service B")
    _seed_project(tmp_path, "proj_gamma", "write docker compose", task_type="devops")

    hits = global_recall("flaky auth tests", k=5, base=tmp_path)
    goals = {h["goal"] for h in hits}
    assert any("service A" in g for g in goals)
    assert any("service B" in g for g in goals)
    # Every hit is tagged with the project it came from.
    assert all(h.get("project_id") in {"proj_alpha", "proj_beta", "proj_gamma"} for h in hits)
    # The unrelated devops episode should not surface for this query.
    assert not any("docker" in g for g in goals)


def test_global_recall_isolated_per_project_by_default(tmp_path: Path) -> None:
    """A single project's own recall never sees another project's episodes."""
    _seed_project(tmp_path, "proj_alpha", "alpha-only secret goal")
    _seed_project(tmp_path, "proj_beta", "beta-only secret goal")

    store = LoopStore(db_path=tmp_path / "projects" / "proj_alpha" / "store.db")
    local = EpisodicStore(store).recall("secret goal", k=5)
    store.close()
    assert local and all("alpha" in h["goal"] for h in local)
    assert not any("beta" in h["goal"] for h in local)


def test_global_recall_respects_k(tmp_path: Path) -> None:
    for i in range(4):
        _seed_project(tmp_path, f"proj_{i}", f"refactor the parser module take {i}")
    hits = global_recall("refactor parser module", k=2, base=tmp_path)
    assert len(hits) == 2


def test_cli_recall_global(tmp_path: Path, capsys) -> None:
    _seed_project(tmp_path, "proj_a", "migrate the sql schema safely")
    # Point the scoped db at this base so --global derives the right projects dir.
    db = tmp_path / "projects" / "current" / "store.db"
    parsed = build_parser().parse_args(
        ["--db", str(db), "recall", "sql schema", "--global", "--json"]
    )
    capsys.readouterr()  # discard schema-creation log noise from seeding
    parsed.func(parsed)
    payload = json.loads(capsys.readouterr().out)
    assert payload and payload[0]["project_id"] == "proj_a"
    assert "sql" in payload[0]["goal"].lower()
