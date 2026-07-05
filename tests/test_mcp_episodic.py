"""Integration tests for episodic recall + recovery via MCP tool handlers."""
from __future__ import annotations

import json

import pytest

import loopllm.mcp_server as m
from loopllm import AdaptivePriors, AgentLoopController


# -- controller-level recovery contract (pure, no module globals) ------------


def test_controller_snapshot_restore_roundtrip() -> None:
    priors = AdaptivePriors()
    c1 = AgentLoopController(priors)
    s = c1.start("fix flaky auth tests", task_type="bugfix", quality_threshold=0.8)
    c1.step(s.session_id, 0.4, step_output="pytest: 3 failed")
    snap = c1.get_session(s.session_id).to_snapshot()

    # New controller = simulated MCP restart.
    c2 = AgentLoopController(priors)
    sid = c2.restore_from_snapshot(snap)
    assert sid == s.session_id
    assert c2.status(sid)["steps_used"] == 1
    # The resumed loop keeps advancing.
    verdict = c2.step(sid, 0.95, step_output="pytest: 42 passed, 0 failed")
    assert verdict["steps_used"] == 2


def test_hydrate_skips_closed_and_non_loop_runs() -> None:
    priors = AdaptivePriors()
    src = AgentLoopController(priors)
    snap = src.get_session(
        src.start("g", task_type="bugfix").session_id
    ).to_snapshot()

    target = AgentLoopController(priors)
    runs = [
        {"run_type": "agent_loop", "state": snap},
        {"run_type": "agent_loop", "state": {**snap, "session_id": "x", "closed": True}},
        {"run_type": "plan", "state": {"plan_id": "p"}},
    ]
    assert target.hydrate_active_loops(runs) == 1


# -- tool-level tests with isolated module state -----------------------------


@pytest.fixture
def mcp_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOPLLM_DB", str(tmp_path / "store.db"))
    monkeypatch.setenv("LOOPLLM_PROVIDER", "mock")
    for g in (
        "_store", "_priors", "_provider", "_episodic", "_agent_loop",
        "_dag_scheduler", "_status_path", "_history_path", "_consultation_path",
    ):
        if hasattr(m, g):
            setattr(m, g, None)
    yield m
    for g in (
        "_store", "_priors", "_provider", "_episodic", "_agent_loop",
        "_dag_scheduler",
    ):
        if hasattr(m, g):
            setattr(m, g, None)


def _checkpointed_step(env, sid: str, score: float, output: str) -> None:
    ctrl = env._get_agent_loop()
    ctrl.step(sid, score, step_output=output)
    env._get_episodic().upsert_active_run(
        sid, "agent_loop", ctrl.get_session(sid).to_snapshot()
    )


def test_loop_resume_after_restart(mcp_env) -> None:
    env = mcp_env
    sid = json.loads(env._tool_loop_start("fix flaky auth tests", task_type="bugfix"))["session_id"]
    _checkpointed_step(env, sid, 0.4, "pytest: 3 failed")

    # Simulate MCP restart: drop the in-memory controller, re-hydrate from store.
    env._agent_loop = None
    restored = env._get_agent_loop().hydrate_active_loops(
        env._get_episodic().list_active_runs(run_type="agent_loop")
    )
    assert restored == 1

    res = json.loads(env._tool_loop_resume(sid))
    assert res["resumed"] is True
    assert res["status"]["steps_used"] == 1
    # Continue stepping post-resume.
    env._get_agent_loop().step(sid, 0.95, step_output="pytest: 42 passed, 0 failed")
    assert env._get_agent_loop().status(sid)["steps_used"] == 2


def test_loop_resume_no_session_id_resumes_single(mcp_env) -> None:
    env = mcp_env
    sid = json.loads(env._tool_loop_start("migrate sql schema", task_type="refactor"))["session_id"]
    _checkpointed_step(env, sid, 0.3, "in progress")
    env._agent_loop = None
    env._get_agent_loop().hydrate_active_loops(
        env._get_episodic().list_active_runs(run_type="agent_loop")
    )
    res = json.loads(env._tool_loop_resume())
    assert res["resumed"] is True and res["status"]["session_id"] == sid


