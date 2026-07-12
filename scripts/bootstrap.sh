#!/usr/bin/env bash
#
# scripts/bootstrap.sh — create venv and install pinned deps.
#
# Idempotent. Safe to run repeatedly.
set -euo pipefail

cd "$(dirname "$0")/.."

VENV_DIR=".venv"
REQUIRED_PY="3.12"
PYTHON_BIN="${RESECTA_PYTHON:-python3.12}"

# --- Python version check -----------------------------------------------------
# Pin to python3.12 directly rather than bare `python3`, which on Ubuntu
# 22.04-derived hosts resolves to /usr/bin/python3.10. Override with
# RESECTA_PYTHON=/path/to/python (e.g. on macOS where Homebrew names the
# binary differently).

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "ERROR: $PYTHON_BIN not found on PATH" >&2
    echo "       This project requires Python $REQUIRED_PY (.python-version)." >&2
    echo "       Install (Ubuntu): apt install python3.12 python3.12-venv" >&2
    echo "       Or override:      RESECTA_PYTHON=/path/to/python$REQUIRED_PY scripts/bootstrap.sh" >&2
    exit 1
fi

ACTUAL_PY=$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if [ "$ACTUAL_PY" != "$REQUIRED_PY" ]; then
    echo "ERROR: Python $REQUIRED_PY required, $PYTHON_BIN reports $ACTUAL_PY" >&2
    echo "       Override with RESECTA_PYTHON=/path/to/python$REQUIRED_PY scripts/bootstrap.sh" >&2
    exit 1
fi

# --- Create venv --------------------------------------------------------------

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating venv at $VENV_DIR (using $PYTHON_BIN)"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# --- Early exit when inputs are unchanged --------------------------------------
# The Makefile re-runs this script whenever pyvenv.cfg is older than
# pyproject.toml / requirements.lock — which checkout-time mtime churn causes
# constantly — and a fully-satisfied `pip install --require-hashes` still
# costs ~2.3 s. A content stamp under .venv/ lets a re-run with byte-identical
# inputs skip pip entirely; any real edit changes the sha and falls through
# to the full install. The stamp is written only after a fully successful
# install (end of script), so a failed bootstrap can never record success.

STAMP_FILE="$VENV_DIR/.bootstrap-input-sha256"

input_sha() {
    "$PYTHON_BIN" -c 'import hashlib, sys
h = hashlib.sha256()
for name in sys.argv[1:]:
    with open(name, "rb") as f:
        h.update(f.read())
print(h.hexdigest())' requirements.lock pyproject.toml
}

if [ -f requirements.lock ] && [ -f pyproject.toml ] && [ -f "$STAMP_FILE" ]; then
    if [ "$(cat "$STAMP_FILE")" = "$(input_sha)" ]; then
        echo "Bootstrap inputs unchanged (sha256 stamp match); skipping pip install."
        touch "$VENV_DIR/pyvenv.cfg"
        exit 0
    fi
fi

# --- Upgrade pip itself (pinned) ---------------------------------------------

python -m pip install --quiet --upgrade "pip>=24.0,<25" "setuptools>=68" "wheel"

# --- Install dependencies from lockfile --------------------------------------

if [ ! -f "requirements.lock" ]; then
    echo "ERROR: requirements.lock is missing" >&2
    echo "       Run scripts/freeze_deps.sh to generate it" >&2
    exit 1
fi

echo "Installing pinned dependencies from requirements.lock"
python -m pip install --quiet --require-hashes -r requirements.lock || {
    # Fallback for initial bootstrap when hashes aren't yet present
    echo "  (retrying without --require-hashes for initial bootstrap)"
    python -m pip install --quiet -r requirements.lock
}

# --- Install this package in editable mode ------------------------------------

echo "Installing resecta-data (editable) with dev extras"
python -m pip install --quiet -e ".[dev]"

# --- Record success ------------------------------------------------------------
# Stamp + cfg touch come LAST so any pip failure above aborts (set -e) before
# success is recorded. The pyvenv.cfg touch closes B1: the Makefile rule
# `$(VENV_DIR)/pyvenv.cfg: pyproject.toml requirements.lock` keys on the cfg
# mtime, but `python -m venv` writes the cfg once at creation — so checkout
# mtimes kept it permanently stale and pip re-ran on every make invocation.

input_sha > "$STAMP_FILE"
touch "$VENV_DIR/pyvenv.cfg"

# --- Summary ------------------------------------------------------------------

echo ""
echo "Bootstrap complete. Activate with:"
echo "  source $VENV_DIR/bin/activate"
echo ""
echo "Next:"
echo "  make help      list build targets"
echo "  make verify    run the verification suite"
