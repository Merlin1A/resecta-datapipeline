"""Tests for resecta_data.common.licensing.

Covers:
  - SOURCES.md pipe-table parsing
  - License allowlist enforcement
  - Gated license behavior under RESECTA_ALLOW_* flags
  - Forbidden license rejection (CPT in particular)
  - Hash verification against the recorded SHA-256
"""

from __future__ import annotations

from pathlib import Path

import pytest

from resecta_data.common.exceptions import LicenseError
from resecta_data.common.io import sha256_bytes
from resecta_data.common.licensing import (
    ALLOWLIST,
    FORBIDDEN,
    GATED,
    SourceEntry,
    parse_sources_md,
    validate_all,
    validate_license,
    validate_source_file,
)

# Header row used by every SOURCES.md fixture below.
_HEADER = (
    "| Path | License | URL | Retrieved | SHA-256 | Description |\n"
    "|------|---------|-----|-----------|---------|-------------|\n"
)


def _write_sources_md(path: Path, rows: list[str]) -> None:
    """Helper: write a SOURCES.md with the given data rows."""
    path.write_text(_HEADER + "\n".join(rows) + "\n", encoding="utf-8")


# -----------------------------------------------------------------------------
# parse_sources_md
# -----------------------------------------------------------------------------


def test_parse_minimal_valid_file(tmp_path: Path) -> None:
    sources = tmp_path / "SOURCES.md"
    _write_sources_md(
        sources,
        [
            "| src/a.csv | Public Domain | https://example.gov/a.csv "
            "| 2026-04-16 | " + ("a" * 64) + " | Example source |",
        ],
    )
    entries = parse_sources_md(sources)
    assert len(entries) == 1
    e = entries[0]
    assert e.relative_path == "src/a.csv"
    assert e.license == "Public Domain"
    assert e.url == "https://example.gov/a.csv"
    assert e.retrieved == "2026-04-16"
    assert e.sha256 == "a" * 64
    assert e.description == "Example source"


def test_parse_multiple_rows(tmp_path: Path) -> None:
    sources = tmp_path / "SOURCES.md"
    _write_sources_md(
        sources,
        [
            "| src/a.csv | Public Domain | https://example.gov/a "
            "| 2026-04-16 | " + ("a" * 64) + " | First |",
            "| src/b.csv | CC0 | https://example.org/b "
            "| 2026-04-16 | " + ("b" * 64) + " | Second |",
            "| src/c.txt | Apache-2.0 | https://example.com/c "
            "| 2026-04-16 | " + ("c" * 64) + " | Third |",
        ],
    )
    entries = parse_sources_md(sources)
    assert len(entries) == 3
    assert [e.license for e in entries] == ["Public Domain", "CC0", "Apache-2.0"]


def test_parse_skips_non_table_content(tmp_path: Path) -> None:
    """Paragraphs and other markdown around the table are ignored."""
    sources = tmp_path / "SOURCES.md"
    sources.write_text(
        "# SOURCES\n\n"
        "This is a preamble paragraph.\n\n"
        + _HEADER
        + "| src/a.csv | CC0 | https://example.org "
        + "| 2026-04-16 | "
        + ("a" * 64)
        + " | Example |\n"
        "\nSome trailing text.\n",
        encoding="utf-8",
    )
    entries = parse_sources_md(sources)
    assert len(entries) == 1


def test_parse_tolerates_extra_whitespace(tmp_path: Path) -> None:
    sources = tmp_path / "SOURCES.md"
    sources.write_text(
        _HEADER + "|   src/a.csv   |   CC0   |   https://example.org   "
        "|   2026-04-16   |   " + ("a" * 64) + "   |   Example   |\n",
        encoding="utf-8",
    )
    entries = parse_sources_md(sources)
    assert len(entries) == 1
    assert entries[0].relative_path == "src/a.csv"
    assert entries[0].description == "Example"


