#!/usr/bin/env bash
#
# scripts/freeze_deps.sh — regenerate requirements.lock from pyproject.toml.
#
# Run this after adding or updating a dependency in pyproject.toml.
# Requires the venv to exist (run scripts/bootstrap.sh first).
#
# Dependency additions require a PR note.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -d ".venv" ]; then
    echo "ERROR: .venv does not exist. Run scripts/bootstrap.sh first." >&2
    exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if ! command -v pip-compile >/dev/null 2>&1; then
    echo "ERROR: pip-compile not found. Install dev extras:" >&2
    echo "       pip install -e '.[dev]'" >&2
    exit 1
fi

echo "Compiling requirements.lock from pyproject.toml"
pip-compile \
    --quiet \
    --generate-hashes \
    --resolver=backtracking \
    --strip-extras \
    --output-file=requirements.lock \
    pyproject.toml

echo ""
echo "requirements.lock regenerated."
echo ""
echo "Review the diff carefully. Unexpected transitive changes are a red flag."
echo "Commit both pyproject.toml and requirements.lock together."
