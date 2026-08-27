"""Helpers that synthesise Swift-side dump fixtures for Phase 3b tests.

These emulate what the Swift DocumentTypeClassifier / PII detector test
target will eventually dump. They are purely for Python-side determinism
and schema tests; they are NOT a substitute for the real Swift dump.

Given a mini G8 corpus payload, ``make_softmax_dump`` and
``make_score_dump`` produce dump payloads whose shape matches the
schemas under ``schemas/doctype_softmax_dump.schema.json`` and
``schemas/detector_score_dump.schema.json``.
"""

from __future__ import annotations

import hashlib
from typing import Any

# Canonical category tuple imported instead of duplicated: the
# routingNumber addition surfaced that a hardcoded copy here goes stale the
# moment the sweep's category set moves (the loader hard-fails on
# exact-order mismatch).
from resecta_data.classifier.sweep_thresholds import _CATEGORIES

_CLASSES: tuple[str, ...] = ("court", "medical", "financial", "foia", "generic")

_TRUE_LOGIT_HIGH = 2.5
_TRUE_LOGIT_LOW = 1.0
_WRONG_LOGIT = 0.1

_SIGNAL_SCORE = 0.85
_NOISE_SCORE = 0.12
_DECOY_SCORE = 0.55


def _docid_hash(doc_id: str) -> int:
    return int.from_bytes(hashlib.sha256(doc_id.encode()).digest()[:4], "big")


def make_softmax_dump(corpus: dict[str, Any]) -> dict[str, Any]:
    """Return a softmax-dump payload for ``corpus``.

    Logits are constructed from the true doctype with a small variation
    driven by ``doc_id`` so NLL_before differs from NLL_after (temperature
    fit has work to do).
    """
    documents = []
    for doc in corpus["documents"]:
        true_idx = _CLASSES.index(doc["doctype"])
        h = _docid_hash(doc["id"])
        # Alternate overconfident / underconfident documents to give the
        # temperature fit a non-trivial optimum.
        true_logit = _TRUE_LOGIT_HIGH if (h & 1) == 0 else _TRUE_LOGIT_LOW
        logits = [_WRONG_LOGIT] * len(_CLASSES)
        logits[true_idx] = true_logit
        documents.append({"doc_id": doc["id"], "logits": logits})

    documents.sort(key=lambda d: d["doc_id"])

    return {
        "version": 1,
        "generated_by": "tests.calibration_fixtures.make_softmax_dump",
        "g8_corpus_seed": int(corpus["seed"]),
        "classes": list(_CLASSES),
        "documents": documents,
    }


def make_score_dump(corpus: dict[str, Any]) -> dict[str, Any]:
    """Return a detector-score dump payload for ``corpus``.

    Every ground-truth ``redact`` span gets a matching high-score candidate;
    every ground-truth ``suppress`` span (adversarial decoy) gets a mid-score
    candidate covering the decoy; and every document gets one noise candidate
    per category at a low score, to exercise the sweep's false-positive arm.
    """
    documents: list[dict[str, Any]] = []
    for doc in corpus["documents"]:
        candidates: list[dict[str, Any]] = []
        # Matching candidates for ground-truth spans.
        for span in doc["pii_spans"]:
            category = span["category"]
            if category not in _CATEGORIES:
                continue
            outcome = span.get("expected_outcome", "redact")
            if outcome == "redact":
                score = _SIGNAL_SCORE
            elif outcome == "suppress":
                score = _DECOY_SCORE
            else:
                score = _DECOY_SCORE
            candidates.append(
                {
                    "category": category,
                    "start": span["start"],
                    "end": span["end"],
                    "raw_score": score,
                    "doctype_prior_applied": False,
                }
            )
        # One low-score noise candidate per category in a tail region.
        text_len = len(doc["text"])
        for idx, category in enumerate(_CATEGORIES):
            start = min(text_len - 1, max(0, text_len - 1 - idx * 3))
            end = min(text_len, start + 2)
            if end <= start:
                continue
            candidates.append(
                {
                    "category": category,
                    "start": start,
                    "end": end,
                    "raw_score": _NOISE_SCORE,
                    "doctype_prior_applied": False,
                }
            )
        candidates.sort(key=lambda c: (c["category"], c["start"], c["end"]))
        documents.append({"doc_id": doc["id"], "candidates": candidates})

    documents.sort(key=lambda d: d["doc_id"])

    return {
        "version": 1,
        "generated_by": "tests.calibration_fixtures.make_score_dump",
        "g8_corpus_seed": int(corpus["seed"]),
        "categories": list(_CATEGORIES),
        "documents": documents,
    }
