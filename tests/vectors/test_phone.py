"""Tests for the phone-number vector builder.

Mirrors the Swift ``detectPhones`` regex at PIIDetector.swift:491 (pattern
declared at :468-470). Live-grep verified 2026-04-20.
"""

from __future__ import annotations

import re
from pathlib import Path

from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.vectors import build_phone_vectors

SCHEMAS_DIR = Path(__file__).parent.parent.parent / "schemas"

# Direct port of PIIDetector.swift:468-470. Anchored with `^...$` for the
# fullmatch shape-compliance assertion below; the inline detector uses
# (?<!\d) / (?!\d) lookarounds for substring detection, which fullmatch
# satisfies trivially.
_SWIFT_PHONE_RE = re.compile(
    r"(?:\+1[\s.-]?)?"
    r"(?:\(\d{3}\)[\s.-]?\d{3}[\s.-]?\d{4}|\d{3}[\s.-]?\d{3}[\s.-]?\d{4})"
)


def test_determinism(tmp_build_dir: Path) -> None:
    """Two runs with the canonical seed must be byte-identical."""
    payload_a = build_phone_vectors(CANONICAL_SEED)
    payload_b = build_phone_vectors(CANONICAL_SEED)
    path_a = tmp_build_dir / "a.json"
    path_b = tmp_build_dir / "b.json"
    dump_canonical_json(payload_a, path_a)
    dump_canonical_json(payload_b, path_b)
    assert path_a.read_bytes() == path_b.read_bytes()


def test_schema(tmp_build_dir: Path) -> None:
    """Output validates against phone_vectors.schema.json."""
    payload = build_phone_vectors(CANONICAL_SEED)
    path = tmp_build_dir / "phone.json"
    dump_canonical_json(payload, path)
    validate_file(path, SCHEMAS_DIR, "phone_vectors")


def test_shape_compliance() -> None:
    """Every valid vector fullmatches the Swift phone regex; every invalid does not."""
    payload = build_phone_vectors(CANONICAL_SEED)
    for vec in payload["vectors"]:
        if vec["valid"]:
            assert _SWIFT_PHONE_RE.fullmatch(vec["phone"]), (
                f"Valid vector {vec['phone']!r} should fullmatch the Swift regex"
            )
        else:
            assert not _SWIFT_PHONE_RE.fullmatch(vec["phone"]), (
                f"Invalid vector {vec['phone']!r} should NOT fullmatch the Swift regex"
            )
