# ZIP → SCF crosswalk sources

This directory holds the raw USPS ZIP-to-state crosswalk that feeds
`build/gazetteers/zip_scf_states.json`.

## Current contents

- `hud_zip_crosswalk_bootstrap_20260416.csv` — a small hand-curated
  factual sample (32 ZIPs across 14 SCF prefixes and 13 states). Ships with
  Phase 1 so the build pipeline is end-to-end testable without a network
  fetch. Values are public-domain USPS postal facts.

## Replacing the bootstrap with the full HUD dataset

Phase 3 needs the full HUD USPS ZIP Code Crosswalk (~42k rows) so
`AddressDetector.swift` has complete coverage. When you are ready to upgrade:

1. Run `scripts/fetch_hud_zip_crosswalk.sh <YYYYQn>` to download the
   quarterly snapshot.
2. Compute its SHA-256 and add a new row to `../../../../../SOURCES.md`.
3. Point `Makefile`'s `ZIP_SCF_SOURCE` variable at the new file.
4. Run `make zip-scf && make verify` and commit the updated
   `asset_hashes.lock`.
5. Leave the bootstrap file in place for test fixtures; do not edit it
   (CLAUDE.md §2.7 — source files are immutable once committed).
