# Contributing to CDV (`cdv`)

Thanks for your interest in improving CDV. This guide covers local setup,
the branch/commit conventions, and the checks your change must pass.

## Local setup

```bash
git clone https://github.com/azank1/cdv
cd cdv
pip install -e ".[dev]"
python -m pytest tests/ -q          # 282 tests (278 pass, 4 integration skipped)
```

## Branch naming convention

Branches follow a compact `<initials>/<type>/<short>` scheme:

- `<initials>` — the author's initials (e.g. `az`)
- `<type>` — `ft` (feature), `fix`, `chore`, `docs`, `refactor`, `test`
- `<short>` — a terse, hyphenated descriptor

Examples: `az/ft/cdv-v07`, `az/fix/store-migration`, `az/docs/readme`.

Do **not** include tool, assistant, or AI names in branch names or commit
metadata.

## Authorship

All commits on this repository are authored by `azank1 <azanhyder49@gmail.com>`.
Use squash-merge PRs into `main`; delete feature branches after merge.

## Commits & pull requests

- Use clear, imperative commit subjects (e.g. `feat: adaptive agent loops`).
- Keep commits focused; add tests for any new tool or behavior under `tests/`.
- Open PRs against `main`. CI (Python 3.11–3.13) must be green.

## Checks (run before pushing)

```bash
ruff check src/ tests/
mypy --strict src/cdv/
python -m pytest tests/ -q
```

## Where things live

| Area | File |
|---|---|
| Core refinement loop | `src/cdv/engine.py` |
| Bayesian priors / learning | `src/cdv/priors.py` |
| Adaptive agent loops | `src/cdv/agent_loop.py` |
| Framework stop adapter (should_continue) | `src/cdv/adapters.py` |
| Conservative Dual-Verify scoring | `src/cdv/step_scorer.py` |
| Agent-loop guard stack | `src/cdv/guards.py` |
| Evaluator factory | `src/cdv/evaluator_factory.py` |
| Bayesian early stopping | `src/cdv/adaptive_exit.py` |
| MCP tools (36) | `src/cdv/mcp_server.py` |
| Episodic memory | `src/cdv/episodes.py` |
| DAG virtual sub-agents | `src/cdv/dag_scheduler.py` |
| SQLite persistence (schema v6) | `src/cdv/store.py` |
| Per-project state scoping | `src/cdv/project_scope.py` |
| CLI | `src/cdv/cli.py` |
| Providers (agent/ollama/openrouter/mock) | `src/cdv/providers/` |
| VS Code extension | `vscode-loopllm/` |

## Publishing the VS Code extension

```bash
cd vscode-loopllm
npm install
npm run package          # produces cdv-judge-<version>.vsix
```

That `.vsix` can be installed locally (`Extensions: Install from VSIX...` in the
command palette) or shared directly. Publishing it to the VS Code Marketplace
additionally requires:

1. A registered Marketplace publisher id matching `"publisher"` in `package.json`
   (currently `cdv` — create one at https://marketplace.visualstudio.com/manage
   if it doesn't exist yet).
2. A personal access token (Azure DevOps) with Marketplace publish scope.
3. `npx @vscode/vsce publish -p <token>` (or `vsce login <publisher>` once, then
   `vsce publish`) from `vscode-loopllm/`.

Marketplace credentials are not part of this repo — only a maintainer with
publisher access can run step 3.

By contributing, you agree your contributions are licensed under the project's
[MIT License](LICENSE).
