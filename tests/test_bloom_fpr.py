"""Empirical FPR check for the Bloom builder.

At m sized from ``optimal_bits(n, 0.001)`` with k=10 and a broadly random
non-member population, the observed FPR should be within 5x of target (we
keep a loose upper bound so the test is not flaky on small samples).
"""

from __future__ import annotations

import random

from resecta_data.bloom import BloomFilter, nfkc_lower, optimal_bits

_FPR_TARGET = 0.001
_FPR_UPPER_BOUND = 0.005  # 5x target
_N_INSERT = 1000
_N_QUERY = 100_000


def _random_token(rng: random.Random) -> str:
    length = rng.randint(3, 14)
    return "".join(chr(rng.randint(97, 122)) for _ in range(length))


def test_observed_fpr_within_bound() -> None:
    rng = random.Random(20260416)  # noqa: S311 — determinism, not security

    inserted: set[str] = set()
    while len(inserted) < _N_INSERT:
        inserted.add(nfkc_lower(_random_token(rng)))

    m = optimal_bits(_N_INSERT, _FPR_TARGET)
    bf = BloomFilter.empty(m=m, k=10, seed=20260416)
    for key in inserted:
        bf.insert(key)

    false_positives = 0
    queried = 0
    query_rng = random.Random(0xCAFEB0BA)  # noqa: S311 — determinism, not security
    while queried < _N_QUERY:
        candidate = nfkc_lower(_random_token(query_rng))
        if candidate in inserted:
            continue
        queried += 1
        if bf.contains(candidate):
            false_positives += 1

    observed = false_positives / queried
    assert observed < _FPR_UPPER_BOUND, (
        f"Observed FPR {observed:.4f} exceeds bound {_FPR_UPPER_BOUND:.4f} "
        f"(target {_FPR_TARGET:.4f}, n={_N_INSERT}, m={m})"
    )
