"""Tests for the EIN vector builder."""

from __future__ import annotations

import re
from pathlib import Path

from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.vectors import build_ein_vectors

SCHEMAS_DIR = Path(__file__).parent.parent.parent / "schemas"

_VALID_EIN_PATTERN = re.compile(r"^\d{2}-\d{7}$")


def test_determinism(tmp_build_dir: Path) -> None:
    """Two runs with the canonical seed must be byte-identical."""
    payload_a = build_ein_vectors(CANONICAL_SEED)
    payload_b = build_ein_vectors(CANONICAL_SEED)
    path_a = tmp_build_dir / "a.json"
    path_b = tmp_build_dir / "b.json"
    dump_canonical_json(payload_a, path_a)
    dump_canonical_json(payload_b, path_b)
    assert path_a.read_bytes() == path_b.read_bytes()


def test_schema(tmp_build_dir: Path) -> None:
    """Output validates against ein_vectors.schema.json."""
    payload = build_ein_vectors(CANONICAL_SEED)
    path = tmp_build_dir / "ein.json"
    dump_canonical_json(payload, path)
    validate_file(path, SCHEMAS_DIR, "ein_vectors")


def test_valid_rows_match_regex() -> None:
    """Every ``valid: true`` EIN matches the Swift regex shape."""
    payload = build_ein_vectors(CANONICAL_SEED)
    for vec in payload["vectors"]:
        if vec["valid"]:
            assert _VALID_EIN_PATTERN.match(vec["ein"]), (
                f"Valid EIN did not match regex: {vec['ein']}"
            )


def test_invalid_rows_fail_regex() -> None:
    """Every ``valid: false`` EIN fails the Swift regex shape."""
    payload = build_ein_vectors(CANONICAL_SEED)
    for vec in payload["vectors"]:
        if not vec["valid"]:
            assert not _VALID_EIN_PATTERN.match(vec["ein"]), (
                f"Invalid EIN unexpectedly matched regex: {vec['ein']}"
            )
