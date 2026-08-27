"""Smoke tests for the CLI.

At Phase 0 the CLI commands are required to handle the empty-build case
gracefully — they should print a message and exit 0, not crash. These tests
lock that contract in.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from resecta_data.cli import INSTALL_ROUTES, SHRINK_GUARDED_ROUTES, main
from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import write_hash_lockfile


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# -----------------------------------------------------------------------------
# Top-level help
# -----------------------------------------------------------------------------


def test_cli_help(runner: CliRunner) -> None:
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "Resecta DataPipeline" in result.output


def test_cli_no_args_shows_help(runner: CliRunner) -> None:
    result = runner.invoke(main, [])
    # Click returns 0 or 2 depending on version when no command is given.
    # Either way, the help text should appear.
    assert "validate-schemas" in result.output or result.exit_code != 0


# -----------------------------------------------------------------------------
# validate-schemas
# -----------------------------------------------------------------------------


def test_validate_schemas_empty_build(
    runner: CliRunner, tmp_build_dir: Path, tmp_schemas_dir: Path
) -> None:
    result = runner.invoke(
        main,
        [
            "validate-schemas",
            "--build-dir",
            str(tmp_build_dir),
            "--schemas-dir",
            str(tmp_schemas_dir),
        ],
    )
    assert result.exit_code == 0
    assert "No artifacts" in result.output


def test_validate_schemas_skips_unrouted_artifacts(
    runner: CliRunner, tmp_build_dir: Path, tmp_schemas_dir: Path
) -> None:
    (tmp_build_dir / "unrouted.json").write_text("{}", encoding="utf-8")
    result = runner.invoke(
        main,
        [
            "validate-schemas",
            "--build-dir",
            str(tmp_build_dir),
            "--schemas-dir",
            str(tmp_schemas_dir),
        ],
    )
    assert result.exit_code == 0
    assert "Skipped" in result.output
    assert "unrouted.json" in result.output


# -----------------------------------------------------------------------------
# verify-determinism
# -----------------------------------------------------------------------------


def test_verify_determinism_rejects_same_rebuild_out_dir(runner: CliRunner, tmp_path: Path) -> None:
    """The side-by-side mode requires distinct paths.

    Passing --rebuild-out-dir equal to --build-dir collapses the two
    snapshots into one location, which would compare the rebuild against
    itself. The CLI rejects this with exit code 2 before invoking any
    subprocess, so the check is fast.
    """
    build_dir = tmp_path / "build"
    build_dir.mkdir()

    result = runner.invoke(
        main,
        [
            "verify-determinism",
            "--build-dir",
            str(build_dir),
            "--rebuild-out-dir",
            str(build_dir),
        ],
    )
    assert result.exit_code == 2
    assert "must differ" in result.stderr


# -----------------------------------------------------------------------------
# verify-hashes
# -----------------------------------------------------------------------------


def test_verify_hashes_empty_build_empty_lockfile(runner: CliRunner, tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    lockfile = tmp_path / "asset_hashes.lock"
    lockfile.write_text("", encoding="utf-8")

    result = runner.invoke(
        main,
        [
            "verify-hashes",
            "--build-dir",
            str(build_dir),
            "--lockfile",
            str(lockfile),
        ],
    )
    assert result.exit_code == 0
    assert "Phase 0" in result.output or "No artifacts" in result.output


def test_verify_hashes_fails_on_missing_artifact(runner: CliRunner, tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    lockfile = tmp_path / "lock"
    # Lockfile references a file that doesn't exist in build_dir.
    write_hash_lockfile(lockfile, {"expected.json": "a" * 64})

    result = runner.invoke(
        main,
        [
            "verify-hashes",
            "--build-dir",
            str(build_dir),
            "--lockfile",
            str(lockfile),
        ],
    )
    assert result.exit_code == 1
    assert "missing from build" in result.stderr


def test_verify_hashes_fails_on_untracked_artifact(runner: CliRunner, tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "surprise.json").write_text("{}", encoding="utf-8")
    lockfile = tmp_path / "lock"
    write_hash_lockfile(lockfile, {})

    result = runner.invoke(
        main,
        [
            "verify-hashes",
            "--build-dir",
            str(build_dir),
            "--lockfile",
            str(lockfile),
        ],
    )
    assert result.exit_code == 1
    assert "missing from lockfile" in result.stderr


def test_verify_hashes_fails_on_hash_mismatch(runner: CliRunner, tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "file.json").write_text("{}", encoding="utf-8")

    lockfile = tmp_path / "lock"
    # Wrong hash in the lockfile.
    write_hash_lockfile(lockfile, {"file.json": "0" * 64})

    result = runner.invoke(
        main,
        [
            "verify-hashes",
            "--build-dir",
            str(build_dir),
            "--lockfile",
            str(lockfile),
        ],
    )
    assert result.exit_code == 1
    assert "mismatch" in result.stderr.lower()


def test_verify_hashes_built_only_skips_missing_artifact(runner: CliRunner, tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "built.json").write_text("{}", encoding="utf-8")
    digest = hashlib.sha256(b"{}").hexdigest()

    lockfile = tmp_path / "lock"
    # One entry is built and matches; one entry has no built file.
    write_hash_lockfile(lockfile, {"built.json": digest, "absent.json": "a" * 64})

    base_args = ["verify-hashes", "--build-dir", str(build_dir), "--lockfile", str(lockfile)]

    # Without the flag the absent entry fails the check.
    strict = runner.invoke(main, base_args)
    assert strict.exit_code == 1
    assert "missing from build" in strict.stderr

    # With --built-only the absent entry is reported as skipped, not failed.
    relaxed = runner.invoke(main, [*base_args, "--built-only"])
    assert relaxed.exit_code == 0
    assert "Skipped 1 lockfile entries" in relaxed.output
    assert "absent.json" in relaxed.output
    assert "Hash check PASSED (1 artifacts)" in relaxed.output


def test_verify_hashes_built_only_still_fails_on_mismatch(
    runner: CliRunner, tmp_path: Path
) -> None:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "built.json").write_text("{}", encoding="utf-8")

    lockfile = tmp_path / "lock"
    # The built file's hash is wrong; a second entry is absent from the build.
    write_hash_lockfile(lockfile, {"built.json": "0" * 64, "absent.json": "a" * 64})

    result = runner.invoke(
        main,
        [
            "verify-hashes",
            "--build-dir",
            str(build_dir),
            "--lockfile",
            str(lockfile),
            "--built-only",
        ],
    )
    assert result.exit_code == 1
    assert "mismatch" in result.stderr.lower()


def test_verify_hashes_built_only_still_fails_on_untracked_artifact(
    runner: CliRunner, tmp_path: Path
) -> None:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "surprise.json").write_text("{}", encoding="utf-8")

    lockfile = tmp_path / "lock"
    write_hash_lockfile(lockfile, {"absent.json": "a" * 64})

    result = runner.invoke(
        main,
        [
            "verify-hashes",
            "--build-dir",
            str(build_dir),
            "--lockfile",
            str(lockfile),
            "--built-only",
        ],
    )
    assert result.exit_code == 1
    assert "missing from lockfile" in result.stderr


# -----------------------------------------------------------------------------
# regenerate-lockfile
# -----------------------------------------------------------------------------


def test_regenerate_lockfile_with_yes_flag(runner: CliRunner, tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "file.json").write_text("{}", encoding="utf-8")

    lockfile = tmp_path / "asset_hashes.lock"
    result = runner.invoke(
        main,
        [
            "regenerate-lockfile",
            "--build-dir",
            str(build_dir),
            "--lockfile",
            str(lockfile),
            "--yes",
        ],
    )
    assert result.exit_code == 0
    assert lockfile.is_file()
    assert "file.json" in lockfile.read_text(encoding="utf-8")


def test_regenerate_lockfile_without_yes_aborts(runner: CliRunner, tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    lockfile = tmp_path / "asset_hashes.lock"

    # Feed "n" to the confirmation prompt.
    result = runner.invoke(
        main,
        [
            "regenerate-lockfile",
            "--build-dir",
            str(build_dir),
            "--lockfile",
            str(lockfile),
        ],
        input="n\n",
    )
    assert result.exit_code != 0
    assert not lockfile.exists()


# -----------------------------------------------------------------------------
# install-assets
# -----------------------------------------------------------------------------


def test_install_assets_empty_build_is_noop(runner: CliRunner, tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    resources_dir = tmp_path / "Resources"
    fixtures_dir = tmp_path / "Fixtures"

    result = runner.invoke(
        main,
        [
            "install-assets",
            "--build-dir",
            str(build_dir),
            "--resources-dir",
            str(resources_dir),
            "--fixtures-dir",
            str(fixtures_dir),
        ],
    )
    assert result.exit_code == 0
    assert "Nothing to install" in result.output


def test_install_assets_skips_unrouted_artifacts(runner: CliRunner, tmp_path: Path) -> None:
    """Artifacts without an INSTALL_ROUTES entry stay in build/ and are not copied."""
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "unrouted.json").write_text("{}", encoding="utf-8")

    result = runner.invoke(
        main,
        [
            "install-assets",
            "--build-dir",
            str(build_dir),
            "--resources-dir",
            str(tmp_path / "Resources"),
            "--fixtures-dir",
            str(tmp_path / "Fixtures"),
        ],
    )
    assert result.exit_code == 0
    assert "Installed: 0" in result.output
    assert "Held in build/ (no install route)" in result.output
    assert "unrouted.json" in result.output


# The 15 vector-fixture families are schema-clean and determinism-clean.
# All must round-trip from build/vectors/ to fixtures/vectors/ via
# INSTALL_ROUTES. Names mirror the build-side filenames (some use the
# `_test_` infix, some don't -- the inconsistency is not a blocker).
# routing_number_vectors.json was added (count: 14->15).
D19_VECTOR_FAMILIES = (
    "npi_test_vectors.json",
    "dea_test_vectors.json",
    "ssn_structural_vectors.json",
    "credit_card_vectors.json",
    "ein_vectors.json",
    "itin_vectors.json",
    "dob_vectors.json",
    "phone_test_vectors.json",
    "email_test_vectors.json",
    "passport_test_vectors.json",
    "drivers_license_test_vectors.json",
    "mrn_test_vectors.json",
    "bates_test_vectors.json",
    "license_plate_test_vectors.json",
    "routing_number_vectors.json",
)


def test_install_assets_routes_all_14_d19_vector_fixtures(
    runner: CliRunner, tmp_path: Path
) -> None:
    """All 15 vector-fixture families must round-trip via INSTALL_ROUTES."""
    build_dir = tmp_path / "build"
    vectors_src = build_dir / "vectors"
    vectors_src.mkdir(parents=True)
    resources_dir = tmp_path / "Resources"
    fixtures_dir = tmp_path / "Fixtures"

    for idx, family in enumerate(D19_VECTOR_FAMILIES):
        rel = f"vectors/{family}"
        assert rel in INSTALL_ROUTES, f"INSTALL_ROUTES missing entry for {rel}"
        target_name, sub_path = INSTALL_ROUTES[rel]
        assert target_name == "fixtures", f"{rel} should route to fixtures, got {target_name}"
        assert sub_path == rel, f"{rel} should map to identity sub_path, got {sub_path}"
        (vectors_src / family).write_text(f'{{"family": "{family}", "id": {idx}}}\n')

    result = runner.invoke(
        main,
        [
            "install-assets",
            "--build-dir",
            str(build_dir),
            "--resources-dir",
            str(resources_dir),
            "--fixtures-dir",
            str(fixtures_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    assert f"Installed: {len(D19_VECTOR_FAMILIES)}" in result.output

    for family in D19_VECTOR_FAMILIES:
        src = vectors_src / family
        dest = fixtures_dir / "vectors" / family
        assert dest.exists(), f"{family} did not land at {dest}"
        assert dest.read_bytes() == src.read_bytes(), f"{family} bytes diverged"


def test_install_routes_has_exactly_14_vector_entries() -> None:
    """Defence against silent route loss: any change to the vector-route count
    is intentional and must update the D19_VECTOR_FAMILIES tuple in lockstep.
    Count is 15 (routing_number_vectors.json added)."""
    vector_routes = [k for k in INSTALL_ROUTES if k.startswith("vectors/")]
    assert len(vector_routes) == len(D19_VECTOR_FAMILIES), (
        f"INSTALL_ROUTES has {len(vector_routes)} vector entries; "
        f"D19_VECTOR_FAMILIES tuple has {len(D19_VECTOR_FAMILIES)}. "
        f"If the route count changed intentionally, update both. "
        f"Current routes: {sorted(vector_routes)}"
    )


# -----------------------------------------------------------------------------
# install-assets — D11-config-golive-F1 shrink-guard (institutions.json)
# -----------------------------------------------------------------------------

_INSTITUTIONS_REL = "gazetteers/institutions.json"
_INSTITUTIONS_DEST_SUB = "Gazetteers/institutions.json"


def _write_entries(path: Path, n: int) -> None:
    """Write an `entries`-list gazetteer of length n (parents created)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"entries": list(range(n))}), encoding="utf-8")