def test_parse_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(LicenseError, match=r"SOURCES\.md not found"):
        parse_sources_md(tmp_path / "nonexistent.md")


def test_parse_empty_table_returns_empty_list(tmp_path: Path) -> None:
    sources = tmp_path / "SOURCES.md"
    sources.write_text(_HEADER, encoding="utf-8")
    assert parse_sources_md(sources) == []


def test_parse_ignores_malformed_rows(tmp_path: Path) -> None:
    """Rows that don't match the full regex are silently skipped.

    This is correct behavior — the separator row |---|---| etc. doesn't match.
    """
    sources = tmp_path / "SOURCES.md"
    _write_sources_md(
        sources,
        [
            "| src/a.csv | CC0 | https://example.org | 2026-04-16 | " + ("a" * 64) + " | Valid |",
            "| incomplete row |",  # deliberately malformed
        ],
    )
    entries = parse_sources_md(sources)
    assert len(entries) == 1


# -----------------------------------------------------------------------------
# validate_license — allowlist
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("license_name", sorted(ALLOWLIST))
def test_allowlist_licenses_pass(license_name: str) -> None:
    entry = SourceEntry(
        relative_path="src/a.csv",
        license=license_name,
        url="https://example.org",
        retrieved="2026-04-16",
        sha256="0" * 64,
        description="x",
    )
    validate_license(entry)  # must not raise


def test_unknown_license_rejected() -> None:
    entry = SourceEntry(
        relative_path="src/a.csv",
        license="Proprietary",
        url="https://example.org",
        retrieved="2026-04-16",
        sha256="0" * 64,
        description="x",
    )
    with pytest.raises(LicenseError, match="not on the allowlist"):
        validate_license(entry)


# -----------------------------------------------------------------------------
# validate_license — forbidden
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("license_name", sorted(FORBIDDEN))
def test_forbidden_licenses_always_rejected(license_name: str) -> None:
    entry = SourceEntry(
        relative_path="src/a.csv",
        license=license_name,
        url="https://example.org",
        retrieved="2026-04-16",
        sha256="0" * 64,
        description="x",
    )
    with pytest.raises(LicenseError, match="forbidden"):
        validate_license(entry)


def test_cpt_codes_forbidden_even_with_env_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CPT codes are AMA-proprietary; no env flag unlocks them."""
    monkeypatch.setenv("RESECTA_ALLOW_CC_BY_ASSETS", "1")
    monkeypatch.setenv("RESECTA_ALLOW_CPT", "1")  # not a real flag
    entry = SourceEntry(
        relative_path="src/cpt.csv",
        license="CPT",
        url="https://ama.example",
        retrieved="2026-04-16",
        sha256="0" * 64,
        description="cpt",
    )
    with pytest.raises(LicenseError, match="forbidden"):
        validate_license(entry)


# -----------------------------------------------------------------------------
# validate_license — gated
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(("license_name", "flag"), sorted(GATED.items()))
def test_gated_license_rejected_without_flag(
    license_name: str,
    flag: str,
    monkeypatch: pytest.MonkeyPatch,
    clean_env: None,
) -> None:
    del flag, clean_env  # fixture clears flags; value checked below
    entry = SourceEntry(
        relative_path="src/a.csv",
        license=license_name,
        url="https://example.org",
        retrieved="2026-04-16",
        sha256="0" * 64,
        description="x",
    )
    with pytest.raises(LicenseError, match="requires environment variable"):
        validate_license(entry)


@pytest.mark.parametrize(("license_name", "flag"), sorted(GATED.items()))
def test_gated_license_accepted_with_flag(
    license_name: str,
    flag: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(flag, "1")
    entry = SourceEntry(
        relative_path="src/a.csv",
        license=license_name,
        url="https://example.org",
        retrieved="2026-04-16",
        sha256="0" * 64,
        description="x",
    )
    validate_license(entry)  # must not raise


def test_gated_license_flag_must_be_exactly_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RESECTA_ALLOW_*=true or =yes must NOT unlock — require strict '1'."""
    monkeypatch.setenv("RESECTA_ALLOW_CC_BY_ASSETS", "true")
    entry = SourceEntry(
        relative_path="src/a.csv",
        license="CC-BY-4.0",
        url="https://example.org",
        retrieved="2026-04-16",
        sha256="0" * 64,
        description="x",
    )
    with pytest.raises(LicenseError, match="requires environment variable"):
        validate_license(entry)


