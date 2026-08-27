# Changelog

All notable changes to resecta-data are recorded in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Added

- GitHub Actions: a hermetic pull-request gate (lint, types, tests, pure-code
  builders, schema and hash checks), a weekly full-verify workflow with cached
  sources, and a supply-chain job (pip-audit, OSV-Scanner, SBOM); the dev tools
  are hash-pinned in `requirements-dev.lock`; `make lint` covers `scripts/`.

## 0.1.0 — 2026-06-24

Initial public release.

### Added

- **Build-time data pipeline** producing the assets bundled into the Resecta iOS
  app: surname and given-name Bloom filters, institution / address-component /
  ZIP→SCF gazetteers, negative-context keyword sets, doctype-classifier
  dictionaries and calibration artifacts, a synthetic document corpus,
  structural test vectors (NPI / DEA / SSN), and ReDoS fuzz payloads.
- **Deterministic, zero-network builds.** Artifacts are byte-reproducible from a
  commit; `make verify` runs schema validation, a hash lock, and a determinism
  rebuild.
- **License-provenance tracking.** `SOURCES.md` records every raw dataset's
  license, source URL, retrieval date, and SHA-256; `NOTICE.txt` aggregates the
  attribution flow-through for the app distribution.
- **Fetch-on-demand ParaNames.** The large ParaNames corpus is fetched via
  `scripts/fetch_paranames.sh` rather than committed; builds degrade to a
  bootstrap sample when it is absent (no Git-LFS).
