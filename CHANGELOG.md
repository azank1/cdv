# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.10.0] — unreleased (branch: az/ft/promptloop-product-v1)

### Added
- **Per-project state scoping**: [`project_scope.py`](src/loopllm/project_scope.py)
  resolves a stable project id from the git remote (falling back to the repo root,
  then the working directory) and keys all local state under
  `~/.loopllm/projects/<id>/` instead of one shared `~/.loopllm/store.db`. Two
  unrelated repos on the same machine no longer interleave episodes, priors, or
  active runs. `LOOPLLM_PROJECT` overrides auto-detection.
- New CLI command `loopllm paths` prints the resolved per-project state file paths
  as JSON; the VS Code extension uses it to watch the same directory the MCP
  server and CLI write to, instead of a hardcoded flat path.
- Fixed a latent bug where `_episodes_feed_path` was never actually persisted to
  the module global in `_init_state` (missing from the `global` declaration),
  silently breaking the Loop Monitor's completed-episode feed.
- Tests: `tests/test_project_scope.py` (env override, git-remote/repo-root/cwd
  fallback chain, same-remote-same-id, different-remotes-different-ids).
- **DAG dependency board** in the VS Code Loop Monitor: [`loopMonitorProvider.ts`](vscode-loopllm/src/loopMonitorProvider.ts)
  now renders `run_type="dag"` active runs as a kanban-style board (Pending /
  Ready / Running / Verified / Failed columns), with each failed node showing
  its CDV deficiencies in plain language.
- Fixed a bug where the Loop Monitor's "Active Loops" panel was silently
  always empty: [`EpisodicStore`](src/loopllm/episodes.py) only ever mirrored
  the single most-recently-updated run to one `active_run.json` file, but the
  extension read from an `active_runs/` *directory* that nothing wrote to.
  Added a `mirror_dir` option that mirrors every active run as its own
  `<run_id>.json` file, so multiple concurrent loops/DAG runs all show up.
- **Verification audit trail**: every episode is now stamped with the git
  commit that was `HEAD` when it was recorded (schema v6, `commit_sha`
  column). New CLI command `loopllm audit [--since <ref>] [--json]` reports
  what the agent did and how it was CDV-verified, scoped to a commit range
  (`git log <ref>..HEAD`) — e.g. `loopllm audit --since origin/main`.
- VS Code Loop Monitor gained an **Export audit** button on the DAG board
  that runs `loopllm audit` and opens the report as a markdown document —
  the reviewable artifact a tech lead keeps.
- **Non-consultation signal**: [`mcp_server.py`](src/loopllm/mcp_server.py)
  stamps a `consultation.json` file whenever the IDE agent actually calls an
  entry-point tool (`loopllm_intercept`, `loopllm_loop_start`, `loopllm_loop_step`,
  `loopllm_dag_compile`, `loopllm_dag_submit`) — not passive reads like
  `run_status`/`recall`. The VS Code Loop Monitor shows an undismissable
  banner, "PromptLoop has not been consulted this session," whenever the user
  has been editing but no entry-point tool has fired since the panel
  activated. This is the only realistic enforcement lever MCP allows: silent
  non-adoption becomes a visible signal instead of going unnoticed.
- New CLI command `loopllm install-mcp --ide {cursor,vscode,antigravity,claude-code,all}`
  merges a `loopllm` MCP server entry into the target IDE's config (Cursor
  `mcpServers`, VS Code/Antigravity `servers` + `type: stdio`, Claude Code's
  project-scoped `.mcp.json`) instead of requiring hand-edited JSON. Preserves
  any other configured servers; `--force` overwrites an existing `loopllm`
  entry; invalid existing JSON is left untouched rather than risked.
- README now leads with the one-sentence pitch and a DAG board demo GIF
  (`.github/assets/dag-board.gif`, generated from real `loopllm_dag_*` tool calls
  via `scripts/generate_dag_board_gif.py`) instead of the MCP tool count.