# -----------------------------------------------------------------------------
# validate_source_file — hash and existence
# -----------------------------------------------------------------------------


def test_validate_source_file_accepts_matching_hash(tmp_path: Path) -> None:
    sources_dir = tmp_path / "src"
    sources_dir.mkdir()
    payload = b"example source data"
    (sources_dir / "a.csv").write_bytes(payload)

    entry = SourceEntry(
        relative_path="src/a.csv",
        license="CC0",
        url="https://example.org",
        retrieved="2026-04-16",
        sha256=sha256_bytes(payload),
        description="x",
    )
    validate_source_file(entry, tmp_path)  # must not raise


def test_validate_source_file_rejects_wrong_hash(tmp_path: Path) -> None:
    sources_dir = tmp_path / "src"
    sources_dir.mkdir()
    (sources_dir / "a.csv").write_bytes(b"actual content")

    entry = SourceEntry(
        relative_path="src/a.csv",
        license="CC0",
        url="https://example.org",
        retrieved="2026-04-16",
        sha256="0" * 64,  # wrong
        description="x",
    )
    with pytest.raises(LicenseError, match="SHA-256 mismatch"):
        validate_source_file(entry, tmp_path)


def test_validate_source_file_rejects_missing_file(tmp_path: Path) -> None:
    entry = SourceEntry(
        relative_path="src/missing.csv",
        license="CC0",
        url="https://example.org",
        retrieved="2026-04-16",
        sha256="a" * 64,
        description="x",
    )
    with pytest.raises(LicenseError, match="file is missing"):
        validate_source_file(entry, tmp_path)


# -----------------------------------------------------------------------------
# validate_all — integration
# -----------------------------------------------------------------------------


def test_validate_all_passes_on_clean_fixture(tmp_path: Path) -> None:
    # Create a source file, then a SOURCES.md that correctly references it.
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    payload = b"clean source"
    (src_dir / "a.csv").write_bytes(payload)

    sources_md = tmp_path / "SOURCES.md"
    _write_sources_md(
        sources_md,
        [
            "| src/a.csv | Public Domain | https://example.gov "
            "| 2026-04-16 | " + sha256_bytes(payload) + " | Clean |",
        ],
    )
    entries = validate_all(sources_md, tmp_path)
    assert len(entries) == 1


def test_validate_all_fails_on_hash_mismatch(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "a.csv").write_bytes(b"actual")

    sources_md = tmp_path / "SOURCES.md"
    _write_sources_md(
        sources_md,
        [
            "| src/a.csv | CC0 | https://example.org "
            "| 2026-04-16 | " + ("0" * 64) + " | Wrong hash |",
        ],
    )
    with pytest.raises(LicenseError, match="SHA-256 mismatch"):
        validate_all(sources_md, tmp_path)


def test_validate_all_fails_on_forbidden_license(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "a.csv").write_bytes(b"x")

    sources_md = tmp_path / "SOURCES.md"
    _write_sources_md(
        sources_md,
        [
            "| src/a.csv | CPT | https://example.org "
            "| 2026-04-16 | " + sha256_bytes(b"x") + " | bad |",
        ],
    )
    with pytest.raises(LicenseError, match="forbidden"):
        validate_all(sources_md, tmp_path)


def test_validate_all_empty_file_returns_empty_list(tmp_path: Path) -> None:
    sources_md = tmp_path / "SOURCES.md"
    sources_md.write_text(_HEADER, encoding="utf-8")
    entries = validate_all(sources_md, tmp_path)
    assert entries == []
