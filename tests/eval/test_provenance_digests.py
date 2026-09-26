"""The eval builders' provenance digests hash ``common.io.canonical_bytes``.

Each builder used to carry a private canonical-JSON encoder for its input
digest. The digests below were computed on these fixtures with those private
encoders before they were replaced, so the literals pin byte identity across
the move: a digest changes only when the fixture does.
"""

from __future__ import annotations

from eval.test_baseline import _synthetic_cells
from eval.test_compare import _TH as _COMPARE_THRESHOLDS
from eval.test_compare import _baseline, _before_cells
from eval.test_compare_documents import _TH as _DOCUMENT_THRESHOLDS
from eval.test_compare_documents import _eval
from eval.test_sitegap import _detector, _siteb
from resecta_data.common.io import canonical_bytes, sha256_bytes
from resecta_data.eval.baseline import build_baseline
from resecta_data.eval.compare import build_compare
from resecta_data.eval.compare_documents import build_compare_documents
from resecta_data.eval.sitegap import build_site_gap


def test_baseline_digests_the_canonical_bytes_of_its_cells() -> None:
    cells = _synthetic_cells()
    payload = build_baseline(cells)
    assert payload["source_cells_sha256"] == sha256_bytes(canonical_bytes(cells))
    assert (
        payload["source_cells_sha256"]
        == "cad0eccdfc69c5278db777b23d6451579b072c5763ff93d5f3498b973c8bff6a"
    )


def test_compare_digests_the_canonical_bytes_of_both_baselines() -> None:
    before = _baseline(_before_cells())
    verdict = build_compare(before, before, _COMPARE_THRESHOLDS)
    assert verdict["before_sha256"] == sha256_bytes(canonical_bytes(before))
    assert verdict["after_sha256"] == verdict["before_sha256"]
    assert (
        verdict["before_sha256"]
        == "2b9cb7a7d932fb6b3b6a816bed11fcc3758d428c6cb0e2564e783cbbb9b2b21f"
    )


def test_compare_documents_digests_the_canonical_bytes_of_both_evals() -> None:
    before = _eval()
    verdict = build_compare_documents(before, before, _DOCUMENT_THRESHOLDS)
    assert verdict["before_sha256"] == sha256_bytes(canonical_bytes(before))
    assert verdict["after_sha256"] == verdict["before_sha256"]
    assert (
        verdict["before_sha256"]
        == "7b137254d01026dc6cee9c027b07ee575e6aa09169842cdc90196bde0f25e5b0"
    )


def test_site_gap_digests_the_canonical_bytes_of_both_baselines() -> None:
    detector, siteb = _detector(), _siteb()
    gap = build_site_gap(detector, siteb)
    assert gap["detector_sha256"] == sha256_bytes(canonical_bytes(detector))
    assert gap["siteb_sha256"] == sha256_bytes(canonical_bytes(siteb))
    assert gap["detector_sha256"] == (
        "c17a0f54bbae1b358dcaa7f7ad4623413e6114e1e012086e454f7d4cd270f82e"
    )
    assert gap["siteb_sha256"] == "9a691ad1864c742e11e76e5d159075db42c03a5220845002a8f2f36de79ba432"
