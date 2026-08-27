#!/usr/bin/env bash
#
# fetch_census_spanish.sh — fetch the Census Spanish Surname List.
# Public Domain (17 U.S.C. §105).
#
# Part of `make sources` network-permitted flow.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEST_DIR="$REPO_ROOT/src/resecta_data/gazetteers/sources/census_spanish"
DEST_FILE="$DEST_DIR/census_spanish_surnames_full.txt"
URL="https://www.census.gov/library/publications/2011/demo/2000-gen-3.html"

echo "The Census Spanish Surname List is not published as a single machine-readable"
echo "file. Inspect the HTML at"
echo "  $URL"
echo "and either extract the list manually or use the bootstrap sample"
echo "already committed under the sources directory."
echo ""
echo "If you have a pre-prepared .txt file, copy it to:"
echo "  $DEST_FILE"
echo "Then compute its SHA-256 with:"
echo "  shasum -a 256 \"\$DEST_FILE\""
echo "and add a row to SOURCES.md."
exit 0
