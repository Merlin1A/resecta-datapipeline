#!/usr/bin/env bash
# fetch_census_2010_surnames.sh — fetch Census 2010 Decennial Surname File (D-06).
# Provenance, license, and SHA-256 are recorded in SOURCES.md.
# §105 PD. Vintage 2010 hardcoded per F-15.
#
# Replaces scripts/fetch_census_surnames.sh (renamed for vintage clarity).
# A 2020 successor product was published 2026-04-14 at a different URL; this
# script intentionally pins the 2010 URL so an accidental upstream pivot
# cannot silently swap vintages out from under the A1/A2 Bloom consumers.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/_fetch_lib.sh"

VINTAGE="2010"
ZIP_URL="https://www2.census.gov/topics/genealogy/${VINTAGE}surnames/names.zip"
DEST_DIR="$REPO_ROOT/src/resecta_data/gazetteers/sources/census_surnames"
INNER_FILE="Names_${VINTAGE}Census.csv"
DEST_REL="src/resecta_data/gazetteers/sources/census_surnames/$INNER_FILE"
DEST_FILE="$DEST_DIR/$INNER_FILE"

mkdir -p "$DEST_DIR"
fetch_lib::probe_url "$ZIP_URL"

if [[ -f "$DEST_FILE" ]]; then
    if [[ -f "$DEST_FILE.sha256" ]]; then
        if ! fetch_lib::verify_sidecar "$DEST_FILE"; then
            echo "Sidecar mismatch — Failure Mode #2 (drift). STOP." >&2
            exit 1
        fi
        echo "fetch_census_2010_surnames: $DEST_FILE already cached (sidecar matches)" >&2
    else
        echo "fetch_census_2010_surnames: $DEST_FILE cached without sidecar; computing now" >&2
        shasum -a 256 "$DEST_FILE" | awk '{print $1}' > "$DEST_FILE.sha256"
    fi
else
    # Fresh fetch path: download zip, extract inner file, write sidecar.
    TMP_ZIP="$(mktemp -t census_2010_surnames.XXXXXX.zip)"
    trap 'rm -f "$TMP_ZIP"' EXIT
    curl --fail --silent --show-error --location --max-time 600 \
        --user-agent "Wget/1.21" --output "$TMP_ZIP" "$ZIP_URL"
    unzip -p "$TMP_ZIP" "$INNER_FILE" > "$DEST_FILE"
    shasum -a 256 "$DEST_FILE" | awk '{print $1}' > "$DEST_FILE.sha256"
    echo "fetch_census_2010_surnames: extracted $INNER_FILE to $DEST_FILE" >&2
fi

fetch_lib::append_sources_row \
    "$DEST_REL" \
    "Public Domain" \
    "$ZIP_URL" \
    "Census ${VINTAGE} Decennial Surname File (D-06; vintage ${VINTAGE} hardcoded per F-15; ~162k rows; columns name,rank,count,prop100k,cum_prop100k,pctwhite,pctblack,pctapi,pctaian,pct2prace,pcthispanic; §105 PD; consumed by A1/A2 Bloom regen + demographic coverage report)."

echo "D-06 fetch complete: $DEST_FILE"
echo "SHA-256: $(cat "$DEST_FILE.sha256")"
