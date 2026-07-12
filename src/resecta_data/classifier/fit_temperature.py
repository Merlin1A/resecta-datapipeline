"""Fit a scalar temperature T for the 5-class DocumentTypeClassifier softmax.

Consumes a Swift-side logits dump over the G8 corpus, splits documents 70/30
stratified by (doctype, demographic_bucket) using a deterministic sub-seed,
and fits T by minimising mean negative log-likelihood on the held-out 30%
split.

The optimiser is a pure-Python golden-section search — no scipy dep. The
search is convex in T for a fixed dataset, so golden-section converges to the
unique minimum.

Output conforms to ``schemas/doctype_temperature.schema.json`` and ships to
``Resources/Classifier/doctype-temperature.json`` via INSTALL_ROUTES.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Final

from resecta_data.common.exceptions import PipelineError
from resecta_data.common.mechanism_language import assert_safe

from ._calibration_io import (
    assert_corpus_seed_matches,
    assert_doc_ids_cover_corpus,
    dump_sha256,
    load_corpus,
    load_softmax_dump,
)

_MODULE_NAME: Final[str] = "resecta_data.classifier.fit_temperature"
_SCHEMA_VERSION: Final[int] = 1

_CALIBRATION_FRACTION: Final[float] = 0.30
_MIN_SPLITTABLE_GROUP: Final[int] = 2
_SEARCH_LOWER: Final[float] = 0.10
_SEARCH_UPPER: Final[float] = 10.0
_TOLERANCE: Final[float] = 1.0e-6
_MAX_ITERATIONS: Final[int] = 128

# Golden-section constants.
_PHI: Final[float] = (math.sqrt(5.0) - 1.0) / 2.0  # ~0.6180339887

_CANONICAL_CLASSES: Final[tuple[str, ...]] = ("court", "medical", "financial", "foia", "generic")

_NOTES: Final[str] = (
    "Temperature fit by golden-section minimisation of mean negative "
    "log-likelihood on a 30% held-out G8 split stratified by (doctype, "
    "demographic_bucket). The pipeline is designed to be deterministic "
    "given a fixed dump and seed; re-running produces byte-identical output."
)


def _split_indices(
    corpus: dict[str, Any],
    seed: int,
) -> tuple[frozenset[str], frozenset[str]]:
    """Return (train_ids, calibration_ids).

    Documents are grouped by (doctype, demographic_bucket); within each
    group, a stable SHA-256-derived rank assigns the first floor(0.3 * n)
    documents to the calibration split. This keeps the split deterministic
    under reseed and under count changes for other groups.
    """
    by_group: dict[tuple[str, str], list[str]] = {}
    for doc in corpus["documents"]:
        key = (doc["doctype"], doc["demographic_bucket"])
        by_group.setdefault(key, []).append(doc["id"])

    calibration: set[str] = set()
    train: set[str] = set()
    for key, ids in sorted(by_group.items()):
        ranked = sorted(ids, key=lambda i: _rank_key(seed, key, i))
        n = len(ranked)
        cutoff = int(n * _CALIBRATION_FRACTION)
        # Integer truncation can empty the calibration set for small groups.
        # Guarantee at least one calibration doc whenever the group has 2+
        # documents so the fit always has something to minimise on.
        if n >= _MIN_SPLITTABLE_GROUP and cutoff == 0:
            cutoff = 1
        calibration.update(ranked[:cutoff])
        train.update(ranked[cutoff:])
    return frozenset(train), frozenset(calibration)


def _rank_key(seed: int, group: tuple[str, str], doc_id: str) -> int:
    """Stable SHA-256 rank for a document within its (doctype, bucket) group."""
    raw = f"{seed}:{group[0]}:{group[1]}:{doc_id}".encode()
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def _mean_nll(
    logits_by_id: dict[str, tuple[float, ...]],
    true_idx_by_id: dict[str, int],
    split: frozenset[str],
    temperature: float,
) -> float:
    """Mean negative log-likelihood of the true class over ``split``."""
    if not split:
        raise PipelineError(
            "Empty calibration split — corpus too small for 70/30 stratified split."
        )
    total = 0.0
    for doc_id in split:
        logits = logits_by_id[doc_id]
        scaled = [z / temperature for z in logits]
        # Log-sum-exp for numerical stability.
        m = max(scaled)
        lse = m + math.log(sum(math.exp(z - m) for z in scaled))
        true_idx = true_idx_by_id[doc_id]
        total += lse - scaled[true_idx]
    return total / len(split)


def _golden_section(
    nll: Any,
    lower: float,
    upper: float,
    tolerance: float,
    max_iterations: int,
) -> tuple[float, int]:
    """Return (T_star, iterations) minimising ``nll`` on [lower, upper]."""
    a = lower
    b = upper
    c = b - _PHI * (b - a)
    d = a + _PHI * (b - a)
    fc = nll(c)
    fd = nll(d)
    iterations = 0
    while abs(b - a) > tolerance and iterations < max_iterations:
        if fc < fd:
            b = d
            d = c
            fd = fc
            c = b - _PHI * (b - a)
            fc = nll(c)
        else:
            a = c
            c = d
            fc = fd
            d = a + _PHI * (b - a)
            fd = nll(d)
        iterations += 1
    return (a + b) / 2.0, iterations


def build(
    seed: int,
    *,
    softmax_dump_path: Path,
    corpus_path: Path,
    schemas_dir: Path,
) -> dict[str, Any]:
    """Return the fitted-temperature payload.

    Args:
        seed: Master seed. Used to derive the deterministic 70/30 split.
        softmax_dump_path: Swift-produced logits dump over the G8 corpus.
        corpus_path: The G8 corpus the dump was produced against.
        schemas_dir: Directory holding ``*.schema.json`` files.

    Returns:
        A payload dict conforming to ``schemas/doctype_temperature.schema.json``.

    Raises:
        PipelineError: If the dump and corpus are inconsistent, if the split
            is empty, or if the fitted NLL is worse than NLL at T=1.
    """
    assert_safe(_NOTES, context="doctype_temperature.json notes")

    dump = load_softmax_dump(softmax_dump_path, schemas_dir)
    corpus = load_corpus(corpus_path, schemas_dir)
    assert_corpus_seed_matches(dump, corpus, context=str(softmax_dump_path))
    assert_doc_ids_cover_corpus(dump, corpus, context=str(softmax_dump_path))

    dump_classes = tuple(dump["classes"])
    if dump_classes != _CANONICAL_CLASSES:
        raise PipelineError(
            f"{softmax_dump_path}: dump classes {dump_classes} do not match canonical order "
            f"{_CANONICAL_CLASSES}. Python side indexes logits positionally."
        )

    logits_by_id: dict[str, tuple[float, ...]] = {
        doc["doc_id"]: tuple(doc["logits"]) for doc in dump["documents"]
    }
    class_to_idx = {name: i for i, name in enumerate(_CANONICAL_CLASSES)}
    true_idx_by_id: dict[str, int] = {
        doc["id"]: class_to_idx[doc["doctype"]] for doc in corpus["documents"]
    }

    _, calibration = _split_indices(corpus, seed)

    def objective(t: float) -> float:
        return _mean_nll(logits_by_id, true_idx_by_id, calibration, t)

    nll_before = objective(1.0)
    temperature, iterations = _golden_section(
        objective,
        _SEARCH_LOWER,
        _SEARCH_UPPER,
        _TOLERANCE,
        _MAX_ITERATIONS,
    )
    nll_after = objective(temperature)

    if nll_after > nll_before + _TOLERANCE:
        raise PipelineError(
            f"Fitted NLL {nll_after} > T=1 NLL {nll_before}; optimiser diverged. "
            "Check that dump logits are pre-softmax and that the class order matches."
        )

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _MODULE_NAME,
        "seed": seed,
        "status": "calibrated",
        "temperature": temperature,
        "fit_metadata": {
            "split_size": len(calibration),
            "nll_before": nll_before,
            "nll_after": nll_after,
            "optimizer": "golden_section",
            "tolerance": _TOLERANCE,
            "iterations": iterations,
            "search_lower": _SEARCH_LOWER,
            "search_upper": _SEARCH_UPPER,
            "input_dump_sha256": dump_sha256(softmax_dump_path),
            "corpus_seed": int(corpus["seed"]),
        },
        "notes": _NOTES,
    }