def _run_install(
    runner: CliRunner,
    build_dir: Path,
    resources_dir: Path,
    fixtures_dir: Path,
    *,
    allow_shrink: bool = False,
) -> Result:
    args = [
        "install-assets",
        "--build-dir",
        str(build_dir),
        "--resources-dir",
        str(resources_dir),
        "--fixtures-dir",
        str(fixtures_dir),
    ]
    if allow_shrink:
        args.append("--allow-shrink")
    return runner.invoke(main, args)


def test_install_assets_refuses_institutions_shrink(runner: CliRunner, tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    _write_entries(build_dir / _INSTITUTIONS_REL, 2)  # smaller build/ draft
    dest = tmp_path / "Resources" / _INSTITUTIONS_DEST_SUB
    _write_entries(dest, 5)  # larger committed superset

    result = _run_install(runner, build_dir, tmp_path / "Resources", tmp_path / "Fixtures")

    assert result.exit_code != 0
    assert isinstance(result.exception, PipelineError)
    msg = str(result.exception)
    assert _INSTITUTIONS_REL in msg
    assert "5 -> 2" in msg
    # The guard fires before the copy, so dest is byte-unchanged.
    assert json.loads(dest.read_text(encoding="utf-8"))["entries"] == list(range(5))


def test_install_assets_allow_shrink_override(runner: CliRunner, tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    _write_entries(build_dir / _INSTITUTIONS_REL, 2)
    dest = tmp_path / "Resources" / _INSTITUTIONS_DEST_SUB
    _write_entries(dest, 5)

    result = _run_install(
        runner, build_dir, tmp_path / "Resources", tmp_path / "Fixtures", allow_shrink=True
    )

    assert result.exit_code == 0, result.output
    assert json.loads(dest.read_text(encoding="utf-8"))["entries"] == list(range(2))


def test_install_assets_allows_growth(runner: CliRunner, tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    _write_entries(build_dir / _INSTITUTIONS_REL, 5)  # build/ is the superset
    dest = tmp_path / "Resources" / _INSTITUTIONS_DEST_SUB
    _write_entries(dest, 2)

    result = _run_install(runner, build_dir, tmp_path / "Resources", tmp_path / "Fixtures")

    assert result.exit_code == 0, result.output
    assert json.loads(dest.read_text(encoding="utf-8"))["entries"] == list(range(5))


def test_install_assets_guard_ignores_non_entries_json(runner: CliRunner, tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    src = build_dir / _INSTITUTIONS_REL
    src.parent.mkdir(parents=True, exist_ok=True)
    # A guarded route whose build/ file is not an `entries` list: the guard
    # cannot compare counts, so it falls through and copies as usual.
    src.write_text(json.dumps({"version": 1}), encoding="utf-8")
    dest = tmp_path / "Resources" / _INSTITUTIONS_DEST_SUB
    _write_entries(dest, 5)

    result = _run_install(runner, build_dir, tmp_path / "Resources", tmp_path / "Fixtures")

    assert result.exit_code == 0, result.output
    assert json.loads(dest.read_text(encoding="utf-8")) == {"version": 1}


def test_shrink_guarded_routes_subset_of_install_routes() -> None:
    assert set(INSTALL_ROUTES) >= SHRINK_GUARDED_ROUTES
    assert _INSTITUTIONS_REL in SHRINK_GUARDED_ROUTES
