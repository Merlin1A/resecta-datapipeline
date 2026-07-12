"""Tests for locale-aware per-document seed derivation (C13 / findings L9).

Covers the three C13-mandated cases:
- determinism of ``(seed, locale)`` for fixed inputs,
- the 30/30/40 es_MX/es_ES/en_US distribution on the Hispanic bucket,
- non-Hispanic buckets always resolve to ``en_US``.
"""

from __future__ import annotations

from collections import Counter

import pytest

from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.corpus.generate import _sub_seed_and_locale

_NON_HISPANIC_BUCKETS = ("white", "black", "asian", "ai_an")
_HISPANIC_EXPECTED_SHARES = {"es_MX": 0.30, "es_ES": 0.30, "en_US": 0.40}
_DISTRIBUTION_TOLERANCE = 0.02
_HISPANIC_SAMPLE_SIZE = 1000


def test_sub_seed_and_locale_determinism() -> None:
    """Same ``(master, doctype, index, demographic)`` must yield the same tuple."""
    cases = [
        (CANONICAL_SEED, "court", 0, "hispanic"),
        (CANONICAL_SEED, "medical", 17, "hispanic"),
        (CANONICAL_SEED, "financial", 42, "white"),
        (CANONICAL_SEED, "foia", 123, "ai_an"),
        (CANONICAL_SEED, "generic", 99, "asian"),
        (CANONICAL_SEED + 1, "court", 0, "hispanic"),
    ]
    for master, doctype, index, demographic in cases:
        first = _sub_seed_and_locale(master, doctype, index, demographic)
        second = _sub_seed_and_locale(master, doctype, index, demographic)
        assert first == second
        # A second call after an intermediate differing call must still match.
        _sub_seed_and_locale(master + 999, doctype, index, demographic)
        third = _sub_seed_and_locale(master, doctype, index, demographic)
        assert first == third


def test_hispanic_locale_distribution() -> None:
    """Across 1,000 Hispanic docs the locale mix stays within ±2% of 30/30/40."""
    counts: Counter[str] = Counter()
    for index in range(_HISPANIC_SAMPLE_SIZE):
        _, locale = _sub_seed_and_locale(CANONICAL_SEED, "court", index, "hispanic")
        counts[locale] += 1

    assert set(counts) <= set(_HISPANIC_EXPECTED_SHARES)
    for locale, expected_share in _HISPANIC_EXPECTED_SHARES.items():
        observed_share = counts[locale] / _HISPANIC_SAMPLE_SIZE
        assert abs(observed_share - expected_share) <= _DISTRIBUTION_TOLERANCE, (
            f"{locale}: observed {observed_share:.3f}, "
            f"expected {expected_share:.2f} ± {_DISTRIBUTION_TOLERANCE:.2f}"
        )


@pytest.mark.parametrize("demographic", _NON_HISPANIC_BUCKETS)
def test_non_hispanic_en_us(demographic: str) -> None:
    """Every non-Hispanic demographic resolves to ``en_US`` for every index."""
    for index in range(200):
        _, locale = _sub_seed_and_locale(CANONICAL_SEED, "medical", index, demographic)
        assert locale == "en_US"
