"""Determinism + schema + shape checks for the bundle-size probe.

The probe is engineer-facing only; there is no Resecta UI for it (the
Swift cold-start hook is the deferred Mac-side half). The probe is
dev/CI only -- not installed to the Swift Resources path.

The body is emitted at ``build/instrumentation/bundle_size.json``; the
build-input ``git_head`` lives in the sibling ``bundle_size.meta.json``
sidecar and is excluded from the hash lock to break the
regen-lock feedback loop.
"""

from __future__ import annotations

from pathlib import Path

from resecta_data import cli
from resecta_data.common import determinism, io
from resecta_data.common.io import dump_canonical_json, sha256_file
from resecta_data.common.schema import validate_file
from resecta_data.instrumentation import bundle_size
from resecta_data.instrumentation.bundle_size import DEFAULT_SUB_DIRS, build, build_meta

_SCHEMAS = Path(__file__).parent.parent / "schemas"
_PINNED_GIT_HEAD = "deadbee"


def _populate(build_dir: Path) -> None:
    """Drop a small set of files into ``build_dir`` mirroring real layout."""
    (build_dir / "vectors").mkdir()
    (build_dir / "vectors" / "a.json").write_bytes(b'{"k": 1}\n')
    (build_dir / "vectors" / "b.json").write_bytes(b'{"k": 22}\n')
    (build_dir / "context").mkdir()
    (build_dir / "context" / "ctx.json").write_bytes(b'{"x": "y"}\n')
    (build_dir / "rules").mkdir()
    (build_dir / "rules" / "rules.json").write_bytes(b'{"r": []}\n')
    (build_dir / "gazetteers").mkdir()
    (build_dir / "gazetteers" / "g.bin").write_bytes(b"\x00" * 1024)
    # Out-of-scope subdir + an excluded sidecar inside an in-scope subdir;
    # neither should appear in the payload.
    (build_dir / "demographics").mkdir()
    (build_dir / "demographics" / "out.json").write_bytes(b"{}\n")
    (build_dir / "_timings.jsonl").write_bytes(b'{"label":"x","wall_seconds":0.1}\n')
    (build_dir / "vectors" / "_timings.jsonl").write_bytes(b'{"label":"y"}\n')


def test_walks_default_subdirs_and_omits_others(tmp_build_dir: Path) -> None:
    _populate(tmp_build_dir)
    payload = build(tmp_build_dir)
    subs = payload["subdirectories"]
    # Default scope omits surfaces meant for manual review (calibration/)
    # and out-of-scope dirs like demographics/.
    assert set(subs.keys()) == set(DEFAULT_SUB_DIRS)
    assert "demographics" not in subs
    assert "calibration" not in subs
    # vectors/_timings.jsonl is excluded; only a.json + b.json count.
    vec_files = [f["path"] for f in subs["vectors"]["files"]]
    assert vec_files == ["vectors/a.json", "vectors/b.json"]


def test_per_file_fields_and_totals(tmp_build_dir: Path) -> None:
    _populate(tmp_build_dir)
    payload = build(tmp_build_dir)
    gaz = payload["subdirectories"]["gazetteers"]
    assert gaz["file_count"] == 1
    assert gaz["total_bytes"] == 1024
    entry = gaz["files"][0]
    assert entry["path"] == "gazetteers/g.bin"
    assert entry["size_bytes"] == 1024
    full_sha = sha256_file(tmp_build_dir / "gazetteers" / "g.bin")
    assert entry["sha256_short"] == full_sha[:12]
    # total_build_size sums across in-scope subdirs only.
    expected_total = sum(s["total_bytes"] for s in payload["subdirectories"].values())
    assert payload["total_build_size"] == expected_total


def test_files_within_subdir_are_sorted(tmp_build_dir: Path) -> None:
    """Sorted iteration is the determinism contract; assert it holds."""
    (tmp_build_dir / "vectors").mkdir()
    for name in ("z.json", "a.json", "m.json"):
        (tmp_build_dir / "vectors" / name).write_bytes(b"{}\n")
    payload = build(tmp_build_dir)
    paths = [f["path"] for f in payload["subdirectories"]["vectors"]["files"]]
    assert paths == ["vectors/a.json", "vectors/m.json", "vectors/z.json"]


def test_byte_identical_across_invocations(tmp_build_dir: Path) -> None:
    _populate(tmp_build_dir)
    a = build(tmp_build_dir)
    b = build(tmp_build_dir)
    dest_a = tmp_build_dir / "a.json"
    dest_b = tmp_build_dir / "b.json"
    dump_canonical_json(a, dest_a)
    dump_canonical_json(b, dest_b)
    assert dest_a.read_bytes() == dest_b.read_bytes()


def test_body_byte_stable_across_git_head_drift(tmp_build_dir: Path) -> None:
    """Body is independent of git_head — the regen-lock loop is broken.

    Two body-builds against the same tree produce byte-identical output even
    though a sibling meta-build would carry a different git_head per cycle.
    """
    _populate(tmp_build_dir)
    body_a = build(tmp_build_dir)
    body_b = build(tmp_build_dir)
    dest_a = tmp_build_dir / "body_a.json"
    dest_b = tmp_build_dir / "body_b.json"
    dump_canonical_json(body_a, dest_a)
    dump_canonical_json(body_b, dest_b)
    assert dest_a.read_bytes() == dest_b.read_bytes()
    assert "git_head" not in body_a["_meta"]


