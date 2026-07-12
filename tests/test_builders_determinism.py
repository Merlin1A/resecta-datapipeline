"""Determinism and schema tests for every Phase 1 builder.

For each builder: run it twice with the canonical seed, assert that the
canonical JSON form is byte-identical, and validate against its schema.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from resecta_data.adversarial import build as build_adversarial
from resecta_data.classifier import build_doctype_keywords
from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.io import dump_canonical_json, load_json
from resecta_data.common.schema import validate_file
from resecta_data.fuzz import build as build_fuzz
from resecta_data.gazetteers.address_components import build as build_address_components
from resecta_data.gazetteers.institutions import build as build_institutions
from resecta_data.gazetteers.passport_patterns import build as build_passport_patterns
from resecta_data.vectors import (
    build_bates_vectors,
    build_credit_card_vectors,
    build_dea_vectors,
    build_dob_vectors,
    build_drivers_license_vectors,
    build_ein_vectors,
    build_email_vectors,
    build_itin_vectors,
    build_license_plate_vectors,
    build_mrn_vectors,
    build_npi_vectors,
    build_passport_vectors,
    build_phone_vectors,
    build_ssn_vectors,
)

SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"


@pytest.mark.parametrize(
    ("builder", "schema_name", "filename", "root_key"),
    [
        (build_npi_vectors, "npi_test_vectors", "npi.json", "vectors"),
        (build_dea_vectors, "dea_test_vectors", "dea.json", "vectors"),
        (build_ssn_vectors, "ssn_structural_vectors", "ssn.json", "vectors"),
        (build_credit_card_vectors, "credit_card_vectors", "cc.json", "vectors"),
        (build_ein_vectors, "ein_vectors", "ein.json", "vectors"),
        (build_itin_vectors, "itin_vectors", "itin.json", "vectors"),
        (build_dob_vectors, "dob_vectors", "dob.json", "vectors"),
        (build_phone_vectors, "phone_vectors", "phone.json", "vectors"),
        (build_email_vectors, "email_vectors", "email.json", "vectors"),
        (build_passport_vectors, "passport_vectors", "passport.json", "vectors"),
        (
            build_drivers_license_vectors,
            "drivers_license_vectors",
            "drivers_license.json",
            "vectors",
        ),
        (build_mrn_vectors, "mrn_vectors", "mrn.json", "vectors"),
        (build_bates_vectors, "bates_vectors", "bates.json", "vectors"),
        (
            build_license_plate_vectors,
            "license_plate_vectors",
            "license_plate.json",
            "vectors",
        ),
        (build_fuzz, "redos_payloads", "redos.json", "payloads"),
        (build_adversarial, "adversarial_patterns", "adv.json", "patterns"),
        (build_doctype_keywords, "doctype_keywords", "doctype_keywords.json", "classes"),
        (build_institutions, "institutions", "institutions.json", "entries"),
        (
            build_address_components,
            "address_components",
            "address_components.json",
            "cities",
        ),
        (build_passport_patterns, "passport_patterns", "passport_patterns.json", "rows"),
    ],
)
@pytest.mark.determinism
def test_builder_deterministic(
    builder: object,
    schema_name: str,
    filename: str,
    root_key: str,
    tmp_build_dir: Path,
) -> None:
    payload_a = builder(CANONICAL_SEED)  # type: ignore[operator]
    payload_b = builder(CANONICAL_SEED)  # type: ignore[operator]

    path_a = tmp_build_dir / ("a_" + filename)
    path_b = tmp_build_dir / ("b_" + filename)
    dump_canonical_json(payload_a, path_a)
    dump_canonical_json(payload_b, path_b)

    assert path_a.read_bytes() == path_b.read_bytes()

    # Schema validation.
    validate_file(path_a, SCHEMAS_DIR, schema_name)

    # Shape sanity.
    loaded = load_json(path_a)
    assert loaded["version"] >= 1
    assert loaded["seed"] == CANONICAL_SEED
    assert len(loaded[root_key]) > 0


def test_ssn_contains_literal_woolworth(tmp_build_dir: Path) -> None:
    payload = build_ssn_vectors(CANONICAL_SEED)
    categories = {v["rejection_reason"] for v in payload["vectors"] if not v["valid"]}
    assert "literal_078_05_1120" in categories
    assert any(v["ssn"] == "078-05-1120" for v in payload["vectors"])
