"""Command-line interface for loop-llm."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from loopllm.elicitation import ClarifyingQuestion, IntentRefiner
from loopllm.engine import LoopConfig, LoopedLLM
from loopllm.evaluators import LengthEvaluator
from loopllm.priors import CallObservation
from loopllm.project_scope import (
    commits_since,
    current_commit_sha,
    legacy_store_path,
    resolve_db_path,
    resolve_project_id,
)
from loopllm.provider import LLMProvider
from loopllm.store import LoopStore, SQLiteBackedPriors
from loopllm.tasks import TaskOrchestrator

# ---------------------------------------------------------------------------
# `loopllm install-mcp` — one-command MCP registration per IDE
# ---------------------------------------------------------------------------


def _user_config_dir(app_name: str) -> Path:
    """OS-appropriate per-user config directory for a VS Code-family app."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app_name
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / app_name
    return Path.home() / ".config" / app_name


class _IdeTarget:
    """Where and how to write the loopllm MCP server entry for one IDE."""

    def __init__(self, name: str, path_fn: Callable[[], Path], key: str, vscode_style: bool):
        self.name = name
        self.path_fn = path_fn
        self.key = key
        self.vscode_style = vscode_style


_IDE_TARGETS: dict[str, _IdeTarget] = {
    "cursor": _IdeTarget(
        "cursor", lambda: Path.home() / ".cursor" / "mcp.json", "mcpServers", False
    ),
    "vscode": _IdeTarget(
        "vscode", lambda: _user_config_dir("Code") / "User" / "mcp.json", "servers", True
    ),
    "antigravity": _IdeTarget(
        "antigravity",
        lambda: _user_config_dir("Antigravity") / "User" / "mcp.json",
        "servers",
        True,
    ),
    "claude-code": _IdeTarget(
        "claude-code", lambda: Path.cwd() / ".mcp.json", "mcpServers", False
    ),
}

# "all" excludes claude-code — it's project-scoped (writes to the current
# directory) and gets committed to git, so it should be opted into explicitly
# rather than dropped into whatever directory the user happened to run from.
_ALL_IDES = ["cursor", "vscode", "antigravity"]


def _mcp_server_entry(vscode_style: bool, provider: str, model: str) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "command": "loopllm",
        "args": ["mcp-server", "--provider", provider],
        "env": {"LOOPLLM_MODEL": model},
    }
    if vscode_style:
        entry = {"type": "stdio", **entry}
    return entry


def _get_provider(name: str, **kwargs: Any) -> LLMProvider:
    """Instantiate an LLM provider by name.

    Args:
        name: Provider name (``"mock"``, ``"ollama"``, or ``"openrouter"``).
        **kwargs: Extra keyword arguments forwarded to the provider constructor.

    Returns:
        An :class:`LLMProvider` instance.

    Raises:
        SystemExit: If the provider name is unknown.
    """
    if name == "mock":
        from loopllm.providers.mock import MockLLMProvider

        responses = [
            '{"result": "initial attempt"}',
            '{"result": "improved version", "details": "added more info"}',
            '{"result": "refined output", "details": "comprehensive", "quality": "high"}',
        ]
        return MockLLMProvider(responses=responses)
    elif name == "ollama":
        from loopllm.providers.ollama import OllamaProvider

        return OllamaProvider(base_url=kwargs.get("base_url", "http://localhost:11434"))
    elif name == "openrouter":
        import os

        from loopllm.providers.openrouter import OpenRouterProvider

        api_key = kwargs.get("api_key") or os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            print("Error: OPENROUTER_API_KEY not set", file=sys.stderr)
            sys.exit(1)
        return OpenRouterProvider(api_key=api_key)
    else:
        print(f"Unknown provider: {name}", file=sys.stderr)
        sys.exit(1)


def _get_store(db_path: str | None) -> LoopStore:
    """Create a LoopStore, defaulting to a per-project ~/.loopllm/projects/<id>/store.db."""
    path = resolve_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return LoopStore(db_path=path)


