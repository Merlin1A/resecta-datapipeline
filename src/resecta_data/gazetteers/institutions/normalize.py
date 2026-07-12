"""Shared business-suffix normalization and alias helpers for institution parsers.

Used by ``parse_fdic`` and ``parse_edgar`` to produce a consistent alias set from a
raw institution name. Factored here so both parsers apply identical rules.

Design reference: design 02 §6 "Business-suffix normalization rules".
"""

from __future__ import annotations

import unicodedata
from typing import Final

# Business suffixes to strip when deriving the short alias. Applied in order —
# the list is traversed left to right and the first matching suffix is stripped.
# Longer suffixes (", national association") appear before their abbreviation
# equivalents to avoid partial matches. See design 02 §6 lines 933-949.
STRIP_SUFFIXES: Final[tuple[str, ...]] = (
    ", national association",
    " national association",
    ", n.a.",
    " n.a.",
    " na",
    ", f.s.b.",
    " federal savings bank",
    ", llc",
    " llc",
    " l.l.c.",
    ", inc.",
    " inc",
    " incorporated",
    ", corp.",
    " corp",
    " corporation",
    ", co.",
    " co",
    ", ltd.",
    " ltd",
    " & co.",
)


def nfkc_lower(s: str) -> str:
    """Return NFKC-normalized, lowercased form of ``s``.

    Args:
        s: Input string.

    Returns:
        NFKC-normalized, lowercased, whitespace-stripped string.
    """
    return unicodedata.normalize("NFKC", s).lower().strip()


def derive_aliases(canonical_name: str) -> list[str]:
    """Derive the alias set from a raw institution name.

    Steps:
    1. Normalize to NFKC lowercase.
    2. Extract the "short name" as the portion before the first comma
       (e.g., "Chase Bank, N.A." → "Chase Bank").
    3. Strip each suffix in STRIP_SUFFIXES from both the full normalized name
       and the short name to produce the "stripped" form.
    4. Return a sorted, de-duplicated list of aliases that differ from the
       canonical name and are non-empty.

    The ``canonical_name`` is the raw display name from the source (not yet
    normalized). The returned aliases are all NFKC-normalized lowercase.

    Args:
        canonical_name: Raw institution name from the source (e.g., "Chase Bank, N.A.").

    Returns:
        Sorted list of alias strings in normalized form.
    """
    normed = nfkc_lower(canonical_name)
    canonical_lower = normed

    # Short name = portion before first comma (or whole name if no comma).
    short = normed[: normed.index(",")].strip() if "," in normed else normed

    candidates: set[str] = set()

    # Add the normalized full form if it differs from canonical (it won't at this
    # point since we already lowercased canonical_lower, but include for parity).
    # The key aliases are the stripped forms.

    # Strip suffixes from both the full normalized name and the short name.
    full_stripped = _strip_suffixes(normed)
    short_stripped = _strip_suffixes(short)

    for candidate in (normed, short, full_stripped, short_stripped):
        trimmed = candidate.strip()
        if trimmed and trimmed != canonical_lower:
            candidates.add(trimmed)

    return sorted(candidates)


def _strip_suffixes(name: str) -> str:
    """Strip the first matching business suffix from ``name``.

    Args:
        name: Lowercased, NFKC-normalized name.

    Returns:
        Name with the first matching suffix removed and whitespace-stripped.
    """
    for suffix in STRIP_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)].strip()
    return name
