"""Build the nicknames sidecar artifact (``build/gazetteers/nicknames.json``).

Consumes the carltonnorthern/nicknames CC0 dataset fetched into
``src/resecta_data/gazetteers/nicknames/sources/nicknames_cc0_YYYYMMDD.csv``
by ``scripts/fetch_nicknames.sh``.

The artifact is a JSON sidecar — not part of the signed bloom manifest —
consumed by ``NicknameGazetteer`` in the Swift engine to widen given-name
lookups via nickname→canonical resolution.  Bloom filters and signing
artifacts are unchanged by this builder.

See ``schemas/nicknames.schema.json`` for the full wire contract.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final

from resecta_data.common.exceptions import MissingSourceError, PipelineError
from resecta_data.common.io import sha256_file

from .parse_nicknames import parse_nicknames

_GENERATED_BY: Final[str] = "resecta-data/gazetteers/nicknames"
_SCHEMA_VERSION: Final[int] = 1
_SOURCE_ID: Final[str] = "nicknames_cc0"

_SOURCE_GLOB: Final[str] = "nicknames_cc0_*.csv"
_DATE_RE: Final[re.Pattern[str]] = re.compile(r"nicknames_cc0_(\d{8})\.csv$")

_DEFAULT_SOURCE_DIR: Final[Path] = Path(__file__).resolve().parent / "sources"


def _parse_retrieval_date(filename: str) -> str:
    """Extract and format the retrieval date from a dated source filename.

    Args:
        filename: Base filename of the form ``nicknames_cc0_YYYYMMDD.csv``.

    Returns:
        ISO date string ``YYYY-MM-DD``.

    Raises:
        PipelineError: If the filename does not match the expected pattern.
    """
    m = _DATE_RE.search(filename)
    if not m:
        raise PipelineError(
            f"Cannot extract retrieval date from source filename {filename!r}; "
            "expected pattern nicknames_cc0_YYYYMMDD.csv"
        )
    raw = m.group(1)
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"


def build(
    seed: int,
    *,
    source_dir: Path | None = None,
) -> dict[str, Any]:
    """Build the nicknames sidecar payload.

    Globs ``source_dir`` for exactly one ``nicknames_cc0_*.csv`` file, parses
    it into an alias→canonicals mapping, and returns a payload conforming to
    ``schemas/nicknames.schema.json``.

    Args:
        seed: PRNG seed.  Recorded for reproducibility; aggregation is
            deterministic without randomness.
        source_dir: Override for the sources directory.  Defaults to the
            ``sources/`` subdirectory beside this module.  Injectable for
            testing.

    Returns:
        Payload dict conforming to ``schemas/nicknames.schema.json``.

    Raises:
        MissingSourceError: If no matching CSV is found.
        PipelineError: If more than one matching CSV is found (ambiguous
            which retrieval date applies), or if the filename is malformed.
    """
    sdir = source_dir if source_dir is not None else _DEFAULT_SOURCE_DIR
    matches = sorted(sdir.glob(_SOURCE_GLOB))

    if not matches:
        raise MissingSourceError(
            f"No nicknames source found at {sdir / _SOURCE_GLOB}. "
            "Run scripts/fetch_nicknames.sh to fetch the CC0 dataset."
        )
    if len(matches) > 1:
        raise PipelineError(
            f"Multiple nicknames source files found in {sdir}: "
            f"{[p.name for p in matches]}. "
            "Remove all but the current dated file."
        )

    source_path = matches[0]
    retrieval_date = _parse_retrieval_date(source_path.name)
    entries = parse_nicknames(source_path)

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _GENERATED_BY,
        "seed": seed,
        "sources": [
            {
                "id": _SOURCE_ID,
                "sha256": sha256_file(source_path),
                "retrieval_date": retrieval_date,
            }
        ],
        "entries": entries,
    }
