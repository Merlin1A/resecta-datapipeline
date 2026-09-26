"""Determinism and shape for the T4.3 malformed-PDF fixture builder.

Double-builds the set at the canonical seed and asserts byte-identical output
for the manifest *and* every fixture. Binary artifacts are compared directly
rather than through the JSON-table harness in ``test_builders_determinism.py``,
which assumes a ``builder(seed) -> dict`` signature.

The base document is synthesised here rather than read from the sample-doc
checkout, so the test is hermetic: it exercises the same landmarks the real
packet has (a classic cross-reference table, a startxref offset, stream
lengths) without depending on a sibling repository being present.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from resecta_data.common.determinism import CANONICAL_SEED, is_out_of_band
from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import dump_canonical_json, sha256_bytes
from resecta_data.common.mechanism_language import scan_text
from resecta_data.common.schema import load_schema, validate
from resecta_data.fuzz.pdf_mutations import (
    DEFAULT_COUNT,
    KINDS,
    MUTATIONS_DIRNAME,
    build,
)

_SCHEMAS = Path(__file__).parent.parent / "schemas"
_LENGTH_PRESERVING = ("byte_flip", "xref_damage", "bad_length")


def _synthetic_pdf() -> bytes:
    """Return bytes of a small PDF with the landmarks every family targets.

    Object offsets are computed as the body is assembled, so the cross-
    reference table is accurate and the ``startxref`` value is real. That
    matters: the mutators locate their targets by scanning for these tokens.
    """
    header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    streams = [b"BT /F1 12 Tf 72 720 Td (alpha) Tj ET\n", b"BT /F1 12 Tf 72 700 Td (bravo) Tj ET\n"]

    body = bytearray()
    offsets: list[int] = []

    def emit(number: int, payload: bytes) -> None:
        offsets.append(len(header) + len(body))
        body.extend(b"%d 0 obj\n" % number)
        body.extend(payload)
        body.extend(b"endobj\n")

    emit(1, b"<<\n/Type /Catalog\n/Pages 2 0 R\n>>\n")
    emit(2, b"<<\n/Type /Pages\n/Kids [3 0 R]\n/Count 1\n>>\n")
    emit(
        3,
        b"<<\n/Type /Page\n/Parent 2 0 R\n/MediaBox [0 0 612 792]\n/Contents 4 0 R\n>>\n",
    )
    for index, stream in enumerate(streams):
        emit(
            4 + index,
            b"<<\n/Length %d\n>>\nstream\n" % len(stream) + stream + b"endstream\n",
        )

    prefix = header + bytes(body)
    xref_offset = len(prefix)
    count = len(offsets) + 1
    xref = bytearray(b"xref\n0 %d\n" % count)
    xref.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        xref.extend(b"%010d 00000 n \n" % offset)
    trailer = b"trailer\n<<\n/Size %d\n/Root 1 0 R\n>>\nstartxref\n%d\n%%%%EOF\n" % (
        count,
        xref_offset,
    )
    return prefix + bytes(xref) + trailer


@pytest.fixture(scope="module")
def base() -> bytes:
    return _synthetic_pdf()


def test_synthetic_base_has_the_landmarks(base: bytes) -> None:
    """Guard the guard: if this drifts, the whole module stops testing anything."""
    assert base.startswith(b"%PDF-")
    assert base.count(b"\nxref") == 1
    assert base.count(b"startxref") == 1
    assert base.rstrip().endswith(b"%%EOF")
    assert base.count(b"/Length ") == 2


@pytest.mark.determinism
def test_manifest_is_byte_identical_across_builds(base: bytes, tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    dump_canonical_json(build(CANONICAL_SEED, source=base).manifest, first)
    dump_canonical_json(build(CANONICAL_SEED, source=base).manifest, second)
    assert first.read_bytes() == second.read_bytes()


@pytest.mark.determinism
def test_every_fixture_is_byte_identical_across_builds(base: bytes) -> None:
    first = build(CANONICAL_SEED, source=base).files
    second = build(CANONICAL_SEED, source=base).files
    assert [name for name, _ in first] == [name for name, _ in second]
    for (name, left), (_, right) in zip(first, second, strict=True):
        assert left == right, f"{name} differs between builds"


def test_seed_changes_the_set(base: bytes) -> None:
    canonical = build(CANONICAL_SEED, source=base).files
    other = build(CANONICAL_SEED + 1, source=base).files
    assert [name for name, _ in canonical] == [name for name, _ in other]
    assert [data for _, data in canonical] != [data for _, data in other]


def test_count_is_split_evenly_across_families(base: bytes) -> None:
    manifest = build(CANONICAL_SEED, source=base, count=DEFAULT_COUNT).manifest
    rows = manifest["mutations"]
    assert len(rows) == DEFAULT_COUNT
    per_kind = {kind: sum(1 for r in rows if r["kind"] == kind) for kind in KINDS}
    assert per_kind == {kind: DEFAULT_COUNT // len(KINDS) for kind in KINDS}


def test_remainder_goes_to_the_earlier_families(base: bytes) -> None:
    rows = build(CANONICAL_SEED, source=base, count=6).manifest["mutations"]
    per_kind = [sum(1 for r in rows if r["kind"] == kind) for kind in KINDS]
    assert per_kind == [2, 2, 1, 1]


def test_rows_are_self_consistent(base: bytes) -> None:
    result = build(CANONICAL_SEED, source=base)
    rows = result.manifest["mutations"]
    files = dict(result.files)

    assert len({r["id"] for r in rows}) == len(rows)
    assert result.manifest["base_sha256"] == sha256_bytes(base)
    assert result.manifest["base_bytes"] == len(base)

    for row in rows:
        data = files[row["path"]]
        assert row["path"] == f"{MUTATIONS_DIRNAME}/{row['id']}.pdf"
        assert row["sha256"] == sha256_bytes(data)
        assert row["bytes"] == len(data)
        assert data != base, f"{row['id']} did not change the source"


def test_only_truncation_changes_the_file_size(base: bytes) -> None:
    for row, (_, data) in zip(
        build(CANONICAL_SEED, source=base).manifest["mutations"],
        build(CANONICAL_SEED, source=base).files,
        strict=True,
    ):
        if row["kind"] in _LENGTH_PRESERVING:
            assert len(data) == len(base), f"{row['id']} moved a byte offset"
        else:
            assert len(data) < len(base), f"{row['id']} did not shorten the file"


def test_byte_flip_differs_in_exactly_one_bit(base: bytes) -> None:
    result = build(CANONICAL_SEED, source=base)
    files = dict(result.files)
    for row in result.manifest["mutations"]:
        if row["kind"] != "byte_flip":
            continue
        data = files[row["path"]]
        differing = [i for i, (a, b) in enumerate(zip(base, data, strict=True)) if a != b]
        assert differing == [row["params"]["offset"]]
        assert base[differing[0]] ^ data[differing[0]] == 1 << row["params"]["bit"]


def test_manifest_validates_against_its_schema(base: bytes) -> None:
    manifest = build(CANONICAL_SEED, source=base).manifest
    validate(manifest, load_schema(_SCHEMAS, "pdf_mutations"), context="pdf_mutations")


def test_generated_text_is_mechanism_safe(base: bytes) -> None:
    manifest = build(CANONICAL_SEED, source=base).manifest
    for row in manifest["mutations"]:
        assert scan_text(row["notes"]) == [], f"{row['id']}: banned phrase in notes"
    schema_text = (_SCHEMAS / "pdf_mutations.schema.json").read_text(encoding="utf-8")
    assert scan_text(schema_text) == []


def test_artifacts_are_classified_out_of_band(base: bytes) -> None:
    """`make build` cannot rebuild these, so the verifiers must skip them."""
    assert is_out_of_band("fuzz/pdf_mutations.json")
    for name, _ in build(CANONICAL_SEED, source=base, count=4).files:
        assert is_out_of_band(f"fuzz/{name}")


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        (b"%PDF-1.4\nnot a pdf at all\n", "startxref"),
        (b"%PDF-1.4\nstartxref\n12\n%%EOF\n", "xref"),
    ],
)
def test_source_without_landmarks_is_rejected(source: bytes, reason: str) -> None:
    with pytest.raises(PipelineError, match=reason):
        build(CANONICAL_SEED, source=source)


def test_non_positive_count_is_rejected(base: bytes) -> None:
    with pytest.raises(PipelineError, match="count must be positive"):
        build(CANONICAL_SEED, source=base, count=0)