def test_schema_validates(tmp_build_dir: Path) -> None:
    _populate(tmp_build_dir)
    payload = build(tmp_build_dir)
    dest = tmp_build_dir / "instrumentation" / "bundle_size.json"
    dump_canonical_json(payload, dest)
    validate_file(dest, _SCHEMAS, "bundle_size")


def test_meta_sidecar_shape(tmp_build_dir: Path) -> None:
    """The sidecar carries the build-input git_head and the canonical fields."""
    meta = build_meta(git_head=_PINNED_GIT_HEAD)
    assert meta == {
        "generated_by": "resecta_data.instrumentation.bundle_size",
        "git_head": _PINNED_GIT_HEAD,
        "schema_version": "v1",
    }


def test_meta_sidecar_unknown_git_head(tmp_build_dir: Path) -> None:
    """Off-tree fallback: meta still pins the literal 'unknown' string."""
    meta = build_meta(git_head="unknown")
    assert meta["git_head"] == "unknown"


def test_body_meta_fields(tmp_build_dir: Path) -> None:
    """Body's _meta drops git_head — body is byte-stable across regen cycles."""
    _populate(tmp_build_dir)
    payload = build(tmp_build_dir)
    meta = payload["_meta"]
    assert meta == {
        "generated_by": "resecta_data.instrumentation.bundle_size",
        "schema_version": "v1",
    }
    assert "git_head" not in meta


def test_n7_out_of_band_files_do_not_change_payload(tmp_path: Path) -> None:
    """N7 regression: sign products + warm cache never reach locked bytes.

    ``make sign-manifest`` drops ``gazetteer_manifest.sig`` /
    ``manifest_public_key.pem`` into ``gazetteers/`` (a walked subdir), and a
    warm tree carries ``gazetteers/_ingest_cache/``. The determinism rebuild
    regenerates neither, so a probe that counts them makes a signed canonical
    tree unmatchable by the rebuild (speed review finding N7). The payload
    must be identical with and without them.
    """
    signed = tmp_path / "signed"
    unsigned = tmp_path / "unsigned"
    for build_dir in (signed, unsigned):
        build_dir.mkdir()
        _populate(build_dir)
    (signed / "gazetteers" / "gazetteer_manifest.sig").write_bytes(b"\x01" * 156)
    (signed / "gazetteers" / "manifest_public_key.pem").write_bytes(b"\x02" * 113)
    cache = signed / "gazetteers" / "_ingest_cache"
    cache.mkdir()
    (cache / "surnames.json").write_bytes(b'{"rows": ["smith"]}\n')
    assert build(signed) == build(unsigned)


def test_walk_parity_with_verify_surface(tmp_build_dir: Path) -> None:
    """The probe's exclusions can never drift from the verify walkers' again.

    Pins (a) identity: bundle_size imports the SAME exclusion objects
    ``common/io.py`` and ``common/determinism.py`` use — a private stale copy
    is the drift that let N7 slip past review; and (b) behavior: the probe
    lists exactly the ``iter_build_artifacts`` view restricted to the walked
    subdirs minus out-of-band paths.
    """
    # vars() because the probe imports these without re-exporting (mypy
    # no-implicit-reexport); identity of the bound objects is the contract.
    probe_ns = vars(bundle_size)
    assert probe_ns["EXCLUDED_ARTIFACT_NAMES"] is io.EXCLUDED_ARTIFACT_NAMES
    assert probe_ns["EXCLUDED_ARTIFACT_DIRS"] is io.EXCLUDED_ARTIFACT_DIRS
    assert probe_ns["is_out_of_band"] is determinism.is_out_of_band
    assert cli._is_out_of_band is determinism.is_out_of_band
    assert cli._OUT_OF_BAND_PREFIXES is determinism.OUT_OF_BAND_PREFIXES

    _populate(tmp_build_dir)
    gaz = tmp_build_dir / "gazetteers"
    # One representative of every excluded class inside a walked subdir.
    (gaz / ".stamp").write_bytes(b"")
    (gaz / "bundle_size.meta.json").write_bytes(b"{}\n")
    (gaz / "_ingest_cache").mkdir()
    (gaz / "_ingest_cache" / "given-names.json").write_bytes(b"{}\n")
    (gaz / "diagnostics").mkdir()
    (gaz / "diagnostics" / "probe.json").write_bytes(b"{}\n")
    (gaz / "gazetteer_manifest.sig").write_bytes(b"\x01")
    (gaz / "manifest_public_key.pem").write_bytes(b"\x02")
    (gaz / "negative_context.json").write_bytes(b"{}\n")
    (gaz / "negative_context.meta.json").write_bytes(b"{}\n")
    (gaz / "sources").mkdir()
    (gaz / "sources" / "raw.csv").write_bytes(b"a,b\n")

    payload = build(tmp_build_dir)
    probe_paths = {f["path"] for sub in payload["subdirectories"].values() for f in sub["files"]}
    verify_paths = {
        p.relative_to(tmp_build_dir).as_posix() for p in io.iter_build_artifacts(tmp_build_dir)
    }
    expected = {
        p
        for p in verify_paths
        if p.split("/", 1)[0] in DEFAULT_SUB_DIRS and not determinism.is_out_of_band(p)
    }
    assert probe_paths == expected
    assert not any("gazetteer_manifest.sig" in p for p in probe_paths)
    assert not any("_ingest_cache" in p for p in probe_paths)
