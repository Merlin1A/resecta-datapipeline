"""Guards on NOTICE.txt third-party attribution completeness.

The pipeline emits name Bloom and gazetteer artifacts derived from MIT-licensed
inputs (Faker and bltlab/paranames). The MIT license requires its copyright and
permission notice to propagate into any distribution that ships a derived
artifact, so the repo-root NOTICE.txt must carry those rows. This module pins:

  - the confirmed no-attribution-required provenance rows (OpenStreetMap ODbL,
    OpenAddresses CC0) stay present;
  - the MIT inbound rows are structurally seeded (positive-substring — a bare
    "no unchecked box" check is vacuous if the rows were never seeded);
  - the MIT permission notice is reproduced and no unchecked ``[ ]`` marker
    remains anywhere in the file.

NOTICE.txt rows are legal text: they change under an approved plan. The iOS
half lives in resecta/Tests/ResectaAppTests/NoticeFileTests.swift.
"""

from __future__ import annotations

from pathlib import Path

_NOTICE_PATH = Path(__file__).resolve().parent.parent / "NOTICE.txt"


def _notice_text() -> str:
    """Return the repo-root NOTICE.txt contents."""
    return _NOTICE_PATH.read_text(encoding="utf-8")


def test_notice_exists_and_nonempty() -> None:
    assert _NOTICE_PATH.is_file(), "NOTICE.txt is missing from the repo root"
    assert _notice_text().strip(), "NOTICE.txt is empty"


def test_notice_carries_confirmed_provenance_rows() -> None:
    """The two confirmed no-attribution-required rows stay present."""
    text = _notice_text()
    assert "OpenStreetMap" in text, "NOTICE.txt dropped the OpenStreetMap (ODbL 1.0) row"
    assert "OpenAddresses" in text, "NOTICE.txt dropped the OpenAddresses (CC0 1.0) row"


def test_notice_seeds_mit_inbound_rows() -> None:
    """The MIT inbound inputs have structural rows (positive-substring guard).

    A bare "no unchecked box" assertion is vacuous if the rows were never
    seeded, so the row identifiers are pinned explicitly.
    """
    text = _notice_text()
    assert "Faker" in text, "NOTICE.txt is missing the Faker MIT inbound row"
    assert "Daniele Faraglia" in text, "NOTICE.txt is missing the Faker copyright holder"
    assert "bltlab/paranames" in text, "NOTICE.txt is missing the bltlab/paranames MIT inbound row"


def test_notice_mit_rows_discharged() -> None:
    """The MIT permission notice is present and no ``[ ]`` marker remains.

    Submission gate: the MIT permission notice must be reproduced and every
    checklist box checked before the Faker / paranames derived Bloom
    artifacts ship.
    """
    text = _notice_text()
    assert "[ ]" not in text, (
        "NOTICE.txt still has an unchecked MIT attribution row — author the row "
        "text and check the box under an approved plan before release"
    )
    assert "Permission is hereby granted" in text, (
        "NOTICE.txt MIT rows lack the MIT permission notice"
    )
