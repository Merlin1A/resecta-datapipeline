#!/usr/bin/env bash
# fetch_nicknames.sh — fetch the carltonnorthern/nicknames CC0 dataset.
# Source: https://github.com/carltonnorthern/nicknames
# License: CC0 (Creative Commons Zero / Public Domain Dedication).
#   CC0 is on the license allowlist; no legal-review stop required.
#
# BEFORE downloading the data file, this script fetches the upstream LICENSE
# file and verifies it contains a CC0 marker.  If the upstream license has
# changed, the script halts loudly so a human can re-evaluate before any
# data lands in sources/.
#
# Usage (run on Linux only — _fetch_lib.sh uses flock, absent on macOS):
#   ./scripts/fetch_nicknames.sh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/_fetch_lib.sh"

LICENSE_URL="https://raw.githubusercontent.com/carltonnorthern/nicknames/master/LICENSE"
DATA_URL="https://raw.githubusercontent.com/carltonnorthern/nicknames/master/names.csv"
DEST_DIR="$REPO_ROOT/src/resecta_data/gazetteers/nicknames/sources"

# ---------------------------------------------------------------------------
# Step 1: Fetch and verify the upstream license file.
#   The CC0 token must appear in the LICENSE text; if it doesn't (e.g. the
#   upstream repository changed its license), halt and require human review
#   (license judgments are not automated).
# ---------------------------------------------------------------------------

echo "Verifying upstream license at: $LICENSE_URL"
LICENSE_TMP="$(mktemp /tmp/nicknames_license.XXXXXX)"
trap 'rm -f "$LICENSE_TMP"' EXIT

curl --fail --silent --show-error --location \
    --max-time 30 \
    --user-agent "Wget/1.21" \
    --output "$LICENSE_TMP" "$LICENSE_URL"

if ! /usr/bin/grep -qE "CC0|Creative Commons Zero" "$LICENSE_TMP"; then
    echo "" >&2
    echo "ERROR: upstream LICENSE file does not contain a CC0 marker." >&2
    echo "  URL: $LICENSE_URL" >&2
    echo "  This dataset is used by the nickname sidecar builder to resolve" >&2
    echo "  given-name aliases; its license must remain CC0 / Public Domain." >&2
    echo "  Fetch halted. Review the upstream license before proceeding." >&2
    exit 1
fi
echo "License verification passed (CC0 marker found)."

# ---------------------------------------------------------------------------
# Step 2: Download the data file.
# ---------------------------------------------------------------------------

mkdir -p "$DEST_DIR"

DATED_DEST="${DEST_DIR}/nicknames_cc0_$(date -u +%Y%m%d).csv"

fetch_lib::probe_url "$DATA_URL"
fetch_lib::download_with_sha "$DATA_URL" "$DATED_DEST"
fetch_lib::append_sources_row \
    "src/resecta_data/gazetteers/nicknames/sources/nicknames_cc0_$(date -u +%Y%m%d).csv" \
    "CC0" \
    "$DATA_URL" \
    "carltonnorthern/nicknames CC0 dataset; given-name alias CSV consumed by the nickname sidecar builder to resolve diminutives and variants to canonical given names."

echo ""
echo "fetch_nicknames: done. Data written to: $DATED_DEST"
