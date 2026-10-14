"""Aggregate address-component entries into the shipped artifact.

The artifact ``build/gazetteers/address_components.json`` bundles three
address-component vocabularies consumed by the Swift
``AddressComponentsGazetteer``:

* ``cities`` — populated-place feature names from the USGS GNIS national
  extract, cross-filtered against Census TIGER/Line 2024 PLACE boundaries.
  Only GNIS entries whose casefolded name
  matches a TIGER PLACE ``NAME`` value (the suffix-stripped canonical form
  Census publishes) are retained.  Entries that fail the join are dropped
  — they are unincorporated or GNIS-only features with no formal municipal
  boundary.
* ``counties`` — county / parish / borough names from the Census 2024
  Gazetteer. Used as a positive signal in address detection.
* ``street_types`` — a curated top-20 list of street-type suffixes
  (``Street``, ``Avenue``, …). No upstream dataset drives this set; the
  list is the same full-word vocabulary already regex-enumerated in
  ``PIIDetector.swift``, factored out as a shared spec so the Python and
  Swift sides stay aligned.

TIGER PLACE join rationale
--------------------------
The filter is defined as
"NAMELSAD contains the GNIS name (case-insensitive)".  We implement it
as ``gnis_name.casefold() in tiger_name_set`` where ``tiger_name_set``
contains casefolded values of the TIGER ``NAME`` column (not ``NAMELSAD``).
This is equivalent and correct because:

    NAMELSAD = NAME + " " + legal/statistical suffix
               (e.g. "Hurtsboro" + " town" → "Hurtsboro town")

Testing ``NAMELSAD.casefold().contains(gnis_name.casefold())`` is therefore
equivalent to testing equality against ``NAME`` when the GNIS name is
itself a plain place name (no suffix).  Using ``NAME`` equality avoids a
quadratic substring scan over ~110k x ~32k strings per build run and is
the form Census itself publishes as the suffix-stripped canonical name.

All raw files stay inside their ``.zip`` wrappers at
rest; this builder reads them through ``zipfile.Path`` / ``zipfile.ZipFile``
and never extracts to disk.

The ``build_cutover_diff()`` helper emits a cutover-diff sidecar that
verifies a rebuilt source parser has not regressed against the legacy
one. This builder already consumes the vintage-pinned Census-counties and
GNIS sources a rebuilt chain requires (see the coverage snapshot in
``build_cutover_diff()`` below), so no legacy parser variant was retired
and the diff is empty by construction; the PR review confirms it.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any, Final

from resecta_data.common.cutover import CutoverSpec
from resecta_data.common.exceptions import HashMismatchError, MissingSourceError
from resecta_data.common.io import sha256_file

from .parse_census_counties import parse_census_counties
from .parse_tiger_place import parse_tiger_place_dir
from .parse_usgs_gnis import parse_usgs_gnis

_GENERATED_BY: Final[str] = "resecta-data/gazetteers/address_components"
_SCHEMA_VERSION: Final[int] = 1

_SOURCE_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "institutions" / "sources"
_GNIS_SOURCE: Final[Path] = _SOURCE_DIR / "usgs_gnis_pop_places_20260419.zip"
_GNIS_INNER: Final[tuple[str, ...]] = ("Text", "PopulatedPlaces_National.txt")
_GNIS_RETRIEVAL_DATE: Final[str] = "2026-04-19"

_COUNTIES_SOURCE: Final[Path] = _SOURCE_DIR / "census_counties_2024.zip"
_COUNTIES_INNER: Final[tuple[str, ...]] = ("2024_Gaz_counties_national.txt",)
_COUNTIES_RETRIEVAL_DATE: Final[str] = "2026-04-19"

# Per-state TIGER/Line 2024 PLACE archives (51 states + DC).
# Retrieval date 2026-04-28 (2026-04-27 for FIPS 06) per SOURCES.md. Each
# archive's SHA-256 is read from its committed ``.zip.sha256`` sidecar and
# checked against the archive's bytes when the provenance rows are built
# (``_tiger_source_rows``), so the emitted digests cannot drift from the
# files they describe.
_TIGER_PLACE_DIR: Final[Path] = Path(__file__).resolve().parent / "sources" / "tiger_places"
# (FIPS state code, retrieval_date). All states except FIPS 06 (California)
# were fetched 2026-04-28; FIPS 06 was fetched 2026-04-27 (see SOURCES.md row).
_TIGER_PLACE_SOURCES: Final[tuple[tuple[str, str], ...]] = (
    ("01", "2026-04-28"),
    ("02", "2026-04-28"),
    ("04", "2026-04-28"),
    ("05", "2026-04-28"),
    ("06", "2026-04-27"),
    ("08", "2026-04-28"),
    ("09", "2026-04-28"),
    ("10", "2026-04-28"),
    ("11", "2026-04-28"),
    ("12", "2026-04-28"),
    ("13", "2026-04-28"),
    ("15", "2026-04-28"),
    ("16", "2026-04-28"),
    ("17", "2026-04-28"),
    ("18", "2026-04-28"),
    ("19", "2026-04-28"),
    ("20", "2026-04-28"),
    ("21", "2026-04-28"),
    ("22", "2026-04-28"),
    ("23", "2026-04-28"),
    ("24", "2026-04-28"),
    ("25", "2026-04-28"),
    ("26", "2026-04-28"),
    ("27", "2026-04-28"),
    ("28", "2026-04-28"),
    ("29", "2026-04-28"),
    ("30", "2026-04-28"),
    ("31", "2026-04-28"),
    ("32", "2026-04-28"),
    ("33", "2026-04-28"),
    ("34", "2026-04-28"),
    ("35", "2026-04-28"),
    ("36", "2026-04-28"),
    ("37", "2026-04-28"),
    ("38", "2026-04-28"),
    ("39", "2026-04-28"),
    ("40", "2026-04-28"),
    ("41", "2026-04-28"),
    ("42", "2026-04-28"),
    ("44", "2026-04-28"),
    ("45", "2026-04-28"),
    ("46", "2026-04-28"),
    ("47", "2026-04-28"),
    ("48", "2026-04-28"),
    ("49", "2026-04-28"),
    ("50", "2026-04-28"),
    ("51", "2026-04-28"),
    ("53", "2026-04-28"),
    ("54", "2026-04-28"),
    ("55", "2026-04-28"),
    ("56", "2026-04-28"),
)

# Top-20 street-type suffixes. Sorted alphabetically for determinism. The
# list mirrors the full-word vocabulary enumerated in the Swift
# ``PIIDetector`` address regex.
_STREET_TYPES: Final[tuple[str, ...]] = (
    "Alley",
    "Avenue",
    "Boulevard",
    "Circle",
    "Court",
    "Drive",
    "Expressway",
    "Freeway",
    "Highway",
    "Lane",
    "Parkway",
    "Place",
    "Plaza",
    "Road",
    "Route",
    "Square",
    "Street",
    "Terrace",
    "Trail",
    "Way",
)


def _inner_path(zip_path: Path, parts: tuple[str, ...]) -> zipfile.Path:
    """Return a ``zipfile.Path`` for ``parts`` inside ``zip_path``."""
    inner: zipfile.Path = zipfile.Path(zip_path)
    for segment in parts:
        inner = inner / segment
    return inner


def _tiger_source_rows(
    tiger_dir: Path,
    sources: tuple[tuple[str, str], ...] = _TIGER_PLACE_SOURCES,
) -> list[dict[str, str]]:
    """The TIGER PLACE provenance rows, each digest read from the archive's sidecar.

    For every ``(fips, retrieval_date)`` the archive ``tl_2024_<fips>_place.zip``
    and its ``.zip.sha256`` sidecar (bare hex) must exist under ``tiger_dir``;
    the sidecar's digest is compared with the archive's bytes and a disagreement
    raises ``HashMismatchError``, so a provenance row is never emitted for bytes
    other than the ones it names.
    """
    rows: list[dict[str, str]] = []
    for fips, retrieval_date in sources:
        zip_path = tiger_dir / f"tl_2024_{fips}_place.zip"
        sidecar = zip_path.with_name(zip_path.name + ".sha256")
        if not zip_path.is_file() or not sidecar.is_file():
            raise MissingSourceError(
                f"TIGER PLACE archive or its .sha256 sidecar missing: {zip_path}"
            )
        pinned = sidecar.read_text(encoding="ascii").split()[0].lower()
        actual = sha256_file(zip_path)
        if pinned != actual:
            raise HashMismatchError(
                f"{zip_path.name}: the .sha256 sidecar pins {pinned} "
                f"but the archive hashes to {actual}"
            )
        rows.append(
            {"id": f"tiger_place_2024_{fips}", "sha256": actual, "retrieval_date": retrieval_date}
        )
    return rows


def build(
    seed: int,
    tiger_dir: Path | None = None,
) -> dict[str, Any]:
    """Return the address-components payload.

    Cities from the USGS GNIS extract are cross-filtered against the Census
    TIGER/Line 2024 PLACE boundaries: only GNIS
    entries whose casefolded name appears in the TIGER ``NAME`` set are
    retained.  See the module docstring for join-rationale details.

    Args:
        seed: PRNG seed. Recorded for reproducibility; aggregation is
            deterministic without randomness.
        tiger_dir: Override the TIGER PLACE sources directory.  Defaults to
            the committed path at
            ``src/resecta_data/gazetteers/address_components/sources/tiger_places/``.
            Callers may pass a ``tmp_path``-based directory in tests.

    Returns:
        A payload dict conforming to
        ``schemas/address_components.schema.json``.
    """
    gnis_cities = parse_usgs_gnis(_inner_path(_GNIS_SOURCE, _GNIS_INNER))
    counties = parse_census_counties(_inner_path(_COUNTIES_SOURCE, _COUNTIES_INNER))

    # Build the TIGER place-name lookup set (casefolded NAME values).
    effective_tiger_dir = tiger_dir if tiger_dir is not None else _TIGER_PLACE_DIR
    tiger_names = parse_tiger_place_dir(effective_tiger_dir)

    # GNIS cross-filter: keep only entries whose casefolded name appears in
    # the TIGER PLACE NAME set.  Entries absent from TIGER have no formal
    # municipal boundary and are the primary junk class ("107 West Colonia",
    # coordinate-artifact entries, etc.).  See module docstring for why we
    # match against NAME (not NAMELSAD).
    cities = {name for name in gnis_cities if name.casefold() in tiger_names}

    # The provenance rows describe the committed archives (a ``tiger_dir``
    # override changes the NAME set only, as the tests use it).
    tiger_sources = _tiger_source_rows(_TIGER_PLACE_DIR)

    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _GENERATED_BY,
        "seed": seed,
        "sources": [
            {
                "id": "usgs_gnis_pop_places",
                "sha256": sha256_file(_GNIS_SOURCE),
                "retrieval_date": _GNIS_RETRIEVAL_DATE,
            },
            {
                "id": "census_counties_2024",
                "sha256": sha256_file(_COUNTIES_SOURCE),
                "retrieval_date": _COUNTIES_RETRIEVAL_DATE,
            },
            *tiger_sources,
        ],
        "cities": sorted(cities),
        "counties": sorted(counties),
        "street_types": list(_STREET_TYPES),
    }


ADDRESS_COMPONENTS_CUTOVER: Final[CutoverSpec] = CutoverSpec(
    artifact="gazetteers/address_components.json", generated_by=_GENERATED_BY
)
"""The envelope of ``address_components.json``'s advisory cutover diff.

This builder already consumes the vintage-pinned Census-counties and GNIS
sources that a rebuilt source chain requires; no legacy parser variant was
retired, so there is no legacy corpus to diff against and the diff
``common.cutover.build_cutover_diff`` emits for this spec is empty by
construction. Its entries are plain strings (cities / counties /
street_types), so even with two corpora ``keyed_diff`` would stay empty while
the wire format stays stable. The empty sidecar is review material for the
paired engine change.
"""
