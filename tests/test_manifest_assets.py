"""The shipped manifest's ``assets[]`` — every installed engine asset's digest.

``resecta-data manifest-assets`` derives ``gazetteers/gazetteer_manifest.shipped.json``
from the bloom builder's manifest: the same ``filters[]``, the shipped version,
and one ``{path, sha256, bytes}`` entry per asset ``install-assets`` routes into
the engine bundle — except the manifest itself, its signature and the public
key. The digests are of the bytes install ships: the built artifact when this
host built it, else the file already installed under the iOS Resources tree.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from resecta_data.bloom.manifest import (
    MANIFEST_TRIPLE_BUNDLE_PATHS,
    build_shipped_manifest,
    collect_asset_entries,
)
from resecta_data.bloom.spec import MANIFEST_VERSION, SHIPPED_MANIFEST_VERSION
from resecta_data.cli import INSTALL_ROUTES
from resecta_data.common.determinism import is_out_of_band
from resecta_data.common.io import dump_canonical_json, read_hash_lockfile
from resecta_data.common.schema import load_schema, validate

_SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"
_REPO = Path(__file__).parent.parent
_IOS_RESOURCES = (
    _REPO.parent
    / "resecta"
    / "Packages"
    / "RedactionEngine"
    / "Sources"
    / "RedactionEngine"
    / "Resources"
)

_RESOURCE_ROUTES = {
    rel: sub
    for rel, (target, sub) in INSTALL_ROUTES.items()
    if target == "resources" and sub not in MANIFEST_TRIPLE_BUNDLE_PATHS
}

_BASE_MANIFEST = {
    "version": MANIFEST_VERSION,
    "hashAlgorithm": "MurmurHash3_x64_128",
    "seed": 20260416,
    "filters": [
        {
            "name": "surnames",
            "type": "surname",
            "n": 11802639,
            "m": 169693480,
            "k": 10,
            "fprTarget": 0.001,
            "sources": ["census_surnames"],
            "builtAt": "2026-04-16T00:00:00Z",
        }
    ],
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _populate(root: Path, routes: dict[str, str], *, built: bool, salt: bytes) -> None:
    """Write one distinct payload per route, at its build path or its bundle path."""
    for rel, sub in routes.items():
        _write(root / (rel if built else sub), salt + rel.encode("utf-8"))


def test_every_routed_resource_asset_is_listed_except_the_triple(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    _populate(build_dir, _RESOURCE_ROUTES, built=True, salt=b"built:")
    # The triple is present in build/ too and must never be listed.
    for rel in (
        "gazetteers/gazetteer_manifest.shipped.json",
        "gazetteers/gazetteer_manifest.sig",
        "gazetteers/manifest_public_key.pem",
    ):
        _write(build_dir / rel, b"triple")

    entries, skipped = collect_asset_entries(
        build_dir=build_dir, resources_dir=None, routes=INSTALL_ROUTES, lock={}
    )

    assert skipped == []
    assert [e.path for e in entries] == sorted(_RESOURCE_ROUTES.values())
    assert not any(e.path in MANIFEST_TRIPLE_BUNDLE_PATHS for e in entries)
    # Fixture routes never reach the bundle manifest.
    assert not any(e.path.startswith("vectors/") or e.path.startswith("corpus/") for e in entries)
    for entry in entries:
        rel = next(r for r, s in _RESOURCE_ROUTES.items() if s == entry.path)
        payload = b"built:" + rel.encode("utf-8")
        assert entry.sha256 == _sha256(payload)
        assert entry.size == len(payload)
        assert entry.source == "build"
        assert entry.lock is None


def test_built_bytes_win_and_installed_bytes_fill_the_gap(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    resources_dir = tmp_path / "Resources"
    routes = {rel: ("resources", sub) for rel, sub in list(_RESOURCE_ROUTES.items())[:3]}
    (rel_built, sub_built), (rel_both, sub_both), (_, sub_installed) = [
        (rel, sub) for rel, (_, sub) in routes.items()
    ]
    _write(build_dir / rel_built, b"only built")
    _write(build_dir / rel_both, b"built copy")
    _write(resources_dir / sub_both, b"stale installed copy")
    _write(resources_dir / sub_installed, b"only installed")

    entries, skipped = collect_asset_entries(
        build_dir=build_dir, resources_dir=resources_dir, routes=routes, lock={}
    )

    by_path = {e.path: e for e in entries}
    assert skipped == []
    assert by_path[sub_built].source == "build"
    assert by_path[sub_both].source == "build"
    assert by_path[sub_both].sha256 == _sha256(b"built copy"), (
        "install copies build/ over the installed file"
    )
    assert by_path[sub_installed].source == "installed"
    assert by_path[sub_installed].sha256 == _sha256(b"only installed")


def test_an_asset_neither_built_nor_installed_is_skipped_and_reported(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    routes = {"gazetteers/nicknames.json": ("resources", "Gazetteers/nicknames.json")}

    entries, skipped = collect_asset_entries(
        build_dir=build_dir, resources_dir=tmp_path / "absent", routes=routes, lock={}
    )

    assert entries == []
    assert skipped == ["gazetteers/nicknames.json"]


def test_lock_cross_check_classifies_each_entry(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    routes = {rel: ("resources", sub) for rel, sub in list(_RESOURCE_ROUTES.items())[:3]}
    rel_match, rel_differs, rel_unlocked = routes
    _write(build_dir / rel_match, b"m")
    _write(build_dir / rel_differs, b"d")
    _write(build_dir / rel_unlocked, b"u")
    lock = {rel_match: _sha256(b"m"), rel_differs: "0" * 64}

    entries, _ = collect_asset_entries(
        build_dir=build_dir, resources_dir=None, routes=routes, lock=lock
    )

    by_rel = {next(r for r, (_, s) in routes.items() if s == e.path): e for e in entries}
    assert by_rel[rel_match].lock == "match"
    assert by_rel[rel_differs].lock == "differs"
    assert by_rel[rel_unlocked].lock is None


def test_shipped_manifest_keeps_filters_bumps_version_and_validates(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    _populate(build_dir, _RESOURCE_ROUTES, built=True, salt=b"x:")
    entries, _ = collect_asset_entries(
        build_dir=build_dir, resources_dir=None, routes=INSTALL_ROUTES, lock={}
    )

    shipped = build_shipped_manifest(_BASE_MANIFEST, entries)

    assert shipped["version"] == SHIPPED_MANIFEST_VERSION == "1.1.0"
    assert shipped["filters"] == _BASE_MANIFEST["filters"]
    assert shipped["hashAlgorithm"] == _BASE_MANIFEST["hashAlgorithm"]
    assert shipped["seed"] == _BASE_MANIFEST["seed"]
    assert [a["path"] for a in shipped["assets"]] == sorted(_RESOURCE_ROUTES.values())
    assert all(set(a) == {"path", "sha256", "bytes"} for a in shipped["assets"])
    schema = load_schema(_SCHEMAS_DIR, "gazetteer_manifest")
    validate(shipped, schema)
    # The bloom builder's manifest (no assets) still validates: the section is optional.
    validate(_BASE_MANIFEST, schema)


def test_shipped_manifest_is_canonical_and_deterministic(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    _populate(build_dir, _RESOURCE_ROUTES, built=True, salt=b"x:")
    outs = []
    for i in range(2):
        entries, _ = collect_asset_entries(
            build_dir=build_dir, resources_dir=None, routes=INSTALL_ROUTES, lock={}
        )
        dest = tmp_path / f"shipped-{i}.json"
        dump_canonical_json(build_shipped_manifest(_BASE_MANIFEST, entries), dest)
        outs.append(dest.read_bytes())
    assert outs[0] == outs[1]
    assets = json.loads(outs[0])["assets"]
    assert assets == sorted(assets, key=lambda a: a["path"])


def test_shipped_manifest_is_out_of_band_and_the_bloom_manifest_stays_locked() -> None:
    assert is_out_of_band("gazetteers/gazetteer_manifest.shipped.json")
    assert not is_out_of_band("gazetteers/gazetteer_manifest.json")
    lock = read_hash_lockfile(_REPO / "asset_hashes.lock")
    assert "gazetteers/gazetteer_manifest.json" in lock
    assert "gazetteers/gazetteer_manifest.shipped.json" not in lock


# The provenance truth of the shipped tree, recorded by name: ten of the
# fifteen installed assets are the locked build's bytes; five are out-of-band
# — the reviewed negative-context file, the two calibrated Classifier files,
# and the two installed supersets the locked build does not produce.
_OUT_OF_BAND_SHIPPED = {
    "Gazetteers/negative-context.json",  # reviewed staging; no lock row
    "Classifier/doctype-temperature.json",  # calibrate product; no lock row
    "Classifier/preset-thresholds.json",  # calibrate product; no lock row
    "Gazetteers/institutions.json",  # installed superset; differs from the lock row
    "Gazetteers/address_components.json",  # installed superset; differs from the lock row
}


@pytest.mark.skipif(not _IOS_RESOURCES.is_dir(), reason="sibling iOS checkout not present")
def test_shipped_tree_digests_against_the_lock() -> None:
    """On a host with the sibling iOS checkout: the installed bytes vs the lock."""
    lock = read_hash_lockfile(_REPO / "asset_hashes.lock")
    entries, skipped = collect_asset_entries(
        build_dir=_REPO / "build" / "does-not-exist",
        resources_dir=_IOS_RESOURCES,
        routes=INSTALL_ROUTES,
        lock=lock,
    )
    assert len(entries) == 15, [e.path for e in entries]
    assert skipped == ["gazetteers/nicknames.json"], skipped
    assert all(e.source == "installed" for e in entries)
    locked = {e.path for e in entries if e.lock == "match"}
    unlocked = {e.path for e in entries if e.lock != "match"}
    assert len(locked) == 10, sorted(locked)
    assert unlocked == _OUT_OF_BAND_SHIPPED, sorted(unlocked)
