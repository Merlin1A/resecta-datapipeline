"""Tests for the in-band context-scorer emitter.

``status="placeholder"`` (the default, B03) stays an input-independent
all-identity payload; ``status="candidates"`` (B04) runs the in-band
Newton-IRLS+L2 fit over the committed File-5 fire dump and is exercised here
against the real committed dump (when present) plus synthetic dumps that pin the
single-class→identity rule, the train-NLL gate, and the D-5 family-key guard.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from resecta_data.classifier import build_context_scorer
from resecta_data.classifier.context_scorer import FAMILIES, FEATURE_ORDER
from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.mechanism_language import assert_safe
from resecta_data.common.schema import validate_file

_REPO = Path(__file__).parent.parent
_SCHEMAS = _REPO / "schemas"
_REAL_CORPUS = _REPO / "build" / "corpus" / "g8_corpus.json"
_REAL_FIRE_DUMP = _REPO / "build" / "corpus" / "g8_fire_features.json"

# Reasons to skip the candidates tests that need the committed build inputs
# (present after `make build` / `make corpus`; absent on a bare checkout).
_NEED_REAL_INPUTS = pytest.mark.skipif(
    not (_REAL_CORPUS.is_file() and _REAL_FIRE_DUMP.is_file()),
    reason="needs build/corpus/{g8_corpus,g8_fire_features}.json (run `make build`)",
)
_NEED_REAL_CORPUS = pytest.mark.skipif(
    not _REAL_CORPUS.is_file(),
    reason="needs build/corpus/g8_corpus.json (run `make corpus`)",
)


def _fire(family: str, gt_class: str, features: list[float], start: int) -> dict[str, Any]:
    """One synthetic File-5 fire row (offset-only, no text)."""
    return {
        "family": family,
        "cell_category_key": "medicalrecord" if family == "mrn" else family,
        "doctype": "generic",
        "bucket": "white",
        "start": start,
        "end": start + 9,
        "raw": 0.6,
        "gt_class": gt_class,
        "surfaced": True,
        "features": list(features),
    }


def _write_dump(path: Path, fires: list[dict[str, Any]]) -> None:
    """Write a minimal valid File-5 dump (canonical feature_order + fires)."""
    payload = {
        "schema_version": 1,
        "generated_by": "test.fireFeatures",
        "feature_order": list(FEATURE_ORDER),
        "fires": fires,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _candidates(fire_dump: Path) -> dict[str, Any]:
    """Build the candidates payload against ``fire_dump`` + the real corpus."""
    return build_context_scorer(
        CANONICAL_SEED,
        status="candidates",
        corpus_path=_REAL_CORPUS,
        fire_features_path=fire_dump,
        schemas_dir=_SCHEMAS,
    )


def test_schema_validates(tmp_build_dir: Path) -> None:
    payload = build_context_scorer(CANONICAL_SEED)
    dest = tmp_build_dir / "context_scorer.json"
    dump_canonical_json(payload, dest)
    validate_file(dest, _SCHEMAS, "context_scorer")


def test_deterministic() -> None:
    assert build_context_scorer(CANONICAL_SEED) == build_context_scorer(CANONICAL_SEED)


def test_status_is_placeholder() -> None:
    assert build_context_scorer(CANONICAL_SEED)["status"] == "placeholder"


def test_feature_order_is_the_canonical_13() -> None:
    payload = build_context_scorer(CANONICAL_SEED)
    assert payload["feature_order"] == list(FEATURE_ORDER)
    assert len(payload["feature_order"]) == 13


def test_families_are_exactly_the_five() -> None:
    fams = build_context_scorer(CANONICAL_SEED)["families"]
    assert set(fams) == set(FAMILIES) == {"account", "phone", "mrn", "ein", "itin"}


def test_placeholder_is_identity_for_every_family() -> None:
    """Every family is the all-zero identity block: w_family 0, zero weights,
    zero means, unit scales — so the on-device additive term is 0 (byte-identity)."""
    fams = build_context_scorer(CANONICAL_SEED)["families"]
    for name, block in fams.items():
        assert block["w_family"] == 0.0, name
        assert block["bias"] == 0.0, name
        assert block["weights"] == [0.0] * 13, name
        assert block["feature_means"] == [0.0] * 13, name
        assert block["feature_scales"] == [1.0] * 13, name


def test_output_is_input_independent_of_seed() -> None:
    """The placeholder weights/feature_order/families are a pure function of the
    contract — only the recorded `seed` provenance field varies."""
    a = build_context_scorer(20260416)
    b = build_context_scorer(7)
    assert a["families"] == b["families"]
    assert a["feature_order"] == b["feature_order"]
    assert a["seed"] == 20260416 and b["seed"] == 7


def test_emitted_strings_are_mechanism_clean() -> None:
    payload = build_context_scorer(CANONICAL_SEED)
    assert_safe(payload["generated_by"], context="generated_by")
    assert_safe(payload["notes"], context="notes")


def test_negative_seed_raises() -> None:
    with pytest.raises(PipelineError):
        build_context_scorer(-1)


def test_unknown_status_raises() -> None:
    with pytest.raises(PipelineError):
        build_context_scorer(CANONICAL_SEED, status="bogus")


def test_candidates_requires_inputs() -> None:
    """status='candidates' without the corpus/dump/schemas inputs raises."""
    with pytest.raises(PipelineError):
        build_context_scorer(CANONICAL_SEED, status="candidates")


# -----------------------------------------------------------------------------
# B04 — the in-band candidates fit
# -----------------------------------------------------------------------------


@_NEED_REAL_INPUTS
def test_candidates_against_real_dump_status_and_schema(tmp_build_dir: Path) -> None:
    payload = _candidates(_REAL_FIRE_DUMP)
    assert payload["status"] == "candidates"
    assert payload["feature_order"] == list(FEATURE_ORDER)
    assert set(payload["families"]) == set(FAMILIES)
    dest = tmp_build_dir / "context_scorer_candidates.json"
    dump_canonical_json(payload, dest)
    validate_file(dest, _SCHEMAS, "context_scorer")


@_NEED_REAL_INPUTS
def test_candidates_g8_per_family_verdict() -> None:
    """On G8: account/phone are two-class → fittable (w=1); mrn/ein/itin are
    single-class → identity (w=0). Support counts equal the dump's class counts."""
    fams = _candidates(_REAL_FIRE_DUMP)["families"]
    assert fams["account"]["w_family"] == 1.0
    assert fams["phone"]["w_family"] == 1.0
    for degenerate in ("mrn", "ein", "itin"):
        block = fams[degenerate]
        assert block["w_family"] == 0.0, degenerate
        assert block["weights"] == [0.0] * 13, degenerate
        assert block["feature_means"] == [0.0] * 13, degenerate
        assert block["feature_scales"] == [1.0] * 13, degenerate
    # A fittable family carries non-trivial standardization (some scale > floor).
    assert any(s > 1.0e-6 for s in fams["phone"]["feature_scales"])
    # Support is the (redact, suppress) class split.
    assert fams["account"]["support"] == {"redact": 200, "suppress": 200}
    assert fams["phone"]["support"] == {"redact": 999, "suppress": 615}
    assert fams["itin"]["support"] == {"redact": 0, "suppress": 50}


