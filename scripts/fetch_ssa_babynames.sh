#!/usr/bin/env bash
# fetch_ssa_babynames.sh — fetch SSA baby-names national + per-state archives (D-07).
# §105 PD. Provenance and SHA-256 are recorded in SOURCES.md.
#
# Replaces scripts/fetch_ssa_baby_names.sh (single-year only).
# Fetches the canonical archives, not extracted yob<YYYY>.txt files. Future
# builders extract per-year on demand.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/_fetch_lib.sh"

DEST_DIR="$REPO_ROOT/src/resecta_data/gazetteers/sources/ssa_given_names"
NATIONAL_URL="https://www.ssa.gov/oact/babynames/names.zip"
NATIONAL_FILE="$DEST_DIR/names.zip"
STATE_URL="https://www.ssa.gov/oact/babynames/state/namesbystate.zip"
STATE_FILE="$DEST_DIR/namesbystate.zip"

mkdir -p "$DEST_DIR"

# National archive
fetch_lib::probe_url "$NATIONAL_URL"
fetch_lib::download_with_sha "$NATIONAL_URL" "$NATIONAL_FILE" "Wget/1.21"
fetch_lib::append_sources_row \
    "src/resecta_data/gazetteers/sources/ssa_given_names/names.zip" \
    "Public Domain" \
    "$NATIONAL_URL" \
    "SSA national baby-names archive (D-07; multi-year yob<YYYY>.txt files 1880→present; §105 PD; consumed by A1/A2 given-names Bloom regen)."

# Per-state archive
fetch_lib::probe_url "$STATE_URL"
fetch_lib::download_with_sha "$STATE_URL" "$STATE_FILE" "Wget/1.21"
fetch_lib::append_sources_row \
    "src/resecta_data/gazetteers/sources/ssa_given_names/namesbystate.zip" \
    "Public Domain" \
    "$STATE_URL" \
    "SSA per-state baby-names archive (D-07; per-state yobYYYY-style files; §105 PD; consumed by A1/A2 demographic-bucket extension via state-specific given-name distribution)."

echo ""
echo "D-07 fetch complete."
echo "  national: $NATIONAL_FILE  (sha: $(cat "$NATIONAL_FILE.sha256"))"
echo "  per-state: $STATE_FILE  (sha: $(cat "$STATE_FILE.sha256"))"
echo ""
echo "Existing on-disk yob2024.txt remains unchanged (Option A from d07-kickoff.md)."
