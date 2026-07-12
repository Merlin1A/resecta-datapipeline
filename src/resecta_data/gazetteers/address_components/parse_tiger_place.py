"""Parser for Census TIGER/Line 2024 PLACE shapefiles (DBF component).

Each per-state TIGER PLACE archive is a ``.zip`` file containing a
``.dbf`` file (dBase III/IV format) alongside the ``.shp``, ``.shx``,
and metadata members.  This module reads only the ``.dbf`` member; the
shapefile geometry components are never touched.

The DBF is opened as raw ``bytes`` via ``zipfile`` (no extraction to disk)
and parsed record-by-record using the ``struct``
module -- no third-party DBF libraries required.

Normalization for the GNIS join
--------------------------------
City names are normalised as ``name.casefold().strip()``.  This is the
same transform applied to GNIS feature names before the membership test,
so the join is case-insensitive and whitespace-neutral.

DBF format summary
------------------
* Byte 0         : version (3 = dBase III, 4+ = dBase IV+; we accept any).
* Bytes 4-7      : number of data records, 32-bit little-endian unsigned.
* Bytes 8-9      : header size in bytes (incl. the 0x0D terminator), 16-bit LE.
* Bytes 10-11    : logical record size, 16-bit LE.
* Byte 32+       : field descriptors, each 32 bytes, until a 0x0D terminator.
* After header   : data records.  First byte is the deletion flag (0x20 =
                   active, 0x2A = deleted).  Fields are fixed-width,
                   space/zero-padded, CP-1252 (latin-1 compatible for ASCII
                   names).
"""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path
from typing import Final

from resecta_data.common.exceptions import PipelineError

# Column name we extract from each record.
_NAME_FIELD: Final[str] = "NAME"

# dBase deletion-flag constants.
_FLAG_DELETED: Final[int] = 0x2A  # asterisk

# Struct layout for the 32-byte file header.
# < = little-endian
# B = version (1 byte)
# 3x = last-update date (ignored, 3 bytes)
# I = record count (4 bytes unsigned)
# H = header size (2 bytes unsigned)
# H = record size (2 bytes unsigned)
_HEADER_FMT: Final[str] = "<B3xIHH"

# Each field descriptor is 32 bytes starting at offset 32.
_FIELD_DESC_OFFSET: Final[int] = 32
_FIELD_DESC_SIZE: Final[int] = 32

# Within a field descriptor:
#   bytes 0-10  : field name, null-padded ASCII
#   byte  11    : field type ('C', 'N', 'D', 'L', 'M')
#   bytes 12-15 : reserved
#   byte  16    : field length in bytes
_FIELD_NAME_LEN: Final[int] = 11
_FIELD_LEN_OFF: Final[int] = 16

_HEADER_TERMINATOR: Final[int] = 0x0D

# Source-file encoding for TIGER/Line DBFs -- CP-1252 (latin-1 superset).
_DBF_ENCODING: Final[str] = "cp1252"


def _find_dbf_member(zf: zipfile.ZipFile) -> str:
    """Return the name of the sole ``.dbf`` member inside *zf*.

    Args:
        zf: An open ``zipfile.ZipFile`` object.

    Returns:
        The member name ending in ``.dbf`` (case-insensitive).

    Raises:
        PipelineError: If no ``.dbf`` member is found, or if more than one
            is present (ambiguous).
    """
    dbf_names = [n for n in zf.namelist() if n.lower().endswith(".dbf")]
    if not dbf_names:
        raise PipelineError(f"No .dbf member found in {zf.filename!r}; available: {zf.namelist()}")
    if len(dbf_names) > 1:
        raise PipelineError(f"Multiple .dbf members in {zf.filename!r}: {dbf_names}")
    return dbf_names[0]


def _locate_name_field(fields: list[tuple[str, int]], source_label: str) -> tuple[int, int]:
    """Return the (byte_offset, byte_length) of the NAME field within a record.

    The offset is relative to the start of the record (deletion-flag byte
    is at offset 0; field data starts at offset 1).

    Args:
        fields: List of (field_name, field_length) pairs in declaration order.
        source_label: Label for error messages.

    Returns:
        ``(offset, length)`` tuple where *offset* is relative to the
        record start (offset 1 = immediately after the deletion flag).

    Raises:
        PipelineError: If the NAME field is not present.
    """
    cursor = 1  # skip the 1-byte deletion flag
    for field_name, field_len in fields:
        if field_name == _NAME_FIELD:
            return cursor, field_len
        cursor += field_len
    available = [f for f, _ in fields]
    raise PipelineError(
        f"{source_label}: DBF missing required field {_NAME_FIELD!r}; available fields: {available}"
    )


