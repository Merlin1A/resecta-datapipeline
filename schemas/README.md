# JSON Schemas

One file per artifact type. Filename convention: `<artifact_name>.schema.json`.

Every schema uses JSON Schema Draft 2020-12 and includes:

- `$schema: "https://json-schema.org/draft/2020-12/schema"`
- `$id` identifying the schema
- `title` and `description` for human readers
- `type`, `required`, `properties` as appropriate
- `additionalProperties: false` at every object level (strict by default)

Schemas are consumed by `resecta_data.common.schema.validate_file`. Routing
from an artifact path in `build/` to a schema name lives in
`src/resecta_data/cli.py::SCHEMA_ROUTES`.

## Phase 0

The placeholder `_example.schema.json` serves as a template and is used by the
test suite to exercise the validator.

## Phase 1 (landed)

- `npi_test_vectors.schema.json` — CMS Luhn-with-80840-prefix checksum vectors
- `dea_test_vectors.schema.json` — DEA position-weighted checksum vectors
- `ssn_structural_vectors.schema.json` — SSA structural rejection vectors (mirrors Swift SSNStructuralValidator)
- `zip_scf_states.schema.json` — USPS SCF-prefix → state table with 5-digit overrides
- `redos_payloads.schema.json` — attacker-shaped strings for the Swift-side regex fuzz harness
- `adversarial_patterns.schema.json` — detector false-positive and classifier-stuffing fragments

## Phase 2 (landed)

- `gazetteer_manifest.schema.json` — manifest for the dual-Bloom-filter bundle (surnames + given-names); the .bloom binaries themselves use the RSBF header format (see `src/resecta_data/bloom/spec.py`) rather than a JSON schema
- `negative_context.schema.json` — candidate keywords with (category_scope × doctype_scope) routing; ships to build/ only, install is hand-review-gated
- `demographic_coverage.schema.json` — per-filter bucket breakdown across five Census race/ethnicity groups; baseline for the Phase 4 G2 parity-gap CI gate

## Phase 3

Phase 3 lands:
- `doctype_keywords.schema.json`
- `preset_thresholds.schema.json`
- `doctype_temperature.schema.json`
- `g8_manifest.schema.json`
- `corpus_stats.schema.json`

## Conventions

- All `description` strings comply with mechanism-description language rules.
  The `assert_safe` pass runs over every schema in CI.
- Version fields are integers. Bump when a consumer needs to distinguish
  formats. Swift-side decoders must check the version field on load.
- Where a field is optional, say so with an explicit `"description"` rather
  than omitting it — the schema doubles as documentation for Swift
  implementers.
