"""Tests for the per-state driver-license-pattern gazetteer (D-13).

Covers schema conformance, determinism, the Disposition §2 + §8 envelope
substitution (11 envelope rows ship the canonical AAMVA M1 envelope with
the per-state format claim retracted), the 3 statute-anchored rows
(NC/TN/UT) carrying state-statute pin citations, the 5-state
``dln_overlap_note`` restriction (AR/HI/ID/LA/MS), the ``_advisory_note``
propagation (F-32 Tier 2 advisory per Disposition delegation 2026-04-26),
and the citation-discipline guard against Skyhigh / MyShn / NTSI lineage
on the four F-39 envelope rows (IL/WA/NV/NJ).

See ``src/resecta_data/gazetteers/dl_patterns/build.py`` for the
builder and ``schemas/dl_patterns.schema.json`` for the shape.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from resecta_data.common.io import dump_canonical_json, load_json
from resecta_data.common.schema import validate_file
from resecta_data.gazetteers.dl_patterns import build as build_dl_patterns

REPO_ROOT = Path(__file__).parent.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"
CANDIDATES_PATH = (
    REPO_ROOT
    / "src"
    / "resecta_data"
    / "gazetteers"
    / "dl_patterns"
    / "sources"
    / "dl_patterns_candidates.json"
)

_CANONICAL_SEED = 20260416

_EXPECTED_ROW_COUNT = 51

_EXPECTED_STATE_CODES = frozenset(
    {
        "AK",
        "AL",
        "AR",
        "AZ",
        "CA",
        "CO",
        "CT",
        "DC",
        "DE",
        "FL",
        "GA",
        "HI",
        "IA",
        "ID",
        "IL",
        "IN",
        "KS",
        "KY",
        "LA",
        "MA",
        "MD",
        "ME",
        "MI",
        "MN",
        "MO",
        "MS",
        "MT",
        "NC",
        "ND",
        "NE",
        "NH",
        "NJ",
        "NM",
        "NV",
        "NY",
        "OH",
        "OK",
        "OR",
        "PA",
        "RI",
        "SC",
        "SD",
        "TN",
        "TX",
        "UT",
        "VA",
        "VT",
        "WA",
        "WI",
        "WV",
        "WY",
    }
)

# Disposition §2 (F-25 envelope): AK MT SC SD WV WY DC.
# Disposition §8 (F-39 envelope): IL WA NV NJ.
_ENVELOPE_STATES = frozenset({"AK", "MT", "SC", "SD", "WV", "WY", "DC", "IL", "WA", "NV", "NJ"})

# Disposition §2 (F-25 statute-anchored).
_STATUTE_ANCHORED_STATES = frozenset({"NC", "TN", "UT"})

# F-39 envelope subset — citations to Skyhigh / MyShn / NTSI are
# specifically retired here per Disposition §11 ingestion-of-record
# discipline.
_F39_ENVELOPE_STATES = frozenset({"IL", "WA", "NV", "NJ"})

# F-35 SSN/DLN two-way ambiguity. ``dln_overlap_note`` is permitted only
# on these 5 jurisdictions (legacy 9-digit DL visually indistinguishable
# from SSN).
_DLN_OVERLAP_STATES = frozenset({"AR", "HI", "ID", "LA", "MS"})

# Statute pins re-verified 2026-04-26 against primary sources (NC:
# ncleg.gov direct fetch; UT: le.utah.gov direct fetch; TN: W-R-5
# evidence_paragraph quotation). Pinned here so a candidates-file
# regen that drops the citation is caught.
_STATUTE_PINS = {
    "NC": "20-7(n)(7)",
    "TN": "55-50-331(b)(1)",
    "UT": "53-3-207(3)(a)(i)",
}

_AGGREGATOR_LINEAGE_TOKENS = ("skyhigh", "myshn", "ntsi")

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@pytest.fixture
def payload() -> dict[str, Any]:
    """Build the shipping payload once per test that needs it."""
    return build_dl_patterns(_CANONICAL_SEED)


def test_build_dl_patterns_shape_and_count(payload: dict[str, Any]) -> None:
    """All 50 states + DC ship; rows are emitted in ascending state_code order."""
    rows = payload["rows"]
    assert len(rows) == _EXPECTED_ROW_COUNT
    codes = [row["state_code"] for row in rows]
    assert codes == sorted(codes)
    assert frozenset(codes) == _EXPECTED_STATE_CODES


def test_build_dl_patterns_envelope_rows(payload: dict[str, Any]) -> None:
    """11 envelope rows ship the canonical AAMVA M1 envelope per legal-ref §8.4."""
    rows_by_code = {row["state_code"]: row for row in payload["rows"]}
    for state in sorted(_ENVELOPE_STATES):
        row = rows_by_code[state]
        assert row["patterns"] == ["^[A-Z0-9]{8,13}$"], (
            f"{state} envelope row carries a non-canonical pattern: {row['patterns']!r}"
        )
        assert row["state_format_claimed"] is False, (
            f"{state} envelope row claims state-format (must be retracted per "
            "legal-ref §8.4 line 1357)"
        )
        assert row["attestation"] == "aamva-envelope"
        assert row["license_posture"] == "aamva-envelope"


def test_build_dl_patterns_statute_anchored_rows(payload: dict[str, Any]) -> None:
    """NC/TN/UT carry the per-state statute pin in ``license_notes``."""
    rows_by_code = {row["state_code"]: row for row in payload["rows"]}
    for state in sorted(_STATUTE_ANCHORED_STATES):
        row = rows_by_code[state]
        assert row["attestation"] == "primary-published-spec", (
            f"{state} statute-anchored row carries non-rank-1 attestation: {row['attestation']!r}"
        )
        assert row["state_format_claimed"] is True
        assert row["license_posture"] == "state-statute-anchored"
        pin = _STATUTE_PINS[state]
        assert pin in row["license_notes"], (
            f"{state} statute-anchored row is missing pin {pin!r} in license_notes; "
            f"got: {row['license_notes']!r}"
        )


def test_build_dl_patterns_advisory_note_propagated(payload: dict[str, Any]) -> None:
    """F-32 Tier 2 advisory rides through the builder verbatim."""
    advisory = payload.get("_advisory_note")
    assert isinstance(advisory, str) and advisory, (
        "_advisory_note missing from shipping payload (F-32 Tier 2 advisory per "
        "Disposition delegation 2026-04-26)"
    )
    candidates = load_json(CANDIDATES_PATH)
    assert payload["_advisory_note"] == candidates["_advisory_note"]


def test_build_dl_patterns_deterministic(tmp_build_dir: Path) -> None:
    """Two consecutive builds produce byte-identical canonical JSON."""
    first = build_dl_patterns(_CANONICAL_SEED)
    second = build_dl_patterns(_CANONICAL_SEED)
    assert first == second

    path_a = tmp_build_dir / "a.json"
    path_b = tmp_build_dir / "b.json"
    dump_canonical_json(first, path_a)
    dump_canonical_json(second, path_b)
    assert path_a.read_bytes() == path_b.read_bytes()


def test_build_dl_patterns_dln_overlap_note_states(payload: dict[str, Any]) -> None:
    """``dln_overlap_note`` appears only on AR/HI/ID/LA/MS rows (F-35 two-way)."""
    actual = {row["state_code"] for row in payload["rows"] if "dln_overlap_note" in row}
    assert actual == _DLN_OVERLAP_STATES, (
        f"dln_overlap_note states diverged from {_DLN_OVERLAP_STATES}: got {actual}"
    )


def test_build_dl_patterns_no_aggregator_cites_on_F39_rows(payload: dict[str, Any]) -> None:
    """IL/WA/NV/NJ rows must not cite Skyhigh / MyShn / NTSI per Disposition §11."""
    rows_by_code = {row["state_code"]: row for row in payload["rows"]}
    for state in sorted(_F39_ENVELOPE_STATES):
        row = rows_by_code[state]
        haystack = (f"{row.get('source_url', '')} {row.get('license_notes', '')}").lower()
        for token in _AGGREGATOR_LINEAGE_TOKENS:
            assert token not in haystack, (
                f"{state} F-39 envelope row leaks aggregator-lineage token "
                f"{token!r} (Disposition §11 ingestion-of-record discipline)"
            )


def test_schema_conformance(payload: dict[str, Any], tmp_build_dir: Path) -> None:
    """Payload validates against schemas/dl_patterns.schema.json."""
    dest = tmp_build_dir / "gazetteers" / "dl_patterns.json"
    dump_canonical_json(payload, dest)
    validate_file(dest, SCHEMAS_DIR, "dl_patterns")


def test_all_patterns_anchored(payload: dict[str, Any]) -> None:
    """Every regex in every row is anchored with ^ and $."""
    for row in payload["rows"]:
        for pattern in row["patterns"]:
            assert pattern.startswith("^"), (
                f"{row['state_code']} pattern {pattern!r} is not ^-anchored"
            )
            assert pattern.endswith("$"), (
                f"{row['state_code']} pattern {pattern!r} is not $-anchored"
            )


def test_source_verified_date_iso_format(payload: dict[str, Any]) -> None:
    """Every source_verified_date is YYYY-MM-DD."""
    for row in payload["rows"]:
        date = row["source_verified_date"]
        assert _ISO_DATE.match(date), (
            f"{row['state_code']} source_verified_date {date!r} is not ISO YYYY-MM-DD"
        )


def test_no_wall_clock_leak(payload: dict[str, Any]) -> None:
    """generated_date is lifted from the candidates file, not from wall-clock."""
    candidates = load_json(CANDIDATES_PATH)
    assert payload["generated_date"] == candidates["generated_date"]
    # Candidates file is authored 2026-04-26 (D-13 session 1); pin the
    # expectation so a wall-clock regression is caught even if the
    # candidates file drifts.
    assert payload["generated_date"] == "2026-04-26"


def test_seed_recorded(payload: dict[str, Any]) -> None:
    """The CLI-supplied seed is recorded verbatim for reproducibility."""
    assert payload["seed"] == _CANONICAL_SEED


def test_license_posture_distribution(payload: dict[str, Any]) -> None:
    """11 state-work + 26 permissive-OSS-MIT + 3 state-statute-anchored + 11 envelope = 51."""
    postures = [row["license_posture"] for row in payload["rows"]]
    assert postures.count("state-work") == 11
    assert postures.count("permissive-OSS-MIT") == 26
    assert postures.count("state-statute-anchored") == 3
    assert postures.count("aamva-envelope") == 11
    # Disposition §2 + legal-ref §8.4 line 1371 retired the label.
    assert postures.count("needs-legal-review") == 0
