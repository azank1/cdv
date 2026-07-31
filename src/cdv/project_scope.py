"""Per-project scoping for local state under ``~/.cdv/``.

Without this, every repo on a machine shares one ``~/.cdv/store.db`` —
episodes, active runs, and priors from unrelated projects interleave in the
same file, which breaks the premise that cdv "remembers what worked for
*this* codebase."

:func:`resolve_project_id` picks a stable short id, in priority order:

1. ``CDV_PROJECT`` env var — explicit override, always wins.
2. Working directory from :func:`resolve_workdir` (see below), then:
   a. ``git remote get-url origin`` of that repo, hashed — the same clone
      (any path on any machine) resolves to one project.
   b. The git repo root (``git rev-parse --show-toplevel``), hashed — used
      when there's a ``.git`` directory but no configured remote.
   c. The literal working directory, hashed — used outside any git repo.

:func:`resolve_workdir` picks the directory used for that identity (and for
git lookups) in priority order:

1. Explicit ``cwd`` argument, when the caller passes one.
2. ``CDV_WORKSPACE`` env var, if set to an existing directory.
3. First existing path in ``WORKSPACE_FOLDER_PATHS`` (Cursor/VS Code set this
   on MCP server processes even when process cwd is ``$HOME``).
4. ``Path.cwd()``.

Cursor launches MCP servers with ``cwd=$HOME`` and
``WORKSPACE_FOLDER_PATHS=<open folder>``. Without step 3, the MCP sidecar and
the VS Code Loop Monitor (which runs ``cdv paths`` from the workspace)
write/read different ``~/.cdv/projects/<id>/`` trees — Loop Monitor stays
blank while agent loops "succeed" into an orphan home-hash store.

:func:`project_state_dir` and :func:`resolve_db_path` build on this to give
every project its own ``~/.cdv/projects/<id>/`` directory, so the SQLite
store and its mirrored JSON files (``status.json``, ``active_run.json``,
``episodes_feed.json``, ``prompt_history.json``) never cross projects.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

_GIT_TIMEOUT_S = 2.0


def _run_git(args: list[str], cwd: Path) -> str | None:
    """Run a git command in *cwd*; return trimmed stdout, or None on any failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    return output or None


def _short_hash(value: str) -> str:
    """Stable, filesystem-safe short id for a project identity string."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _workspace_folder_from_env() -> Path | None:
    """Return the IDE workspace folder when the MCP process cwd is useless.

    Cursor sets ``WORKSPACE_FOLDER_PATHS`` (pathsep-joined for multi-root).
    ``CDV_WORKSPACE`` is an explicit single-folder override for other hosts.
    """
    explicit = os.environ.get("CDV_WORKSPACE")
    if explicit:
        path = Path(explicit.strip()).expanduser()
        if path.is_dir():
            return path.resolve()

    raw = os.environ.get("WORKSPACE_FOLDER_PATHS")
    if not raw:
        return None
    # Accept both os.pathsep and commas — hosts disagree on the separator.
    normalized = raw.replace(",", os.pathsep)
    for part in normalized.split(os.pathsep):
        part = part.strip()
        if not part:
            continue
        path = Path(part).expanduser()
        if path.is_dir():
            return path.resolve()
    return None


def resolve_workdir(cwd: Path | None = None) -> Path:
    """Resolve the directory used for project identity and git lookups."""
    if cwd is not None:
        return cwd.resolve()
    workspace = _workspace_folder_from_env()
    if workspace is not None:
        return workspace
    return Path.cwd().resolve()


def resolve_project_id(cwd: Path | None = None) -> str:
    """Return a stable short id scoping local state to one project.

    ``CDV_PROJECT`` wins outright when set. Otherwise the id is derived
    from the git remote of :func:`resolve_workdir` (so the same repo cloned
    twice still shares state), falling back to the repo root path, then the
    raw working directory when there's no git repo at all.
    """
    override = os.environ.get("CDV_PROJECT")
    if override:
        return _short_hash(override.strip())

    root = resolve_workdir(cwd)

    remote = _run_git(["remote", "get-url", "origin"], root)
    if remote:
        return _short_hash(remote)

    toplevel = _run_git(["rev-parse", "--show-toplevel"], root)
    if toplevel:
        return _short_hash(str(Path(toplevel).resolve()))

    return _short_hash(str(root))


def project_state_dir(base: Path | None = None, cwd: Path | None = None) -> Path:
    """Directory holding this project's local state: ``<base>/projects/<id>/``."""
    base = base or (Path.home() / ".cdv")
    return base / "projects" / resolve_project_id(cwd)


def current_commit_sha(cwd: Path | None = None) -> str | None:
    """Return the current ``HEAD`` commit sha, or None outside a git repo.

    Used to stamp episodes with the commit that was checked out when a
    verification ran, so ``cdv audit --since <ref>`` can build a
    verification trail scoped to a commit range.
    """
    return _run_git(["rev-parse", "HEAD"], resolve_workdir(cwd))


def commits_since(
    ref: str, cwd: Path | None = None, *, no_merges: bool = False
) -> set[str] | None:
    """Return the set of commit shas reachable from HEAD but not from *ref*.

    Returns None (meaning "don't filter, git/ref unavailable") only on an
    actual git failure — an empty-but-successful range (e.g. ``ref == HEAD``)
    correctly returns an empty set rather than being treated as a failure.

    ``no_merges=True`` excludes merge commits — used by ``cdv audit-gate``,
    since a merge commit isn't itself something an agent "wrote" and verified;
    it's the individual commits merged in that carry (or lack) a verification
    record.
    """
    root = resolve_workdir(cwd)
    args = ["log", "--format=%H", f"{ref}..HEAD"]
    if no_merges:
        args.insert(1, "--no-merges")
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def legacy_store_path(base: Path | None = None) -> Path | None:
    """Return the pre-v0.10 flat global store (``<base>/store.db``) if present.

    v0.10 moved state under ``<base>/projects/<id>/``; a store left behind by
    v0.8/v0.9 is otherwise invisible to the scoped layout, so callers use this
    to surface a ``cdv migrate-legacy`` hint instead of silently orphaning
    the user's learned priors and episodes.
    """
    base = base or (Path.home() / ".loopllm")
    legacy = base / "store.db"
    return legacy if legacy.exists() else None


def resolve_db_path(
    explicit: str | os.PathLike[str] | None,
    *,
    base: Path | None = None,
    cwd: Path | None = None,
) -> Path:
    """Resolve the SQLite store path.

    An explicit path (typically from ``CDV_DB`` or a ``--db`` flag) always
    wins and is used as-is, unscoped — this keeps tests and single-project
    power-user setups fully backward compatible. Otherwise the path is scoped
    per-project under :func:`project_state_dir`.
    """
    if explicit:
        return Path(explicit)
    return project_state_dir(base=base, cwd=cwd) / "store.db"
