"""MurmurHash3_x64_128 parity between the pure-Python port and ``mmh3``.

Goldens were generated with ``mmh3.hash64(..., signed=False)``. The pure-Python
port in ``bloom.hasher`` is the source of truth for the Swift-side port —
keeping all three in sync is the keystone of the cross-language Bloom
contract (see ``bloom/spec.py``).
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import mmh3
import pytest

from resecta_data.bloom.hasher import murmurhash3_x64_128

_GOLDENS_PATH = Path(__file__).parent / "fixtures" / "murmurhash3_goldens.json"


def _load_goldens() -> list[dict[str, Any]]:
    payload = json.loads(_GOLDENS_PATH.read_text())
    vectors: list[dict[str, Any]] = payload["vectors"]
    return vectors


def _vector_id(v: dict[str, Any]) -> str:
    return f"len{v['length']}_seed{v['seed']}"


@pytest.mark.parametrize("vector", _load_goldens(), ids=_vector_id)
def test_pure_python_matches_goldens(vector: dict[str, Any]) -> None:
    data = bytes.fromhex(vector["input_hex"])
    got = murmurhash3_x64_128(data, vector["seed"])
    expected = (vector["h1"], vector["h2"])
    assert got == expected


def test_mmh3_also_matches_goldens() -> None:
    """Sanity: regenerating against the current mmh3 should not drift."""
    for vector in _load_goldens():
        data = bytes.fromhex(vector["input_hex"])
        got = mmh3.hash64(data, seed=vector["seed"], signed=False)
        assert got == (vector["h1"], vector["h2"])


def test_pure_python_matches_mmh3_on_random_inputs() -> None:
    """Cross-check on inputs outside the golden set — catches drift early."""
    rng = random.Random(20260416)  # noqa: S311 — determinism, not security
    for _ in range(200):
        n = rng.randint(0, 257)
        data = bytes(rng.randint(0, 255) for _ in range(n))
        seed = rng.randint(0, 0xFFFFFFFF)
        assert murmurhash3_x64_128(data, seed) == mmh3.hash64(data, seed=seed, signed=False)
