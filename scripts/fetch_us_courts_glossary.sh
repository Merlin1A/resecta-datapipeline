#!/usr/bin/env bash
#
# fetch_us_courts_glossary.sh — scrape the US Courts glossary page into a
# plain-text term list for the negative-context gazetteer candidate builder.
# Public Domain (17 U.S.C. §105).
#
# Part of `make sources` network-permitted flow.
#
# The US Courts glossary is served as HTML. This script downloads the page
# and extracts the <dt> term entries into a plain-text file; the maintainer
# reviews and trims noise before the new file replaces the bootstrap
# sample.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEST_DIR="$REPO_ROOT/src/resecta_data/gazetteers/sources/negative_context"
DEST_HTML="$DEST_DIR/us_courts_glossary_full.html"
DEST_FILE="$DEST_DIR/us_courts_glossary_full.txt"
URL="https://www.uscourts.gov/glossary"

if [ -f "$DEST_FILE" ]; then
    echo "File already exists: $DEST_FILE" >&2
    echo "Refusing to overwrite. Source files are immutable." >&2
    exit 1
fi

mkdir -p "$DEST_DIR"

echo "Fetching $URL"
curl --fail --silent --show-error --location --output "$DEST_HTML" "$URL"

echo "Extracting <dt> terms"
python3 - "$DEST_HTML" "$DEST_FILE" <<'PY'
import re
import sys
from pathlib import Path

src = Path(sys.argv[1]).read_text(encoding="utf-8")
terms = re.findall(r"<dt[^>]*>(.+?)</dt>", src, flags=re.DOTALL | re.IGNORECASE)
cleaned = []
for raw in terms:
    t = re.sub(r"<[^>]+>", "", raw).strip().lower()
    if t and t not in cleaned:
        cleaned.append(t)
Path(sys.argv[2]).write_text(
    "# US Courts glossary — scraped from uscourts.gov/glossary.\n"
    + "# The maintainer reviews and trims the extracted list before it replaces the bootstrap sample.\n"
    + "\n".join(cleaned) + "\n",
    encoding="utf-8",
)
print(f"{len(cleaned)} terms extracted")
PY

echo ""
echo "Wrote $DEST_FILE"
echo "SHA-256:"
shasum -a 256 "$DEST_FILE" | awk '{print $1}'
echo ""
echo "Next steps:"
echo "  1. Hand-review the extracted list; trim irrelevant or overly generic terms."
echo "  2. Add a row to SOURCES.md with the final SHA-256."
echo "  3. Run \`make gazetteers && make verify\`."
