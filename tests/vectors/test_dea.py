"""Tests for the DEA vector builder.

Mirrors the Swift DEA detector contract: two uppercase letters followed by
seven digits, with the seventh derived from the position-weighted checksum
``(d0+d2+d4 + 2*(d1+d3+d5)) mod 10``. The Python reference lives at
``resecta_data.vectors._checksum.dea_check_digit``;
live-grep verified 2026-04-26.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from hypothesis import given, settings
from hypothesis import strategies as st

from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.io import dump_canonical_json
from resecta_data.common.schema import validate_file
from resecta_data.vectors import build_dea_vectors
from resecta_data.vectors._checksum import dea_check_digit

SCHEMAS_DIR = Path(__file__).parent.parent.parent / "schemas"

_DEA_LENGTH: Final[int] = 9
_CATEGORY_ENUM: Final[frozenset[str]] = frozenset(
    {"valid", "invalid_checksum", "invalid_prefix_letter", "invalid_length"},
)


def test_determinism(tmp_build_dir: Path) -> None:
    """Two runs with the canonical seed must be byte-identical."""
    payload_a = build_dea_vectors(CANONICAL_SEED)
    payload_b = build_dea_vectors(CANONICAL_SEED)
    path_a = tmp_build_dir / "a.json"
    path_b = tmp_build_dir / "b.json"
    dump_canonical_json(payload_a, path_a)
    dump_canonical_json(payload_b, path_b)
    assert path_a.read_bytes() == path_b.read_bytes()


def test_schema(tmp_build_dir: Path) -> None:
    """Output validates against dea_test_vectors.schema.json."""
    payload = build_dea_vectors(CANONICAL_SEED)
    path = tmp_build_dir / "dea.json"
    dump_canonical_json(payload, path)
    validate_file(path, SCHEMAS_DIR, "dea_test_vectors")


def test_category_coverage() -> None:
    """At least one vector per category enum value."""
    payload = build_dea_vectors(CANONICAL_SEED)
    seen = {vec["category"] for vec in payload["vectors"]}
    assert seen == _CATEGORY_ENUM, f"Missing categories: {_CATEGORY_ENUM - seen}"


def test_expected_check_digit_matches_for_valid_rows() -> None:
    """For valid rows, ``expected_check_digit`` matches ``dea_check_digit(dea[2:8])``
    and equals the trailing digit ``dea[8]``."""
    payload = build_dea_vectors(CANONICAL_SEED)
    for vec in payload["vectors"]:
        if vec["category"] == "valid":
            first_six = vec["dea"][2:8]
            assert vec["expected_check_digit"] == dea_check_digit(first_six)
            assert vec["expected_check_digit"] == int(vec["dea"][8])


@settings(deadline=None, max_examples=25)
@given(seed=st.integers(min_value=0, max_value=2**32 - 1))
def test_any_seed_yields_a_consistent_catalog(seed: int) -> None:
    """For every seed the catalog is a pure function of the seed, every valid row's trailing
    digit closes the checksum, every invalid_checksum row's breaks it, and every row of those
    two categories is two letters + seven digits with ``expected_check_digit`` = the reference."""
    payload = build_dea_vectors(seed)
    assert payload == build_dea_vectors(seed)
    for vec in payload["vectors"]:
        if vec["category"] not in ("valid", "invalid_checksum"):
            continue
        dea = vec["dea"]
        assert len(dea) == _DEA_LENGTH
        assert dea[:2].isalpha() and dea[:2].isupper()
        assert dea[2:].isdigit()
        reference = dea_check_digit(dea[2:8])
        assert vec["expected_check_digit"] == reference
        if vec["category"] == "valid":
            assert int(dea[8]) == reference
        else:
            assert int(dea[8]) != reference
