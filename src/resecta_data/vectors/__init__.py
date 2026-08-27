"""Test vector generators — NPI Luhn, DEA checksum, SSN structural (Phase 1)."""

from __future__ import annotations

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

__all__ = [
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
