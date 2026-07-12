"""Tests for the Bates-stamp vector builder.

Mirrors the Swift ``detectBates`` regex at PIIDetector.swift:755 (pattern
declared at :748-751). Live-grep verified 2026-04-20.
"""

from __future__ import annotations

import re
from pathlib import Path

from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.vectors import build_bates_vectors

SCHEMAS_DIR = Path(__file__).parent.parent.parent / "schemas"

# Direct port of PIIDetector.swift:748-751. NOT case-insensitive in Swift.
_SWIFT_BATES_RE = re.compile(r"\b[A-Z]{1,10}[-_ ]?\d{4,10}\b")


def test_determinism(tmp_build_dir: Path) -> None:
    payload_a = build_bates_vectors(CANONICAL_SEED)
    payload_b = build_bates_vectors(CANONICAL_SEED)
    path_a = tmp_build_dir / "a.json"
    path_b = tmp_build_dir / "b.json"
    dump_canonical_json(payload_a, path_a)
    dump_canonical_json(payload_b, path_b)
    assert path_a.read_bytes() == path_b.read_bytes()


def test_schema(tmp_build_dir: Path) -> None:
    payload = build_bates_vectors(CANONICAL_SEED)
    path = tmp_build_dir / "bates.json"
    dump_canonical_json(payload, path)
    validate_file(path, SCHEMAS_DIR, "bates_vectors")


def test_shape_compliance() -> None:
    """Each valid vector matches the Swift Bates regex; each invalid does not.

    For invalid vectors we require fullmatch failure: ``\\b...\\b`` is a
    substring match in Swift, so a partial match against a longer rejection
    string is permitted as long as the literal vector itself is not the
    full match.
    """
    payload = build_bates_vectors(CANONICAL_SEED)
    for vec in payload["vectors"]:
        m = _SWIFT_BATES_RE.fullmatch(vec["stamp"])
        if vec["valid"]:
            assert m is not None, f"Valid vector {vec['stamp']!r} should fullmatch the Swift regex"
        else:
            assert m is None, (
                f"Invalid vector {vec['stamp']!r} should NOT fullmatch the Swift regex"
            )