def test_run_status_lists_active_loop(mcp_env) -> None:
    env = mcp_env
    sid = json.loads(env._tool_loop_start("g", task_type="bugfix"))["session_id"]
    status = json.loads(env._tool_run_status())
    ids = [loop.get("session_id") for loop in status["active_agent_loops"]]
    assert sid in ids


def test_loop_start_injects_similar_episode(mcp_env) -> None:
    env = mcp_env
    # Record an episode by finishing a loop.
    sid = json.loads(env._tool_loop_start("fix flaky auth tests", task_type="bugfix"))["session_id"]
    env._get_agent_loop().step(sid, 0.95, step_output="pytest: 42 passed, 0 failed")
    env._tool_loop_end(sid)

    # A new similar loop should surface it.
    start2 = json.loads(env._tool_loop_start("fix flaky auth tests again", task_type="bugfix"))
    assert start2["similar_episodes"]
    assert "auth" in start2["similar_episodes"][0]["goal"].lower()


def test_recall_and_resume_unknown(mcp_env) -> None:
    env = mcp_env
    assert json.loads(env._tool_loop_resume("nope"))["error"]
    assert json.loads(env._tool_recall("anything"))["count"] == 0


def test_tool_loop_step_cdv_path(mcp_env) -> None:
    """End-to-end: loop_start → loop_step (channel_a_only) → checkpoint → step again → stop."""
    import asyncio

    env = mcp_env

    # Start with a low quality_threshold so a passing CDV score triggers stop.
    start = json.loads(env._tool_loop_start(
        "add retry logic to download()",
        task_type="bugfix",
        evaluator_type="composite",
        quality_criteria=["retry logic", "raise"],
        required_patterns=["retry"],
        quality_threshold=0.6,
    ))
    sid = start["session_id"]
    assert start["suggested_budget"] >= 1
    assert start["similar_episodes"] == []  # cold start

    # Step 1 — artifact missing 'retry', should continue.
    step1 = json.loads(asyncio.run(env._tool_loop_step(
        session_id=sid,
        step_output="def download(url): return requests.get(url).text",
        ctx=None,
    )))
    assert step1["decision"] == "continue"
    assert step1["cdv_mode"] == "channel_a_only"
    assert "channel_a_score" in step1
    assert step1["channel_a_score"] < 0.8
    # passed must be consistent with the verdict (score < threshold → not passed)
    assert step1["passed"] is False
    # Active run should be checkpointed.
    runs = env._get_episodic().list_active_runs(run_type="agent_loop")
    assert any(r["run_id"] == sid for r in runs), "step 1 not checkpointed"
    snap = next(r for r in runs if r["run_id"] == sid)
    assert snap["state"]["scores"] == pytest.approx([step1["score"]], abs=1e-3)

    # Step 2 — artifact explicitly contains 'retry logic' and 'raise', should stop.
    step2 = json.loads(asyncio.run(env._tool_loop_step(
        session_id=sid,
        step_output=(
            "# retry logic: exponential backoff\n"
            "def download(url, max_retry=3):\n"
            "    for attempt in range(max_retry):\n"
            "        try:\n"
            "            return requests.get(url).text\n"
            "        except Exception:\n"
            "            time.sleep(2 ** attempt)\n"
            "    raise RuntimeError('download failed after retry')"
        ),
        ctx=None,
    )))
    assert step2["decision"] == "stop", f"expected stop, got {step2.get('reason')}"
    assert step2["cdv_mode"] == "channel_a_only"
    assert step2["channel_a_score"] >= 0.6
    assert step2["score"] >= 0.6

    # loop_end should record an episode.
    end = json.loads(env._tool_loop_end(sid))
    assert end["converged"] is True  # score >= quality_threshold (0.6)
    assert end["steps_run"] == 2

    # Episode now exists in episodic store.
    episodes = env._get_episodic().recall("retry download", task_type="bugfix")
    assert episodes, "episode not recorded after loop_end"
    assert "retry" in episodes[0]["goal"].lower() or "download" in episodes[0]["goal"].lower()

    # similar_episodes injected on next loop_start.
    start2 = json.loads(env._tool_loop_start(
        "add retry logic to upload()", task_type="bugfix"
    ))
    assert start2["similar_episodes"], "similar_episodes not injected"
    assert start2["similar_episodes"][0].get("task_type") == "bugfix"  # Bug 4 fix


