"""Email-address structural test vector builder.

Emits a deterministic catalog of email candidates exercising the Swift
``detectEmails`` regex at PIIDetector.swift:451 (pattern declared at
:447-449) plus the RFC 5321 ≤254-character cap enforced inline at :454-455.

Pattern (from the Swift source):

    [a-zA-Z0-9_%+-](?:[a-zA-Z0-9_%+-]|\\.(?=[a-zA-Z0-9_%+-]))*
        @(?:[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?)\\.[a-zA-Z]{2,}

Local-part anchors on a non-dot character; subsequent dots permitted only
before another local-part character (so ``.foo@x.co`` and ``foo..bar@x.co``
fail). Domain anchors on alphanumeric at both ends (so ``a@.b.co`` and
``a@b..co`` fail). TLD is ``[a-zA-Z]{2,}`` so all-digit TLDs fail.

This builder emits valid candidates plus structural rejections that violate
the regex shape (leading dot, consecutive dots, bare ``@``, no TLD, all-
digit TLD) plus a slate of length-cap-exceeding candidates that the inline
``> 254`` guard rejects.
"""

from __future__ import annotations

import random
from typing import Any, Final

from resecta_data.common.determinism import seeded_context

_GENERATED_BY: Final[str] = "resecta-data/vectors/email"
_SCHEMA_VERSION: Final[int] = 1

# RFC 5321 maximum length of an email address.
_RFC5321_MAX: Final[int] = 254

_VALID_SIMPLE_COUNT: Final[int] = 25
_VALID_DOTTED_LOCAL_COUNT: Final[int] = 10
_VALID_PLUS_TAG_COUNT: Final[int] = 5
_INVALID_LEADING_DOT_COUNT: Final[int] = 5
_INVALID_CONSECUTIVE_DOT_COUNT: Final[int] = 5
_INVALID_DOMAIN_DOT_COUNT: Final[int] = 5
_INVALID_NO_TLD_COUNT: Final[int] = 5
_INVALID_NUMERIC_TLD_COUNT: Final[int] = 4
_INVALID_LENGTH_CAP_COUNT: Final[int] = 5

_LOCAL_CHARS: Final[str] = "abcdefghijklmnopqrstuvwxyz0123456789_%+-"
_DOMAIN_CHARS: Final[str] = "abcdefghijklmnopqrstuvwxyz0123456789-"
_TLDS: Final[tuple[str, ...]] = ("com", "org", "net", "io", "co", "ai", "app")


def _word(rng: random.Random, n: int, alphabet: str = _LOCAL_CHARS) -> str:
    return "".join(rng.choice(alphabet) for _ in range(n))


def _domain(rng: random.Random) -> str:
    """Return ``label.tld`` where label anchors on alphanumerics."""
    middle = _word(rng, rng.randint(2, 8), _DOMAIN_CHARS)
    # Strip leading/trailing hyphens to keep the regex anchor satisfied.
    middle = middle.strip("-") or "x"
    head = rng.choice("abcdefghijklmnopqrstuvwxyz0123456789")
    tail = rng.choice("abcdefghijklmnopqrstuvwxyz0123456789")
    label = f"{head}{middle}{tail}" if len(middle) >= 1 else f"{head}{tail}"
    return f"{label}.{rng.choice(_TLDS)}"


