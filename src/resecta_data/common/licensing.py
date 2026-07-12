"""License and source provenance validation.

Every file under ``src/resecta_data/*/sources/`` must have a row in
``SOURCES.md`` with its license, retrieval URL, retrieval date, and SHA-256.

This module validates that invariant.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .exceptions import LicenseError
from .io import sha256_file

logger = logging.getLogger(__name__)

# Licenses that may be added without Jesse's review.
ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "Public Domain",
        "CC0",
        "CC0-1.0",
        "Apache-2.0",
        "MIT",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "US-Federal-Government",  # 17 U.S.C. § 105
    }
)

# Licenses that require explicit legal review. Adding a source under one of
# these licenses will halt the pipeline unless the corresponding env flag is set.
GATED: Final[dict[str, str]] = {
    "CC-BY-4.0": "RESECTA_ALLOW_CC_BY_ASSETS",
    "CC-BY-SA-4.0": "RESECTA_ALLOW_CC_BY_SA_ASSETS",
}

# Licenses that are outright forbidden.
FORBIDDEN: Final[frozenset[str]] = frozenset(
    {
        "CPT",  # AMA proprietary
        "AMA-CPT",
    }
)


@dataclass(frozen=True, slots=True)
class SourceEntry:
    """One row from ``SOURCES.md``.

    Attributes:
        relative_path: Path relative to ``DataPipeline/``, POSIX style.
        license: SPDX-style license identifier.
        url: URL the source was fetched from.
        retrieved: Retrieval date in ISO ``YYYY-MM-DD`` form.
        sha256: Lowercase hex SHA-256 of the source file.
        description: One-line human description.
    """

    relative_path: str
    license: str
    url: str
    retrieved: str
    sha256: str
    description: str


# Pipe-table row matcher — permissive on whitespace.
_ROW_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\s*\|\s*(?P<path>[^|]+?)\s*"
    r"\|\s*(?P<license>[^|]+?)\s*"
    r"\|\s*(?P<url>[^|]+?)\s*"
    r"\|\s*(?P<retrieved>\d{4}-\d{2}-\d{2})\s*"
    r"\|\s*(?P<sha256>[0-9a-f]{64})\s*"
    r"\|\s*(?P<description>[^|]+?)\s*\|\s*$"
)


def parse_sources_md(path: Path) -> list[SourceEntry]:
    """Parse the pipe-table in ``SOURCES.md``.

    The table is expected to have this header (any order of subsequent rows):

        | Path | License | URL | Retrieved | SHA-256 | Description |
        |------|---------|-----|-----------|---------|-------------|

    Non-table lines, the header row, and the separator row are ignored.

    Args:
        path: SOURCES.md location.

    Returns:
        One ``SourceEntry`` per data row.

    Raises:
        LicenseError: If the file does not exist.
    """
    if not path.is_file():
        raise LicenseError(
            f"SOURCES.md not found at {path}. Every sources/ file must be recorded there."
        )

    entries: list[SourceEntry] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        match = _ROW_PATTERN.match(raw)
        if not match:
            continue
        # Skip the header and separator rows (they look like data rows but have
        # no sha256 that matches the 64-hex pattern).
        entries.append(
            SourceEntry(
                relative_path=match.group("path"),
                license=match.group("license"),
                url=match.group("url"),
                retrieved=match.group("retrieved"),
                sha256=match.group("sha256"),
                description=match.group("description"),
            )
        )
    return entries


def validate_license(entry: SourceEntry) -> None:
    """Check that ``entry.license`` is permitted.

    Args:
        entry: The source entry to validate.

    Raises:
        LicenseError: If the license is forbidden, gated without env flag set,
            or unrecognized.
    """
    if entry.license in FORBIDDEN:
        raise LicenseError(
            f"{entry.relative_path}: license {entry.license!r} is forbidden. See CLAUDE.md §1.2."
        )

    if entry.license in GATED:
        flag = GATED[entry.license]
        if os.environ.get(flag) != "1":
            raise LicenseError(
                f"{entry.relative_path}: license {entry.license!r} requires "
                f"environment variable {flag}=1 (pending legal review). "
                "See CLAUDE.md §1.2."
            )
        return

    if entry.license not in ALLOWLIST:
        raise LicenseError(
            f"{entry.relative_path}: license {entry.license!r} is not on the "
            f"allowlist. Either add it to ALLOWLIST (after review) or escalate. "
            "See CLAUDE.md §2.1."
        )


def validate_source_file(
    entry: SourceEntry,
    pipeline_root: Path,
) -> None:
    """Check that the file at ``entry.relative_path`` exists and hashes correctly.

    Args:
        entry: The source entry to validate.
        pipeline_root: Root of the DataPipeline directory.

    Raises:
        LicenseError: If the file is missing or its hash does not match.
    """
    path = pipeline_root / entry.relative_path
    if not path.is_file():
        raise LicenseError(
            f"{entry.relative_path}: declared in SOURCES.md but file is missing. "
            "Run `make sources` or restore from version control."
        )
    actual = sha256_file(path)
    if actual != entry.sha256:
        raise LicenseError(
            f"{entry.relative_path}: SHA-256 mismatch. "
            f"Expected {entry.sha256}, got {actual}. "
            "Either the file was tampered with or the SOURCES.md row is stale."
        )


def validate_all(
    sources_md: Path,
    pipeline_root: Path,
) -> list[SourceEntry]:
    """Parse SOURCES.md, validate every entry's license and hash, return entries.

    Args:
        sources_md: Path to SOURCES.md.
        pipeline_root: Root of DataPipeline/.

    Returns:
        The validated entries.

    Raises:
        LicenseError: If any row fails validation.
    """
    entries = parse_sources_md(sources_md)
    for entry in entries:
        validate_license(entry)
        validate_source_file(entry, pipeline_root)
    logger.info("SOURCES.md: %d entries validated", len(entries))
    return entries