@_NEED_REAL_INPUTS
def test_candidates_active_families_beat_the_train_nll_gate() -> None:
    """Every family shipped with w_family != 0 must, by construction, have
    strictly beaten its base-rate NLL — the build() backstop would have raised
    otherwise, so reaching here with active families is the assertion."""
    fams = _candidates(_REAL_FIRE_DUMP)["families"]
    active = [f for f, b in fams.items() if b["w_family"] != 0.0]
    assert active == ["account", "phone"]


@_NEED_REAL_INPUTS
def test_candidates_are_byte_deterministic(tmp_build_dir: Path) -> None:
    a = _candidates(_REAL_FIRE_DUMP)
    b = _candidates(_REAL_FIRE_DUMP)
    path_a = tmp_build_dir / "a.json"
    path_b = tmp_build_dir / "b.json"
    dump_canonical_json(a, path_a)
    dump_canonical_json(b, path_b)
    assert path_a.read_bytes() == path_b.read_bytes()


@_NEED_REAL_INPUTS
def test_final_placeholder_unchanged_when_candidates_fit() -> None:
    """The candidates fit never perturbs the placeholder path: status='placeholder'
    stays the all-identity payload regardless of the committed dump."""
    final = build_context_scorer(CANONICAL_SEED, status="placeholder")
    assert final["status"] == "placeholder"
    for block in final["families"].values():
        assert block["w_family"] == 0.0
        assert block["weights"] == [0.0] * 13
        assert block["feature_scales"] == [1.0] * 13


