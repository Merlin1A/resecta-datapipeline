# ZIP → SCF crosswalk sources

This directory holds the raw ZIP-to-state sources that feed
`build/gazetteers/zip_scf_states.json`.

## Current contents

- `census_zcta_tract_2020_20260419.txt` — the Census 2020 ZCTA-to-Tract
  Relationship File. The build reads this file when it is present, which it
  is in a checkout.
- `hud_zip_crosswalk_bootstrap_20260416.csv` — a small hand-curated factual
  sample in HUD crosswalk format (32 ZIPs across 16 SCF prefixes and 13
  states), the fallback when the Census file is absent. Its values are
  public-domain USPS postal facts.

Both files have a row in `SOURCES.md`.
