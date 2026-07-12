"""Text normalization mirroring the Swift ``TextNormalizer``.

=== SHARED WITH SWIFT ===
Output must match byte-for-byte the Swift implementation at
  ../Packages/RedactionEngine/Sources/RedactionEngine/Utilities/TextNormalizer.swift
``TextNormalizer.normalize(_:).lowercased()``.

The Swift side applies five targeted Latin ligature expansions before NFKC,
because Apple's ``precomposedStringWithCompatibilityMapping`` does not expand
them in all locales the way Python's ``unicodedata.normalize`` does. Applying
the same expansions on both sides is what gives us the byte-identical inputs
the Bloom filter's SHA-256 header depends on.
"""

from __future__ import annotations

import unicodedata
from typing import Final

# Ordered: three-character ligatures before two-character, so we don't
# accidentally consume the leading "ff" of "ffi" as the "ff" ligature.
_LIGATURE_MAP: Final[tuple[tuple[str, str], ...]] = (
    ("\ufb03", "ffi"),
    ("\ufb04", "ffl"),
    ("\ufb00", "ff"),
    ("\ufb01", "fi"),
    ("\ufb02", "fl"),
)


def nfkc_lower(text: str) -> str:
    """Apply ligature expansion + NFKC + lowercase.

    Args:
        text: Arbitrary user-supplied or corpus-supplied string.

    Returns:
        The normalized lowercased form used as the Bloom filter insertion key.
    """
    for ligature, replacement in _LIGATURE_MAP:
        text = text.replace(ligature, replacement)
    return unicodedata.normalize("NFKC", text).lower()
