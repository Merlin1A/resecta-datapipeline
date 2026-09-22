"""The temperature fit's objective is convex in u = 1/T on a synthetic set.

Mean negative log-likelihood is a log-sum-exp of a linear map in u minus a
linear term in u, so its second differences on a u grid are non-negative; in
T the same objective is only unimodal, which is what golden-section needs.
"""

from __future__ import annotations

import random

from resecta_data.classifier.fit_temperature import _mean_nll


def test_second_differences_in_inverse_temperature_are_non_negative() -> None:
    rng = random.Random(20260416)  # noqa: S311 — deterministic test data
    logits: dict[str, tuple[float, ...]] = {}
    truth: dict[str, int] = {}
    for i in range(60):
        true_idx = rng.randrange(3)
        row = [rng.gauss(0.0, 2.0) + (1.5 if k == true_idx else 0.0) for k in range(3)]
        logits[f"doc-{i:03d}"], truth[f"doc-{i:03d}"] = tuple(row), true_idx
    split = frozenset(logits)
    grid = [0.1 + i * (9.9 / 40) for i in range(41)]
    f = [_mean_nll(logits, truth, split, 1.0 / u) for u in grid]
    second_differences = [f[i + 1] - 2.0 * f[i] + f[i - 1] for i in range(1, 40)]
    assert min(second_differences) >= -1e-9
