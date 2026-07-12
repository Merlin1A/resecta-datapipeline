"""Tests for the driver's-license vector builder.

Mirrors the Swift ``detectDriversLicenses`` regex at PIIDetector.swift:648
(pattern declared at :643-646). Live-grep verified 2026-04-20.
"""

from __future__ import annotations

import re
from pathlib import Path

from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.vectors import build_drivers_license_vectors

SCHEMAS_DIR = Path(__file__).parent.parent.parent / "schemas"

# Direct port of PIIDetector.swift:643-646. Label is case-insensitive; the
# capture group's letter is case-sensitive [A-Z].
_SWIFT_DL_RE = re.compile(
    r"(?:Driver(?:'?s)?\s+Lic(?:ense)?|DL|D\.?L\.?)\s*[:#]?\s*([A-Z]\d{4,14}|\d{6,12})",
    re.IGNORECASE,
)
_CAPTURE_RE = re.compile(r"[A-Z]\d{4,14}|\d{6,12}")


def _captures(text: str, expected: str) -> bool:
    m = _SWIFT_DL_RE.search(text)
    if m is None:
        return False
    captured = m.group(1)
    return captured == expected and _CAPTURE_RE.fullmatch(captured) is not None


def test_determinism(tmp_build_dir: Path) -> None:
    payload_a = build_drivers_license_vectors(CANONICAL_SEED)
    payload_b = build_drivers_license_vectors(CANONICAL_SEED)
    path_a = tmp_build_dir / "a.json"
    path_b = tmp_build_dir / "b.json"
    dump_canonical_json(payload_a, path_a)
    dump_canonical_json(payload_b, path_b)
    assert path_a.read_bytes() == path_b.read_bytes()


def test_schema(tmp_build_dir: Path) -> None:
    payload = build_drivers_license_vectors(CANONICAL_SEED)
    path = tmp_build_dir / "drivers_license.json"
    dump_canonical_json(payload, path)
    validate_file(path, SCHEMAS_DIR, "drivers_license_vectors")


def test_shape_compliance() -> None:
    """Each valid vector matches with the expected capture; each invalid does not."""
    payload = build_drivers_license_vectors(CANONICAL_SEED)
    for vec in payload["vectors"]:
        ok = _captures(vec["text"], vec["license_number"])
        if vec["valid"]:
            assert ok, (
                f"Valid vector {vec['text']!r} should match with capture {vec['license_number']!r}"
            )
        else:
            assert not ok, f"Invalid vector {vec['text']!r} should NOT match the Swift regex"
