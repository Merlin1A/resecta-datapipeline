"""Parser for the Census 2024 Gazetteer counties file.

The counties gazetteer ships as a ``.zip`` wrapper with an inner tab-delimited
UTF-8-with-BOM file at ``2024_Gaz_counties_national.txt``. The file is a
fixed-width export padded with trailing spaces on the last column — the
standard ``csv`` reader handles that correctly without extra trimming. We
stream-read through ``zipfile.Path`` (raw sources
immutable, no extraction step).

Columns consumed: ``NAME``. Other columns (USPS, GEOID, land/water area,
internal-point lat/long) are preserved in the raw file but not emitted — the
downstream Swift ``AddressComponentsGazetteer`` only needs the name set.
"""

from __future__ import annotations

import csv
import zipfile
from typing import Final

from resecta_data.common.exceptions import PipelineError

_NAME_COLUMN: Final[str] = "NAME"


def parse_census_counties(source: zipfile.Path) -> set[str]:
    """Parse the Census counties gazetteer into a set of county names.

    Args:
        source: A ``zipfile.Path`` pointing at the inner
            ``2024_Gaz_counties_national.txt``.

    Returns:
        A set of trimmed, non-empty county/parish/borough names — the full
        set of subdivisions the Census publishes as "counties-equivalent".

    Raises:
        PipelineError: If the header is wrong or the stream is empty.
    """
    with source.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        header = reader.fieldnames or []
        if _NAME_COLUMN not in header:
            raise PipelineError(
                f"{source!s}: missing required column {_NAME_COLUMN!r}; header was {header!r}"
            )

        names: set[str] = set()
        for raw in reader:
            name = (raw.get(_NAME_COLUMN) or "").strip()
            if name:
                names.add(name)

    if not names:
        raise PipelineError(f"{source!s}: parsed zero county rows")

    return names
