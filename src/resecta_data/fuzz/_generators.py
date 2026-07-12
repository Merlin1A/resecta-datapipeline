"""String generators for ReDoS fuzz payloads.

Every function in this module returns a plain ``str`` — the point is to
catalog attacker-shaped inputs, not to compile or execute the regex shapes
they target. Do NOT import ``re`` anywhere in this package; a test enforces
that.
"""

from __future__ import annotations

from typing import Final

# Length ladder for each pattern class. Lengths chosen so superlinear regex
# engines show quadratic-or-worse blowup around the higher rungs while small
# rungs remain tractable baselines.
LENGTHS: Final[tuple[int, ...]] = (8, 16, 32, 64, 128, 256, 512, 1024)


def nested_quantifier_input(length: int) -> str:
    """Return an attacker input that stresses ``(a+)+b`` shaped regexes.

    The payload is ``length`` copies of ``a`` with no trailing ``b``. An
    engine that naively tries every partition of the run of ``a``s into one-or-
    more groups exhibits exponential backtracking before concluding there is
    no match.
    """
    return "a" * length


def alternation_overlap_input(length: int) -> str:
    """Return an attacker input for ``(a|ab)*b`` shaped regexes.

    Alternating branches overlap on ``a``, so an engine that tries both
    branches at each position must explore an exponential set of parse paths.
    """
    return "a" * length


def backreference_trap_input(length: int) -> str:
    """Return an input shaped for ``^(.*)\\1$`` backreference regexes.

    Odd lengths cannot be split into two equal halves, so an engine must
    attempt every split before concluding there is no match.
    """
    # Odd length by construction.
    odd_len = length if length % 2 == 1 else length + 1
    return "x" * odd_len


def catastrophic_anchored_input(length: int) -> str:
    """Return a payload for anchored catastrophic-backtracking shapes.

    ``^(a+)+$`` against a run of ``a``s followed by a non-matching character
    backtracks over every partition of the run.
    """
    return ("a" * length) + "!"
