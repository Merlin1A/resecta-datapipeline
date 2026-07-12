"""Name Bloom filter builder (Phase 2).

Builds ``surnames.bloom``, ``given-names.bloom``, and ``gazetteer_manifest.json``
consumed by ``NameGazetteer.swift`` via ``Bundle.module``. Format contract is
pinned by the Swift side; see ``spec.py`` for the shared-with-Swift header
layout and ``hasher.py`` for the MurmurHash3_x64_128 parity requirement.
"""

from __future__ import annotations

from .corpus_ingest import IngestedRow, IngestResult, merge
from .filter import BloomFilter, optimal_bits
from .manifest import FilterBuildResult, build_cutover_diff, build_manifest
from .normalize import nfkc_lower
from .packer import RsbfHeader, pack, unpack

__all__ = [
    "BloomFilter",
    "FilterBuildResult",
    "IngestResult",
    "IngestedRow",
    "RsbfHeader",
    "build_cutover_diff",
    "build_manifest",
    "merge",
    "nfkc_lower",
    "optimal_bits",
    "pack",
    "unpack",
]