def test_tool_dag_compile_ready_submit_status_merge(mcp_env) -> None:
    """End-to-end: dag_compile → dag_ready → dag_submit (CDV) → dag_status → dag_merge."""
    import asyncio

    env = mcp_env

    compiled = json.loads(env._tool_dag_compile(
        "refactor download module and pass tests",
        nodes=[
            {
                "id": "n1",
                "role": "implementer",
                "description": "Refactor the download module",
                "dependencies": [],
                "quality_criteria": ["retry logic"],
                "required_patterns": ["retry"],
            },
            {
                "id": "n2",
                "role": "test_runner",
                "description": "Run pytest against the refactor",
                "dependencies": ["n1"],
                "required_patterns": ["passed"],
            },
        ],
        task_type="bugfix",
    ))
    run_id = compiled["run_id"]
    assert compiled["nodes"]["n1"]["state"] == "ready"
    assert compiled["nodes"]["n2"]["state"] == "pending"
    assert compiled["similar_episodes"] == []  # cold start

    ready1 = json.loads(env._tool_dag_ready(run_id))
    assert [n["node_id"] for n in ready1["frontier"]] == ["n1"]

    # ctx=None → submit_async degrades to Channel-A-only scoring (same as the
    # loop_step path when the MCP client doesn't support sampling).
    submit1 = json.loads(asyncio.run(env._tool_dag_submit(
        run_id, "n1",
        "# retry logic: exponential backoff\ndef download(url): ...",
        None,
    )))
    assert submit1["accepted"] is True
    assert submit1["node_state"] == "verified"
    assert submit1["dag_complete"] is False
    assert [n["node_id"] for n in submit1["ready_next"]] == ["n2"]

    submit2 = json.loads(asyncio.run(env._tool_dag_submit(
        run_id, "n2", "pytest: 12 passed, 0 failed", None,
    )))
    assert submit2["accepted"] is True
    assert submit2["dag_complete"] is True

    status = json.loads(env._tool_dag_status(run_id))
    assert status["nodes"]["n1"]["state"] == "verified"
    assert status["nodes"]["n2"]["state"] == "verified"
    assert status["ready"] == []

    merged = json.loads(env._tool_dag_merge(run_id))
    assert "n1" in merged["merged_output"]
    assert "n2" in merged["merged_output"]

    # merge() records a "dag" episode and clears the active run.
    episodes = env._get_episodic().recall("refactor download module", task_type="bugfix")
    assert episodes, "DAG episode not recorded after merge"
    runs = env._get_episodic().list_active_runs(run_type="dag")
    assert not any(r["run_id"] == run_id for r in runs), "active DAG run not cleared"


def test_consultation_signal_written_by_entry_point_tools(mcp_env) -> None:
    """The 'was PromptLoop consulted' file updates on real entry points, not passive reads."""
    env = mcp_env
    env._init_state()
    consultation_path = env._consultation_path
    assert consultation_path is not None
    assert not consultation_path.exists()

    env._tool_intercept("add retry logic to download()")
    assert consultation_path.exists()
    first = json.loads(consultation_path.read_text())
    assert first["last_tool"] == "intercept"
    assert first["last_consulted_at"] > 0

    env._tool_loop_start("fix flaky auth tests", task_type="bugfix")
    second = json.loads(consultation_path.read_text())
    assert second["last_tool"] == "loop_start"
    assert second["last_consulted_at"] >= first["last_consulted_at"]

    # A passive read (run_status) must NOT count as consultation.
    consultation_path.unlink()
    env._tool_run_status()
    assert not consultation_path.exists(), "run_status should not count as consultation"


def test_server_registers_expected_tool_count(mcp_env) -> None:
    """36 MCP tools are registered (keeps the README banner honest)."""
    import asyncio

    server = mcp_env.create_mcp_server()
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert len(names) == 36
    assert {"loopllm_recall", "loopllm_run_status", "loopllm_loop_resume"} <= names
    assert {
        "loopllm_dag_compile", "loopllm_dag_ready", "loopllm_dag_submit",
        "loopllm_dag_status", "loopllm_dag_merge",
    } <= names
