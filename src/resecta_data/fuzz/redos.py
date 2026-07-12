"""Deterministic ReDoS payload builder.

Emits a fixed catalog of ``(id, pattern_class, attacker_input, target_regex_shape,
expected_behavior)`` records. The Swift-side fuzz harness feeds each
``attacker_input`` into the production regex pattern whose shape matches
``pattern_class`` and asserts the run completes under the per-page deadline.

This module must not import ``re`` — payloads are strings, not regex objects.
A test in ``tests/test_fuzz_no_re.py`` enforces that.
"""

from __future__ import annotations

from typing import Any, Final

from ._generators import (
    LENGTHS,
    alternation_overlap_input,
    backreference_trap_input,
    catastrophic_anchored_input,
    nested_quantifier_input,
)

_GENERATED_BY: Final[str] = "resecta-data/fuzz/redos"
_SCHEMA_VERSION: Final[int] = 1

# How each pattern class maps to its shape description and expected behavior.
# Smaller inputs are linear baselines; longer ones are where a pathological
# engine starts to blow up.
_LINEAR_CEILING: Final[int] = 32


def build(seed: int) -> dict[str, Any]:
    """Return the ReDoS payload catalog.

    Args:
        seed: PRNG seed. Recorded in the artifact for reproducibility. The
            current catalog is fully deterministic without randomness; the
            seed is accepted for uniformity with other Phase 1 builders and
            in case future payload families introduce stochastic mutations.

    Returns:
        A payload dict conforming to ``schemas/redos_payloads.schema.json``.
    """
    payloads: list[dict[str, Any]] = []

    pattern_families: tuple[tuple[str, str, object], ...] = (
        (
            "nested_quantifier",
            "(a+)+b",
            nested_quantifier_input,
        ),
        (
            "alternation_overlap",
            "(a|ab)*b",
            alternation_overlap_input,
        ),
        (
            "backreference_trap",
            "^(.*)\\1$",
            backreference_trap_input,
        ),
        (
            "catastrophic_anchored",
            "^(a+)+$",
            catastrophic_anchored_input,
        ),
    )

    for pattern_class, regex_shape, generator in pattern_families:
        for rung, length in enumerate(LENGTHS):
            attacker_input = generator(length)  # type: ignore[operator]
            expected = "linear" if length <= _LINEAR_CEILING else "superlinear"
            payloads.append(
                {
                    "id": f"{pattern_class}_{rung:02d}",
                    "pattern_class": pattern_class,
                    "attacker_input": attacker_input,
                    "target_regex_shape": regex_shape,
                    "expected_behavior": expected,
                    "notes": f"Length {length}.",
                }
            )

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _GENERATED_BY,
        "seed": seed,
        "payloads": payloads,
    }
