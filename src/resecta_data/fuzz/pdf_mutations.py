"""Deterministic malformed-PDF fixture builder (Phase 1, T4.3).

Produces a bounded family of damaged copies of one source PDF so the iOS
importer's *degradation* behaviour can be measured: every fixture must either
open, or be rejected with a classified error, and must never take the host
down. The fixtures are inputs to the H4.2 robustness runner; they are
development-only and are never installed into the app bundle.

Every mutation is a pure byte-buffer edit over the source bytes. This module
parses nothing and imports no PDF library, which keeps the pipeline free of new
dependencies (CONTRIBUTING.md) and makes each fixture reproducible from
``(source bytes, seed)`` alone. Three of the four families are length
preserving, so a fixture differs from its source in a handful of bytes and
nothing downstream shifts.

The ``expected`` field on each record is a *structural prior* — what the PDF
layout says should happen — not ground truth. The runner measures the real
outcome; a disagreement between prior and measurement is a finding to
adjudicate, which is the whole point of the family.

Like the sibling ReDoS builder, this module must not import ``re``;
``tests/test_fuzz_no_re.py`` enforces that across the package.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from random import Random
from typing import Any, Final

from resecta_data.common.determinism import seeded_context
from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import sha256_bytes

logger = logging.getLogger(__name__)

_GENERATED_BY: Final[str] = "resecta-data/fuzz/pdf-mutations"
_SCHEMA_VERSION: Final[int] = 1

DEFAULT_COUNT: Final[int] = 100
"""Size of the v0 set (`31-ASSET-BUILD-LIST` §B row 8: "first 100 mutations")."""

MUTATIONS_DIRNAME: Final[str] = "pdf_mutations"
"""Directory the fixture files land in, as a sibling of the manifest."""

KINDS: Final[tuple[str, ...]] = ("byte_flip", "truncation", "xref_damage", "bad_length")
"""Mutation families, in manifest order. Mirrors the schema's ``kind`` enum."""

_ASCII_DIGITS: Final[frozenset[int]] = frozenset(b"0123456789")
_SPACE: Final[int] = 0x20
_LF: Final[int] = 0x0A
_CR: Final[int] = 0x0D
_EOL_BYTES: Final[tuple[int, int]] = (_LF, _CR)
_SEED_SPACE: Final[int] = 2**31
_BITS_PER_BYTE: Final[int] = 8
_SIGNATURE: Final[bytes] = b"%PDF-"

# Stratum ladder for byte_flip. Cycled positionally so the split is stable for
# any count: body draws half the flips (it is by far the largest region) and
# the three structural regions split the rest evenly.
_FLIP_CYCLE: Final[tuple[str, ...]] = (
    "body",
    "signature",
    "body",
    "xref_table",
    "body",
    "version",
    "body",
    "trailer",
)

# Sub-target ladder for xref_damage, cycled the same way.
_XREF_CYCLE: Final[tuple[str, ...]] = (
    "startxref_offset",
    "xref_entry_offset",
    "xref_entry_offset",
    "xref_keyword",
)

_TRAILER_WINDOW: Final[int] = 200
"""Bytes at the end of the file treated as the trailer stratum."""

_XREF_ENTRY_WIDTH: Final[int] = 20
"""Bytes per cross-reference entry: ``nnnnnnnnnn ggggg n\\r\\n``."""

_XREF_OFFSET_WIDTH: Final[int] = 10
"""Digits in an entry's byte-offset field."""

_MAX_DRAW_ATTEMPTS: Final[int] = 64
"""Bounded retries when a random draw collides with one already emitted."""

_MIN_TRUNCATION_BYTES: Final[int] = 1


@dataclass(frozen=True, slots=True)
class Mutation:
    """One damaged copy of the source document.

    Attributes:
        id: Stable identifier, unique across the set. Also the file stem.
        kind: One of :data:`KINDS`.
        seed: The per-mutation seed drawn from the master PRNG.
        expected: Structural prior, ``"open"`` or ``"reject"``.
        notes: Human-readable statement of what was damaged and why the prior
            is what it is.
        params: Family-specific parameters, recorded so a fixture can be
            re-derived or reasoned about without diffing bytes.
        data: The mutated document bytes.
    """

    id: str
    kind: str
    seed: int
    expected: str
    notes: str
    params: dict[str, Any]
    data: bytes


@dataclass(frozen=True, slots=True)
class MutationSet:
    """Builder output: the manifest plus the bytes each row describes."""

    manifest: dict[str, Any]
    files: tuple[tuple[str, bytes], ...]
    """``(relative filename, bytes)`` pairs, in manifest order."""


