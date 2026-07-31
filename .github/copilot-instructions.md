# CDV — Agent Instructions

CDV is an MCP server for prompt quality, refinement loops, and Conservative
Dual-Verify agent-loop control. See [README.md](../README.md) for architecture,
tools, and examples.

---

## Required on every message

Always call `cdv_intercept` with the user's exact prompt before responding. Do not skip this even for simple requests.

```
cdv_intercept(prompt="<user's exact message>")
```

Act on the returned `route`: `elicit` → clarify first | `decompose` → plan first | `refine` → proceed directly.

For non-trivial tasks use `cdv_run_pipeline` as the entry point.

For multi-step iterative work, use `cdv_loop_start` → `cdv_loop_step(step_output=...)` → `cdv_loop_end`. Submit step artifacts; do not self-grade. The verdict includes `cdv_mode` (`full` = independent critic consulted; `channel_a_only` = deterministic checks only).

---

## Memory & recovery (v0.8)

**Recall before planning.** For tasks similar to past work, call `cdv_recall("<goal>")` before planning. `cdv_loop_start` also returns `similar_episodes` automatically — read them. If `cdv_intercept` returns `recall_available: true`, recall first. `cdv_loop_end` records the outcome to episodic memory automatically.

**Resume after an IDE reload.** Call `cdv_run_status`. If it shows an active run, call `cdv_loop_resume` (optionally with `session_id`) **before** starting a new loop, so the in-progress loop continues instead of restarting cold.
