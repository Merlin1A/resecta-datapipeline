"""Tests for the ITIN vector builder."""

from __future__ import annotations

from pathlib import Path

from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.vectors import build_itin_vectors

SCHEMAS_DIR = Path(__file__).parent.parent.parent / "schemas"

# IRS ranges per M6. Every boundary ±1 must surface as a vector.
_VALID_YY_RANGES = ((50, 65), (70, 88), (90, 92), (94, 99))
_REQUIRED_BOUNDARIES = frozenset(
    {49, 50, 65, 66, 69, 70, 88, 89, 90, 92, 93, 94, 99},
)


def _yy_from_itin(itin: str) -> int:
    # Format is 9AA-YY-SSSS.
    return int(itin.split("-")[1])


def _yy_is_valid(yy: int) -> bool:
    return any(low <= yy <= high for low, high in _VALID_YY_RANGES)


def test_determinism(tmp_build_dir: Path) -> None:
    """Two runs with the canonical seed must be byte-identical."""
    payload_a = build_itin_vectors(CANONICAL_SEED)
    payload_b = build_itin_vectors(CANONICAL_SEED)
    path_a = tmp_build_dir / "a.json"
    path_b = tmp_build_dir / "b.json"
    dump_canonical_json(payload_a, path_a)
    dump_canonical_json(payload_b, path_b)
    assert path_a.read_bytes() == path_b.read_bytes()


def test_schema(tmp_build_dir: Path) -> None:
    """Output validates against itin_vectors.schema.json."""
    payload = build_itin_vectors(CANONICAL_SEED)
    path = tmp_build_dir / "itin.json"
    dump_canonical_json(payload, path)
    validate_file(path, SCHEMAS_DIR, "itin_vectors")


def test_yy_coverage() -> None:
    """Every IRS-range boundary ±1 appears as a vector with matching yy_bucket."""
    payload = build_itin_vectors(CANONICAL_SEED)
    seen_boundaries: set[int] = set()
    for vec in payload["vectors"]:
        yy = _yy_from_itin(vec["itin"])
        expected_bucket = "valid" if _yy_is_valid(yy) else "invalid"
        assert vec["yy_bucket"] == expected_bucket, (
            f"YY {yy} expected bucket {expected_bucket}, got {vec['yy_bucket']}"
        )
        if yy in _REQUIRED_BOUNDARIES:
            seen_boundaries.add(yy)
    missing = _REQUIRED_BOUNDARIES - seen_boundaries
    assert not missing, f"Missing boundary YYs: {sorted(missing)}"


def test_yy_bucket_matches_valid_flag() -> None:
    """`valid` and `yy_bucket` are consistent."""
    payload = build_itin_vectors(CANONICAL_SEED)
    for vec in payload["vectors"]:
        if vec["yy_bucket"] == "valid":
            assert vec["valid"] is True
            assert vec["rejection_reason"] is None
        else:
            assert vec["valid"] is False
            assert vec["rejection_reason"] == "yy_out_of_range"
