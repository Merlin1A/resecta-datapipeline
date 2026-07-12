"""Reachability envelope: every shipped threshold < achievable max.

This test pins the invariant that no category's calibrated threshold can
exceed the maximum score its detector can physically emit on a real device.
The _DETECTOR_ACHIEVABLE_MAX table in sweep_thresholds is the authority;
this test cross-checks the shipped artifact against it.

Introduced by search-impl S1 (design 03 §0.2). It was RED on the pre-S1
artifact (conservative address=0.75 > 0.65 ceiling, conservative
account=0.75 > 0.70 ceiling); the 0.3 hand-fix turned it green — that
red→green transition is the S1 acceptance evidence.

S1 staging note: ``dob`` is excluded from the hard assert and pinned by a
companion expected-state test instead. The dob detector's textual max is
0.50 (ceiling 0.45), below every shipped preset — the C6a incident. S2
ships the label-anchored DOB revival plus the 0.30/0.40/0.45 dob preset
values; at that point the companion pin goes red and both it and the
exclusion must be removed. This mirrors the iOS EnvelopeReachabilityTests'
``withKnownIssue`` dob row (design 01 §12 staging note).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

import pytest

from resecta_data.classifier.sweep_thresholds import (
    _CATEGORIES,
    _DETECTOR_ACHIEVABLE_MAX,
    _FEASIBILITY_MARGIN,
)

_PRESET_PATH = Path(__file__).parent.parent / "build" / "classifier" / "preset_thresholds.json"

# S2 (search-impl, design 01 §1 D4): the label-anchored dob path shipped
# with the 0.30/0.40/0.45 preset rows, so dob is reachable and back under
# the hard assert. The set stays (empty) as the staging mechanism for any
# future known-unreachable window.
_S1_KNOWN_UNREACHABLE: Final[frozenset[str]] = frozenset()


def _load_presets() -> dict[str, dict[str, float]]:
    data = json.loads(_PRESET_PATH.read_text())
    presets: dict[str, dict[str, float]] = data["presets"]
    return presets


@pytest.mark.skipif(
    not _PRESET_PATH.exists(),
    reason="build/classifier/preset_thresholds.json not present; "
    "run make calibrate or make classifier first",
)
def test_all_thresholds_below_achievable_max() -> None:
    """Every category x preset threshold <= achievable_max - margin."""
    failures: list[str] = []
    for preset_name, vector in _load_presets().items():
        for category, threshold in vector.items():
            if category not in _DETECTOR_ACHIEVABLE_MAX:
                continue  # ungated categories not yet in the table are skipped
            if category in _S1_KNOWN_UNREACHABLE:
                continue  # pinned by test_dob_unreachable_is_pinned_until_s2
            # Round to the clamp's 6-place convention: 0.70 - 0.05 is
            # 0.6499999... in binary floating point.
            ceiling = round(_DETECTOR_ACHIEVABLE_MAX[category] - _FEASIBILITY_MARGIN, 6)
            if threshold > ceiling:
                failures.append(
                    f"{preset_name}/{category}: threshold={threshold:.4f} "
                    f"> ceiling={ceiling:.4f} "
                    f"(max={_DETECTOR_ACHIEVABLE_MAX[category]:.2f})"
                )
    assert not failures, (
        "Reachability violations found — these thresholds can never be "
        "reached by the detector and will silently suppress the category:\n" + "\n".join(failures)
    )


def test_gated_categories_covered_by_achievable_max() -> None:
    """All W4-gated categories are in _DETECTOR_ACHIEVABLE_MAX."""
    for cat in _CATEGORIES:
        assert cat in _DETECTOR_ACHIEVABLE_MAX, (
            f"Category '{cat}' is in the sweep but missing from "
            f"_DETECTOR_ACHIEVABLE_MAX; add it with a Swift citation."
        )


@pytest.mark.skipif(
    not _PRESET_PATH.exists(),
    reason="preset file not built",
)
def test_no_preset_is_zero_or_one() -> None:
    """No threshold may be 0.0 or 1.0 (sentinels, not calibrated values)."""
    for preset_name, vector in _load_presets().items():
        for category, threshold in vector.items():
            assert 0.0 < threshold < 1.0, (
                f"{preset_name}/{category}={threshold} is a sentinel value, "
                "not a calibrated threshold."
            )