def _interactive_answer(question: ClarifyingQuestion) -> str:
    """Prompt the user for an answer to a clarifying question."""
    print(f"\n  [{question.question_type.upper()}] {question.text}")
    if question.options:
        for i, opt in enumerate(question.options, 1):
            print(f"    {i}. {opt}")
        print("    (enter number or free text)")

    answer = input("  > ").strip()

    # If they entered a number and we have options, map it
    if question.options and answer.isdigit():
        idx = int(answer) - 1
        if 0 <= idx < len(question.options):
            answer = question.options[idx]

    return answer


def cmd_refine(args: argparse.Namespace) -> None:
    """Execute the ``refine`` subcommand: elicit intent then refine."""
    provider = _get_provider(args.provider)
    store = _get_store(args.db)
    priors = SQLiteBackedPriors(store)

    prompt = args.prompt
    model = args.model

    if args.no_questions:
        # Skip elicitation, go straight to refinement
        print(f"Refining: {prompt[:80]}...")
    else:
        # Run intent elicitation
        refiner = IntentRefiner(
            provider=provider,
            priors=priors,
            model=model,
            max_questions=args.max_questions,
        )

        print(f"Analyzing prompt: {prompt[:80]}...")
        session = refiner.run_session(prompt, answer_func=_interactive_answer)

        if session.refined_spec:
            print("\n--- Refined Spec ---")
            print(f"Task type: {session.refined_spec.task_type}")
            print(f"Complexity: {session.refined_spec.estimated_complexity:.1f}")
            print(f"Prompt: {session.refined_spec.refined_prompt[:200]}")
            if session.refined_spec.quality_criteria:
                print("Quality criteria:")
                for c in session.refined_spec.quality_criteria:
                    print(f"  - {c}")
            prompt = session.refined_spec.refined_prompt

    # Run refinement loop
    evaluator = LengthEvaluator(min_words=5, max_words=10_000)
    config = LoopConfig(
        max_iterations=args.max_iterations,
        quality_threshold=args.threshold,
    )
    loop = LoopedLLM(provider=provider, config=config)

    print(f"\nRunning refinement loop (max {config.max_iterations} iterations)...")
    result = loop.refine(prompt, evaluator, model=model)

    print("\n--- Result ---")
    print(f"Exit: {result.metrics.exit_reason.condition}")
    print(f"Iterations: {result.metrics.total_iterations}")
    print(f"Best score: {result.metrics.best_score:.3f}")
    print(f"Output:\n{result.output}")

    # Record observation
    obs = CallObservation(
        task_type="cli_refine",
        model_id=model,
        scores=result.metrics.score_trajectory,
        latencies_ms=[it.latency_ms for it in result.iterations],
        converged=result.metrics.converged,
        total_iterations=result.metrics.total_iterations,
        max_iterations=config.max_iterations,
        quality_threshold=config.quality_threshold,
    )
    priors.observe(obs)
    store.close()


def cmd_report(args: argparse.Namespace) -> None:
    """Execute the ``report`` subcommand: show learned priors."""
    store = _get_store(args.db)
    priors = SQLiteBackedPriors(store)
    reports = priors.report_all()

    if not reports:
        print("No observations recorded yet.")
    else:
        for r in reports:
            print(f"\n=== {r['task_type']} / {r['model_id']} ===")
            print(f"  Calls: {r['total_calls']}")
            print(f"  Optimal depth: {r['optimal_depth']}")
            print(f"  Converge rate: {r['converge_rate']:.1%}")
            print(f"  First-call quality: {r['first_call_quality']:.3f}")
            print(f"  Confidence: {r['confidence']:.3f}")
            if r.get("iterations"):
                print("  Iterations:")
                for k, v in r["iterations"].items():
                    print(
                        f"    {k}: score={v['expected_score']:.3f} "
                        f"delta={v['expected_delta']:.3f} "
                        f"converge={v['converge_prob']:.1%}"
                    )

    # Also show question stats
    stats = store.get_question_stats()
    if stats:
        print("\n=== Question Effectiveness ===")
        for s in stats:
            print(
                f"  {s['question_type']:15s}  "
                f"asked={s['asked_count']:3d}  "
                f"effectiveness={s['effectiveness']:.1%}  "
                f"info_gain={s['avg_info_gain']:.3f}"
            )

    store.close()


