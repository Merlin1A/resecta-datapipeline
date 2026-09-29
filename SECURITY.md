# Security Policy

resecta-data is the build-time pipeline that produces the detection data
shipped inside the Resecta iOS app (name Bloom filters, gazetteers and pattern
tables, classifier assets, the rule catalog) and the engine's test fixtures
(test vectors, fuzz payloads, the synthetic G8 corpus). Because the shipped
artifacts sit inside a privacy tool, we take reports of security and
supply-chain issues seriously and welcome good-faith research.

## Reporting a vulnerability

Please report suspected issues through either of these channels:

- **Email:** `security@resecta.app`.
- **GitHub Security Advisories:** open a private advisory on this repository's
  *Security* tab.

Please **do not** open public issues for security reports until the issue has
been addressed and coordinated disclosure has been agreed upon.

### What to include

- A description of the issue and its impact on the generated artifacts or the
  app that consumes them.
- Steps to reproduce, including the commit, Python version, and build target.
- Any proof-of-concept artifacts, logs, or diffs.
- Your preferred credit line (or a request to remain anonymous).

### What to expect

- **Acknowledgement:** within 7 days of receipt.
- **Triage update:** within 30 days of acknowledgement.
- **Disclosure coordination:** we request a 90-day embargo from the date of
  first report and work in good faith to ship a fix within that window.

## Scope

**In scope:**

- The Python tooling in this repository (`src/`, `scripts/`, `Makefile`,
  `schemas/`, lock and config files).
- The generated data artifacts and their determinism and license-provenance
  properties (`SOURCES.md`, `NOTICE.txt`, `asset_hashes.lock`).
- Any issue that could cause a mislicensed, poisoned, or non-deterministic
  artifact to be produced and bundled downstream.

**Out of scope:**

- The Resecta iOS app itself — report app issues through
  [that repository's `SECURITY.md`](https://github.com/Merlin1A/resecta/blob/main/SECURITY.md).
- Third-party upstream datasets and their hosting — report to the upstream
  project; this repo records each source in `SOURCES.md`.
- Issues requiring a compromised build host or developer machine — except a
  suspected exposure of the manifest-signing key
  ([`KEY-MANAGEMENT.md`](./KEY-MANAGEMENT.md)), which is in scope.

## Supply-chain posture

- **No builder makes a network call.** Raw inputs are fetched by hand with
  `scripts/fetch_*.sh`, which record each file's SHA-256 in `SOURCES.md` and
  refuse a later fetch whose bytes differ; ParaNames is checked against its
  pinned row.
- **Hash-locked, deterministic outputs.** `make verify` checks every in-band
  artifact against `asset_hashes.lock` and rebuilds them to confirm
  byte-identical output.
- **A signed shipped manifest.** The detection data the app loads is listed in
  an Ed25519-signed manifest; what the signature proves and how the key is held
  are in [`KEY-MANAGEMENT.md`](./KEY-MANAGEMENT.md).

Every third-party raw file under `src/resecta_data/**/sources/` has a row in
`SOURCES.md` (license, retrieval URL, retrieval date, SHA-256). The checks a
change must pass and the plan-sign-off changes are in `CONTRIBUTING.md`.

## Coordinated disclosure

When a reported issue is resolved we publish a changelog entry referencing the
fix and credit the reporter unless anonymity was requested.

Nothing in this policy is legal advice.
