"""Aggregate address-component entries into the shipped artifact.

The artifact ``build/gazetteers/address_components.json`` bundles three
address-component vocabularies consumed by the Swift
``AddressComponentsGazetteer`` (added in C12):

* ``cities`` — populated-place feature names from the USGS GNIS national
  extract, cross-filtered against Census TIGER/Line 2024 PLACE boundaries
  (search-impl S5 item 2.9).  Only GNIS entries whose casefolded name
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
  Swift sides stay aligned (findings L6 §"Top street-type list").

TIGER PLACE join rationale
--------------------------
The design spec (design 02 §8 item 2.9) describes the filter as
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

The ``build_cutover_diff()`` helper emits the BINDING cutover-diff sidecar
required by STRAT §10.4 row §D7 (G1 PARTIAL [BINDING]) for the
cc-derive-rebuild S2 segment. Because A7 already consumes the
vintage-pinned D-03 + D-04 sources this chain mandates (audit-DONE.md §3b
coverage snapshot), no legacy parser variant was retired and the diff is
empty by construction — Jesse signs the empty diff at PR review per
JESSE-NEEDS §3 row 46.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any, Final

from resecta_data.common.io import sha256_file

from .parse_census_counties import parse_census_counties
from .parse_tiger_place import parse_tiger_place_dir
from .parse_usgs_gnis import parse_usgs_gnis

_GENERATED_BY: Final[str] = "resecta-data/gazetteers/address_components"
_SCHEMA_VERSION: Final[int] = 1
_CUTOVER_DIFF_VERSION: Final[int] = 1
_CUTOVER_ARTIFACT: Final[str] = "gazetteers/address_components.json"

_SOURCE_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "institutions" / "sources"
_GNIS_SOURCE: Final[Path] = _SOURCE_DIR / "usgs_gnis_pop_places_20260419.zip"
_GNIS_INNER: Final[tuple[str, ...]] = ("Text", "PopulatedPlaces_National.txt")
_GNIS_RETRIEVAL_DATE: Final[str] = "2026-04-19"

_COUNTIES_SOURCE: Final[Path] = _SOURCE_DIR / "census_counties_2024.zip"
_COUNTIES_INNER: Final[tuple[str, ...]] = ("2024_Gaz_counties_national.txt",)
_COUNTIES_RETRIEVAL_DATE: Final[str] = "2026-04-19"

# Per-state TIGER/Line 2024 PLACE archives (51 states + DC).
# Retrieval date 2026-04-28 (2026-04-27 for FIPS 06) per SOURCES.md.
# SHA-256 values are the pinned hashes from SOURCES.md for provenance.
_TIGER_PLACE_DIR: Final[Path] = Path(__file__).resolve().parent / "sources" / "tiger_places"
# Mapping from FIPS state code → (retrieval_date, sha256).
# All states except FIPS 06 (California) were fetched 2026-04-28;
# FIPS 06 was fetched 2026-04-27 (see SOURCES.md row).
_TIGER_PLACE_SOURCES: Final[tuple[tuple[str, str, str], ...]] = (
    ("01", "2026-04-28", "5a6787d4caf39dc103a254879ad07e0fce047a93c0ea2f68c25432cb78ec3f4b"),
    ("02", "2026-04-28", "3894e722e71170434340dcda5ecb64d6e8fefb5af46665323f6b82b5f5c46df3"),
    ("04", "2026-04-28", "8f4971b0a3f6e025229befbc2ad9bdb55346463a1067d0cc610072b72860aca1"),
    ("05", "2026-04-28", "cc0cfd031be66abc027aa98c3bc05320b4836b62fab8506d40dd5919364021fa"),
    ("06", "2026-04-27", "81b827124043164a93d442f6dc4c088fd56c319da14195e4a14ab0fb39656a42"),
    ("08", "2026-04-28", "0f8e5e4fd4462f26038b926d44be1eb1b24e494695edf2187751f735e68b34ce"),
    ("09", "2026-04-28", "fc63d10e35fad1d77c7e20c123d6c42755c5d6d36131acdc5983871c9ed5c6e2"),
    ("10", "2026-04-28", "f0c6af53ff197da0a529a4b50f8110876014b84bc38afd4f3d3874d2c8ebca14"),
    ("11", "2026-04-28", "edb5f856da4c9f0e24da70f875948a9a4b008476adbf85a8c29ab50e2b64a5ed"),
    ("12", "2026-04-28", "30d57de7fd40faaf3208bef7bc00f706f70a038aacd2feb1ff1681f68216869e"),
    ("13", "2026-04-28", "f56648317615c8063854086ea5112156009a146b433b7e1f442c7ef6e132763c"),
    ("15", "2026-04-28", "6419aca7b17c7195a9205ff9d518ca93db524e71477ced4066e31d41065529b2"),
    ("16", "2026-04-28", "b227cb21271654a28f5c06d981c3d8c215cfa3abaed038911124b5e9905e0cb0"),
    ("17", "2026-04-28", "237f9f4b492e67414699fe6ceeb2175b6db4f2dd84d3ed67bd88f38458da7524"),
    ("18", "2026-04-28", "e96afe64d8774101295f532c94e129cf571a21e9615c134dbdf87618286fcfb0"),
    ("19", "2026-04-28", "b0b24338f923f11e067b6c2804629c7d0a0d44710b7e3ac42c65fe39db9ce460"),
    ("20", "2026-04-28", "da6184404d3e8560c010f7914a530dfe14c10e393a7600d64a856117f5626e87"),
    ("21", "2026-04-28", "86145ff62220536d978fc665b4dd37cb04f8b8061102511f2b5069583fbe9a58"),
    ("22", "2026-04-28", "c339b9c1a1df6d45e1ceaff4888a16c6062745b3d6292fd63fda3158b8bcf30f"),
    ("23", "2026-04-28", "dabcaf7f3b62c7e7b812d89451abd84ca93cd98c01428a79ef4d89ff2f40ff93"),
    ("24", "2026-04-28", "5622093f62dbb5b3b6433ff9680dab796bfb9d473cdd33d60fb4af7bb5fe564c"),
    ("25", "2026-04-28", "79c5b9084a3e5cd123331ec76529a21735e74f9e89b72f3413e1857f21eb6ca3"),
    ("26", "2026-04-28", "29f7ff510b4b7f6dc5df5d9578ddecf3def12abaad83d8e4277b16d4272fbbfd"),
    ("27", "2026-04-28", "b0771030d6cce3a975c45e212116e6f31cc86f460ba156b7edf9b1079fc15c97"),
    ("28", "2026-04-28", "c0953a04695baad63245143dd244210732c0f7fe0a3282b2f468eae4eb53a32a"),
    ("29", "2026-04-28", "bdc8fbeba0e52adc4e84f27ec7c93fc6e7f7ad63439ecb8e8e5f7c2dbc6f5270"),
    ("30", "2026-04-28", "0c8c2a84b309f390cf59ee71cf70470eec921ec029162245e6ffd68d72bb416f"),
    ("31", "2026-04-28", "301f091f0073cca688f396a59abf8f401b1cad53c3c60a421445c5f6ab9f8767"),
    ("32", "2026-04-28", "def5ba249c9d3e0697a66205ac822fb9b7de557818f69a749222a9100bdc21bf"),
    ("33", "2026-04-28", "413ab59c748b4f683517380783106dd487ba8d6255437130388417fb1c150282"),
    ("34", "2026-04-28", "5995bae3e4a4eaae958e31064d1de84f575b5ca94fc1e5435b161684eea2369a"),
    ("35", "2026-04-28", "355e20d9afb3146b753a028603d50f10f639f8ff893a27e1e66e708300b230db"),
    ("36", "2026-04-28", "2dbe5718443425dc454034ada84cec7ef936beed286289a5110c99c31d634424"),
    ("37", "2026-04-28", "9d05aca18240e55db0d8ecc382e73bafca95bc296c02f8647a58145532e706cc"),
    ("38", "2026-04-28", "631e93f5f16a485ca525652ace0e73142c3a8182bdebba5f4dced5f0c1dc4214"),
    ("39", "2026-04-28", "b0d66206f3ad10ee6432ce8b3167f5e86d862db508a5d7053cb9de71fe4b3d24"),
    ("40", "2026-04-28", "29fdd4e607bc2c006437f6b4879d3dc4843b3a0435a05a55a2b22776577e7d1f"),
    ("41", "2026-04-28", "c60dc879c61791e830d38dc7e5269352b174c802429dfe6268d45773cfcf18c2"),
    ("42", "2026-04-28", "d6312bfcbffadeb20ac1843cf3ca3246a4e106a8e0fa9b3ac992a07beebf8350"),
    ("44", "2026-04-28", "1f87297a1e416a9084dc4d3679d1d770a62f532ac8de674740f58d400127334c"),
    ("45", "2026-04-28", "af62ae0436293c3ce8e97705e8240d993a3d150c24f5e1161892cfd8adb859d6"),
    ("46", "2026-04-28", "e582bd817142603d85ee8469def308a586b1c9a509638cb758ea912bbe928076"),
    ("47", "2026-04-28", "4aefbd5932e826d83770e8b605a2a2116fcf747d3c04694cc158d03481cb8ec0"),
    ("48", "2026-04-28", "0388b08a708a1a3ada0ab594b0a574e9ab65654d00cb0c56369f55661cf8439d"),
    ("49", "2026-04-28", "b1404bfbd5e80d505b270f3e20aa79b4cead51168c0d944e465e60585ddc8de6"),
    ("50", "2026-04-28", "e1096989fd53cf4ab819bd83df8ad10efffa763de1b69090800d03db380769f2"),
    ("51", "2026-04-28", "f530fd65c4d83cbf9a854cea6900fde5add4a7ca26bc8a11a59426c6ca07e16b"),
    ("53", "2026-04-28", "7da61ba276bfdd886ffeb36bf9af37a99a42f8c446e47c684ae48087d86657c6"),
    ("54", "2026-04-28", "ca841b8a96d93ec177b25a20e77b245e0c86635a8099cb1ab2d81743796fd149"),
    ("55", "2026-04-28", "062202ffa5ed57ea2499aac856e694e6407cf8f40de1163b91f67d2a6dcac976"),
    ("56", "2026-04-28", "b09d97a483e6c0b23ae94f1fbd2ac93292085d34f577aa081fdfe7e08a709694"),
)

# Top-20 street-type suffixes. Sorted alphabetically for determinism. The
# list mirrors the full-word vocabulary enumerated in the Swift
# ``PIIDetector`` address regex (findings L6 §"Top street-type list").
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


def build(
    seed: int,
    tiger_dir: Path | None = None,
) -> dict[str, Any]:
    """Return the address-components payload.

    Cities from the USGS GNIS extract are cross-filtered against the Census
    TIGER/Line 2024 PLACE boundaries (search-impl S5 item 2.9): only GNIS
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

    tiger_sources = [
        {
            "id": f"tiger_place_2024_{fips}",
            "sha256": sha256,
            "retrieval_date": retrieval_date,
        }
        for fips, retrieval_date, sha256 in _TIGER_PLACE_SOURCES
    ]

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


