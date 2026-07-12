#!/usr/bin/env bash
#
# scripts/reinstall_signatures.sh — re-copy the SEC-6 signature pair into the
# iOS Gazetteers resources (speed plan #19, verifier C6).
#
# The two files are gitignored on the iOS side, so a corpus refresh or
# re-clone over there drops them and every Auto-Detect run degrades until
# they are reinstalled. This script copies exactly:
#
#   build/gazetteers/gazetteer_manifest.sig   -> <dest>/gazetteer_manifest.sig
#   build/gazetteers/manifest_public_key.pem  -> <dest>/manifest_public_key.pem
#
# Filenames stay snake_case on BOTH sides — only the manifest JSON is renamed
# to kebab-case (gazetteer-manifest.json) by the install routing (cli.py).
#
# Manifest-currency gate: a detached Ed25519 .sig verifies only against the
# exact manifest bytes it signed. If the installed gazetteer-manifest.json
# does not byte-match build/gazetteers/gazetteer_manifest.json, copying the
# .sig alone ships a pair that will not verify at detector init — this
# script refuses and points at `gmake install-assets` (which routes the
# manifest and its signature peers together, behind full verify).
set -euo pipefail

cd "$(dirname "$0")/.."

SRC="build/gazetteers"
# Lowercase ../resecta is the real sibling-checkout casing; the old
# ../Resecta spelling resolved only via case-insensitive APFS (N3/#20).
DST="${RESECTA_GAZETTEERS_DEST:-../resecta/Packages/RedactionEngine/Sources/RedactionEngine/Resources/Gazetteers}"

if [ ! -d "$DST" ]; then
    echo "ERROR: destination not found: $DST" >&2
    echo "       Run from inside the sibling-repo layout, or set RESECTA_GAZETTEERS_DEST." >&2
    exit 1
fi

for f in gazetteer_manifest.sig manifest_public_key.pem; do
    if [ ! -f "$SRC/$f" ]; then
        echo "ERROR: $SRC/$f missing — run 'gmake sign-manifest' first." >&2
        exit 1
    fi
done

# Manifest-currency gate (see header).
if ! cmp -s "$SRC/gazetteer_manifest.json" "$DST/gazetteer-manifest.json"; then
    echo "ERROR: installed gazetteer-manifest.json does not byte-match $SRC/gazetteer_manifest.json." >&2
    echo "       A .sig copied without its matching manifest will not verify at detector init." >&2
    echo "       Run 'gmake install-assets' to route the manifest and signature peers together." >&2
    exit 1
fi

for f in gazetteer_manifest.sig manifest_public_key.pem; do
    cp -p "$SRC/$f" "$DST/$f"
    cmp -s "$SRC/$f" "$DST/$f" || { echo "ERROR: copy mismatch for $f" >&2; exit 1; }
    echo "reinstalled $f"
done

echo "Signature pair reinstalled into $DST (manifest verified current)."
