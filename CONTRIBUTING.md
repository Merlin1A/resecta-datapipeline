# Contributing to resecta-data

Thanks for your interest. This is the build-time data pipeline for the Resecta
iOS app: it produces name Bloom filters, gazetteers, classifier dictionaries,
calibration vectors, and test corpora, then copies them into the engine's
`Resources/` tree. This document covers the workflow and the invariants that
keep generated artifacts license-clean and reproducible, including the hard
stops (license judgments, dependency additions, device-validated thresholds)
that require maintainer sign-off — see below for the full list.

## Setup

Python 3.12 is required.

```sh
scripts/bootstrap.sh     # create .venv, install pinned deps from requirements.lock
```

On macOS, install GNU Make 4.x (`brew install make`) and invoke targets as
`gmake` — stock `/usr/bin/make` is 3.81 and the `verify` recipe needs make ≥ 4.

## Build and verify

```sh
gmake build      # generate all artifacts into build/ (no network)
gmake verify     # ruff, mypy, pytest, schema validation, hash + determinism check
gmake all        # build + verify + install-assets
```

`scripts/ci_verify.sh` runs the same sequence for a local smoke check. There is
no remote CI runner; `gmake verify` (or `ci_verify.sh`) is the gate before any
change ships.

## Invariants

These are non-negotiable; the test suite enforces them.

- **Determinism.** Every artifact is byte-identical across machines and
  rebuilds. Seeds are explicit (canonical seed `20260416`); no wall-clock
  content; artifact JSON is written only through
  `common/io.py::dump_canonical_json`. Every new builder ships a determinism
  test.
- **License provenance.** Every file under `src/resecta_data/*/sources/` needs a
  row in `SOURCES.md` (license, URL, retrieval date, SHA-256). Adding a dataset
  whose license is not on `common/licensing.py`'s `ALLOWLIST` is a hard stop.
- **Zero-network builds.** `make build` never reaches the network; only
  `make sources` fetches, and it validates hashes. ParaNames is fetched on
  demand via `scripts/fetch_paranames.sh` (not committed; no Git-LFS), and the
  build degrades to the bootstrap sample when the full corpus is absent.
- **Mechanism-description language.** Any human-readable string this pipeline
  emits (docstrings, JSON `description` fields, `NOTICE.txt` rows, error
  messages) describes the mechanism, not an outcome. The banned-phrase list is
  in `common/mechanism_language.py`.

## Commit format and sign-off

Commit subjects describe the mechanism a change introduces (verbs like `add`,
`extend`, `seed`, `amend`). Every commit needs a DCO sign-off under the
[Developer Certificate of Origin 1.1](https://developercertificate.org/):

```sh
git commit -s -m "add <thing>"
```

If `asset_hashes.lock` changes, the commit body must explain why — an
unexplained hash change usually signals a determinism defect, not a reason to
regenerate.

## Hard stops (maintainer sign-off required)

License-compatibility judgments, negative-context keyword curation,
device-validated thresholds, on-device calibration artifacts, dependency
additions, and any release or lockfile decision. If a change crosses one of
these, open a draft PR with a plan and stop.

## Security

Vulnerability disclosure goes through [`SECURITY.md`](./SECURITY.md), not the
public issue tracker.
