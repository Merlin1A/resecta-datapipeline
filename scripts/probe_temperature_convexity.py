#!/usr/bin/env python3
"""Probe the shape of the temperature fit's objective in T and in u = 1/T.

Evaluates ``fit_temperature._mean_nll`` on 41 points linear in T over
[0.1, 10] and 41 points linear in u = 1/T over [0.1, 10], prints the interior
second-difference sign counts for both grids, and prints the golden-section
minimiser next to the T-grid argmin. Reads the Swift softmax dump and the G8
corpus when both are under ``build/`` (the same split as the fit), else a
fixed-seed synthetic 3-class set. Read-only: writes nothing.

Run from the repo root: ``.venv/bin/python scripts/probe_temperature_convexity.py``.
"""

from __future__ import annotations

import random
from pathlib import Path

from resecta_data.classifier import fit_temperature as ft
from resecta_data.classifier._calibration_io import load_corpus, load_softmax_dump
from resecta_data.common.determinism import CANONICAL_SEED

_DUMP = Path("build/calibration/softmax_dump.json")
_CORPUS = Path("build/corpus/g8_corpus.json")
_SCHEMAS = Path("schemas")
_GRID = [0.1 + i * (9.9 / 40) for i in range(41)]
_NEGATIVE = -1e-9  # a second difference below this counts as negative


def synthetic_set(
    seed: int = CANONICAL_SEED, n_docs: int = 60, n_classes: int = 3
) -> tuple[dict[str, tuple[float, ...]], dict[str, int]]:
    """Return (logits_by_id, true_idx_by_id): Gaussian logits with a bump on the true class."""
    rng = random.Random(seed)  # noqa: S311 — a probe, not a security use
    logits: dict[str, tuple[float, ...]] = {}
    truth: dict[str, int] = {}
    for i in range(n_docs):
        true_idx = rng.randrange(n_classes)
        row = [rng.gauss(0.0, 2.0) + (1.5 if k == true_idx else 0.0) for k in range(n_classes)]
        logits[f"doc-{i:03d}"], truth[f"doc-{i:03d}"] = tuple(row), true_idx
    return logits, truth


def _real_set() -> tuple[dict[str, tuple[float, ...]], dict[str, int], frozenset[str]]:
    dump, corpus = load_softmax_dump(_DUMP, _SCHEMAS), load_corpus(_CORPUS, _SCHEMAS)
    classes = {name: i for i, name in enumerate(dump["classes"])}
    logits = {d["doc_id"]: tuple(d["logits"]) for d in dump["documents"]}
    truth = {d["id"]: classes[d["doctype"]] for d in corpus["documents"]}
    return logits, truth, ft._split_indices(corpus, CANONICAL_SEED)[1]


def main() -> None:
    if _DUMP.is_file() and _CORPUS.is_file():
        logits, truth, split = _real_set()
        print(f"dataset: {_DUMP} + {_CORPUS} (calibration split, n={len(split)})")
    else:
        logits, truth = synthetic_set()
        split = frozenset(logits)
        print(f"dataset: synthetic (seed {CANONICAL_SEED}, n={len(split)})")

    def nll(temperature: float) -> float:
        return ft._mean_nll(logits, truth, split, temperature)

    f_t = [nll(t) for t in _GRID]
    f_u = [nll(1.0 / u) for u in _GRID]
    for label, f in (("T", f_t), ("u = 1/T", f_u)):
        d2 = [f[i + 1] - 2.0 * f[i] + f[i - 1] for i in range(1, len(f) - 1)]
        neg = sum(1 for d in d2 if d < _NEGATIVE)
        print(f"second differences in {label}: {neg} negative of {len(d2)} interior points")
    t_star, iterations = ft._golden_section(nll, 0.10, 10.0, 1.0e-6, 128)
    t_grid = _GRID[min(range(len(f_t)), key=f_t.__getitem__)]
    print(
        f"golden-section T* = {t_star:.6f} ({iterations} iterations); T-grid argmin = {t_grid:.4f}"
    )
    print(f"|T* - grid argmin| = {abs(t_star - t_grid):.4f} (grid step {9.9 / 40:.4f})")


if __name__ == "__main__":
    main()