def cmd_run(args: argparse.Namespace) -> None:
    """Execute the ``run`` subcommand: full pipeline with task orchestration."""
    provider = _get_provider(args.provider)
    store = _get_store(args.db)
    priors = SQLiteBackedPriors(store)

    orchestrator = TaskOrchestrator(
        provider=provider,
        priors=priors,
        store=store,
        model=args.model,
    )

    answer_func = None if args.no_questions else _interactive_answer

    print(f"Running full pipeline: {args.prompt[:80]}...")
    result = orchestrator.run(
        args.prompt,
        model=args.model,
        answer_func=answer_func,
    )

    print("\n--- Result ---")
    print(f"Exit: {result.metrics.exit_reason.condition}")
    print(f"Iterations: {result.metrics.total_iterations}")
    print(f"Best score: {result.metrics.best_score:.3f}")
    print(f"Output:\n{result.output}")
    store.close()


def cmd_score(args: argparse.Namespace) -> None:
    """Score a prompt and update status.json (per-project) for the VS Code extension.

    This is the same scoring used by loopllm_intercept but runs standalone,
    with no MCP server or agent required. The extension watches status.json
    via fs.watch and updates the gauge and dashboard immediately.
    """
    import time

    # Import scorer — mcp import is guarded so this is safe even without mcp pkg
    from loopllm.mcp_server import (
        _score_prompt_quality,
        _classify_task_type,
    )

    # Read prompt: argument, or "-" / empty arg means stdin
    raw = args.prompt
    if not raw or raw == "-":
        raw = sys.stdin.read()
    prompt = raw.strip()
    if not prompt:
        print("Error: empty prompt", file=sys.stderr)
        sys.exit(1)

    quality = _score_prompt_quality(prompt)
    task_type = _classify_task_type(prompt)

    db_path = resolve_db_path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    status_path = db_path.parent / "status.json"
    history_path = db_path.parent / "prompt_history.json"

    payload = {
        "quality_score": quality["quality_score"],
        "grade": quality["grade"],
        "gauge": quality["gauge"],
        "task_type": task_type,
        "route": "score",
        "dimensions": quality["dimensions"],
        "suggestions": quality.get("suggestions", []),
        "issues": quality.get("issues", []),
    }

    # Write status.json — picked up by StatusWatcher in the extension
    status_path.write_text(json.dumps({
        "timestamp": time.time(),
        "tool": "score",
        "data": payload,
    }, indent=2))

    # Append to prompt_history.json — picked up by DataProvider poll
    try:
        history: list[dict[str, Any]] = []
        if history_path.exists():
            try:
                history = json.loads(history_path.read_text())
                if not isinstance(history, list):
                    history = []
            except (json.JSONDecodeError, OSError):
                history = []
        history.append({
            "id": len(history) + 1,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "prompt_text": prompt[:500],
            **payload,
        })
        history_path.write_text(json.dumps(history, indent=2))
    except OSError:
        pass

    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2))
    else:
        print(f"{quality['gauge']}")
        if quality.get("suggestions"):
            for s in quality["suggestions"][:3]:
                print(f"  · {s}")


def cmd_tasks_list(args: argparse.Namespace) -> None:
    """List tasks from the store."""
    store = _get_store(args.db)
    tasks = store.get_tasks(state=args.state, limit=args.limit)

    if not tasks:
        print("No tasks found.")
    else:
        for t in tasks:
            print(
                f"  [{t['state']:12s}] {t['id'][:8]}  {t['title']}"
            )
    store.close()


