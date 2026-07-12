"""Phone-number structural test vector builder.

Emits a deterministic catalog of NANP phone candidates exercising the Swift
``detectPhones`` regex at PIIDetector.swift:491. The regex pattern itself is
declared at PIIDetector.swift:468-470:

    (?<!\\d)(?:\\+1[\\s.-]?)?(?:\\(\\d{3}\\)[\\s.-]?\\d{3}[\\s.-]?\\d{4}
        |\\d{3}[\\s.-]?\\d{3}[\\s.-]?\\d{4})(?!\\d)

Two alternations enforce balanced parentheses. Optional ``+1`` country-code
lead. Inter-group separators may be space, dot, or hyphen. Each side has
``(?<!\\d)`` / ``(?!\\d)`` lookarounds so adjacent digits do not bleed in.

This builder emits valid candidates across every paren / separator / lead
combination plus a set of structural rejections that violate the regex
shape (unbalanced parens, leading-bare ``+``, embedded letters, wrong
group lengths). The Swift detector also applies a context-keyword score,
but vectors here cover only the regex gate.
"""

from __future__ import annotations

import random
from typing import Any, Final

from resecta_data.common.determinism import seeded_context

_GENERATED_BY: Final[str] = "resecta-data/vectors/phone"
_SCHEMA_VERSION: Final[int] = 1

_VALID_PARENS_COUNT: Final[int] = 25
_VALID_BARE_COUNT: Final[int] = 25
_VALID_LEAD_PLUS1_COUNT: Final[int] = 10
_INVALID_UNBALANCED_PAREN_COUNT: Final[int] = 6
_INVALID_BARE_PLUS_COUNT: Final[int] = 4
_INVALID_GROUP_LENGTH_COUNT: Final[int] = 8
_INVALID_LETTERS_COUNT: Final[int] = 6

_SEPARATORS: Final[tuple[str, ...]] = (" ", ".", "-", "")


def _three_digits(rng: random.Random) -> str:
    """Return a 3-digit string, leading-zero allowed (regex shape only)."""
    return f"{rng.randint(0, 999):03d}"


def _four_digits(rng: random.Random) -> str:
    """Return a 4-digit string, leading-zero allowed."""
    return f"{rng.randint(0, 9999):04d}"


def _format_parens(area: str, prefix: str, line: str, sep: str) -> str:
    """``(AAA) PPP-LLLL`` — first separator is the post-paren character."""
    return f"({area}){sep}{prefix}{sep}{line}"


def _format_bare(area: str, prefix: str, line: str, sep: str) -> str:
    """``AAA PPP-LLLL`` — no parentheses."""
    return f"{area}{sep}{prefix}{sep}{line}"


