# ZIP → SCF crosswalk sources

This directory holds the raw ZIP-to-state crosswalk sources that feed
`build/gazetteers/zip_scf_states.json`.

## Current contents

- `hud_zip_crosswalk_bootstrap_20260416.csv` — a small hand-curated
  factual sample (32 ZIPs across 14 SCF prefixes and 13 states). Ships with
  Phase 1 so the build pipeline is end-to-end testable without a network
  fetch. Values are public-domain USPS postal facts.
