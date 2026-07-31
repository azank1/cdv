#!/usr/bin/env bash
# Build and publish cdv to PyPI. Requires:
#   export TWINE_USERNAME=__token__
#   export TWINE_PASSWORD=pypi-...   # API token from pypi.org
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VERSION="$(grep '^version' pyproject.toml | head -1 | sed 's/.*"\(.*\)".*/\1/')"

if [[ -z "${TWINE_PASSWORD:-}" ]]; then
  echo "error: TWINE_PASSWORD not set (PyPI API token)." >&2
  echo "  export TWINE_USERNAME=__token__" >&2
  echo "  export TWINE_PASSWORD=pypi-..." >&2
  exit 1
fi

export TWINE_USERNAME="${TWINE_USERNAME:-__token__}"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi

.venv/bin/pip install -q build twine
rm -rf dist/
.venv/bin/python -m build
.venv/bin/twine check "dist/cdv-${VERSION}"*

echo "Uploading cdv ${VERSION} to PyPI..."
.venv/bin/twine upload "dist/cdv-${VERSION}"*

echo "Verifying install from PyPI..."
rm -rf /tmp/cdv-pypi-verify
python3 -m venv /tmp/cdv-pypi-verify
/tmp/cdv-pypi-verify/bin/pip install -q "cdv[mcp]==${VERSION}"
/tmp/cdv-pypi-verify/bin/python -c "import cdv; assert cdv.__version__ == '${VERSION}'"
/tmp/cdv-pypi-verify/bin/cdv --help | head -1
echo "OK: pip install cdv[mcp]==${VERSION} works."
