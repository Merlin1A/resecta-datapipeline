"""Cross-check the built manifest against the .bloom file headers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from resecta_data.bloom.manifest import (
    FilterBuildResult,
    build_cutover_diff,
    build_manifest,
)
from resecta_data.bloom.packer import unpack
from resecta_data.bloom.spec import (
    GIVEN_NAME_FILTER_FILE,
    HASH_ALGORITHM,
    SURNAME_FILTER_FILE,
)
from resecta_data.common.io import load_json
from resecta_data.common.schema import load_schema, validate

_SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"

_BUILD = Path(__file__).parent.parent / "build"
_MANIFEST = _BUILD / "gazetteers" / "gazetteer_manifest.json"


def _load_manifest() -> dict[str, Any]:
    if not _MANIFEST.is_file():
        pytest.skip("build/gazetteers/gazetteer_manifest.json not present; run `make bloom`")
    payload: dict[str, Any] = load_json(_MANIFEST)
    return payload


def test_manifest_hash_algorithm_matches_constant() -> None:
    manifest = _load_manifest()
    assert manifest["hashAlgorithm"] == HASH_ALGORITHM


def test_manifest_filter_entries_cross_check_bloom_headers() -> None:
    manifest = _load_manifest()
    by_name = {entry["name"]: entry for entry in manifest["filters"]}

    for name, filename in [
        ("surnames", SURNAME_FILTER_FILE),
        ("given-names", GIVEN_NAME_FILTER_FILE),
    ]:
        bloom_path = _BUILD / "gazetteers" / filename
        header, _ = unpack(bloom_path.read_bytes())
        entry = by_name[name]
        assert entry["m"] == header.m, name
        assert entry["k"] == header.k, name
        assert entry["n"] == header.row_count, name
        assert manifest["seed"] == header.seed, name


def test_manifest_paranames_full_entry_validates_against_schema() -> None:
    """Confirm `paranames_full` flows through as a `sources` string per M34.

    M34 resolved that the manifest schema's `filters[*].sources` is an
    array-of-strings, so adding a new source identifier appends cleanly
    without a schema-version bump. C1 wires `paranames_full` into both the
    surnames and given-names ingest paths; this test pins the contract by
    building a manifest with the new source ID and validating it.
    """
    schema = load_schema(_SCHEMAS_DIR, "gazetteer_manifest")
    manifest = build_manifest(
        [
            FilterBuildResult(
                name="surnames",
                type_="surname",
                n=3,
                m=64,
                k=4,
                fpr_target=0.01,
                sources=("census_surnames", "paranames", "paranames_full"),
            ),
            FilterBuildResult(
                name="given-names",
                type_="givenName",
                n=2,
                m=64,
                k=4,
                fpr_target=0.01,
                sources=("paranames", "paranames_full", "ssa_given_names"),
            ),
        ],
        seed=20260416,
        built_at="2026-04-16T00:00:00Z",
    )
    validate(manifest, schema, context="gazetteer_manifest")
    surnames_entry = next(f for f in manifest["filters"] if f["name"] == "surnames")
    given_entry = next(f for f in manifest["filters"] if f["name"] == "given-names")
    assert "paranames_full" in surnames_entry["sources"]
    assert "paranames_full" in given_entry["sources"]


def test_manifest_census_spanish_full_entry_validates_against_schema() -> None:
    """Confirm `census_spanish_full` flows through as a `sources` string.

    Same bc-OK basis as M34 (array-of-strings manifest schema). C2 wires
    `census_spanish_full` into the surnames path only — the Census Spanish
    Surname List has no given-name equivalent — so this test only asserts
    the surnames filter carries the new source identifier.
    """
    schema = load_schema(_SCHEMAS_DIR, "gazetteer_manifest")
    manifest = build_manifest(
        [
            FilterBuildResult(
                name="surnames",
                type_="surname",
                n=4,
                m=64,
                k=4,
                fpr_target=0.01,
                sources=(
                    "census_surnames",
                    "census_spanish",
                    "census_spanish_full",
                    "paranames_full",
                ),
            ),
            FilterBuildResult(
                name="given-names",
                type_="givenName",
                n=2,
                m=64,
                k=4,
                fpr_target=0.01,
                sources=("paranames_full", "ssa_given_names"),
            ),
        ],
        seed=20260416,
        built_at="2026-04-16T00:00:00Z",
    )
    validate(manifest, schema, context="gazetteer_manifest")
    surnames_entry = next(f for f in manifest["filters"] if f["name"] == "surnames")
    given_entry = next(f for f in manifest["filters"] if f["name"] == "given-names")
    assert "census_spanish_full" in surnames_entry["sources"]
    assert "census_spanish_full" not in given_entry["sources"]


def _sample_filters() -> list[FilterBuildResult]:
    return [
        FilterBuildResult(
            name="surnames",
            type_="surname",
            n=3,
            m=64,
            k=4,
            fpr_target=0.01,
            sources=("census_surnames", "paranames"),
        ),
        FilterBuildResult(
            name="given-names",
            type_="givenName",
            n=2,
            m=64,
            k=4,
            fpr_target=0.01,
            sources=("paranames", "ssa_given_names"),
        ),
    ]


def test_cutover_diff_schema() -> None:
    """Validate the verification-posture cutover-diff against the shared schema.

    cc-derive-rebuild S3 reuses ``schemas/cutover_diff.schema.json`` (S1's
    generic legacy→rebuild schema) for the name-filter manifest. Under
    verification-posture (no legacy variant retired in this rebuild) the
    diff is empty by construction.
    """
    schema = load_schema(_SCHEMAS_DIR, "cutover_diff")
    diff = build_cutover_diff(_sample_filters())
    validate(diff, schema, context="cutover_diff")
    assert diff["artifact"] == "gazetteers/gazetteer_manifest.json"
    assert diff["summary"]["legacy_only_count"] == 0
    assert diff["summary"]["rebuild_only_count"] == 0
    assert diff["summary"]["keyed_diff_count"] == 0
    assert diff["legacy_only"] == []
    assert diff["rebuild_only"] == []
    assert diff["keyed_diff"] == []


def test_cutover_diff_determinism() -> None:
    """Two consecutive ``build_cutover_diff`` calls return byte-identical dicts."""
    first = build_cutover_diff(_sample_filters())
    second = build_cutover_diff(_sample_filters())
    assert first == second
