"""Tests for the preset-threshold candidates builder."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from resecta_data.classifier import build_preset_thresholds
from resecta_data.classifier.preset_thresholds import CATEGORIES
from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.mechanism_language import assert_safe
from resecta_data.common.schema import validate_file

_SCHEMAS = Path(__file__).parent.parent / "schemas"
_EXPECTED_PRESETS = ("conservative", "balanced", "aggressive")


def test_schema_validates(tmp_build_dir: Path) -> None:
    payload = build_preset_thresholds(CANONICAL_SEED)
    dest = tmp_build_dir / "preset_thresholds_candidates.json"
    dump_canonical_json(payload, dest)
    validate_file(dest, _SCHEMAS, "preset_thresholds")


def test_deterministic() -> None:
    assert build_preset_thresholds(CANONICAL_SEED) == build_preset_thresholds(CANONICAL_SEED)


def test_status_is_placeholder() -> None:
    payload = build_preset_thresholds(CANONICAL_SEED)
    assert payload["status"] == "placeholder"


def test_categories_match_canonical() -> None:
    payload = build_preset_thresholds(CANONICAL_SEED)
    assert payload["categories"] == list(CATEGORIES)


def test_all_presets_cover_all_categories() -> None:
    payload = build_preset_thresholds(CANONICAL_SEED)
    for preset_name in _EXPECTED_PRESETS:
        preset = payload["presets"][preset_name]
        assert set(preset) == set(CATEGORIES)
        for value in preset.values():
            assert 0 <= value <= 1


def test_conservative_ssn_higher_than_aggressive_ssn() -> None:
    """Preset posture invariant: conservative thresholds >= aggressive thresholds."""
    payload = build_preset_thresholds(CANONICAL_SEED)
    for category in CATEGORIES:
        assert (
            payload["presets"]["conservative"][category]
            > payload["presets"]["aggressive"][category]
        ), f"{category}: conservative not > aggressive"


def test_notes_field_is_mechanism_safe() -> None:
    """Regression: the notes field must pass mechanism-language checks."""
    payload = build_preset_thresholds(CANONICAL_SEED)
    assert_safe(payload["notes"], context="preset_thresholds notes")


# -----------------------------------------------------------------------------
# 0.3: artifact-level pins on the SHIPPED preset file
# -----------------------------------------------------------------------------

_SHIPPED_PRESET_PATH = (
    Path(__file__).parent.parent / "build" / "classifier" / "preset_thresholds.json"
)


@pytest.mark.skipif(
    not _SHIPPED_PRESET_PATH.exists(),
    reason="shipped preset absent (fresh tree; produced by the calibrate flow)",
)
def test_conservative_address_not_above_regex_max() -> None:
    """Conservative address threshold must be below the regex-path max (0.70).

    This pins the fix from item 0.3 so a future sweep can't reinstate the
    dead value. The clamp in sweep_thresholds._clamp_to_feasible provides
    the same guarantee programmatically; this test pins it at the artifact
    level too.
    """
    data = json.loads(_SHIPPED_PRESET_PATH.read_text())
    assert data["presets"]["conservative"]["address"] < 0.70, (
        "conservative.address >= 0.70 (regex-path max); address detection "
        "is designed to be unreachable on the conservative preset."
    )


@pytest.mark.skipif(
    not _SHIPPED_PRESET_PATH.exists(),
    reason="shipped preset absent (fresh tree; produced by the calibrate flow)",
)
def test_conservative_account_keeps_feasibility_margin() -> None:
    """Conservative account threshold must sit at or below max (0.75) - 0.05."""
    data = json.loads(_SHIPPED_PRESET_PATH.read_text())
    assert data["presets"]["conservative"]["account"] <= 0.70, (
        "conservative.account > 0.70 leaves no margin under the account "
        "detector's 0.75 ceiling; only a perfect context-boosted match "
        "could pass the confidence gate."
    )


# -----------------------------------------------------------------------------
# 8 previously ungated categories
# -----------------------------------------------------------------------------

_ALL_17_CATEGORIES = frozenset(
    {
        "ssn",
        "npi",
        "dea",
        "dob",
        "address",
        "account",
        "mrn",
        "name",
        "routingNumber",
        "ein",
        "itin",
        "creditCard",
        "email",
        "phone",
        "driversLicense",
        "passport",
        "licensePlate",
    }
)


@pytest.mark.skipif(
    not _SHIPPED_PRESET_PATH.exists(),
    reason="shipped preset absent (fresh tree; produced by the calibrate flow)",
)
def test_ungated_categories_in_schema() -> None:
    """All 17 categories appear in the shipped preset file's categories array
    and each preset vector carries all 17 keys.

    The 8 ungated categories (ein, itin, creditCard, email, phone,
    driversLicense, passport, licensePlate) must be present alongside the
    9 previously gated categories so the Swift confidence gate can read them.
    """
    data = json.loads(_SHIPPED_PRESET_PATH.read_text())
    shipped_categories = set(data["categories"])
    missing_in_categories = _ALL_17_CATEGORIES - shipped_categories
    assert not missing_in_categories, (
        f"Missing from 'categories' array: {sorted(missing_in_categories)}"
    )
    for preset_name in ("conservative", "balanced", "aggressive"):
        preset_keys = set(data["presets"][preset_name].keys())
        missing_in_preset = _ALL_17_CATEGORIES - preset_keys
        assert not missing_in_preset, (
            f"Preset '{preset_name}' missing keys: {sorted(missing_in_preset)}"
        )