def cmd_tasks_show(args: argparse.Namespace) -> None:
    """Show details for a specific task."""
    store = _get_store(args.db)
    task = store.get_task(args.task_id)

    if task is None:
        print(f"Task not found: {args.task_id}")
    else:
        print(json.dumps(task, indent=2, default=str))
    store.close()


def cmd_paths(args: argparse.Namespace) -> None:
    """Print resolved per-project state file paths as JSON.

    Used by the VS Code extension so it watches the same per-project
    directory the MCP server and CLI write to, instead of hardcoding the
    legacy flat ``~/.loopllm/`` layout.
    """
    db_path = resolve_db_path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    legacy = legacy_store_path()
    payload = {
        "project_id": resolve_project_id(),
        "dir": str(db_path.parent),
        "db": str(db_path),
        "status": str(db_path.parent / "status.json"),
        "history": str(db_path.parent / "prompt_history.json"),
        "episodes_feed": str(db_path.parent / "episodes_feed.json"),
        "active_run": str(db_path.parent / "active_run.json"),
        "active_runs_dir": str(db_path.parent / "active_runs"),
        "consultation": str(db_path.parent / "consultation.json"),
        # Pre-v0.10 flat global store, if one exists and isn't already the
        # resolved db — a hint that `loopllm migrate-legacy` applies here.
        "legacy_store": (
            str(legacy)
            if legacy is not None and legacy.resolve() != db_path.resolve()
            else None
        ),
    }
    print(json.dumps(payload, indent=2))


def cmd_audit(args: argparse.Namespace) -> None:
    """Print a human-readable verification audit trail: what the agent did, and how it was verified.

    Every episode (agent-loop, DAG-node, or DAG-merge outcome) is stamped with
    the git commit that was HEAD when it was recorded. ``--since <ref>``
    scopes the report to episodes recorded on commits reachable from HEAD but
    not from *ref* (i.e. ``git log <ref>..HEAD``) — e.g. ``--since origin/main``
    to audit everything on the current branch.

    ``--export <path>`` additionally writes the (filtered) episodes as a
    portable JSON artifact, keyed by ``commit_sha``. The local per-project
    SQLite store this reads from lives under ``~/.loopllm/`` and does not
    exist on a CI runner; committing this artifact alongside the code it
    verifies is what lets ``loopllm audit-gate`` check a PR in CI without
    that local state.
    """
    store = _get_store(args.db)
    episodes = store.list_episodes(limit=args.limit)

    if args.since:
        commit_set = commits_since(args.since)
        if commit_set is None:
            print(
                f"Warning: could not resolve commit range for '{args.since}' "
                "(not a git repo, or ref doesn't exist) — showing all episodes.",
                file=sys.stderr,
            )
        else:
            episodes = [e for e in episodes if e.get("commit_sha") in commit_set]

    if args.export:
        export_path = Path(args.export)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        artifact = {
            "version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "head": current_commit_sha(),
            "since": args.since,
            "episodes": episodes,
        }
        export_path.write_text(json.dumps(artifact, indent=2, default=str) + "\n")
        print(f"Exported {len(episodes)} episode(s) to {export_path}")

    if args.json:
        print(json.dumps(episodes, indent=2, default=str))
        store.close()
        return

    if not episodes:
        print("No verification episodes recorded" + (f" since {args.since}." if args.since else "."))
        store.close()
        return

    scores = [e["score_final"] for e in episodes if e.get("score_final") is not None]
    avg_score = sum(scores) / len(scores) if scores else None

    print(f"=== Verification Audit{' (since ' + args.since + ')' if args.since else ''} ===")
    print(f"{len(episodes)} episode(s)" + (f" · avg score {avg_score:.2f}" if avg_score is not None else ""))
    print()

    for ep in episodes:
        commit = (ep.get("commit_sha") or "")[:7] or "no-commit"
        score = ep.get("score_final")
        score_str = f"{score:.2f}" if score is not None else "—"
        steps = ep.get("steps_used")
        steps_str = f"{steps} step(s)" if steps is not None else ""
        stop = (ep.get("stop_reason") or "").replace("_", " ")
        print(f"[{commit}] {ep['episode_type']:10s} score={score_str}  {ep['goal'][:70]}")
        meta = "  ".join(x for x in (ep.get("task_type", ""), steps_str, stop) if x)
        if meta:
            print(f"          {meta}")

    store.close()


