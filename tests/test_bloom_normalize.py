"""Normalizer parity with the Swift TextNormalizer.

The Swift side normalizes keys via ``TextNormalizer.normalize(_:).lowercased()``
before hashing. This test fixes the expected outputs for a representative set
of inputs so any Python-side drift shows up in CI.
"""

from __future__ import annotations

import pytest

from resecta_data.bloom.normalize import nfkc_lower


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Ligatures — Swift replaces these before NFKC.
        ("\ufb01sher", "fisher"),
        ("\ufb02ame", "flame"),
        ("\ufb00ood", "ffood"),
        ("o\ufb03ce", "office"),
        ("wa\ufb04e", "waffle"),
        # NFKC examples.
        ("José", "josé"),
        ("Nuñez", "nuñez"),
        ("\u00c6thelred", "æthelred"),
        # Full-width → half-width via NFKC compatibility decomposition.
        ("\uff21\uff22\uff23", "abc"),
        # Plain ASCII case folding.
        ("Smith", "smith"),
        ("O'Brien", "o'brien"),
        # Empty string round-trips.
        ("", ""),
    ],
)
def test_nfkc_lower(text: str, expected: str) -> None:
    assert nfkc_lower(text) == expected