- **Minimal public repo tree**: `examples/`, `benchmarks/`, `AUDIT.md`,
  `_scratch/`, `scripts/audit_cdv_loop.py`, and the old `img/` gallery are no
  longer tracked (kept locally and in git history, just untracked going
  forward). The public tree is now the `loopllm` package, the `vscode-loopllm`
  extension, tests, CI, and IDE-adoption files (`.cursor/rules/loopllm.mdc`,
  `.github/copilot-instructions.md`) — README-embedded images moved to
  `.github/assets/` rather than a distinct top-level `img/` gallery.

## [0.9.0] — unreleased (branch: az/ft/dag-virtual-agents-v2)

### Added
- **DAG virtual sub-agents**: [`DagScheduler`](src/loopllm/dag_scheduler.py) compiles a
  goal + node spec (id, role, description, dependencies) into a dependency-ordered
  graph, hands the IDE agent one frontier node at a time, and scores each submission
  through the existing Conservative Dual-Verify path — no new verification model.
- New tools: `loopllm_dag_compile`, `loopllm_dag_ready`, `loopllm_dag_submit`,
  `loopllm_dag_status`, `loopllm_dag_merge` (**36 tools total**).
- `loopllm_intercept`'s `route: decompose` now points at `loopllm_dag_compile`
  instead of the plain synchronous `loopllm_plan_tasks` pipeline.
- Verified DAG nodes are recorded as `plan_node` episodes; a merged run is recorded
  as a `dag` episode and its active-run snapshot is cleared — DAG runs recover
  through the same `loopllm_run_status` snapshot as agent loops.
- Tests: `tests/test_dag_scheduler.py` (scheduler unit tests: compile/ready/submit/
  merge, cycle detection, restore); `test_tool_dag_compile_ready_submit_status_merge`
  in `tests/test_mcp_episodic.py` (full MCP-tool-level DAG lifecycle).

## [0.8.0] — unreleased (branch: az/ft/episodic-memory)

### Added
- **Episodic memory** (SQLite schema v5): `episodes` and `active_runs` tables.
- [`EpisodicStore`](src/loopllm/episodes.py): record completed loops/plans, keyword recall,
  crash-safe active run snapshots (`~/.loopllm/active_run.json` mirror).
- **Session continuity / recovery contract**: agent loops survive an MCP/IDE restart.
  - `AgentLoopSession.to_snapshot()` / `AgentLoopController.restore_from_snapshot()` +
    `hydrate_active_loops()`; in-progress loops are rehydrated on server startup.
  - `loopllm_loop_step` checkpoints the verified session to `active_runs` each step.
  - New tool `loopllm_loop_resume` continues a loop where it left off.
- **Recall injection**: `loopllm_loop_start` returns `similar_episodes` + `memory_hint`;
  `loopllm_intercept` flags `recall_available` on clear prompts.
- **Sampling transparency**: `loopllm_loop_step` verdict reports `cdv_mode`
  (`full` vs `channel_a_only`) with a reason.
- Improved recall ranking (stopword filter, dedup, tag/task_type boost, recency
  tie-break) behind a stable seam for a future FTS5/vector backend.
- MCP tools: `loopllm_recall`, `loopllm_run_status`, `loopllm_loop_resume` (**31 tools total**).
- Hooks: `loopllm_loop_end`, `loopllm_plan_register` / `plan_update` record episodes;
  `loopllm_plan_delete` clears the recovery snapshot.
- Cross-IDE agent contract: server `instructions` updated; `.cursor/rules/loopllm.mdc`
  (new) and `.github/copilot-instructions.md` document recall + resume.
- Tests: `tests/test_episodes.py` (migration + ranking), `tests/test_mcp_episodic.py` (new);
  example `examples/episodic_recall_loop.py`.
- CI now also runs on `az/ft/**` pushes and `workflow_dispatch`.