def cmd_audit_gate(args: argparse.Namespace) -> None:
    """CI gate: check that code-touching commits in a range carry a verification record.

    Reads a *committed* audit artifact (default ``.loopllm/audit.json``,
    produced by ``loopllm audit --export``) instead of the local per-project
    SQLite store — a CI runner has no ``~/.loopllm/`` state, so the artifact
    is the only portable record of what happened locally. Scopes to non-merge
    commits reachable from HEAD but not from ``--since`` (a merge commit
    isn't itself something an agent wrote and verified).

    Without ``--require-verified`` or ``--min-score`` this only reports and
    always exits 0 — a team can dogfood the report before turning on
    enforcement. ``--require-verified`` fails commits with no matching
    record; ``--min-score`` fails commits whose record scores below the
    threshold (also failing commits with no record, since there's nothing to
    compare).
    """
    commit_set = commits_since(args.since, no_merges=True)
    if commit_set is None:
        print(
            f"Warning: could not resolve commit range for '{args.since}' "
            "(not a git repo, or ref doesn't exist) — nothing to gate.",
            file=sys.stderr,
        )
        return

    if not commit_set:
        print(f"No commits since {args.since} — nothing to gate.")
        return

    artifact_path = Path(args.artifact)
    records_by_commit: dict[str, dict[str, Any]] = {}
    if artifact_path.exists():
        try:
            artifact = json.loads(artifact_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            print(f"Warning: could not parse audit artifact {artifact_path}: {exc}", file=sys.stderr)
            artifact = {"episodes": []}
        for ep in artifact.get("episodes", []):
            sha = ep.get("commit_sha")
            if not sha:
                continue
            existing = records_by_commit.get(sha)
            if existing is None or (ep.get("score_final") or 0) > (existing.get("score_final") or 0):
                records_by_commit[sha] = ep
    else:
        print(
            f"Warning: no audit artifact at {artifact_path} — treating all "
            f"commits as unverified. Run `loopllm audit --export {artifact_path}`.",
            file=sys.stderr,
        )

    failures: list[str] = []
    print(f"=== Verification Gate (since {args.since}) ===")
    for sha in sorted(commit_set):
        short = sha[:7]
        record = records_by_commit.get(sha)
        if record is None:
            print(f"[{short}] NOT VERIFIED")
            if args.require_verified or args.min_score is not None:
                failures.append(f"{short}: no verification record")
            continue

        score = record.get("score_final")
        score_str = f"{score:.2f}" if score is not None else "—"
        print(f"[{short}] verified  score={score_str}  {record.get('goal', '')[:60]}")
        if args.min_score is not None and (score is None or score < args.min_score):
            failures.append(f"{short}: score {score_str} below --min-score {args.min_score}")

    print()
    if failures:
        print(f"FAIL: {len(failures)}/{len(commit_set)} commit(s) failed the verification gate:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)

    print(f"PASS: {len(commit_set)} commit(s) checked.")


def cmd_migrate_legacy(args: argparse.Namespace) -> None:
    """Copy the pre-v0.10 global store into this project's scoped store.

    Before v0.10 every project shared one ``~/.loopllm/store.db``. State is
    now scoped per project (``~/.loopllm/projects/<id>/store.db``), which
    leaves an existing global store invisible — its learned priors and
    episodes would be silently orphaned. This command imports it, once,
    explicitly: run it from each project that should inherit the legacy
    history. The legacy file is left in place.
    """
    source = Path(args.source) if args.source else (Path.home() / ".loopllm" / "store.db")
    dest = resolve_db_path(args.db)

    if not source.exists():
        print(f"No legacy store found at {source} — nothing to migrate.")
        return
    if dest.exists() and source.samefile(dest):
        print(f"{dest} already is the legacy store — nothing to migrate.")
        return
    if dest.exists() and not args.force:
        print(
            f"Refusing to overwrite existing project store {dest} (use --force).",
            file=sys.stderr,
        )
        sys.exit(1)

    dest.parent.mkdir(parents=True, exist_ok=True)
    # sqlite3 backup, not a file copy: carries WAL pages that a raw copy of
    # just store.db would lose if the last writer wasn't checkpointed.
    src_conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        dst_conn = sqlite3.connect(dest)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()

    store = LoopStore(db_path=dest)  # opening runs any pending schema migrations
    episode_count = len(store.list_episodes(limit=100_000))
    store.close()
    print(
        f"Migrated {source} -> {dest} ({episode_count} episode(s)). "
        "The legacy file was left in place."
    )


def cmd_install_mcp(args: argparse.Namespace) -> None:
    """Register the loopllm MCP server in one or more IDE configs, one command.

    Merges a ``loopllm`` entry into each target's existing MCP config —
    other configured servers (e.g. GitHub's MCP server) are preserved.
    Cursor/VS Code/Antigravity are user-scoped (``~/.cursor/mcp.json`` etc.);
    Claude Code is project-scoped (``.mcp.json`` in the current directory,
    meant to be committed so the whole team gets the same server).
    """
    requested = args.ide if args.ide != "all" else _ALL_IDES
    ides = [requested] if isinstance(requested, str) else requested

    for ide_name in ides:
        target = _IDE_TARGETS.get(ide_name)
        if target is None:
            print(f"Unknown IDE: {ide_name}", file=sys.stderr)
            continue

        path = target.path_fn()
        try:
            existing: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {}
        except json.JSONDecodeError:
            print(
                f"[{ide_name}] {path} has invalid JSON — skipping rather than "
                "risk corrupting it. Fix or remove it and re-run.",
                file=sys.stderr,
            )
            continue

        servers = dict(existing.get(target.key, {}))
        if args.name in servers and not args.force:
            print(f"[{ide_name}] '{args.name}' already configured at {path} (use --force to overwrite)")
            continue

        servers[args.name] = _mcp_server_entry(target.vscode_style, args.provider, args.model)
        existing[target.key] = servers
        if target.vscode_style and "inputs" not in existing:
            existing["inputs"] = []

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(existing, indent=2) + "\n")
        print(f"[{ide_name}] wrote {path}")

    print("\nRestart the IDE (or reload its MCP servers) to pick up the change.")


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the ``loopllm`` CLI."""
    parser = argparse.ArgumentParser(
        prog="loopllm",
        description="Iterative refinement engine with Bayesian intent elicitation.",
    )
    parser.add_argument(
        "--db", default=None,
        help="Path to SQLite database (default: per-project ~/.loopllm/projects/<id>/store.db)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # --- score ---
    p_score = subparsers.add_parser(
        "score",
        help="Score a prompt and update the VS Code gauge instantly (no MCP server needed)",
    )
    p_score.add_argument(
        "prompt",
        nargs="?",
        default="-",
        help="Prompt text to score, or '-' to read from stdin (default: stdin)",
    )
    p_score.add_argument(
        "--json",
        action="store_true",
        help="Output full JSON result instead of gauge bar",
    )
    p_score.set_defaults(func=cmd_score)

    # --- refine ---
    p_refine = subparsers.add_parser(
        "refine", help="Elicit intent and refine a prompt"
    )
    p_refine.add_argument("prompt", help="The prompt to refine")
    p_refine.add_argument(
        "--provider", default="mock",
        choices=["mock", "ollama", "openrouter"],
        help="LLM provider (default: mock)",
    )
    p_refine.add_argument("--model", default="gpt-4o-mini", help="Model to use")
    p_refine.add_argument(
        "--no-questions", action="store_true",
        help="Skip intent elicitation",
    )
    p_refine.add_argument(
        "--max-questions", type=int, default=3,
        help="Maximum clarifying questions (default: 3)",
    )
    p_refine.add_argument(
        "--max-iterations", type=int, default=5,
        help="Maximum refinement iterations (default: 5)",
    )
    p_refine.add_argument(
        "--threshold", type=float, default=0.8,
        help="Quality threshold (default: 0.8)",
    )
    p_refine.set_defaults(func=cmd_refine)

    # --- run ---
    p_run = subparsers.add_parser(
        "run", help="Full pipeline: elicit → decompose → execute → verify"
    )
    p_run.add_argument("prompt", help="The prompt to process")
    p_run.add_argument(
        "--provider", default="mock",
        choices=["mock", "ollama", "openrouter"],
    )
    p_run.add_argument("--model", default="gpt-4o-mini")
    p_run.add_argument("--no-questions", action="store_true")
    p_run.add_argument("--max-questions", type=int, default=3)
    p_run.add_argument("--max-iterations", type=int, default=5)
    p_run.add_argument("--threshold", type=float, default=0.8)
    p_run.set_defaults(func=cmd_run)

    # --- report ---
    p_report = subparsers.add_parser(
        "report", help="Show learned priors and statistics"
    )
    p_report.set_defaults(func=cmd_report)

    # --- tasks ---
    p_tasks = subparsers.add_parser("tasks", help="Task management")
    tasks_sub = p_tasks.add_subparsers(dest="tasks_command")

    p_tlist = tasks_sub.add_parser("list", help="List tasks")
    p_tlist.add_argument("--state", default=None, help="Filter by state")
    p_tlist.add_argument("--limit", type=int, default=20)
    p_tlist.set_defaults(func=cmd_tasks_list)

    p_tshow = tasks_sub.add_parser("show", help="Show task details")
    p_tshow.add_argument("task_id", help="Task ID")
    p_tshow.set_defaults(func=cmd_tasks_show)

    # --- paths ---
    # Note: --db is intentionally *not* redeclared here — it reads from the
    # top-level --db (parser.add_argument("--db", ...) above), so `loopllm
    # --db X paths` and `loopllm paths` both work as expected.
    p_paths = subparsers.add_parser(
        "paths", help="Print resolved per-project state file paths as JSON"
    )
    p_paths.set_defaults(func=cmd_paths)

    # --- audit ---
    p_audit = subparsers.add_parser(
        "audit",
        help="Verification audit trail: what the agent did, and how it was CDV-verified",
    )
    p_audit.add_argument(
        "--since", default=None,
        help="Git ref (branch/tag/commit) to scope the report to — episodes "
             "recorded since this ref diverged from HEAD",
    )
    p_audit.add_argument("--limit", type=int, default=200, help="Max episodes to scan")
    p_audit.add_argument("--json", action="store_true", help="Output JSON instead of a report")
    p_audit.add_argument(
        "--export", default=None, metavar="PATH",
        help="Also write the (filtered) episodes as a portable JSON artifact at PATH "
             "(e.g. .loopllm/audit.json), for `loopllm audit-gate` to read in CI",
    )
    p_audit.set_defaults(func=cmd_audit)

    # --- audit-gate ---
    p_gate = subparsers.add_parser(
        "audit-gate",
        help="CI gate: fail when code-touching commits in a range lack a verification record",
    )
    p_gate.add_argument(
        "--since", required=True,
        help="Git ref (e.g. origin/main) the commit range is scoped against: "
             "non-merge commits reachable from HEAD but not from this ref",
    )
    p_gate.add_argument(
        "--artifact", default=".loopllm/audit.json", metavar="PATH",
        help="Committed audit artifact written by `loopllm audit --export` (default: .loopllm/audit.json)",
    )
    p_gate.add_argument(
        "--min-score", type=float, default=None, metavar="X",
        help="Fail commits whose verification score is below X (also fails commits with no record)",
    )
    p_gate.add_argument(
        "--require-verified", action="store_true",
        help="Fail commits that have no verification record at all",
    )
    p_gate.set_defaults(func=cmd_audit_gate)

    # --- migrate-legacy ---
    p_migrate = subparsers.add_parser(
        "migrate-legacy",
        help="Import the pre-v0.10 global ~/.loopllm/store.db into this project's scoped store",
    )
    p_migrate.add_argument(
        "--from", dest="source", default=None,
        help="Legacy store path (default: ~/.loopllm/store.db)",
    )
    p_migrate.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing project store",
    )
    p_migrate.set_defaults(func=cmd_migrate_legacy)

    # --- install-mcp ---
    p_install = subparsers.add_parser(
        "install-mcp",
        help="Register the loopllm MCP server in Cursor/VS Code/Antigravity/Claude Code — one command",
    )
    p_install.add_argument(
        "--ide", default="all",
        choices=["all", *sorted(_IDE_TARGETS.keys())],
        help="Target IDE ('all' = cursor+vscode+antigravity; claude-code is opt-in "
             "since it writes a project-scoped .mcp.json in the current directory)",
    )
    p_install.add_argument("--name", default="loopllm", help="MCP server name to register")
    p_install.add_argument("--provider", default="agent", help="LOOPLLM provider (default: agent)")
    p_install.add_argument("--model", default="agent", help="LOOPLLM_MODEL env value to set")
    p_install.add_argument(
        "--force", action="store_true", help="Overwrite an existing entry with this name"
    )
    p_install.set_defaults(func=cmd_install_mcp)

    # --- mcp-server ---
    p_mcp = subparsers.add_parser(
        "mcp-server", help="Start MCP server for IDE integration (VS Code, Cursor)"
    )
    p_mcp.add_argument(
        "--provider", default=None,
        choices=["agent", "mock", "ollama", "openrouter"],
        help="LLM provider (default: LOOPLLM_PROVIDER env or agent)",
    )
    p_mcp.add_argument(
        "--model", default=None,
        help="Default model (default: LOOPLLM_MODEL env or gpt-4o-mini)",
    )
    p_mcp.add_argument(
        "--db", default=None,
        help="Path to SQLite database (default: per-project ~/.loopllm/projects/<id>/store.db)",
    )
    p_mcp.set_defaults(func=cmd_mcp_server)

    # --- serve ---
    p_serve = subparsers.add_parser(
        "serve",
        help="Start REST scoring server for local models (Ollama, llama.cpp, etc.)",
    )
    p_serve.add_argument(
        "--host", default="127.0.0.1",
        help="Bind address (default: 127.0.0.1)",
    )
    p_serve.add_argument(
        "--port", type=int, default=8765,
        help="Port to listen on (default: 8765)",
    )
    p_serve.add_argument(
        "--reload", action="store_true",
        help="Enable auto-reload (development only)",
    )
    p_serve.set_defaults(func=cmd_serve)

    return parser


def cmd_serve(args: argparse.Namespace) -> None:
    """Start the loopllm scoring REST server."""
    try:
        from loopllm.serve import run_server
    except ImportError:
        print(
            "Error: FastAPI and uvicorn are required for `loopllm serve`.\n"
            "Install with: pip install loopllm[serve]",
            file=sys.stderr,
        )
        sys.exit(1)
    run_server(host=args.host, port=args.port, reload=args.reload)


def cmd_mcp_server(args: argparse.Namespace) -> None:
    """Start the MCP server for IDE integration."""
    import os

    # Pass CLI args as env vars so mcp_server.py picks them up
    if args.provider:
        os.environ["LOOPLLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LOOPLLM_MODEL"] = args.model
    if args.db:
        os.environ["LOOPLLM_DB"] = args.db

    try:
        from loopllm.mcp_server import main as mcp_main
    except ImportError:
        print(
            "Error: The mcp package is required for the MCP server.\n"
            "Install it with: pip install loopllm[mcp]",
            file=sys.stderr,
        )
        sys.exit(1)

    mcp_main()


def main() -> None:
    """Entry point for the ``loopllm`` CLI."""
    parser = build_parser()
    args = parser.parse_args()

    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