@dataclass(frozen=True, slots=True)
class _Layout:
    """Byte offsets of the structural landmarks the mutators aim at.

    Located by scanning for literal tokens — no object graph is built. Every
    field is derived from the source bytes alone, so the layout is stable
    across runs.
    """

    size: int
    header_end: int
    xref_keyword: int
    xref_table_end: int
    startxref_digits: tuple[int, int]
    entry_offsets: tuple[int, ...]
    length_fields: tuple[tuple[int, int, int], ...]
    """``(digit_start, digit_end, value)`` for each ``/Length <n>``."""


def _find_all(haystack: bytes, needle: bytes) -> list[int]:
    """Return every start offset of ``needle`` in ``haystack``, ascending."""
    hits: list[int] = []
    start = 0
    while True:
        found = haystack.find(needle, start)
        if found < 0:
            return hits
        hits.append(found)
        start = found + 1


def _digit_span(data: bytes, start: int) -> tuple[int, int]:
    """Return ``(start, end)`` of the ASCII digit run beginning at ``start``.

    ``end`` equals ``start`` when there is no digit there.
    """
    end = start
    while end < len(data) and data[end] in _ASCII_DIGITS:
        end += 1
    return start, end


def _header_end(data: bytes) -> int:
    """Return the offset just past the ``%PDF-x.y`` signature.

    Falls back to a short fixed window when the signature is absent, so a
    caller passing an already-odd file still gets a usable header stratum.
    """
    marker = b"%PDF-"
    if not data.startswith(marker):
        return min(len(data), len(marker))
    end = len(marker)
    while end < len(data) and data[end] not in _EOL_BYTES:
        end += 1
    return end


def _locate(data: bytes) -> _Layout:
    """Derive the mutation targets from the source bytes.

    Raises:
        PipelineError: If the document lacks the landmarks every mutation
            family needs (a cross-reference table, a ``startxref`` offset, and
            at least one stream length).
    """
    startxref_hits = _find_all(data, b"startxref")
    if not startxref_hits:
        raise PipelineError("Source PDF has no 'startxref' token; cannot damage its xref.")
    startxref = startxref_hits[-1]
    cursor = startxref + len(b"startxref")
    while cursor < len(data) and data[cursor] not in _ASCII_DIGITS:
        cursor += 1
    digits = _digit_span(data, cursor)
    if digits[0] == digits[1]:
        raise PipelineError("Source PDF has a 'startxref' with no offset digits.")

    # The final cross-reference table. Anchored on the newline so the token
    # cannot match the tail of "startxref".
    xref_hits = _find_all(data, b"\nxref")
    if not xref_hits:
        raise PipelineError("Source PDF has no classic 'xref' table; T4.3 v0 needs one.")
    xref_keyword = xref_hits[-1] + 1

    entry_offsets = _xref_entry_offsets(data, xref_keyword)
    if not entry_offsets:
        raise PipelineError("Source PDF's xref table has no parsable subsection header.")

    length_fields = _length_fields(data)
    if not length_fields:
        raise PipelineError("Source PDF has no '/Length <n>' stream dictionaries.")

    header_end = _header_end(data)
    if header_end <= len(_SIGNATURE):
        raise PipelineError("Source PDF has no version string after its %PDF- token.")

    return _Layout(
        size=len(data),
        header_end=header_end,
        xref_keyword=xref_keyword,
        xref_table_end=entry_offsets[-1] + _XREF_ENTRY_WIDTH,
        startxref_digits=digits,
        entry_offsets=entry_offsets,
        length_fields=length_fields,
    )


def _xref_entry_offsets(data: bytes, xref_keyword: int) -> tuple[int, ...]:
    """Return the byte offset of each entry's 10-digit offset field.

    Reads only the first subsection header (``<first> <count>``) and walks
    fixed-width entries from there, which is all a byte-level mutator needs.
    """
    cursor = xref_keyword + len(b"xref")
    while cursor < len(data) and data[cursor] in (_LF, _CR, _SPACE):
        cursor += 1
    first = _digit_span(data, cursor)
    if first[0] == first[1]:
        return ()
    cursor = first[1]
    while cursor < len(data) and data[cursor] == _SPACE:
        cursor += 1
    count_span = _digit_span(data, cursor)
    if count_span[0] == count_span[1]:
        return ()
    count = int(data[count_span[0] : count_span[1]])
    cursor = count_span[1]
    while cursor < len(data) and data[cursor] in _EOL_BYTES:
        cursor += 1

    offsets: list[int] = []
    for index in range(count):
        entry = cursor + index * _XREF_ENTRY_WIDTH
        if entry + _XREF_ENTRY_WIDTH > len(data):
            break
        span = _digit_span(data, entry)
        if span[1] - span[0] != _XREF_OFFSET_WIDTH:
            break
        offsets.append(entry)
    return tuple(offsets)