## [0.7.0] — 2026-06-22

### Added
- **Conservative Dual-Verify (CDV) for agent loops.** Agents submit `step_output`
  artifacts; the MCP server scores each step through two independent channels:
  - **Channel A:** deterministic evaluators (regex, JSON, completeness, composite).
  - **Channel B:** separate critic via MCP sampling (verifier hat).
  - **Final score:** `min(channel_a, channel_b)` — the stricter channel wins.
- New modules: `src/loopllm/step_scorer.py`, `src/loopllm/guards.py`,
  `src/loopllm/evaluator_factory.py`.
- Composable **guard stack** on verified scores: timeout, token budget, output-repeat,
  plateau, threshold, Bayesian ROI, max steps, budget exhausted.
- `loopllm_loop_start` accepts verifier recipe (`evaluator_type`, `quality_criteria`,
  `required_patterns`, `max_wall_ms`, `max_tokens`).
- `loopllm_loop_step` is now async and artifact-primary (`step_output`); legacy
  `score` self-report still works with deprecation warning.
- Verdict JSON exposes `channel_a_score`, `channel_b_score`, `score_source`,
  `deficiencies` for demos and debugging.
- Tests: `tests/test_step_scorer.py`, `tests/test_guards.py` (CDV inflation-block test).
- Package exports: `DualVerifyScore`, `conservative_dual_verify`, `GuardStack`.

### Changed
- `AgentLoopController` uses `GuardStack` instead of inline `_decide` logic.
- `AgentLoopSession` stores verifier config, step artifacts, token accumulators.
- `CallObservation.prompt_tokens` / `completion_tokens` populated on `loop_end`.
- README and demo doc reframed around Conservative Dual-Verify.

## [0.6.0] — 2026-06-21

### Added
- **Adaptive agent loops.** A new `AgentLoopController` (`src/loopllm/agent_loop.py`)
  brings the Bayesian early-exit machinery to an agent's own plan → act → observe
  loop. Lifecycle: `start` → `step` → `end`.
  - Suggests a learned step budget and quality threshold per `task_type` from
    `AdaptivePriors` — no training data required.
  - Returns a continue/stop verdict on each step (goal reached, plateau, low
    expected ROI, or budget exhausted) using the same logic as
    `BayesianExitCondition`.
  - Records every completed loop so future budgets sharpen over time.
- Four new MCP tools (now 28 total): `loopllm_loop_start`, `loopllm_loop_step`,
  `loopllm_loop_end`, `loopllm_loop_status`.
- `AgentLoopController` and `AgentLoopSession` exported from the package root.
- Runnable example `examples/agent_loop.py` and walkthrough
  `docs/demo/agent_loop_demo.md`.
- Reproducible benchmark `benchmarks/adaptive_vs_fixed.py` comparing adaptive
  stopping against fixed/threshold strategies (adaptive: ~41% fewer steps than a
  fixed 6-step budget at 99.7% goal-reach).
- Tests: `tests/test_agent_loop.py`, `tests/test_benchmark.py`.
- Repo hygiene: `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, GitHub issue/PR
  templates, `[project.urls]` + classifiers in `pyproject.toml`, CI badge, and a
  committed `.cursor/mcp.json` for Cursor auto-detection.

### Changed
- README deduplicated and corrected (28 tools, 204 tests, schema v4); added
  "Adaptive agent loops" and "Benchmark" sections.

### Fixed
- Version drift: `__init__.__version__` now matches `pyproject.toml`.

## [0.5.0]

### Added
- Online SGD weight learning and Thompson Sampling for question ordering
  (SQLite schema v4).
- Prompt Lab sidebar panel in the VS Code extension.
- Expanded MCP surface to 24 tools, MCP Sampling, and persistent confidence-gated
  plans.

## [0.4.0]

### Added
- Prompt quality scoring system (5 deterministic dimensions, grade A–F) and the
  initial VS Code extension.
