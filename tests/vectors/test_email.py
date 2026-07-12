"""Tests for the email-address vector builder.

Mirrors the Swift ``detectEmails`` regex at PIIDetector.swift:451 (pattern
declared at :447-449) plus the inline RFC 5321 ``> 254`` cap at :454-455.
Live-grep verified 2026-04-20.
"""

from __future__ import annotations

import re
from pathlib import Path

from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.vectors import build_email_vectors

SCHEMAS_DIR = Path(__file__).parent.parent.parent / "schemas"

# Direct port of PIIDetector.swift:447-449. Anchored with fullmatch.
_SWIFT_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9_%+-](?:[a-zA-Z0-9_%+-]|\.(?=[a-zA-Z0-9_%+-]))*"
    r"@(?:[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?)\.[a-zA-Z]{2,}"
)
_RFC5321_MAX = 254


def test_determinism(tmp_build_dir: Path) -> None:
    payload_a = build_email_vectors(CANONICAL_SEED)
    payload_b = build_email_vectors(CANONICAL_SEED)
    path_a = tmp_build_dir / "a.json"
    path_b = tmp_build_dir / "b.json"
    dump_canonical_json(payload_a, path_a)
    dump_canonical_json(payload_b, path_b)
    assert path_a.read_bytes() == path_b.read_bytes()


def test_schema(tmp_build_dir: Path) -> None:
    payload = build_email_vectors(CANONICAL_SEED)
    path = tmp_build_dir / "email.json"
    dump_canonical_json(payload, path)
    validate_file(path, SCHEMAS_DIR, "email_vectors")


def test_shape_compliance() -> None:
    """Valid vectors fullmatch the Swift regex AND fall under the cap;
    invalid vectors fail at least one of those gates."""
    payload = build_email_vectors(CANONICAL_SEED)
    for vec in payload["vectors"]:
        regex_ok = _SWIFT_EMAIL_RE.fullmatch(vec["email"]) is not None
        length_ok = len(vec["email"]) <= _RFC5321_MAX
        if vec["valid"]:
            assert regex_ok and length_ok, (
                f"Valid vector {vec['email']!r} should pass both gates "
                f"(regex={regex_ok}, length={length_ok})"
            )
        else:
            assert not (regex_ok and length_ok), (
                f"Invalid vector {vec['email']!r} should fail at least one gate "
                f"(regex={regex_ok}, length={length_ok})"
            )


def test_rfc5321_length_cap() -> None:
    """No emitted vector marked valid exceeds the 254-character cap."""
    payload = build_email_vectors(CANONICAL_SEED)
    for vec in payload["vectors"]:
        if vec["valid"]:
            assert len(vec["email"]) <= _RFC5321_MAX, (
                f"Valid vector exceeds RFC 5321 cap: len={len(vec['email'])} "
                f"vector={vec['email']!r}"
            )
    # And: at least one vector exists explicitly to exercise the cap.
    cap_vectors = [v for v in payload["vectors"] if v["rejection_reason"] == "length_cap_exceeded"]
    assert cap_vectors, "Expected at least one length_cap_exceeded vector"
    for vec in cap_vectors:
        assert len(vec["email"]) > _RFC5321_MAX, (
            f"length_cap_exceeded vector should exceed {_RFC5321_MAX} chars; "
            f"got {len(vec['email'])}"
        )
