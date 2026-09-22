"""Test vector generators — NPI Luhn, DEA checksum, SSN structural (Phase 1)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ._checksum import cms_luhn, dea_check_digit, luhn_mod10
from .bates import build as build_bates_vectors
from .credit_card import build as build_credit_card_vectors
from .dea import build as build_dea_vectors
from .dob import build as build_dob_vectors
from .drivers_license import build as build_drivers_license_vectors
from .ein import build as build_ein_vectors
from .email import build as build_email_vectors
from .itin import build as build_itin_vectors
from .license_plate import build as build_license_plate_vectors
from .mrn import build as build_mrn_vectors
from .npi import build as build_npi_vectors
from .passport import build as build_passport_vectors
from .phone import build as build_phone_vectors
from .routing_number import build as build_routing_number_vectors
from .ssn import build as build_ssn_vectors

VectorBuilder = Callable[[int], dict[str, Any]]
"""A test-vector builder: ``build(seed)`` returns the family's payload."""


@dataclass(frozen=True)
class VectorFamily:
    """One test-vector family: the CLI ``kind``, its builder and its output filename."""

    kind: str
    builder: VectorBuilder
    output_filename: str


VECTOR_FAMILIES: tuple[VectorFamily, ...] = (
    VectorFamily("npi", build_npi_vectors, "npi_test_vectors.json"),
    VectorFamily("dea", build_dea_vectors, "dea_test_vectors.json"),
    VectorFamily("ssn", build_ssn_vectors, "ssn_structural_vectors.json"),
    VectorFamily("credit-card", build_credit_card_vectors, "credit_card_vectors.json"),
    VectorFamily("ein", build_ein_vectors, "ein_vectors.json"),
    VectorFamily("itin", build_itin_vectors, "itin_vectors.json"),
    VectorFamily("dob", build_dob_vectors, "dob_vectors.json"),
    VectorFamily("phone", build_phone_vectors, "phone_test_vectors.json"),
    VectorFamily("email", build_email_vectors, "email_test_vectors.json"),
    VectorFamily("passport", build_passport_vectors, "passport_test_vectors.json"),
    VectorFamily(
        "drivers-license", build_drivers_license_vectors, "drivers_license_test_vectors.json"
    ),
    VectorFamily("mrn", build_mrn_vectors, "mrn_test_vectors.json"),
    VectorFamily("bates", build_bates_vectors, "bates_test_vectors.json"),
    VectorFamily("license-plate", build_license_plate_vectors, "license_plate_test_vectors.json"),
    VectorFamily("routing-number", build_routing_number_vectors, "routing_number_vectors.json"),
)
"""The fifteen families in CLI order.

The CLI's ``kind`` choices, builder lookup and output filenames are views over
this tuple, and the install-routing tests read it too, so a family is added or
renamed in exactly one place.
"""

__all__ = [
    "VECTOR_FAMILIES",
    "VectorBuilder",
    "VectorFamily",
    "build_bates_vectors",
    "build_credit_card_vectors",
    "build_dea_vectors",
    "build_dob_vectors",
    "build_drivers_license_vectors",
    "build_ein_vectors",
    "build_email_vectors",
    "build_itin_vectors",
    "build_license_plate_vectors",
    "build_mrn_vectors",
    "build_npi_vectors",
    "build_passport_vectors",
    "build_phone_vectors",
    "build_routing_number_vectors",
    "build_ssn_vectors",
    "cms_luhn",
    "dea_check_digit",
    "luhn_mod10",
]