def _length_fields(data: bytes) -> tuple[tuple[int, int, int], ...]:
    """Return the digit span and value of every ``/Length <n>`` in ``data``.

    ``/Length1`` and friends (font subset markers) are skipped: the token must
    be followed by a space and then digits.
    """
    fields: list[tuple[int, int, int]] = []
    for hit in _find_all(data, b"/Length"):
        cursor = hit + len(b"/Length")
        if cursor >= len(data) or data[cursor] != _SPACE:
            continue
        start, end = _digit_span(data, cursor + 1)
        if end > start:
            fields.append((start, end, int(data[start:end])))
    return tuple(fields)


def _plan(count: int) -> tuple[int, ...]:
    """Split ``count`` evenly across :data:`KINDS`, remainder to the earlier kinds."""
    base, extra = divmod(count, len(KINDS))
    return tuple(base + (1 if i < extra else 0) for i in range(len(KINDS)))


def _replace_padded(data: bytes, start: int, end: int, value: int) -> bytes:
    """Return ``data`` with ``[start:end)`` overwritten by ``value``, same width.

    The replacement is zero-padded to the original width so no byte offset in
    the document shifts.
    """
    width = end - start
    digits = str(value).rjust(width, "0").encode("ascii")
    if len(digits) != width:
        raise PipelineError(f"Replacement {value} does not fit {width} digits at {start}.")
    return data[:start] + digits + data[end:]


def _draw_distinct(
    rng: Random,
    lo: int,
    hi: int,
    taken: set[int],
) -> int:
    """Draw from ``[lo, hi)`` avoiding ``taken``, falling back to a linear scan.

    The fallback keeps the builder total even when a stratum is small enough
    that random draws collide (the header is eight bytes wide).
    """
    for _ in range(_MAX_DRAW_ATTEMPTS):
        candidate = rng.randrange(lo, hi)
        if candidate not in taken:
            return candidate
    for candidate in range(lo, hi):
        if candidate not in taken:
            return candidate
    raise PipelineError(f"Exhausted every distinct value in [{lo}, {hi}).")


def _flip_bounds(layout: _Layout, stratum: str) -> tuple[int, int]:
    """Return the ``[lo, hi)`` byte range a byte_flip stratum draws from."""
    if stratum == "signature":
        return 0, len(_SIGNATURE)
    if stratum == "version":
        return len(_SIGNATURE), layout.header_end
    if stratum == "xref_table":
        return layout.xref_keyword, layout.xref_table_end
    if stratum == "trailer":
        return max(layout.header_end, layout.size - _TRAILER_WINDOW), layout.size
    return layout.header_end, layout.xref_keyword


_FLIP_PRIORS: Final[dict[str, tuple[str, str]]] = {
    "signature": (
        "reject",
        (
            "damages the %PDF- token itself, which is what a reader keys on to "
            "recognize the file at all"
        ),
    ),
    "version": (
        "open",
        (
            "damages only the advisory version digits after %PDF-, which readers "
            "are expected to tolerate"
        ),
    ),
    "body": (
        "open",
        "damages one object's bytes; the document structure around it is intact",
    ),
    "xref_table": (
        "open",
        "damages a cross-reference entry; readers that rebuild the table recover",
    ),
    "trailer": (
        "open",
        "damages the trailer dictionary; readers that scan for the catalog recover",
    ),
}