def build(seed: int) -> dict[str, Any]:
    """Return the email-address structural test vector payload.

    Args:
        seed: PRNG seed. Recorded in the artifact for reproducibility.

    Returns:
        A payload dict conforming to ``schemas/email_vectors.schema.json``.
    """
    vectors: list[dict[str, Any]] = []

    with seeded_context(seed) as rng:
        # Valid simple addresses.
        for _ in range(_VALID_SIMPLE_COUNT):
            local = _word(rng, rng.randint(3, 12))
            email = f"{local}@{_domain(rng)}"
            vectors.append(
                {
                    "email": email,
                    "valid": True,
                    "rejection_reason": None,
                    "notes": "Simple local@domain.tld.",
                },
            )

        # Valid: dotted local parts (dots only between local chars).
        for _ in range(_VALID_DOTTED_LOCAL_COUNT):
            parts = [_word(rng, rng.randint(2, 6)) for _ in range(rng.randint(2, 3))]
            local = ".".join(parts)
            email = f"{local}@{_domain(rng)}"
            vectors.append(
                {
                    "email": email,
                    "valid": True,
                    "rejection_reason": None,
                    "notes": "Dotted local part with dots only between local chars.",
                },
            )

        # Valid: ``+`` subaddressing tags.
        for _ in range(_VALID_PLUS_TAG_COUNT):
            local = _word(rng, rng.randint(3, 8))
            tag = _word(rng, rng.randint(2, 6))
            email = f"{local}+{tag}@{_domain(rng)}"
            vectors.append(
                {
                    "email": email,
                    "valid": True,
                    "rejection_reason": None,
                    "notes": "``+`` subaddressing tag in the local part.",
                },
            )

        # Invalid: leading dot in local part.
        for _ in range(_INVALID_LEADING_DOT_COUNT):
            local = _word(rng, rng.randint(3, 8))
            email = f".{local}@{_domain(rng)}"
            vectors.append(
                {
                    "email": email,
                    "valid": False,
                    "rejection_reason": "leading_dot_local",
                    "notes": "Leading dot violates the non-dot anchor.",
                },
            )

        # Invalid: consecutive dots in the local part.
        for _ in range(_INVALID_CONSECUTIVE_DOT_COUNT):
            a, b = _word(rng, rng.randint(2, 5)), _word(rng, rng.randint(2, 5))
            email = f"{a}..{b}@{_domain(rng)}"
            vectors.append(
                {
                    "email": email,
                    "valid": False,
                    "rejection_reason": "consecutive_dot_local",
                    "notes": "Consecutive dots fail the dot-followed-by-local lookahead.",
                },
            )

        # Invalid: leading or trailing dot in the domain.
        for idx in range(_INVALID_DOMAIN_DOT_COUNT):
            local = _word(rng, rng.randint(3, 8))
            tld = rng.choice(_TLDS)
            label = _word(rng, rng.randint(3, 6), _DOMAIN_CHARS).strip("-") or "x"
            if idx % 2 == 0:
                email = f"{local}@.{label}.{tld}"
                reason_note = "Leading-dot domain."
            else:
                email = f"{local}@{label}..{tld}"
                reason_note = "Consecutive dots in the domain."
            vectors.append(
                {
                    "email": email,
                    "valid": False,
                    "rejection_reason": "domain_dot_anchor",
                    "notes": reason_note,
                },
            )

        # Invalid: missing TLD entirely.
        for _ in range(_INVALID_NO_TLD_COUNT):
            local = _word(rng, rng.randint(3, 8))
            label = _word(rng, rng.randint(3, 6), _DOMAIN_CHARS).strip("-") or "x"
            vectors.append(
                {
                    "email": f"{local}@{label}",
                    "valid": False,
                    "rejection_reason": "no_tld",
                    "notes": "No ``.tld`` suffix; ``\\.[a-zA-Z]{2,}`` fails.",
                },
            )

        # Invalid: numeric TLD (``[a-zA-Z]{2,}`` requires letters).
        for _ in range(_INVALID_NUMERIC_TLD_COUNT):
            local = _word(rng, rng.randint(3, 8))
            label = _word(rng, rng.randint(3, 6), _DOMAIN_CHARS).strip("-") or "x"
            digits = "".join(str(rng.randint(0, 9)) for _ in range(rng.randint(2, 4)))
            vectors.append(
                {
                    "email": f"{local}@{label}.{digits}",
                    "valid": False,
                    "rejection_reason": "numeric_tld",
                    "notes": "All-digit TLD fails the letter-only anchor.",
                },
            )

        # Invalid: exceeds the RFC 5321 254-char cap. The regex itself accepts
        # these; the inline guard at PIIDetector.swift:454-455 rejects them.
        for idx in range(_INVALID_LENGTH_CAP_COUNT):
            target_len = _RFC5321_MAX + 1 + idx  # 255, 256, 257, 258, 259
            tld = rng.choice(_TLDS)
            label = _word(rng, 8, _DOMAIN_CHARS).strip("-") or "xxxxxxxx"
            domain_part = f"@{label}.{tld}"
            local_len = target_len - len(domain_part)
            local = _word(rng, local_len)
            email = f"{local}{domain_part}"
            vectors.append(
                {
                    "email": email,
                    "valid": False,
                    "rejection_reason": "length_cap_exceeded",
                    "notes": (
                        f"Length {len(email)} exceeds RFC 5321 cap of "
                        f"{_RFC5321_MAX}; rejected by the inline guard."
                    ),
                },
            )

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _GENERATED_BY,
        "seed": seed,
        "vectors": vectors,
    }
