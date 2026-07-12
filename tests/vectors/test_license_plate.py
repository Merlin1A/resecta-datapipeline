"""Tests for the license-plate vector builder.

Mirrors the Swift ``detectLicensePlate`` regex at PIIDetector.swift:795
(pattern declared at :789-792). Live-grep verified 2026-04-20.
"""

from __future__ import annotations

import re
from pathlib import Path

from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.vectors import build_license_plate_vectors

SCHEMAS_DIR = Path(__file__).parent.parent.parent / "schemas"

# Direct port of PIIDetector.swift:789-792.
_SWIFT_PLATE_RE = re.compile(
    r"\b(?:license\s+plate|plate\s+(?:no|number|#)\.?|tag\s+(?:no|number|#)\.?"
    r"|lp\s*#|reg(?:istration)?\s*#|veh(?:icle)?\s+plate)"
    r"[:#\s]+[A-Z0-9]{2,3}[-\s]?[A-Z0-9]{2,5}\b",
    re.IGNORECASE,
)
# The plate-value-only sub-pattern from the same source.
_PLATE_VALUE_RE = re.compile(r"[A-Z0-9]{2,3}[-\s]?[A-Z0-9]{2,5}")


def test_determinism(tmp_build_dir: Path) -> None:
    payload_a = build_license_plate_vectors(CANONICAL_SEED)
    payload_b = build_license_plate_vectors(CANONICAL_SEED)
    path_a = tmp_build_dir / "a.json"
    path_b = tmp_build_dir / "b.json"
    dump_canonical_json(payload_a, path_a)
    dump_canonical_json(payload_b, path_b)
    assert path_a.read_bytes() == path_b.read_bytes()


def test_schema(tmp_build_dir: Path) -> None:
    payload = build_license_plate_vectors(CANONICAL_SEED)
    path = tmp_build_dir / "license_plate.json"
    dump_canonical_json(payload, path)
    validate_file(path, SCHEMAS_DIR, "license_plate_vectors")


def test_shape_compliance() -> None:
    """Valid vectors fullmatch the plate sub-pattern AND match the full Swift
    regex. Invalid vectors fail per the rejection_reason: ``no_label`` fails
    the full regex; segment / separator failures cause the labeled plate
    value to fail the plate sub-pattern (greedy backtracking inside the full
    text could still produce a partial match elsewhere — that is a feature
    of the Swift regex, not a violation of these vectors)."""
    payload = build_license_plate_vectors(CANONICAL_SEED)
    for vec in payload["vectors"]:
        plate_ok = _PLATE_VALUE_RE.fullmatch(vec["plate"]) is not None
        full_ok = _SWIFT_PLATE_RE.search(vec["text"]) is not None
        if vec["valid"]:
            assert plate_ok, f"Valid plate {vec['plate']!r} should fullmatch the plate sub-pattern"
            assert full_ok, f"Valid vector {vec['text']!r} should match the full Swift regex"
        elif vec["rejection_reason"] == "no_label":
            assert not full_ok, (
                f"no_label vector {vec['text']!r} should NOT match the full Swift regex"
            )
        else:
            assert not plate_ok, (
                f"Invalid plate {vec['plate']!r} should NOT fullmatch the plate sub-pattern"
            )