def _build_byte_flips(rng: Random, data: bytes, layout: _Layout, count: int) -> list[Mutation]:
    """Flip one bit per fixture, stratified across the document's regions."""
    mutations: list[Mutation] = []
    used: dict[str, set[int]] = {name: set() for name in _FLIP_PRIORS}
    for index in range(count):
        stratum = _FLIP_CYCLE[index % len(_FLIP_CYCLE)]
        lo, hi = _flip_bounds(layout, stratum)
        seed = rng.randrange(_SEED_SPACE)
        # Draw a distinct (offset, bit) slot rather than a distinct offset: the
        # header stratum is only a handful of bytes wide, and dedup on the byte
        # alone would run it dry long before an interesting count is reached.
        slot = _draw_distinct(rng, 0, (hi - lo) * _BITS_PER_BYTE, used[stratum])
        used[stratum].add(slot)
        offset = lo + slot // _BITS_PER_BYTE
        bit = slot % _BITS_PER_BYTE
        original = data[offset]
        mutated = bytearray(data)
        mutated[offset] = original ^ (1 << bit)
        expected, why = _FLIP_PRIORS[stratum]
        mutations.append(
            Mutation(
                id=f"byte_flip_{index:03d}",
                kind="byte_flip",
                seed=seed,
                expected=expected,
                notes=(
                    f"Bit {bit} of the byte at offset {offset} ({stratum} region) inverted: "
                    f"0x{original:02x} to 0x{original ^ (1 << bit):02x}. Prior is "
                    f"{expected} because the edit {why}."
                ),
                params={"offset": offset, "bit": bit, "region": stratum},
                data=bytes(mutated),
            )
        )
    return mutations


