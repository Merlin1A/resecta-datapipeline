# Security Policy

resecta-data is the build-time pipeline that produces the data assets — name
Bloom filters, gazetteers, classifier dictionaries, calibration vectors, and
test corpora — bundled into the Resecta iOS app. Because those artifacts ship
inside a privacy tool, we take reports of security and supply-chain issues
seriously and welcome good-faith research.

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

- The Resecta iOS app itself — report app issues through that repository's
  `SECURITY.md`.
- Third-party upstream datasets and their hosting — report to the upstream
  project; this repo records each source in `SOURCES.md`.
- Issues requiring a compromised build host or developer machine.

## Supply-chain posture

- **Zero-network builds.** `make build` makes no network calls; only
  `make sources` fetches raw inputs, and it validates each against the SHA-256
  recorded in `SOURCES.md`.
- **Deterministic outputs.** Every artifact is byte-reproducible from a given
  commit; `make verify` rebuilds each artifact and diffs against
  `asset_hashes.lock`.
- **License provenance.** Every raw dataset has a row in `SOURCES.md` with its
  license, retrieval URL, retrieval date, and hash.

See `CONTRIBUTING.md` for the verification workflow and the full list of
plan-sign-off changes.

## Coordinated disclosure

When a reported issue is resolved we publish a changelog entry referencing the
fix and credit the reporter unless anonymity was requested.

Nothing in this policy is legal advice.
