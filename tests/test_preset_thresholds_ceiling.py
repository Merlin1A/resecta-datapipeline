"""Ceiling regression test for the SHIPPED preset_thresholds.json (speed plan #18).

Targets ``build/classifier/preset_thresholds.json`` — the sweep OUTPUT — not
the candidates builder: ``tests/test_preset_thresholds.py`` covers
``build_preset_thresholds``, and that gap is exactly how the degenerate
balanced/conservative ``name=0.98`` cutoffs shipped (they sat above the
NLTagger name detector's 0.65-0.85 reachable range, so the W4 gate withheld
every name candidate; see the notes field of the 2026-06-01 hand-fix inside
the file).

Strictness history: shipped 2026-06-10 as ``xfail(strict=False)`` because
the committed file still carried the degenerate bytes (the safe-floor
hand-fix lived only in the working tree). PR #100 (2026-06-11) committed
the hand-fixed file, so the marker is gone and this is now a hard assert:
any future sweep that regenerates an unreachable cutoff goes red here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_PRESET_PATH = Path(__file__).parent.parent / "build" / "classifier" / "preset_thresholds.json"

# Highest per-category posterior cutoff a preset may ship. Sourced from the
# NLTagger name detector's documented 0.65-0.85 reachable output range (see
# the hand-fix notes): a fresh document carries prior mean 0.5, so the
# posterior equals the raw score, and any cutoff above the reachable range
# withholds every candidate of that category from triage (W4 gate).
_CEILING = 0.90


@pytest.mark.skipif(
    not _PRESET_PATH.exists(),
    reason="shipped preset absent (fresh tree; produced by the calibrate flow)",
)
def test_shipped_preset_thresholds_within_detector_reach() -> None:
    payload = json.loads(_PRESET_PATH.read_text())
    offenders = [
        f"{preset}.{category}={value}"
        for preset, vals in sorted(payload["presets"].items())
        for category, value in sorted(vals.items())
        if value > _CEILING
    ]
    assert not offenders, (
        f"preset thresholds exceed the detector-reachable ceiling ({_CEILING}): "
        + ", ".join(offenders)
        + " — candidates in these categories would be withheld from triage"
    )
