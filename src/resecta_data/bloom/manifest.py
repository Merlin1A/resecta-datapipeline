"""Build the ``gazetteer_manifest.json`` payload.

=== SHARED WITH SWIFT ===
The decoded struct is ``GazetteerManifest`` at
  ../Packages/RedactionEngine/Sources/RedactionEngine/Detection/Gazetteer/GazetteerManifest.swift
Field names here must match the Codable case names there exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from resecta_data.common.cutover import CutoverSpec
from resecta_data.common.io import sha256_file

from .spec import HASH_ALGORITHM, MANIFEST_VERSION, SHIPPED_MANIFEST_VERSION

_GENERATED_BY: Final[str] = "resecta-data/bloom/manifest"


@dataclass(frozen=True, slots=True)
class FilterBuildResult:
    """Summary of one built filter, for manifest synthesis.

    Attributes:
        name: File stem (``surnames`` or ``given-names``).
        type_: Swift-side type code (``surname`` or ``givenName``).
        n: Number of unique keys inserted.
        m: Bit-array size in bits.
        k: Number of hash functions.
        fpr_target: Target FPR used to size ``m``.
        sources: Identifiers of the source corpora that contributed.
    """

    name: str
    type_: str
    n: int
    m: int
    k: int
    fpr_target: float
    sources: tuple[str, ...]


def build_manifest(
    filters: list[FilterBuildResult],
    *,
    seed: int,
    built_at: str,
) -> dict[str, Any]:
    """Assemble the manifest payload.

    Args:
        filters: One entry per built .bloom file.
        seed: Hash seed used for both filters. Stored as int; Swift decodes
            it into ``Int`` on 64-bit devices (the seed fits in 32 bits).
        built_at: ISO-8601 timestamp string. Determinism note — pass a fixed
            value (e.g., ``"2026-04-16T00:00:00Z"``) for reproducible builds.

    Returns:
        A dict ready for ``dump_canonical_json``.
    """
    return {
        "version": MANIFEST_VERSION,
        "hashAlgorithm": HASH_ALGORITHM,
        "seed": seed,
        "filters": [
            {
                "name": f.name,
                "type": f.type_,
                "n": f.n,
                "m": f.m,
                "k": f.k,
                "fprTarget": f.fpr_target,
                "sources": list(f.sources),
                "builtAt": built_at,
            }
            for f in filters
        ],
    }


NAME_FILTERS_CUTOVER: Final[CutoverSpec] = CutoverSpec(
    artifact="gazetteers/gazetteer_manifest.json", generated_by=_GENERATED_BY
)
"""The envelope of the name-filter manifest's advisory cutover diff.

The surname and given-name filters are 1:1 wire-stable and no legacy variant
was retired in the rebuild (the CC-SCRIPT fetcher chain already routes the
manifest's source identifiers onto vintage-pinned paths), so the diff
``common.cutover.build_cutover_diff`` emits for this spec is empty by
construction: the sidecar attests that the two filters carry no
shipped-vs-rebuild divergence.
"""


# --- The shipped manifest: `assets[]` ---------------------------------------

#: The three files the signature verdict itself rests on. They are never
#: listed in `assets[]`: the manifest cannot carry its own digest, and the
#: `.sig`/`.pem` are the verifier's inputs.
MANIFEST_TRIPLE_BUNDLE_PATHS: Final[frozenset[str]] = frozenset(
    {
        "Gazetteers/gazetteer-manifest.json",
        "Gazetteers/gazetteer_manifest.sig",
        "Gazetteers/manifest_public_key.pem",
    }
)


@dataclass(frozen=True, slots=True)
class AssetEntry:
    """One installed engine asset as the shipped manifest lists it.

    Attributes:
        path: Bundle-relative path (``Gazetteers/surnames.bloom``).
        sha256: Lowercase hex SHA-256 of the bytes ``install-assets`` ships.
        size: Byte count of the same bytes (serialized as ``bytes``).
        source: Where the bytes were read — ``build`` (the artifact is in
            ``build/`` and install copies it) or ``installed`` (not built on
            this host; the file already under the iOS Resources tree is what
            ships). Informational; not serialized.
        lock: The lock cross-check for the artifact's build path — ``match``,
            ``differs`` (an installed file the locked build did not produce:
            the out-of-band supersets) or ``None`` (no lock row: the reviewed
            and calibrated products). Informational; not serialized.
    """

    path: str
    sha256: str
    size: int
    source: Literal["build", "installed"]
    lock: Literal["match", "differs"] | None


def collect_asset_entries(
    *,
    build_dir: Path,
    resources_dir: Path | None,
    routes: dict[str, tuple[str, str]],
    lock: dict[str, str],
) -> tuple[list[AssetEntry], list[str]]:
    """Digest the bytes ``install-assets`` will ship for every resource route.

    For each ``routes`` entry whose target is ``resources`` and whose bundle
    path is not one of :data:`MANIFEST_TRIPLE_BUNDLE_PATHS`: the artifact in
    ``build_dir`` when it is built (install copies it), else the file already
    installed under ``resources_dir`` (install leaves it alone), else the
    route is skipped and reported — an optional sidecar that neither exists
    nor ships (the Swift-side pin on the shipped manifest's asset count is
    what catches an asset that should have been listed).

    Returns:
        ``(entries sorted by bundle path, skipped build paths)``.
    """
    entries: list[AssetEntry] = []
    skipped: list[str] = []
    for rel, (target, sub_path) in routes.items():
        if target != "resources" or sub_path in MANIFEST_TRIPLE_BUNDLE_PATHS:
            continue
        built = build_dir / rel
        installed = resources_dir / sub_path if resources_dir is not None else None
        if built.is_file():
            chosen, source = built, "build"
        elif installed is not None and installed.is_file():
            chosen, source = installed, "installed"
        else:
            skipped.append(rel)
            continue
        digest = sha256_file(chosen)
        lock_row = lock.get(rel)
        lock_state: Literal["match", "differs"] | None = (
            None if lock_row is None else ("match" if lock_row == digest else "differs")
        )
        entries.append(
            AssetEntry(
                path=sub_path,
                sha256=digest,
                size=chosen.stat().st_size,
                source=source,  # type: ignore[arg-type]
                lock=lock_state,
            )
        )
    entries.sort(key=lambda e: e.path)
    return entries, sorted(skipped)


def build_shipped_manifest(base: dict[str, Any], entries: list[AssetEntry]) -> dict[str, Any]:
    """Return the shipped manifest: ``base`` (the bloom builder's manifest,
    ``filters[]`` and all, carried verbatim) with ``version`` set to
    :data:`SHIPPED_MANIFEST_VERSION` and an ``assets[]`` section.

    The dict is ready for ``dump_canonical_json``; the Swift ``GazetteerManifest``
    decodes ``assets[].{path, sha256, bytes}`` and fences ``version``.
    """
    shipped = dict(base)
    shipped["version"] = SHIPPED_MANIFEST_VERSION
    shipped["assets"] = [
        {"path": e.path, "sha256": e.sha256, "bytes": e.size}
        for e in sorted(entries, key=lambda e: e.path)
    ]
    return shipped
