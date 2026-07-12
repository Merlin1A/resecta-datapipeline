#!/usr/bin/env bash
# fetch_popnames_snapshot.sh — fetch sigpwned/popular-names-by-country CSVs (D-08).
# CC0. Provenance and SHA-256 are recorded in SOURCES.md.
#
# Replaces scripts/fetch_popular_names.sh (renamed for spec-alignment per §2.1).
# Consumed by popnames_adapter (parse_popnames_fullfile in bloom/corpus_ingest.py).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/_fetch_lib.sh"

DEST_DIR="$REPO_ROOT/src/resecta_data/gazetteers/sources/popnames"
BASE_URL="https://raw.githubusercontent.com/sigpwned/popular-names-by-country-dataset/main"
FILES=(
    "common-forenames-by-country.csv:forenames"
    "common-surnames-by-country.csv:surnames"
)

mkdir -p "$DEST_DIR"

for entry in "${FILES[@]}"; do
    fname="${entry%%:*}"
    kind="${entry##*:}"
    url="$BASE_URL/$fname"
    dest="$DEST_DIR/$fname"
    fetch_lib::probe_url "$url"
    fetch_lib::download_with_sha "$url" "$dest"
    fetch_lib::append_sources_row \
        "src/resecta_data/gazetteers/sources/popnames/$fname" \
        "CC0" \
        "$url" \
        "sigpwned popular-names-by-country dataset — common ${kind} (D-08; CC0; consumed by popnames_adapter / parse_popnames_fullfile; rows tagged demographic='unlabeled' on ingest per CLAUDE.md §9 2026-04-17 changelog)."
done

echo ""
echo "D-08 fetch complete (or cache-hit if already on disk)."
