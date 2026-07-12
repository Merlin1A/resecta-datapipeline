"""Parser for the carltonnorthern/nicknames CC0 dataset.

Raw format (``nicknames_cc0_YYYYMMDD.csv``): one record per line, comma-separated,
no header row.  The first field is the canonical given name; every subsequent
field is a nickname or diminutive form that the canonical name is known by.
Example::

    william,bill,will,willy,billy,liam

Inversion: the parser builds an alias → sorted-unique-canonicals mapping.
An alias can map to multiple canonicals — "al" maps to albert, alfred, allen,
etc. — so each nickname key's value is a list rather than a scalar.

NFKC-normalization + lowercase + strip is applied to every token (both
canonical names and alias forms) before insertion; empty tokens after
normalization are silently dropped.  Unicode normalization is applied via
``unicodedata.normalize("NFKC", ...)`` rather than a locale-specific
case-fold so the behaviour is reproducible across platforms.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path


def _normalize(token: str) -> str:
    """NFKC-normalize, lowercase, and strip a single token."""
    return unicodedata.normalize("NFKC", token).lower().strip()


def parse_nicknames(path: Path) -> dict[str, list[str]]:
    """Parse a nicknames CC0 CSV into an alias → sorted canonicals mapping.

    Each non-empty line of ``path`` is interpreted as a comma-separated record
    whose first field is the canonical given name and whose remaining fields
    are alias forms (nicknames, diminutives, variant spellings).  Blank lines
    and lines where the first field normalizes to the empty string are ignored.

    The returned dict maps each normalized alias (and canonical name, treated
    as its own alias) to a sorted, deduplicated list of canonical names it is
    known to stand for.  Canonicals listed in the value are themselves
    normalized.

    Args:
        path: Path to the dated raw CSV file.  Must exist.  The caller is
            responsible for ensuring the file is the expected format;
            ``build()`` globs for exactly one matching source.

    Returns:
        Dict mapping each normalized alias surface form to a sorted list of
        canonical given names it is used by the nickname table to resolve.
        The dict keys are sorted for deterministic iteration.
    """
    alias_to_canonicals: dict[str, set[str]] = {}

    with path.open("r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n\r")
            if not line.strip():
                continue
            parts = [_normalize(t) for t in line.split(",")]
            # First field is the canonical; rest are aliases.
            if not parts or not parts[0]:
                continue
            canonical = parts[0]
            aliases = [a for a in parts[1:] if a]  # drop blanks after normalize

            # Map every alias → canonical.
            for alias in aliases:
                alias_to_canonicals.setdefault(alias, set()).add(canonical)
            # The canonical form can itself appear as an alias in another row;
            # register it pointing to itself so direct lookups work.
            alias_to_canonicals.setdefault(canonical, set()).add(canonical)

    return {key: sorted(vals) for key, vals in sorted(alias_to_canonicals.items())}