def _build_truncations(rng: Random, data: bytes, layout: _Layout, count: int) -> list[Mutation]:
    """Cut the file short at an even ladder of fractions.

    Every rung lands before the cross-reference table, so each fixture is a
    document with no table, no trailer and no ``%%EOF``.
    """
    mutations: list[Mutation] = []
    for index in range(count):
        seed = rng.randrange(_SEED_SPACE)
        numerator = index + 1
        denominator = count + 1
        kept = max(_MIN_TRUNCATION_BYTES, layout.size * numerator // denominator)
        into_table = kept > layout.xref_keyword
        where = (
            "cuts partway through the cross-reference table"
            if into_table
            else "lands before the cross-reference table"
        )
        mutations.append(
            Mutation(
                id=f"truncation_{index:03d}",
                kind="truncation",
                seed=seed,
                expected="reject",
                notes=(
                    f"Kept the first {kept} of {layout.size} bytes "
                    f"({numerator}/{denominator} of the file); the cut {where}. "
                    "Prior is reject because the trailer, the startxref offset and "
                    "the end-of-file marker are all gone, so a reader has no "
                    "documented route to the catalog."
                ),
                params={"kept_bytes": kept, "original_bytes": layout.size},
                data=data[:kept],
            )
        )
    return mutations


def _build_xref_damage(rng: Random, data: bytes, layout: _Layout, count: int) -> list[Mutation]:
    """Corrupt the cross-reference machinery without moving any byte."""
    mutations: list[Mutation] = []
    used_keyword: set[int] = set()
    entry_cursor = 0
    for index in range(count):
        target = _XREF_CYCLE[index % len(_XREF_CYCLE)]
        seed = rng.randrange(_SEED_SPACE)
        if target == "startxref_offset":
            start, end = layout.startxref_digits
            original = int(data[start:end])
            replacement = _wrong_value(rng, original, end - start)
            mutated = _replace_padded(data, start, end, replacement)
            note = (
                f"startxref now points at byte {replacement} instead of {original}. "
                "Prior is open because readers that rebuild the table recover."
            )
            params: dict[str, Any] = {
                "target": target,
                "offset": start,
                "original_value": original,
                "replacement_value": replacement,
            }
        elif target == "xref_entry_offset":
            # Walk the table in order and wrap. Entries are a finite resource,
            # so a large count revisits them; the replacement value is redrawn
            # each time, which keeps every fixture distinct.
            entry_index = entry_cursor % len(layout.entry_offsets)
            entry_cursor += 1
            start = layout.entry_offsets[entry_index]
            end = start + _XREF_OFFSET_WIDTH
            original = int(data[start:end])
            replacement = _wrong_value(rng, original, _XREF_OFFSET_WIDTH)
            mutated = _replace_padded(data, start, end, replacement)
            note = (
                f"Cross-reference entry {entry_index} now points at byte {replacement} "
                f"instead of {original}. Prior is open because readers that rebuild "
                "the table recover."
            )
            params = {
                "target": target,
                "offset": start,
                "entry_index": entry_index,
                "original_value": original,
                "replacement_value": replacement,
            }
        else:
            slot = _draw_distinct(rng, 0, len(b"xref") * _BITS_PER_BYTE, used_keyword)
            used_keyword.add(slot)
            byte_index, bit = divmod(slot, _BITS_PER_BYTE)
            offset = layout.xref_keyword + byte_index
            original_byte = data[offset]
            buffer = bytearray(data)
            buffer[offset] = original_byte ^ (1 << bit)
            mutated = bytes(buffer)
            note = (
                f"The 'xref' keyword is misspelled at offset {offset} "
                f"(bit {bit} inverted). Prior is open because readers that scan for "
                "objects recover."
            )
            params = {"target": target, "offset": offset, "bit": bit}

        mutations.append(
            Mutation(
                id=f"xref_damage_{index:03d}",
                kind="xref_damage",
                seed=seed,
                expected="open",
                notes=note,
                params=params,
                data=mutated,
            )
        )
    return mutations


def _build_bad_lengths(rng: Random, data: bytes, layout: _Layout, count: int) -> list[Mutation]:
    """Misstate stream lengths, alternating understated and overstated."""
    mutations: list[Mutation] = []
    fields = layout.length_fields
    for index in range(count):
        seed = rng.randrange(_SEED_SPACE)
        start, end, original = fields[index % len(fields)]
        width = end - start
        understate = index % 2 == 0
        replacement = _wrong_value(rng, original, width, smaller=understate)
        direction = "understates" if replacement < original else "overstates"
        mutations.append(
            Mutation(
                id=f"bad_length_{index:03d}",
                kind="bad_length",
                seed=seed,
                expected="open",
                notes=(
                    f"Stream /Length at offset {start} now reads {replacement} "
                    f"instead of {original}, which {direction} the stream. Prior is "
                    "open because readers that search for the endstream keyword recover."
                ),
                params={
                    "offset": start,
                    "original_value": original,
                    "replacement_value": replacement,
                },
                data=_replace_padded(data, start, end, replacement),
            )
        )
    return mutations


def _wrong_value(rng: Random, original: int, width: int, *, smaller: bool | None = None) -> int:
    """Return a value that differs from ``original`` and still fits ``width`` digits.

    Args:
        rng: Seeded PRNG.
        original: The value being replaced.
        width: Digit width the replacement must fit in.
        smaller: When True prefer a smaller value, when False a larger one, and
            when None draw from the whole range. The preference is dropped when
            the requested side is empty.
    """
    # Annotated: mypy widens int ** int to Any (negative exponents yield float).
    ceiling: int = 10**width
    if smaller is True and original > 0:
        return rng.randrange(0, original)
    if smaller is False and original + 1 < ceiling:
        return rng.randrange(original + 1, ceiling)
    for _ in range(_MAX_DRAW_ATTEMPTS):
        candidate = rng.randrange(0, ceiling)
        if candidate != original:
            return candidate
    return (original + 1) % ceiling


def build(seed: int, *, source: bytes, count: int = DEFAULT_COUNT) -> MutationSet:
    """Build the malformed-PDF fixture set.

    Args:
        seed: Master PRNG seed. Every per-mutation seed is drawn from it, so the
            whole set is reproducible from this one number plus ``source``.
        source: Bytes of the document to damage.
        count: How many fixtures to emit, split evenly across :data:`KINDS`.

    Returns:
        A :class:`MutationSet` carrying the manifest and each fixture's bytes.

    Raises:
        PipelineError: If ``count`` is not positive, or ``source`` lacks the
            landmarks the mutation families target.
    """
    if count <= 0:
        raise PipelineError(f"count must be positive, got {count}")
    layout = _locate(source)
    per_kind = _plan(count)

    mutations: list[Mutation] = []
    with seeded_context(seed) as rng:
        mutations.extend(_build_byte_flips(rng, source, layout, per_kind[0]))
        mutations.extend(_build_truncations(rng, source, layout, per_kind[1]))
        mutations.extend(_build_xref_damage(rng, source, layout, per_kind[2]))
        mutations.extend(_build_bad_lengths(rng, source, layout, per_kind[3]))

    rows: list[dict[str, Any]] = []
    files: list[tuple[str, bytes]] = []
    for mutation in mutations:
        filename = f"{MUTATIONS_DIRNAME}/{mutation.id}.pdf"
        rows.append(
            {
                "id": mutation.id,
                "kind": mutation.kind,
                "seed": mutation.seed,
                "expected": mutation.expected,
                "path": filename,
                "sha256": sha256_bytes(mutation.data),
                "bytes": len(mutation.data),
                "notes": mutation.notes,
                "params": mutation.params,
            }
        )
        files.append((filename, mutation.data))

    logger.debug(
        "Built %d mutations across %s from a %d-byte source",
        len(rows),
        ",".join(KINDS),
        layout.size,
    )

    manifest: dict[str, Any] = {
        "version": _SCHEMA_VERSION,
        "generated_by": _GENERATED_BY,
        "seed": seed,
        "base_sha256": sha256_bytes(source),
        "base_bytes": layout.size,
        "mutations": rows,
    }
    return MutationSet(manifest=manifest, files=tuple(files))
