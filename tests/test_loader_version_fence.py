"""W-O — paired DataPipeline-side test for the Swift loader version-fence.

The engine asserts version-fence rejection in the per-loader Swift test
(e.g. ``Tests/RedactionEngineTests/AddressTests/ZIPStateTableLoaderTests.swift``
``versionFenceRejectsOutOfRange``). This Python side ships the
adversarial fixture file (``version: 99``) under
``tests/fixtures/loader_version_fence/<asset>.json`` so the parametrized
contract is exercised symmetrically across all 6 Int-versioned loaders
and the NameGazetteer special case.

Pilot scope (W-O-pilot) shipped:

- The parametrized harness scaffolding for all 7 loaders.
- The committed fixture for the pilot row (``zip_scf_states``).
- A skipped placeholder for each follower row + NameGazetteer special
  case.

W-O-followers extends the parametrization with the 6 follower fixtures
(``address_components``, ``dl_patterns``, ``institutions``,
``negative_context``, ``passport_patterns``, and the ``NameGazetteer``
manifest fixture under Q2 path B).

Each parametrized case asserts the **fixture-emission contract**: the
file exists, parses as valid JSON, and carries an out-of-range
``version``. Engine-side rejection is asserted by ``xcodebuild test``
on the iOS side. If the iOS test layout drifts, update the fixture
path to match.

Run via ``make verify`` (DataPipeline) — wires into pyproject.toml's
pytest scope automatically.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "loader_version_fence"

# Asset basenames for the 6 Int-versioned loaders. All ship a fixture
# carrying ``version: 99`` so the Swift fence (``1...1``) rejects them.
LOADERS: list[str] = [
    "zip_scf_states",
    "address_components",
    "dl_patterns",
    "institutions",
    "negative_context",
    "passport_patterns",
]


@pytest.mark.parametrize("asset", LOADERS)
def test_version_fence_fixture_emits_out_of_range(asset: str) -> None:
    """The committed fixture carries ``version: 99`` so the Swift fence rejects it.

    The Swift loader's range is ``1...1`` (W-O pilot policy); 99 is
    deliberately far outside so any future range expansion still
    triggers the fence.
    """
    fixture_path = FIXTURE_DIR / f"{asset}.json"
    assert fixture_path.exists(), (
        f"Fixture missing: {fixture_path}. The Swift "
        f"versionFenceRejectsOutOfRange test depends on this file shape."
    )
    payload = json.loads(fixture_path.read_text())
    assert payload["version"] == 99, (
        f"{asset} fixture must carry version 99 to exercise the fence; "
        f"found {payload.get('version')!r}"
    )


def test_name_gazetteer_version_fence_fixture() -> None:
    """NameGazetteer special case — ``GazetteerManifest.version`` is ``String``.

    Per STRAT §5.5 v5 hardening: the fence pattern is
    ``Set<String>`` rather than ``ClosedRange<Int>`` because semver
    strings don't admit a single-range comparison. The fixture is
    ``gazetteer-manifest.json`` carrying ``version: "99.0.0"`` so the
    Swift ``NameGazetteer.LoaderError.unsupportedManifestVersion`` path
    is exercised by the engine-side test.
    """
    fixture_path = FIXTURE_DIR / "gazetteer-manifest.json"
    assert fixture_path.exists(), f"NameGazetteer fence fixture missing: {fixture_path}."
    payload = json.loads(fixture_path.read_text())
    # Q2 path B: version is a semver String, not an Int.
    assert payload["version"] == "99.0.0", (
        f"gazetteer-manifest.json fence fixture must carry version "
        f'"99.0.0" (semver String) to exercise the Set<String> fence; '
        f"found {payload.get('version')!r}"
    )
