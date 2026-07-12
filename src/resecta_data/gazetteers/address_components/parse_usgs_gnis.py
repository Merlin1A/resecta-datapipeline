"""Parser for the USGS GNIS Populated Places national extract.

The GNIS text extract ships as a ``.zip`` wrapper with an inner pipe-delimited
UTF-8-with-BOM file at ``Text/PopulatedPlaces_National.txt``. Both the zip
wrapper and the inner file layout are preserved as published; we
stream-read the inner text via ``zipfile.Path`` rather than
extracting to disk.

Columns consumed: ``feature_name``. Other columns (state, county, lat/long,
date-created, etc.) are documented for reference but not emitted — the
downstream Swift ``AddressComponentsGazetteer`` only needs the name set.
"""

from __future__ import annotations

import csv
import zipfile
from typing import Final

from resecta_data.common.exceptions import PipelineError

_NAME_COLUMN: Final[str] = "feature_name"


def parse_usgs_gnis(source: zipfile.Path) -> set[str]:
    """Parse the GNIS Populated Places inner file into a set of city names.

    Args:
        source: A ``zipfile.Path`` pointing at the inner
            ``Text/PopulatedPlaces_National.txt``. The caller is responsible
            for constructing the path from a ``ZipFile`` wrapper so that no
            extraction step touches disk.

    Returns:
        A set of trimmed, non-empty feature names. Duplicate names (same
        feature name in different states) collapse to a single entry because
        the shipped artifact only needs membership lookup.

    Raises:
        PipelineError: If the header is wrong or the stream is empty.
    """
    with source.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="|")
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
        raise PipelineError(f"{source!s}: parsed zero populated-place rows")

    return names
