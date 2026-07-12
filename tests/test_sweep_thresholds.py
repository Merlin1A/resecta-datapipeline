"""Tests for the Phase 3b threshold sweep."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from calibration_fixtures import make_score_dump, make_softmax_dump
from resecta_data.classifier import (
    build_fit_temperature,
    build_sweep_thresholds,
    finalize_sweep_thresholds,
)
from resecta_data.classifier.sweep_thresholds import (
    _CATEGORIES,
    _DETECTOR_ACHIEVABLE_MAX,
    _FEASIBILITY_MARGIN,
    _PRESET_NAMES,
    _clamp_to_feasible,
    _compose_posterior_with_mode,
    _select_for_category,
)
from resecta_data.cli import main as cli_main
from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.mechanism_language import assert_safe
from resecta_data.common.schema import validate_file
from resecta_data.corpus import build_g8_corpus

_SCHEMAS = Path(__file__).parent.parent / "schemas"
_MINI_COUNTS = {
    "court": 10,
    "medical": 10,
    "financial": 10,
    "foia": 10,
    "generic": 10,
}


def _setup(tmp_path: Path) -> tuple[Path, Path, Path]:
    corpus = build_g8_corpus(CANONICAL_SEED, counts=_MINI_COUNTS)
    corpus_path = tmp_path / "g8_corpus.json"
    dump_canonical_json(corpus, corpus_path)

    softmax_path = tmp_path / "softmax_dump.json"
    dump_canonical_json(make_softmax_dump(corpus), softmax_path)

    score_path = tmp_path / "score_dump.json"
    dump_canonical_json(make_score_dump(corpus), score_path)

    temperature = build_fit_temperature(
        CANONICAL_SEED,
        softmax_dump_path=softmax_path,
        corpus_path=corpus_path,
        schemas_dir=_SCHEMAS,
    )
    temp_path = tmp_path / "doctype_temperature.json"
    dump_canonical_json(temperature, temp_path)

    return score_path, temp_path, corpus_path


def test_schema_validates(tmp_build_dir: Path) -> None:
    score_path, temp_path, corpus_path = _setup(tmp_build_dir)
    payload = build_sweep_thresholds(
        CANONICAL_SEED,
        score_dump_path=score_path,
        temperature_path=temp_path,
        corpus_path=corpus_path,
        schemas_dir=_SCHEMAS,
    )
    dest = tmp_build_dir / "preset_thresholds.json"
    dump_canonical_json(payload, dest)
    validate_file(dest, _SCHEMAS, "preset_thresholds")


def test_deterministic(tmp_build_dir: Path) -> None:
    score_path, temp_path, corpus_path = _setup(tmp_build_dir)
    a = build_sweep_thresholds(
        CANONICAL_SEED,
        score_dump_path=score_path,
        temperature_path=temp_path,
        corpus_path=corpus_path,
        schemas_dir=_SCHEMAS,
    )
    b = build_sweep_thresholds(
        CANONICAL_SEED,
        score_dump_path=score_path,
        temperature_path=temp_path,
        corpus_path=corpus_path,
        schemas_dir=_SCHEMAS,
    )
    assert a == b


def test_status_is_sweep_raw(tmp_build_dir: Path) -> None:
    """0.4: the sweep emits an inspection artifact; finalize promotes it."""
    score_path, temp_path, corpus_path = _setup(tmp_build_dir)
    payload = build_sweep_thresholds(
        CANONICAL_SEED,
        score_dump_path=score_path,
        temperature_path=temp_path,
        corpus_path=corpus_path,
        schemas_dir=_SCHEMAS,
    )
    assert payload["status"] == "sweep_raw"


def test_all_presets_cover_all_categories(tmp_build_dir: Path) -> None:
    score_path, temp_path, corpus_path = _setup(tmp_build_dir)
    payload = build_sweep_thresholds(
        CANONICAL_SEED,
        score_dump_path=score_path,
        temperature_path=temp_path,
        corpus_path=corpus_path,
        schemas_dir=_SCHEMAS,
    )
    for preset_name in _PRESET_NAMES:
        preset = payload["presets"][preset_name]
        assert set(preset) == set(_CATEGORIES)
        for value in preset.values():
            assert 0.0 <= value <= 1.0


def test_conservative_at_least_as_high_as_aggressive(tmp_build_dir: Path) -> None:
    """With the fixture dumps, signal scores dominate; conservative threshold
    should never be strictly below aggressive threshold for the same category.
    If this ever fails in real runs it is a signal to Jesse that the sweep
    found an inversion — not a hard build failure."""
    score_path, temp_path, corpus_path = _setup(tmp_build_dir)
    payload = build_sweep_thresholds(
        CANONICAL_SEED,
        score_dump_path=score_path,
        temperature_path=temp_path,
        corpus_path=corpus_path,
        schemas_dir=_SCHEMAS,
    )
    for category in _CATEGORIES:
        cons = payload["presets"]["conservative"][category]
        agg = payload["presets"]["aggressive"][category]
        assert cons >= agg, f"{category}: conservative {cons} < aggressive {agg}"


def test_notes_field_is_mechanism_safe(tmp_build_dir: Path) -> None:
    score_path, temp_path, corpus_path = _setup(tmp_build_dir)
    payload = build_sweep_thresholds(
        CANONICAL_SEED,
        score_dump_path=score_path,
        temperature_path=temp_path,
        corpus_path=corpus_path,
        schemas_dir=_SCHEMAS,
    )
    assert_safe(payload["notes"], context="preset_thresholds notes")


def test_categories_match_canonical(tmp_build_dir: Path) -> None:
    score_path, temp_path, corpus_path = _setup(tmp_build_dir)
    payload = build_sweep_thresholds(
        CANONICAL_SEED,
        score_dump_path=score_path,
        temperature_path=temp_path,
        corpus_path=corpus_path,
        schemas_dir=_SCHEMAS,
    )
    assert payload["categories"] == list(_CATEGORIES)


# -----------------------------------------------------------------------------
# 0.1 feasibility clamp
# -----------------------------------------------------------------------------


def test_clamp_above_feasibility_ceiling(tmp_build_dir: Path) -> None:
    """Adversarial: every name candidate scores 0.99 (all matched truth).

    The swept balanced threshold lands near 0.99 via tie-break-to-larger;
    the clamp must pull it to at most 0.85 - 0.05 = 0.80. This is the
    engineered name=0.98 incident shape.
    """
    score_path, temp_path, corpus_path = _setup(tmp_build_dir)
    dump = json.loads(score_path.read_text())
    for doc in dump["documents"]:
        for cand in doc["candidates"]:
            if cand["category"] == "name":
                cand["raw_score"] = 0.99
    dump_canonical_json(dump, score_path)

    payload = build_sweep_thresholds(
        CANONICAL_SEED,
        score_dump_path=score_path,
        temperature_path=temp_path,
        corpus_path=corpus_path,
        schemas_dir=_SCHEMAS,
    )
    ceiling = _DETECTOR_ACHIEVABLE_MAX["name"] - _FEASIBILITY_MARGIN
    for preset_name in _PRESET_NAMES:
        value = payload["presets"][preset_name]["name"]
        assert value <= round(ceiling, 6), (
            f"{preset_name}/name={value} exceeds the feasibility ceiling {ceiling}"
        )


def test_clamp_preserves_below_ceiling() -> None:
    """No over-clamping: a value already below the ceiling passes through."""
    result = _clamp_to_feasible({"balanced": {"name": 0.70}})
    assert result == {"balanced": {"name": 0.70}}


def test_clamp_unknown_category_raises() -> None:
    with pytest.raises(PipelineError, match="_DETECTOR_ACHIEVABLE_MAX"):
        _clamp_to_feasible({"balanced": {"foo": 0.5}})


def test_achievable_max_covers_all_gated_categories() -> None:
    for cat in _CATEGORIES:
        assert cat in _DETECTOR_ACHIEVABLE_MAX, (
            f"Category {cat!r} is in the sweep but missing from "
            f"_DETECTOR_ACHIEVABLE_MAX; add it with a Swift citation."
        )


# -----------------------------------------------------------------------------
# 3.1 prior modes
# -----------------------------------------------------------------------------


def test_prior_mode_fresh_equals_raw() -> None:
    """fresh mode: posterior == raw_score for non-applied priors."""
    for raw in [0.3, 0.5, 0.75, 0.85]:
        result = _compose_posterior_with_mode(raw, 0.995, False, "fresh")
        assert abs(result - raw) < 1e-9, f"fresh mode: {result} != {raw}"


def test_prior_mode_corpus_inflates_high_prior() -> None:
    """corpus mode with prior=0.995: raw=0.75 pushes the posterior near 1."""
    result = _compose_posterior_with_mode(0.75, 0.995, False, "corpus")
    assert result > 0.98, f"corpus mode with high prior: {result} not inflated"


def test_prior_mode_mixed_between_fresh_and_corpus() -> None:
    fresh = _compose_posterior_with_mode(0.75, 0.995, False, "fresh")
    corpus = _compose_posterior_with_mode(0.75, 0.995, False, "corpus")
    mixed = _compose_posterior_with_mode(0.75, 0.995, False, "mixed")
    assert fresh < mixed < corpus


def test_prior_mode_unknown_raises() -> None:
    with pytest.raises(PipelineError, match="prior_mode"):
        _compose_posterior_with_mode(0.5, 0.5, False, "invalid")


# -----------------------------------------------------------------------------
# 3.3 balanced recall floor
# -----------------------------------------------------------------------------


def test_balanced_degenerate_recall_falls_back_to_aggressive() -> None:
    """When no threshold meets the 0.70 recall floor, balanced == aggressive.

    All candidates are false positives against a non-zero truth count, so
    recall is 0 at every grid point. Without the floor, the F1=0 tie breaks
    to the largest threshold — the dead-cutoff path.
    """
    rows = [("doc-1", 0, 4, 0.55, False), ("doc-1", 10, 14, 0.80, False)]
    thresholds, _metrics = _select_for_category(rows, truth_total=5, category="name")
    assert thresholds["balanced"] == thresholds["aggressive"]


# -----------------------------------------------------------------------------
# 0.4 two-stage flow: sweep writes raw only; finalize promotes
# -----------------------------------------------------------------------------


def test_sweep_writes_to_raw_not_shipping(tmp_build_dir: Path) -> None:
    """A sweep run writes _sweep_raw.json and never preset_thresholds.json."""
    score_path, temp_path, corpus_path = _setup(tmp_build_dir)
    runner = CliRunner()
    result = runner.invoke(
        cli_main,
        [
            "build",
            "calibrate",
            "sweep",
            "--score-dump",
            str(score_path),
            "--temperature",
            str(temp_path),
            "--corpus",
            str(corpus_path),
            "--schemas-dir",
            str(_SCHEMAS),
            "--build-dir",
            str(tmp_build_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    shipping = tmp_build_dir / "classifier" / "preset_thresholds.json"
    raw = tmp_build_dir / "classifier" / "preset_thresholds_sweep_raw.json"
    assert not shipping.exists(), "sweep wrote the shipping preset file (clobber)"
    assert raw.exists()
    payload = json.loads(raw.read_text())
    assert payload["status"] == "sweep_raw"
    validate_file(raw, _SCHEMAS, "preset_thresholds")


def test_sweep_does_not_alter_existing_shipping_file(tmp_build_dir: Path) -> None:
    """Clobber simulation: a pre-existing shipping file survives a sweep byte-exact."""
    score_path, temp_path, corpus_path = _setup(tmp_build_dir)
    shipping = tmp_build_dir / "classifier" / "preset_thresholds.json"
    shipping.parent.mkdir(parents=True, exist_ok=True)
    sentinel = b'{"hand-fix": "do not clobber"}\n'
    shipping.write_bytes(sentinel)

    runner = CliRunner()
    result = runner.invoke(
        cli_main,
        [
            "build",
            "calibrate",
            "sweep",
            "--score-dump",
            str(score_path),
            "--temperature",
            str(temp_path),
            "--corpus",
            str(corpus_path),
            "--schemas-dir",
            str(_SCHEMAS),
            "--build-dir",
            str(tmp_build_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    assert shipping.read_bytes() == sentinel, "sweep altered the shipping preset file"


def test_finalize_promotes_sweep_raw(tmp_build_dir: Path) -> None:
    score_path, temp_path, corpus_path = _setup(tmp_build_dir)
    payload = build_sweep_thresholds(
        CANONICAL_SEED,
        score_dump_path=score_path,
        temperature_path=temp_path,
        corpus_path=corpus_path,
        schemas_dir=_SCHEMAS,
    )
    raw_path = tmp_build_dir / "classifier" / "preset_thresholds_sweep_raw.json"
    dump_canonical_json(payload, raw_path)

    promoted = finalize_sweep_thresholds(raw_path, _SCHEMAS)
    assert promoted["status"] == "calibrated"
    assert promoted["presets"] == payload["presets"]

    dest = tmp_build_dir / "classifier" / "preset_thresholds.json"
    dump_canonical_json(promoted, dest)
    validate_file(dest, _SCHEMAS, "preset_thresholds")


def test_finalize_preserves_unswept_shipping_categories(tmp_build_dir: Path) -> None:
    """Finalize carries hand-set rows the sweep does not produce (S4 finding).

    The shipping file holds 8 ungated categories beyond the sweep's canonical
    tuple (search-impl S3 item 1.7); a wholesale promote would delete them.
    """
    score_path, temp_path, corpus_path = _setup(tmp_build_dir)
    payload = build_sweep_thresholds(
        CANONICAL_SEED,
        score_dump_path=score_path,
        temperature_path=temp_path,
        corpus_path=corpus_path,
        schemas_dir=_SCHEMAS,
    )
    raw_path = tmp_build_dir / "classifier" / "preset_thresholds_sweep_raw.json"
    dump_canonical_json(payload, raw_path)

    hand_set = {"ein": 0.55, "email": 0.62}
    shipping = dict(payload)
    shipping["status"] = "placeholder"
    shipping["categories"] = list(payload["categories"]) + sorted(hand_set)
    shipping["presets"] = {
        name: {**vector, **hand_set} for name, vector in payload["presets"].items()
    }
    shipping_path = tmp_build_dir / "classifier" / "preset_thresholds.json"
    dump_canonical_json(shipping, shipping_path)

    promoted = finalize_sweep_thresholds(raw_path, _SCHEMAS, shipping_path=shipping_path)

    assert promoted["status"] == "calibrated"
    assert promoted["categories"] == shipping["categories"]
    for name, vector in promoted["presets"].items():
        for category, threshold in hand_set.items():
            assert vector[category] == threshold, f"{name}/{category} lost the hand-set row"
        for category in payload["categories"]:
            assert vector[category] == payload["presets"][name][category], (
                f"{name}/{category} does not match the swept value"
            )
    assert "carried 2 unswept" in promoted["notes"]
    assert_safe(promoted["notes"], context="test merged notes")

    dest = tmp_build_dir / "classifier" / "preset_thresholds_promoted.json"
    dump_canonical_json(promoted, dest)
    validate_file(dest, _SCHEMAS, "preset_thresholds")


def test_finalize_rejects_shipping_preset_missing_carried_row(tmp_build_dir: Path) -> None:
    """Adversarial: a carried category absent from one preset vector raises."""
    score_path, temp_path, corpus_path = _setup(tmp_build_dir)
    payload = build_sweep_thresholds(
        CANONICAL_SEED,
        score_dump_path=score_path,
        temperature_path=temp_path,
        corpus_path=corpus_path,
        schemas_dir=_SCHEMAS,
    )
    raw_path = tmp_build_dir / "classifier" / "preset_thresholds_sweep_raw.json"
    dump_canonical_json(payload, raw_path)

    shipping = dict(payload)
    shipping["status"] = "placeholder"
    shipping["categories"] = [*payload["categories"], "ein"]
    shipping["presets"] = {
        name: ({**vector, "ein": 0.55} if name != "balanced" else dict(vector))
        for name, vector in payload["presets"].items()
    }
    shipping_path = tmp_build_dir / "classifier" / "preset_thresholds.json"
    dump_canonical_json(shipping, shipping_path)

    with pytest.raises(PipelineError, match="balanced"):
        finalize_sweep_thresholds(raw_path, _SCHEMAS, shipping_path=shipping_path)


def test_finalize_refuses_double_promotion(tmp_build_dir: Path) -> None:
    """Adversarial: finalize on an already-calibrated file raises."""
    score_path, temp_path, corpus_path = _setup(tmp_build_dir)
    payload = build_sweep_thresholds(
        CANONICAL_SEED,
        score_dump_path=score_path,
        temperature_path=temp_path,
        corpus_path=corpus_path,
        schemas_dir=_SCHEMAS,
    )
    payload["status"] = "calibrated"
    finalized_path = tmp_build_dir / "classifier" / "preset_thresholds.json"
    dump_canonical_json(payload, finalized_path)

    with pytest.raises(PipelineError, match="sweep_raw"):
        finalize_sweep_thresholds(finalized_path, _SCHEMAS)


def test_sweep_does_not_clobber_ungated_categories(tmp_build_dir: Path) -> None:
    """The sweep writes only the _CATEGORIES tuple to its output presets.

    The 8 ungated categories (ein, itin, creditCard, email, phone,
    driversLicense, passport, licensePlate) are not in the score dump and
    keep hand-set values in the shipped file. This test pins that the sweep
    output does NOT contain any of the 8 ungated categories so a sweep output
    cannot overwrite hand-set values when merged into the shipping file.

    Per design 02 section 2 WS3 note: sweep_thresholds._CATEGORIES is a
    strict subset of the full preset category list.
    """
    score_path, temp_path, corpus_path = _setup(tmp_build_dir)
    payload = build_sweep_thresholds(
        CANONICAL_SEED,
        score_dump_path=score_path,
        temperature_path=temp_path,
        corpus_path=corpus_path,
        schemas_dir=_SCHEMAS,
    )
    swept_categories = set(payload["categories"])
    ungated = {
        "ein",
        "itin",
        "creditCard",
        "email",
        "phone",
        "driversLicense",
        "passport",
        "licensePlate",
    }
    leaked = swept_categories & ungated
    assert not leaked, (
        f"Sweep output contains ungated categories that should be hand-set: {sorted(leaked)}. "
        "A calibrate-sweep run with these categories present could clobber hand-set values."
    )
    # Each preset vector should also be free of ungated categories.
    for preset_name in _PRESET_NAMES:
        preset_keys = set(payload["presets"][preset_name].keys())
        leaked_keys = preset_keys & ungated
        assert not leaked_keys, (
            f"Preset '{preset_name}' contains ungated categories: {sorted(leaked_keys)}"
        )
