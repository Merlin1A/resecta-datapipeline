"""Build the ``gazetteer_manifest.json`` payload.

=== SHARED WITH SWIFT ===
The decoded struct is ``GazetteerManifest`` at
  ../Packages/RedactionEngine/Sources/RedactionEngine/Detection/Gazetteer/GazetteerManifest.swift
Field names here must match the Codable case names there exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from .spec import HASH_ALGORITHM, MANIFEST_VERSION

_GENERATED_BY: Final[str] = "resecta-data/bloom/manifest"
_CUTOVER_DIFF_VERSION: Final[int] = 1
_CUTOVER_ARTIFACT: Final[str] = "gazetteers/gazetteer_manifest.json"


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


def build_cutover_diff(filters: list[FilterBuildResult]) -> dict[str, Any]:
    """Return the legacy→rebuild cutover diff for the name-filter manifest.

    Emitted alongside ``gazetteer_manifest.json`` under the
    cc-derive-rebuild S3 chain (STRAT §10.4 row §D1 family — A1/A2 1:1,
    advisory). The diff covers the manifest's ``filters[]`` entries
    (surnames + given-names): ``legacy_only`` lists source identifiers
    retired in the rebuild, ``rebuild_only`` lists source identifiers
    added in the rebuild, and ``keyed_diff`` lists filter names whose
    FPR / size / sources lineage changed.

    The CC-SCRIPT fetcher chain (D-06 / D-07 / D-08) already routed the manifest's
    source identifiers (``census_surnames``, ``ssa_given_names``,
    ``popnames_common_surnames``, ``popnames_common_forenames``, etc.)
    onto vintage-pinned CC-SCRIPT-managed paths; the manifest itself is
    dynamically built from ``_surname_ingest_specs()`` /
    ``_given_name_ingest_specs()`` in ``cli.py``. No legacy variant was
    retired in this rebuild, so the diff is **empty by construction** —
    the verification-posture stub attests that A1+A2 carry no
    shipped-vs-rebuild divergence.

    The diff is **advisory** under the §D1 family (A1/A2 — 1:1
    wire-stable; no Jesse sign required at S3) — used for PR-review
    context, consolidated into the S4 PR description.

    Args:
        filters: The post-rebuild filter list. Accepted to keep the
            signature future-extensible (when a real legacy-vs-rebuild
            diff is needed); unused under verification-posture.
    """
    del filters  # accepted for future-extensibility; verification-posture is empty

    legacy_only: list[str] = []
    rebuild_only: list[str] = []
    keyed_diff: list[dict[str, Any]] = []

    return {
        "version": _CUTOVER_DIFF_VERSION,
        "generated_by": _GENERATED_BY,
        "artifact": _CUTOVER_ARTIFACT,
        "summary": {
            "legacy_only_count": len(legacy_only),
            "rebuild_only_count": len(rebuild_only),
            "keyed_diff_count": len(keyed_diff),
        },
        "legacy_only": legacy_only,
        "rebuild_only": rebuild_only,
        "keyed_diff": keyed_diff,
    }
