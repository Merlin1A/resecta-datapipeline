"""Binary format constants for the RSBF Bloom filter file.

=== SHARED WITH SWIFT ===
Changes to this file require a matching change in:
  ../Packages/RedactionEngine/Sources/RedactionEngine/Detection/Gazetteer/BloomFilter.swift
  ../Packages/RedactionEngine/Sources/RedactionEngine/Detection/Gazetteer/GazetteerManifest.swift
Coordinated PRs only.

The RSBF (Resecta Bloom Filter) format is a 63-byte header + bit array:

    Offset  Size   Field
    [0..3]  4B     Magic "RSBF" (ASCII)
    [4..5]  u16 LE Version (currently 1)
    [6]     u8     k — number of hash functions
    [7..14] u64 LE m — number of bits in the filter
    [15..22] u64 LE Seed for MurmurHash3 (lower 32 bits used)
    [23..30] u64 LE Row count — entries inserted
    [31..62] 32B   SHA-256 of sorted, NFKC-lowercased source rows
    [63..]  ceil(m/8) B  Bit array, little-endian bit ordering
"""

from __future__ import annotations

from typing import Final

RSBF_MAGIC: Final[bytes] = b"RSBF"
RSBF_VERSION: Final[int] = 1
HEADER_SIZE: Final[int] = 63
SOURCE_HASH_SIZE: Final[int] = 32

# Sizing defaults. A 0.1% FPR with k=10 requires m ≈ n * 14.378 bits.
# For ~600 bootstrap names we use a conservative m of 65,536 bits (8 KiB) — far
# more than needed, but keeps the bit array size stable while the bootstrap
# corpora grow. Full corpora (see scripts/fetch_*.sh) target ~1.2 MiB per filter
# at ~600K names. The builder sizes m dynamically from the actual n and the
# target FPR; DEFAULT_M is only the floor.
K_HASHES: Final[int] = 10
FPR_TARGET: Final[float] = 0.001
DEFAULT_M_FLOOR: Final[int] = 65_536

HASH_ALGORITHM: Final[str] = "MurmurHash3_x64_128"
MANIFEST_VERSION: Final[str] = "1.0.0"

# Asset file names expected by NameGazetteer.swift / Bundle.module.
SURNAME_FILTER_FILE: Final[str] = "surnames.bloom"
GIVEN_NAME_FILTER_FILE: Final[str] = "given-names.bloom"
MANIFEST_FILE: Final[str] = "gazetteer_manifest.json"
