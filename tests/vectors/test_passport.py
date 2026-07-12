"""Tests for the passport vector builder.

Mirrors the Swift ``detectPassports`` regex at PIIDetector.swift:668 (pattern
declared at :663-666). Live-grep verified 2026-04-20.
"""

from __future__ import annotations

import re
from pathlib import Path

from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.vectors import build_passport_vectors

SCHEMAS_DIR = Path(__file__).parent.parent.parent / "schemas"

# Direct port of PIIDetector.swift:663-666 (case-insensitive on label only;
# the captured number portion is letter-case-sensitive [A-Z]{1,2}\d{6,9}).
_SWIFT_PASSPORT_RE = re.compile(
    r"(?:Passport|PP|Passport\s+No|Passport\s+Number)\s*[#:]?\s*([A-Z]{1,2}\d{6,9})",
    re.IGNORECASE,
)


def _captures_uppercase_number(text: str, expected: str) -> bool:
    """Reproduce the Swift behaviour: capture group is [A-Z]{1,2}\\d{6,9}.

    Python's IGNORECASE applies to the whole pattern, so we re-check the
    captured group against the case-sensitive sub-pattern.
    """
    m = _SWIFT_PASSPORT_RE.search(text)
    if m is None:
        return False
    captured = m.group(1)
    return captured == expected and re.fullmatch(r"[A-Z]{1,2}\d{6,9}", captured) is not None


def test_determinism(tmp_build_dir: Path) -> None:
    payload_a = build_passport_vectors(CANONICAL_SEED)
    payload_b = build_passport_vectors(CANONICAL_SEED)
    path_a = tmp_build_dir / "a.json"
    path_b = tmp_build_dir / "b.json"
    dump_canonical_json(payload_a, path_a)
    dump_canonical_json(payload_b, path_b)
    assert path_a.read_bytes() == path_b.read_bytes()


def test_schema(tmp_build_dir: Path) -> None:
    payload = build_passport_vectors(CANONICAL_SEED)
    path = tmp_build_dir / "passport.json"
    dump_canonical_json(payload, path)
    validate_file(path, SCHEMAS_DIR, "passport_vectors")


def test_shape_compliance() -> None:
    """Every valid vector matches the Swift regex with the expected capture."""
    payload = build_passport_vectors(CANONICAL_SEED)
    for vec in payload["vectors"]:
        ok = _captures_uppercase_number(vec["text"], vec["passport_number"])
        if vec["valid"]:
            assert ok, (
                f"Valid vector {vec['text']!r} should match the Swift regex with "
                f"capture {vec['passport_number']!r}"
            )
        else:
            assert not ok, f"Invalid vector {vec['text']!r} should NOT match the Swift regex"
