"""Unit + property tests for NPI / DEA checksum helpers.

The ``@given`` tests carry ``@settings(deadline=None)`` (speed plan #21,
verifier B-C6): under ``gmake verify``'s ``-j5`` ensemble the CPU is
oversubscribed and Hypothesis's default 200 ms per-example deadline can
fire as a spurious ``DeadlineExceeded`` — the only realistic flake mode
for these pure-arithmetic properties, and one whose cost is a full verify
re-run. The properties themselves are unaffected; this is insurance, not
a speed change.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from resecta_data.vectors._checksum import (
    cms_luhn,
    compute_npi_check_digit,
    dea_check_digit,
)

# -----------------------------------------------------------------------------
# CMS Luhn (NPI)
# -----------------------------------------------------------------------------


@settings(deadline=None)
@given(first_nine=st.from_regex(r"^\d{9}$", fullmatch=True))
def test_compute_npi_check_digit_roundtrip(first_nine: str) -> None:
    """Composing an NPI from 9 random digits plus the computed check digit
    always yields cms_luhn remainder 0."""
    check = compute_npi_check_digit(first_nine)
    npi = first_nine + str(check)
    assert cms_luhn(npi) == 0


@settings(deadline=None)
@given(first_nine=st.from_regex(r"^\d{9}$", fullmatch=True))
def test_wrong_check_digit_never_valid(first_nine: str) -> None:
    """Substituting any other digit for the correct check breaks Luhn."""
    correct = compute_npi_check_digit(first_nine)
    for off in range(1, 10):
        wrong = (correct + off) % 10
        assert cms_luhn(first_nine + str(wrong)) != 0


def test_cms_luhn_rejects_wrong_length() -> None:
    with pytest.raises(ValueError):
        cms_luhn("12345")
    with pytest.raises(ValueError):
        cms_luhn("12345678901")


def test_cms_luhn_rejects_non_digits() -> None:
    with pytest.raises(ValueError):
        cms_luhn("1234abcd56")


# -----------------------------------------------------------------------------
# DEA check digit
# -----------------------------------------------------------------------------


@settings(deadline=None)
@given(first_six=st.from_regex(r"^\d{6}$", fullmatch=True))
def test_dea_check_digit_in_range(first_six: str) -> None:
    check = dea_check_digit(first_six)
    assert 0 <= check <= 9