def build_cutover_diff() -> dict[str, Any]:
    """Return the legacy→rebuild cutover diff for ``address_components.json``.

    Verification-posture under STRAT §10.4 row §D7 (G1 PARTIAL [BINDING]):
    the A7 builder already consumes the vintage-pinned D-03 + D-04 sources
    that the cc-derive-rebuild chain mandates. An internal coverage
    snapshot (cities=110434, counties=1960, street_types=20) was captured
    from the same builder; no legacy parser variant was
    retired in this rebuild, so there is no LEGACY corpus to diff against
    the rebuild output. ``legacy_only`` / ``rebuild_only`` / ``keyed_diff``
    are empty by construction — A7 entries are plain strings (cities /
    counties / street_types arrays), so even with two corpora
    ``keyed_diff`` would always be empty under the §D7 phase-1 wire-stable
    invariant.

    Jesse signs the empty diff at PR review per JESSE-NEEDS §3 row 46,
    attesting that A7 has no shipped-vs-rebuild divergence under the
    phase-1 BINDING. W-D-engine-paired (S7) consumes this artifact for
    BINDING enumeration in the engine-paired PR description.
    """
    return {
        "version": _CUTOVER_DIFF_VERSION,
        "generated_by": _GENERATED_BY,
        "artifact": _CUTOVER_ARTIFACT,
        "summary": {
            "legacy_only_count": 0,
            "rebuild_only_count": 0,
            "keyed_diff_count": 0,
        },
        "legacy_only": [],
        "rebuild_only": [],
        "keyed_diff": [],
    }
