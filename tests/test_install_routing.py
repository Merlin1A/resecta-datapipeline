"""Assertions about the INSTALL_ROUTES contract.

Phase 1 ships five test fixtures. Phase 2 adds three runtime resources (the
dual Bloom filters plus the gazetteer manifest). The negative-context
candidate list and the demographic coverage report are held in build/;
zip_scf_states.json ships as a runtime resource since 0.7 (Phase 3
AddressDetector landed).
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterator
from pathlib import Path

import click

from resecta_data import commands
from resecta_data.cli import main
from resecta_data.routes import INSTALL_ROUTES, SCHEMA_ROUTES

SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "schemas"


def test_every_install_route_is_schema_routed() -> None:
    """Any installed artifact must also have a schema route (or be a non-JSON binary)."""
    # Non-JSON artifacts validate by other mechanisms:
    #  - .bloom: round-trip + manifest cross-check.
    #  - .sig: Ed25519 signature over the (schema-validated) manifest; the iOS
    #    verifier is the source of truth.
    #  - .pem: SubjectPublicKeyInfo wrapper that the cryptography library
    #    parses on load; structural correctness is enforced at signing time.
    non_schema_suffixes = (".bloom", ".sig", ".pem")
    for rel in INSTALL_ROUTES:
        if rel.endswith(non_schema_suffixes):
            continue
        assert rel in SCHEMA_ROUTES, f"{rel} installed without schema route"


def test_zip_scf_in_install_routes() -> None:
    """zip_scf_states.json ships to iOS as a runtime resource (0.7).

    Phase 3 AddressDetector landed; ZIPStateTableLoader.swift consumes the
    file at runtime. The old assertion-of-absence became a regression once
    the route was added.
    """
    assert "gazetteers/zip_scf_states.json" in SCHEMA_ROUTES
    assert "gazetteers/zip_scf_states.json" in INSTALL_ROUTES, (
        "zip_scf_states.json is absent from INSTALL_ROUTES; "
        "Phase 3 AddressDetector requires this file at runtime."
    )
    target, sub_path = INSTALL_ROUTES["gazetteers/zip_scf_states.json"]
    assert target == "resources"
    assert sub_path == "Gazetteers/zip_scf_states.json"


def test_phase_1_fixtures_are_routed_to_fixtures_target() -> None:
    expected_fixture_paths = {
        "vectors/npi_test_vectors.json",
        "vectors/dea_test_vectors.json",
        "vectors/ssn_structural_vectors.json",
        "fuzz/redos_payloads.json",
        "adversarial/adversarial_patterns.json",
    }
    for rel in expected_fixture_paths:
        target, _ = INSTALL_ROUTES[rel]
        assert target == "fixtures", f"{rel}: expected fixtures, got {target}"


def test_phase_2_bloom_bundle_is_routed_to_resources() -> None:
    """surnames.bloom, given-names.bloom, and the SHIPPED manifest land in Resources/Gazetteers/."""
    expected_resource_paths = {
        "gazetteers/surnames.bloom": "Gazetteers/surnames.bloom",
        "gazetteers/given-names.bloom": "Gazetteers/given-names.bloom",
        "gazetteers/gazetteer_manifest.shipped.json": "Gazetteers/gazetteer-manifest.json",
    }
    for rel, sub in expected_resource_paths.items():
        target, sub_path = INSTALL_ROUTES[rel]
        assert target == "resources", f"{rel}: expected resources, got {target}"
        assert sub_path == sub, f"{rel}: expected sub_path {sub!r}, got {sub_path!r}"
    # The bloom builder's manifest is a locked build product held in build/;
    # only the shipped manifest (with assets[]) reaches the bundle, so the
    # bundle manifest has exactly one source.
    assert "gazetteers/gazetteer_manifest.json" not in INSTALL_ROUTES
    dests = [sub for target, sub in INSTALL_ROUTES.values() if target == "resources"]
    assert dests.count("Gazetteers/gazetteer-manifest.json") == 1


def test_review_gated_artifacts_are_not_installed() -> None:
    """Auto-install of negative_context is blocked by policy; coverage report is dev-only."""
    assert "gazetteers/negative_context_candidates.json" in SCHEMA_ROUTES
    assert "gazetteers/negative_context_candidates.json" not in INSTALL_ROUTES
    assert "demographics/coverage_report.json" in SCHEMA_ROUTES
    assert "demographics/coverage_report.json" not in INSTALL_ROUTES


def test_phase_3_doctype_keywords_ships_to_resources() -> None:
    rel = "classifier/doctype_keywords.json"
    assert rel in SCHEMA_ROUTES
    target, sub = INSTALL_ROUTES[rel]
    assert target == "resources"
    assert sub == "Classifier/doctype-keywords.json"


def test_phase_3_preset_thresholds_candidates_stay_in_build() -> None:
    """Candidates are not shipped pending Phase 3b G9 sweep."""
    rel = "classifier/preset_thresholds_candidates.json"
    assert rel in SCHEMA_ROUTES
    assert rel not in INSTALL_ROUTES


def test_phase_3_g8_corpus_routes_to_fixtures() -> None:
    rel = "corpus/g8_corpus.json"
    assert rel in SCHEMA_ROUTES
    target, sub = INSTALL_ROUTES[rel]
    assert target == "fixtures"
    assert sub == "corpus/g8_corpus.json"


def test_s5_gazetteer_sidecars_route_to_resources() -> None:
    """institutions, address_components, and nicknames gazetteer sidecars are routed.

    institutions.json and address_components.json were manual copies in the
    iOS tree before this routing was added; the routed install owns them now
    (the first install reconciles the drifted iOS copies — a diff to review
    at install time, not an error).
    nicknames.json is the new given-name sidecar; its route is inert until
    the artifact is built post-fetch.
    """
    expected = {
        "gazetteers/institutions.json": "Gazetteers/institutions.json",
        "gazetteers/address_components.json": "Gazetteers/address_components.json",
        "gazetteers/nicknames.json": "Gazetteers/nicknames.json",
    }
    for rel, sub in expected.items():
        assert rel in SCHEMA_ROUTES, f"{rel} missing schema route"
        target, sub_path = INSTALL_ROUTES[rel]
        assert target == "resources", f"{rel}: expected resources, got {target}"
        assert sub_path == sub, f"{rel}: expected sub_path {sub!r}, got {sub_path!r}"


# -----------------------------------------------------------------------------
# Drift: the command modules and the routing tables stay whole
# -----------------------------------------------------------------------------


def _command_module_names() -> list[str]:
    """Every module under ``commands/``, sorted (``pkgutil`` order is not a contract)."""
    return sorted(module.name for module in pkgutil.iter_modules(commands.__path__))


def _leaf_commands(
    group: click.Group, path: tuple[str, ...] = ()
) -> Iterator[tuple[str, click.Command]]:
    ctx = click.Context(group, info_name=group.name)
    for name in group.list_commands(ctx):
        command = group.get_command(ctx, name)
        assert command is not None, name
        if isinstance(command, click.Group):
            yield from _leaf_commands(command, (*path, name))
        else:
            yield " ".join((*path, name)), command


def test_every_command_module_registers_at_least_one_command() -> None:
    """A module under commands/ that registers nothing is dead weight or a typo."""
    names = _command_module_names()
    assert names, "no modules under commands/ — wrong package?"
    for name in names:
        module = importlib.import_module(f"resecta_data.commands.{name}")
        register = getattr(module, "register", None)
        assert callable(register), f"commands/{name}.py has no register(main, build, calibrate)"
        root, build, calibrate = click.Group("main"), click.Group("build"), click.Group("calibrate")
        register(root, build, calibrate)
        registered = len(root.commands) + len(build.commands) + len(calibrate.commands)
        assert registered >= 1, f"commands/{name}.py registers no command"


def test_every_command_in_the_tree_comes_from_a_command_module() -> None:
    """cli.py holds the groups only; every leaf command is defined under commands/."""
    homes = {f"resecta_data.commands.{name}" for name in _command_module_names()}
    leaves = list(_leaf_commands(main))
    assert len(leaves) == 30
    for path, command in leaves:
        assert command.callback is not None, path
        assert command.callback.__module__ in homes, (
            f"{path!r} is defined in {command.callback.__module__}, not under commands/"
        )


def test_every_schema_route_names_a_schema_file() -> None:
    """A schema route must point at a file under schemas/, or validate-schemas cannot run."""
    for schema_name in sorted(set(SCHEMA_ROUTES.values())):
        assert (SCHEMAS_DIR / f"{schema_name}.schema.json").is_file(), schema_name
