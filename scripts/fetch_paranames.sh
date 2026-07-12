#!/usr/bin/env bash
#
# fetch_paranames.sh — fetch the ParaNames multilingual name-entity TSV dump.
# CC0; on the license allowlist.
#
# Part of `make sources` network-permitted flow.
#
# The upstream archive is large (~1 GB). This script fetches the latest
# release asset and expects the caller to run the PER-filter + sitelinks
# filter step offline. See scripts/filter_paranames.py (not in Phase 2 scope;
# filter step will land when the full corpus is adopted).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEST_DIR="$REPO_ROOT/src/resecta_data/gazetteers/sources/paranames"
DEST_FILE="$DEST_DIR/paranames_full.tsv.gz"
URL="https://github.com/bltlab/paranames/releases/latest/download/paranames.tsv.gz"

if [ -f "$DEST_FILE" ]; then
    echo "File already exists: $DEST_FILE" >&2
    echo "Refusing to overwrite. Source files are immutable (CLAUDE.md §2.7)." >&2
    exit 1
fi

mkdir -p "$DEST_DIR"

echo "Fetching $URL"
echo "(this is a large file; expect several minutes on a typical connection)"
curl --fail --show-error --location --output "$DEST_FILE" "$URL"

echo ""
echo "Wrote $DEST_FILE"
echo "SHA-256:"
shasum -a 256 "$DEST_FILE" | awk '{print $1}'
echo ""
echo "Next steps:"
echo "  1. Filter to PER entities with >=5 sitelinks before adopting."
echo "  2. Add a row to SOURCES.md for the filtered TSV."
echo "  3. Update bloom.corpus_ingest.parse_paranames if the format changed."