def _parse_dbf_bytes(raw: bytes, source_label: str) -> set[str]:
    """Parse a dBase III/IV ``.dbf`` byte string and return normalised NAME values.

    Records flagged as deleted (deletion byte ``0x2A``) are skipped.
    Only the ``NAME`` field is returned; ``NAMELSAD`` (NAME + legal suffix)
    is present in TIGER PLACE DBFs but the GNIS join uses the suffix-stripped
    ``NAME`` directly (see module docstring for rationale).

    Normalisation: ``name.casefold().strip()``.

    Args:
        raw: The complete raw bytes of a ``.dbf`` file.
        source_label: Human-readable label for error messages (e.g. the
            zip path + member name).

    Returns:
        Set of normalised ``NAME`` strings from active (non-deleted) records.

    Raises:
        PipelineError: If the header is malformed, required fields are
            absent, or the byte stream is too short for the declared record
            layout.
    """
    if len(raw) < _FIELD_DESC_OFFSET:
        raise PipelineError(f"{source_label}: DBF too short to contain a header ({len(raw)} bytes)")

    # Unpack the fixed-size file header (12 meaningful bytes at offset 0).
    _version, num_records, header_size, record_size = struct.unpack_from(_HEADER_FMT, raw, 0)

    if header_size < _FIELD_DESC_OFFSET + _FIELD_DESC_SIZE:
        raise PipelineError(
            f"{source_label}: DBF header_size={header_size} too small to hold any field descriptors"
        )
    if record_size == 0:
        raise PipelineError(f"{source_label}: DBF record_size=0; malformed file")

    # Parse field descriptors until the 0x0D terminator.
    fields: list[tuple[str, int]] = []
    pos = _FIELD_DESC_OFFSET
    found_terminator = False
    while pos + _FIELD_DESC_SIZE <= len(raw):
        if raw[pos] == _HEADER_TERMINATOR:
            found_terminator = True
            break
        name_bytes = raw[pos : pos + _FIELD_NAME_LEN]
        field_name = name_bytes.rstrip(b"\x00").decode("ascii", errors="replace")
        field_len = raw[pos + _FIELD_LEN_OFF]
        fields.append((field_name, field_len))
        pos += _FIELD_DESC_SIZE

    if not found_terminator:
        raise PipelineError(
            f"{source_label}: DBF header terminator (0x0D) not found before byte {len(raw)}"
        )
    if not fields:
        raise PipelineError(f"{source_label}: DBF contains no field descriptors")

    # Locate the NAME field offset within a record.
    name_offset, name_len = _locate_name_field(fields, source_label)

    # Verify the data area is large enough.
    data_start = header_size
    if len(raw) < data_start + num_records * record_size:
        raise PipelineError(
            f"{source_label}: DBF data area shorter than declared "
            f"(header_size={header_size}, num_records={num_records}, "
            f"record_size={record_size}, file_size={len(raw)})"
        )

    # Stream records, accumulating normalised NAME values.
    names: set[str] = set()
    offset = data_start
    for _ in range(num_records):
        deletion_flag = raw[offset]
        if deletion_flag != _FLAG_DELETED:
            field_bytes = raw[offset + name_offset : offset + name_offset + name_len]
            raw_name = field_bytes.decode(_DBF_ENCODING, errors="replace").rstrip()
            if raw_name:
                names.add(raw_name.casefold().strip())
        offset += record_size

    return names


def parse_tiger_place_zip(zip_path: Path) -> set[str]:
    """Return normalised place NAMEs from a single TIGER PLACE ``.zip`` archive.

    Opens the zip in-memory (without extraction), reads the ``.dbf``
    member as raw bytes, and parses record-by-record.  Only the ``NAME``
    field is returned; ``NAMELSAD`` (NAME + suffix like "city" / "town")
    is present in the DBF but the GNIS join uses suffix-stripped ``NAME``
    directly (see module docstring for rationale).

    Args:
        zip_path: Path to a ``tl_2024_<FIPS>_place.zip`` file.

    Returns:
        Set of normalised (casefolded, stripped) place name strings.

    Raises:
        PipelineError: If the zip, the DBF member, or the DBF bytes are
            malformed.
    """
    if not zip_path.is_file():
        raise PipelineError(f"TIGER PLACE zip not found: {zip_path}")

    try:
        with zipfile.ZipFile(zip_path) as zf:
            dbf_member = _find_dbf_member(zf)
            raw = zf.read(dbf_member)
    except zipfile.BadZipFile as exc:
        raise PipelineError(f"Malformed zip at {zip_path}: {exc}") from exc

    source_label = f"{zip_path.name}/{dbf_member}"
    return _parse_dbf_bytes(raw, source_label)


def parse_tiger_place_dir(
    tiger_dir: Path | None = None,
) -> set[str]:
    """Return the union of normalised place NAMEs from all TIGER PLACE zips.

    Iterates over every ``tl_2024_*_place.zip`` file in *tiger_dir*,
    parsing each one record-by-record.  Each file's raw bytes are read
    and released before the next file is opened so peak memory is bounded
    by the largest single-state DBF (a few MB), not the sum of all 51.

    Args:
        tiger_dir: Directory containing the per-state TIGER PLACE zips.
            Defaults to the committed sources directory at
            ``src/resecta_data/gazetteers/address_components/sources/tiger_places/``.

    Returns:
        Union set of normalised place name strings across all 51 state
        archives.

    Raises:
        PipelineError: If *tiger_dir* does not exist, contains no TIGER
            PLACE zips, or any individual zip/DBF is malformed.
    """
    if tiger_dir is None:
        tiger_dir = Path(__file__).resolve().parent / "sources" / "tiger_places"

    if not tiger_dir.is_dir():
        raise PipelineError(f"TIGER PLACE sources directory not found: {tiger_dir}")

    zip_paths = sorted(tiger_dir.glob("tl_2024_*_place.zip"))
    if not zip_paths:
        raise PipelineError(f"No TIGER PLACE zips (tl_2024_*_place.zip) found in {tiger_dir}")

    all_names: set[str] = set()
    for zip_path in zip_paths:
        names = parse_tiger_place_zip(zip_path)
        all_names |= names

    return all_names
