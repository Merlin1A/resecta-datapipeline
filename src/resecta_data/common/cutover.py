"""One legacy→rebuild cutover diff for every builder that emits a sidecar.

A builder rewire ships an advisory ``*.cutover-diff.json`` beside its rebuilt
artifact (``schemas/cutover_diff.schema.json``): the keys present only in the
legacy output, the keys present only in the rebuild output, and the keys
present in both whose fields differ. Three builders used to carry their own
copy of this envelope; this module holds the one shape, and each builder
contributes only its :class:`CutoverSpec` and, where it still parses a legacy
source, its rows.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final, TypedDict

from .exceptions import PipelineError

CUTOVER_DIFF_VERSION: Final[int] = 1
"""Schema version of the emitted diff (``schemas/cutover_diff.schema.json``)."""


@dataclass(frozen=True)
class CutoverSpec:
    """What a builder contributes to its cutover diff's envelope.

    ``artifact`` is the build-relative path of the rebuilt artifact the diff
    covers, ``generated_by`` identifies the builder, and ``key`` names the row
    field the legacy and rebuild outputs are matched on.
    """

    artifact: str
    generated_by: str
    key: str = "name"


class CutoverSummary(TypedDict):
    """The three counts that mirror the diff's array lengths."""

    legacy_only_count: int
    rebuild_only_count: int
    keyed_diff_count: int


class KeyedDiff(TypedDict):
    """A key present in both outputs, with the two rows that differ."""

    key: str
    legacy: dict[str, object]
    rebuild: dict[str, object]


class CutoverDiff(TypedDict):
    """The sidecar payload, in the shape ``cutover_diff.schema.json`` pins."""

    version: int
    generated_by: str
    artifact: str
    summary: CutoverSummary
    legacy_only: list[str]
    rebuild_only: list[str]
    keyed_diff: list[KeyedDiff]


def _keyed(
    rows: Iterable[Mapping[str, object]], key: str, side: str
) -> dict[str, dict[str, object]]:
    """Index ``rows`` by their ``key`` field; a later row with the same key wins."""
    keyed: dict[str, dict[str, object]] = {}
    for row in rows:
        value = row.get(key)
        if not isinstance(value, str):
            raise PipelineError(f"cutover diff: {side} row has no string {key!r} field: {row!r}")
        keyed[value] = dict(row)
    return keyed


def build_cutover_diff(
    legacy_rows: Iterable[Mapping[str, object]],
    rebuild_rows: Iterable[Mapping[str, object]],
    *,
    spec: CutoverSpec,
) -> CutoverDiff:
    """Return the legacy→rebuild diff of two row sets matched on ``spec.key``.

    ``legacy_only`` and ``rebuild_only`` are the unmatched keys, sorted;
    ``keyed_diff`` lists, in key order, the matched keys whose rows differ,
    with both rows. Two empty inputs give the empty diff a builder without a
    retired legacy variant emits.
    """
    legacy = _keyed(legacy_rows, spec.key, "legacy")
    rebuild = _keyed(rebuild_rows, spec.key, "rebuild")

    legacy_only = sorted(legacy.keys() - rebuild.keys())
    rebuild_only = sorted(rebuild.keys() - legacy.keys())
    keyed_diff: list[KeyedDiff] = [
        {"key": name, "legacy": legacy[name], "rebuild": rebuild[name]}
        for name in sorted(legacy.keys() & rebuild.keys())
        if legacy[name] != rebuild[name]
    ]

    return {
        "version": CUTOVER_DIFF_VERSION,
        "generated_by": spec.generated_by,
        "artifact": spec.artifact,
        "summary": {
            "legacy_only_count": len(legacy_only),
            "rebuild_only_count": len(rebuild_only),
            "keyed_diff_count": len(keyed_diff),
        },
        "legacy_only": legacy_only,
        "rebuild_only": rebuild_only,
        "keyed_diff": keyed_diff,
    }


__all__ = [
    "CUTOVER_DIFF_VERSION",
    "CutoverDiff",
    "CutoverSpec",
    "CutoverSummary",
    "KeyedDiff",
    "build_cutover_diff",
]
