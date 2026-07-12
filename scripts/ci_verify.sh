#!/usr/bin/env bash
#
# scripts/ci_verify.sh — local verification sequence (lint -> typecheck ->
# test -> build -> schema/hash checks). There is no remote CI runner; this
# script and `gmake verify` are the verification gates.
#
# Invariants:
#   - No network access after bootstrap
#   - All raw sources already present under src/*/sources/
#   - PYTHONHASHSEED pinned
#   - *-only check targets validate the single build above them (no
#     re-evaluated build prereqs)
#   - Determinism runs in CI's forced mode (never the witness/stamp-key
#     cache) when RESECTA_FORCE_DETERMINISM=1 is exported, which this
#     script does by default to match CI. Expect the full cold-rebuild
#     cost (≈44-55 min on an M1 Pro).
#     RESECTA_FORCE_DETERMINISM=0 uses the local witness path instead.
set -euo pipefail

cd "$(dirname "$0")/.."

export PYTHONHASHSEED=0
export PIP_DISABLE_PIP_VERSION_CHECK=1
export RESECTA_CI=1
export RESECTA_FORCE_DETERMINISM="${RESECTA_FORCE_DETERMINISM:-1}"

# Stock macOS make is 3.81; the Makefile's guarded goals need GNU make >= 4.
# Prefer gmake when present, as CI's ubuntu make is 4.x.
MAKE_BIN="$(command -v gmake || command -v make)"

echo "=== PII guard ==="
python3 scripts/check_no_pii.py

echo "=== Bootstrap ==="
scripts/bootstrap.sh

echo ""
echo "=== Lint ==="
"$MAKE_BIN" lint

echo ""
echo "=== Typecheck ==="
"$MAKE_BIN" typecheck

echo ""
echo "=== Tests ==="
"$MAKE_BIN" test

echo ""
echo "=== Build ==="
"$MAKE_BIN" build

echo ""
echo "=== Schema check (against the build above) ==="
"$MAKE_BIN" schema-check-only

echo ""
echo "=== Hash check (against the build above) ==="
"$MAKE_BIN" hash-check-only

echo ""
echo "=== Determinism check ==="
"$MAKE_BIN" determinism-check

echo ""
echo "All checks passed."