@_NEED_REAL_CORPUS
def test_single_class_family_ships_identity_not_raise(tmp_path: Path) -> None:
    """A family with only one class present (here: all-positive account) ships
    the identity block with no exception (the ITIN-on-G8 / off-corpus case)."""
    dump = tmp_path / "single_class.json"
    _write_dump(
        dump,
        [_fire("account", "positive", [0.0] * 13, start=10 * k) for k in range(8)],
    )
    fams = _candidates(dump)["families"]
    assert fams["account"]["w_family"] == 0.0
    assert fams["account"]["support"] == {"redact": 8, "suppress": 0}


@_NEED_REAL_CORPUS
def test_non_separable_family_fails_gate_ships_identity(tmp_path: Path) -> None:
    """A two-class family whose features carry no signal (identical vectors for
    both classes) cannot beat the base-rate NLL → ships identity (w=0), no raise."""
    dump = tmp_path / "non_separable.json"
    rows: list[dict[str, Any]] = []
    for k in range(12):
        gt = "positive" if k % 2 == 0 else "none"
        rows.append(_fire("account", gt, [1.0] * 13, start=10 * k))  # constant features
    _write_dump(dump, rows)
    fams = _candidates(dump)["families"]
    assert fams["account"]["w_family"] == 0.0
    assert fams["account"]["support"] == {"redact": 6, "suppress": 6}


@_NEED_REAL_CORPUS
def test_separable_family_passes_gate_ships_active(tmp_path: Path) -> None:
    """A two-class family with a perfectly separating feature beats the gate and
    ships w_family 1 (the fittable path)."""
    dump = tmp_path / "separable.json"
    rows: list[dict[str, Any]] = []
    for k in range(16):
        positive = k % 2 == 0
        feats = [0.0] * 13
        feats[4] = 10.0 if positive else 0.0  # digit_run_length separates cleanly
        rows.append(_fire("account", "positive" if positive else "none", feats, start=10 * k))
    _write_dump(dump, rows)
    block = _candidates(dump)["families"]["account"]
    assert block["w_family"] == 1.0
    assert any(w != 0.0 for w in block["weights"])


@_NEED_REAL_CORPUS
def test_off_contract_family_key_raises(tmp_path: Path) -> None:
    """A dump 'family' value outside the five scored families is the D-5 guard
    (the dump must bucket by wireName, never cell_category_key) → raises."""
    dump = tmp_path / "bad_family.json"
    _write_dump(dump, [_fire("medicalrecord", "positive", [0.0] * 13, start=0)])
    with pytest.raises(PipelineError):
        _candidates(dump)


@_NEED_REAL_CORPUS
def test_feature_order_drift_raises(tmp_path: Path) -> None:
    """A dump whose feature_order disagrees with the canonical contract raises
    (the single index authority cannot silently drift)."""
    dump = tmp_path / "drift.json"
    payload = {
        "schema_version": 1,
        "generated_by": "test.fireFeatures",
        "feature_order": list(reversed(FEATURE_ORDER)),
        "fires": [_fire("account", "positive", [0.0] * 13, start=0)],
    }
    dump.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PipelineError):
        _candidates(dump)