def build(seed: int) -> dict[str, Any]:
    """Return the phone-number structural test vector payload.

    Args:
        seed: PRNG seed. Recorded in the artifact for reproducibility.

    Returns:
        A payload dict conforming to ``schemas/phone_vectors.schema.json``.
    """
    vectors: list[dict[str, Any]] = []

    with seeded_context(seed) as rng:
        # Valid parenthesised: (AAA) PPP-LLLL with separator variations.
        for _ in range(_VALID_PARENS_COUNT):
            area, prefix, line = _three_digits(rng), _three_digits(rng), _four_digits(rng)
            sep = rng.choice(_SEPARATORS)
            vectors.append(
                {
                    "phone": _format_parens(area, prefix, line, sep),
                    "valid": True,
                    "rejection_reason": None,
                    "notes": "Balanced parens; separator variation.",
                },
            )

        # Valid bare-format: AAA PPP-LLLL.
        for _ in range(_VALID_BARE_COUNT):
            area, prefix, line = _three_digits(rng), _three_digits(rng), _four_digits(rng)
            sep = rng.choice(_SEPARATORS)
            vectors.append(
                {
                    "phone": _format_bare(area, prefix, line, sep),
                    "valid": True,
                    "rejection_reason": None,
                    "notes": "Bare 10-digit shape; separator variation.",
                },
            )

        # Valid +1 country-code lead.
        for idx in range(_VALID_LEAD_PLUS1_COUNT):
            area, prefix, line = _three_digits(rng), _three_digits(rng), _four_digits(rng)
            sep = rng.choice(_SEPARATORS)
            lead_sep = rng.choice(_SEPARATORS)
            body = (
                _format_parens(area, prefix, line, sep)
                if idx % 2 == 0
                else _format_bare(area, prefix, line, sep)
            )
            vectors.append(
                {
                    "phone": f"+1{lead_sep}{body}",
                    "valid": True,
                    "rejection_reason": None,
                    "notes": "Optional +1 country-code lead.",
                },
            )

        # Invalid: unbalanced parens — only the open or only the close present.
        for idx in range(_INVALID_UNBALANCED_PAREN_COUNT):
            area, prefix, line = _three_digits(rng), _three_digits(rng), _four_digits(rng)
            sep = rng.choice(_SEPARATORS)
            if idx % 2 == 0:
                phone = f"({area}{sep}{prefix}{sep}{line}"  # open only
            else:
                phone = f"{area}){sep}{prefix}{sep}{line}"  # close only
            vectors.append(
                {
                    "phone": phone,
                    "valid": False,
                    "rejection_reason": "unbalanced_parens",
                    "notes": "Open-only or close-only paren; alternation forbids.",
                },
            )

        # Invalid: bare ``+`` lead with no ``1``. The regex only permits ``+1``.
        for _ in range(_INVALID_BARE_PLUS_COUNT):
            area, prefix, line = _three_digits(rng), _three_digits(rng), _four_digits(rng)
            sep = rng.choice(_SEPARATORS)
            vectors.append(
                {
                    "phone": f"+{_format_bare(area, prefix, line, sep)}",
                    "valid": False,
                    "rejection_reason": "bare_plus_lead",
                    "notes": "Bare ``+`` is dropped; only ``+1`` leads.",
                },
            )

        # Invalid group lengths — area, prefix, or line off by one digit.
        length_variants: list[tuple[int, int, int]] = [
            (2, 3, 4),
            (4, 3, 4),
            (3, 2, 4),
            (3, 4, 4),
            (3, 3, 3),
            (3, 3, 5),
            (5, 3, 4),
            (3, 3, 6),
        ]
        for a_len, p_len, l_len in length_variants[:_INVALID_GROUP_LENGTH_COUNT]:
            area = "".join(str(rng.randint(0, 9)) for _ in range(a_len))
            prefix = "".join(str(rng.randint(0, 9)) for _ in range(p_len))
            line = "".join(str(rng.randint(0, 9)) for _ in range(l_len))
            vectors.append(
                {
                    "phone": f"{area}-{prefix}-{line}",
                    "valid": False,
                    "rejection_reason": "wrong_group_length",
                    "notes": (
                        f"Group lengths {a_len}-{p_len}-{l_len} do not match the "
                        "fixed 3-3-4 NANP shape."
                    ),
                },
            )

        # Invalid: embedded letters break the all-digit groups.
        letter_positions = ["A", "B", "X", "O", "I", "Z"]
        for idx in range(_INVALID_LETTERS_COUNT):
            area, prefix, line = _three_digits(rng), _three_digits(rng), _four_digits(rng)
            letter = letter_positions[idx]
            corrupted_prefix = letter + prefix[1:]
            vectors.append(
                {
                    "phone": f"{area}-{corrupted_prefix}-{line}",
                    "valid": False,
                    "rejection_reason": "non_digit",
                    "notes": f"Letter ``{letter}`` in the prefix group; \\d{{3}} fails.",
                },
            )

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _GENERATED_BY,
        "seed": seed,
        "vectors": vectors,
    }
