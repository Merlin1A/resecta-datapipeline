#!/usr/bin/env bash
# fetch_gsa_agencies.sh — fetch Federal Register /agencies.json + dated mirror.
# Provenance, license, and SHA-256 are recorded in SOURCES.md.
# Source: NARA Office of the Federal Register + GPO. §105 PD.
# Naming: script keeps `gsa_agencies` for pipeline continuity; payload is named
#         `federalregister_agencies.json` to reflect real source.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/_fetch_lib.sh"

URL="https://www.federalregister.gov/api/v1/agencies.json?per_page=1000"
DEST_DIR="$REPO_ROOT/src/resecta_data/gazetteers/institutions/sources"
LIVE_FILE="$DEST_DIR/federalregister_agencies.json"
MIRROR_DIR="$DEST_DIR"
MIRROR_PREFIX="federalregister-agencies"

mkdir -p "$DEST_DIR"

fetch_lib::probe_url "$URL"
fetch_lib::download_with_sha "$URL" "$LIVE_FILE"
fetch_lib::write_dated_mirror "$LIVE_FILE" "$MIRROR_DIR" "$MIRROR_PREFIX"

fetch_lib::append_sources_row \
    "src/resecta_data/gazetteers/institutions/sources/federalregister_agencies.json" \
    "Public Domain" \
    "$URL" \
    "Federal Register agencies API feed (D-01); ~444 rows on 2026-04-22 probe; NARA Office of the Federal Register + GPO; §105 PD; consumed by gazetteers/institutions.json (A3) rebuild scoped to federal_agency only."

echo ""
echo "D-01 fetch complete."
echo "  live:   $LIVE_FILE"
echo "  mirror: $MIRROR_DIR/$MIRROR_PREFIX-$(date -u +%Y-%m-%d).json"
echo "  sha256: $(cat "$LIVE_FILE.sha256")"
