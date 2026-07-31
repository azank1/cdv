# Releasing

This documents the one-shot sequence for cutting a real, externally-visible
release once a version is ready. Nothing below has been run for real yet —
everything through v0.10.0 has only been dry-run verified locally (build +
`twine check` + fresh-venv install; `vsce package` produced a clean `.vsix`).
Treat this file as the checklist for the first time either publish path is
actually flipped on.

## PyPI (`cdv` package)

The [`publish.yml`](.github/workflows/publish.yml) workflow already builds
and uploads to PyPI on `release: published`. It reads the version straight
from `pyproject.toml`, so nothing in CI needs to change — the only missing
piece is the token.

1. **Bump the version** in both `pyproject.toml` (`[project].version`) and
   `src/cdv/__init__.py` (`__version__`) — they must match exactly, or
   the workflow's own "Verify install" step will fail after upload.
2. **Local dry run** before tagging anything:
   ```bash
   rm -rf dist build *.egg-info
   python -m build
   python -m twine check dist/*
   ```
   Both the sdist and wheel should print `PASSED`.
3. **Add the `PYPI_API_TOKEN` repo secret** (Settings -> Secrets and
   variables -> Actions). As of this writing **no secrets are configured on
   `azank1/cdv`** — `gh secret list` returns empty — so the workflow
   will fail at the `twine upload` step until this is added. Use a
   [PyPI API token](https://pypi.org/manage/account/token/) scoped to the
   `cdv` project (or your PyPI account, for the very first upload before
   the project exists on PyPI).
4. **Tag and cut the GitHub Release**:
   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   gh release create vX.Y.Z --title "vX.Y.Z" --notes-from-tag
   ```
   Publishing the release (not just creating a draft) fires `publish.yml`
   via the `release: published` trigger.
5. **Verify**: `pip install "cdv[mcp]==X.Y.Z"` in a scratch venv and
   confirm `python -c "import cdv; print(cdv.__version__)"` matches.

If you want to stage without triggering the real upload, create the release
as a **draft** — drafts do not fire `release: published`.

## VS Code Marketplace (`vscode-loopllm` extension)

There is no CI workflow for this yet; publishing is manual via `vsce`.

1. **Bump the version** in `vscode-loopllm/package.json` (`version`) and
   keep `vscode-loopllm/package-lock.json`'s version field in sync (see the
   `chore: sync extension package-lock version` precedent commit).
2. **Local dry run**:
   ```bash
   cd vscode-loopllm
   npm run compile
   npx @vscode/vsce package --no-dependencies
   ```
   Confirm the file list looks right (no `src/`, `node_modules/`, or
   `.map` files — see `.vscodeignore`) and delete the generated `.vsix`
   afterward; it should never be committed.
3. **Known gap to close before a real Marketplace listing**: `package.json`
   has no top-level `icon` field (a 128x128 PNG). The activity-bar icon
   (`media/icon.svg`) works fine inside VS Code, but the Marketplace
   listing itself needs a PNG `icon` path — add one and reference it before
   publishing for real, or the listing will show a generic placeholder.
4. **Get a publisher access token**: create/verify the `cdv` publisher
   on the [Marketplace management page](https://marketplace.visualstudio.com/manage),
   generate a PAT with `Marketplace: Manage` scope from Azure DevOps.
5. **Publish**:
   ```bash
   npx @vscode/vsce publish --no-dependencies -p <PAT>
   ```
   (or `vsce login cdv` once, then `vsce publish` without `-p` each time).

## Order of operations for a combined release

1. Merge the feature branch/PR into `main`, confirm CI green (this is what
   happened for v0.10.0 via #9).
2. Fix version drift (`pyproject.toml`, `__init__.py`, `package.json`) and
   stamp the CHANGELOG with a real date — do this *before* tagging, not after.
3. Run both dry runs above (PyPI build/check, `vsce package`).
4. Tag once (`vX.Y.Z`), publish the GitHub Release (fires PyPI), then run the
   `vsce publish` step separately — they're independent registries with
   independent version numbers (package vs. extension), so there's no
   ordering dependency between them.
