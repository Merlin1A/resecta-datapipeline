"""Install-side commands: install-assets, manifest-assets, sign-manifest, stage-reviewed-negctx."""

from __future__ import annotations

import shutil
from pathlib import Path

import click

from resecta_data.bloom import build_shipped_manifest, collect_asset_entries
from resecta_data.bloom.spec import MANIFEST_FILE, SHIPPED_MANIFEST_FILE
from resecta_data.common.exceptions import PipelineError
from resecta_data.common.io import (
    dump_canonical_json,
    iter_build_artifacts,
    load_json,
    read_hash_lockfile,
    sha256_file,
)
from resecta_data.gazetteers.negative_context.stage_reviewed import (
    stage_reviewed as stage_reviewed_negative_context,
)
from resecta_data.manifest_signing import (
    DEFAULT_ENCRYPTED_KEY_PATH,
    DEFAULT_IDENTITY_PATH,
    export_public_key,
    generate_private_key,
    is_encrypted_key_path,
    load_private_key,
    resolve_default_key_path,
    sign_manifest_file,
)
from resecta_data.routes import INSTALL_ROUTES, SHRINK_GUARDED_ROUTES


def _entries_count(path: Path) -> int | None:
    """Return ``len(payload["entries"])`` for an ``entries``-list gazetteer, else None.

    Returns None on any read / parse problem or a non-``entries`` shape, so the
    shrink guard fails open (skips) rather than blocking an unrelated artifact.
    """
    try:
        payload = load_json(path)
    except PipelineError:
        return None
    entries = payload.get("entries") if isinstance(payload, dict) else None
    return len(entries) if isinstance(entries, list) else None


# -----------------------------------------------------------------------------
# Stage reviewed negative-context (D6)
# -----------------------------------------------------------------------------


@click.command("stage-reviewed-negctx")
@click.option(
    "--build-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--reviewed-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help=(
        "Directory holding the committed reviewed files. Defaults to "
        "src/resecta_data/gazetteers/negative_context/reviewed/."
    ),
)
def stage_reviewed_negctx_cmd(build_dir: Path, reviewed_dir: Path | None) -> None:
    """Stage the committed reviewed negative_context.json into build/.

    Verifies the meta sidecar's ``reviewed_version`` against the live
    candidates hash before copying — a mismatch means the candidates
    changed without the sidecar being re-stamped under an approved change
    plan, and staging refuses.
    """
    if reviewed_dir is None:
        staged = stage_reviewed_negative_context(build_dir)
    else:
        staged = stage_reviewed_negative_context(build_dir, reviewed_dir)
    for dest in staged:
        click.echo(f"Staged {dest}")
    click.echo("Reviewed negative_context.json staged into build/ (survives make clean).")


# -----------------------------------------------------------------------------
# Install assets
# -----------------------------------------------------------------------------


@click.command("install-assets")
@click.option(
    "--build-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--resources-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    help="Target: Packages/RedactionEngine/Sources/RedactionEngine/Resources/",
)
@click.option(
    "--fixtures-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    help="Target: Packages/RedactionEngine/Tests/RedactionEngineTests/Fixtures/",
)
@click.option(
    "--allow-shrink",
    is_flag=True,
    default=False,
    help=(
        "Permit a shrink-guarded gazetteer (e.g. institutions.json) to be "
        "overwritten by a smaller build/ artifact. Required only after the "
        "institutions fetches make build/ a verified superset. Off by default "
        "so a bare refresh cannot regress a committed corpus."
    ),
)
def install_assets_cmd(
    build_dir: Path,
    resources_dir: Path,
    fixtures_dir: Path,
    allow_shrink: bool,
) -> None:
    """Copy artifacts from build/ into the Swift tree.

    Routing is defined in ``INSTALL_ROUTES``. Each route names a target
    ("resources" or "fixtures") and a path within that target directory.
    Artifacts without an install route remain in build/ and are not copied.
    """
    artifacts = iter_build_artifacts(build_dir)
    if not artifacts:
        click.echo("Nothing to install.")
        return

    targets = {"resources": resources_dir, "fixtures": fixtures_dir}
    copied = 0
    skipped_no_route: list[str] = []

    for path in artifacts:
        rel = path.relative_to(build_dir).as_posix()
        route = INSTALL_ROUTES.get(rel)
        if route is None:
            skipped_no_route.append(rel)
            continue
        target_name, sub_path = route
        if target_name not in targets:
            raise PipelineError(
                f"{rel}: unknown install target {target_name!r}; expected one of {sorted(targets)}"
            )
        dest = targets[target_name] / sub_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        # D11-config-golive-F1 shrink-guard — fail loud before overwriting a
        # larger committed file. Acts only when the route is shrink-guarded, the
        # dest already exists, both parse as `entries`-list gazetteers, and the
        # source has strictly fewer entries. Growth / equal / non-gazetteer /
        # unparseable paths fall through to the copy unchanged.
        if rel in SHRINK_GUARDED_ROUTES and dest.exists() and not allow_shrink:
            src_n = _entries_count(path)
            dst_n = _entries_count(dest)
            if src_n is not None and dst_n is not None and src_n < dst_n:
                raise PipelineError(
                    f"{rel}: refusing to shrink shipped gazetteer "
                    f"{dst_n} -> {src_n} entries. The committed file is a "
                    f"superset; complete the institutions fetches + "
                    f"`gmake gazetteers` so build/ is >= shipped, or pass "
                    f"--allow-shrink to override. See cutover-diff "
                    f"build/gazetteers/institutions.cutover-diff.json "
                    f"(legacy_only entries are the names that would be dropped)."
                )
        shutil.copy2(path, dest)
        if sha256_file(dest) != sha256_file(path):
            raise PipelineError(f"Install verify failed: {dest} differs from {path}")
        copied += 1

    click.echo(f"Installed: {copied}")
    if skipped_no_route:
        click.echo(f"Held in build/ (no install route): {len(skipped_no_route)}")
        for r in skipped_no_route:
            click.echo(f"  - {r}")


# -----------------------------------------------------------------------------
# Shipped manifest: the bloom manifest + every installed asset's digest
# -----------------------------------------------------------------------------


@click.command("manifest-assets")
@click.option(
    "--build-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--resources-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help=(
        "The iOS Resources/ tree install-assets targets. A routed asset this "
        "host did not build is digested from its installed copy here (install "
        "leaves it alone, so that is what ships). Absent or missing: only built "
        "artifacts are listed."
    ),
)
@click.option(
    "--lockfile",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("asset_hashes.lock"),
    show_default=True,
    help=(
        "Cross-check source: each entry is reported as matching, differing from, "
        "or absent from the lock."
    ),
)
def manifest_assets_cmd(build_dir: Path, resources_dir: Path | None, lockfile: Path) -> None:
    """Derive the shipped manifest from the bloom builder's manifest.

    Reads ``build/gazetteers/gazetteer_manifest.json`` (a locked ``make build``
    product) and writes ``gazetteer_manifest.shipped.json`` beside it: the
    same ``filters[]``, ``version`` raised to the shipped version, and an
    ``assets[]`` section with ``{path, sha256, bytes}`` for every asset
    ``install-assets`` routes into the engine bundle except the manifest
    triple. The digests are of the bytes install ships — the built artifact
    when present, else the installed file — so the engine's first-load
    verification never refuses the bundle the pipeline itself installed.
    ``sign-manifest`` signs this file; it is out-of-band relative to the lock.
    """
    base_path = build_dir / "gazetteers" / MANIFEST_FILE
    if not base_path.is_file():
        raise click.ClickException(f"Manifest not found at {base_path}. Run `make bloom` first.")
    base = load_json(base_path)
    if not isinstance(base, dict):
        raise click.ClickException(f"{base_path}: manifest root must be an object")
    lock = read_hash_lockfile(lockfile) if lockfile.is_file() else {}
    resources = resources_dir if resources_dir is not None and resources_dir.is_dir() else None
    entries, skipped = collect_asset_entries(
        build_dir=build_dir, resources_dir=resources, routes=INSTALL_ROUTES, lock=lock
    )
    shipped = build_shipped_manifest(base, entries)
    dest = build_dir / "gazetteers" / SHIPPED_MANIFEST_FILE
    dump_canonical_json(shipped, dest)

    for entry in entries:
        lock_state = entry.lock if entry.lock is not None else "no lock row"
        click.echo(
            f"  {entry.path}  {entry.sha256[:12]}…  {entry.size} B  "
            f"[{entry.source}; lock: {lock_state}]"
        )
    click.echo(
        f"Shipped manifest: {dest.relative_to(build_dir)} "
        f"({len(entries)} assets; version {shipped['version']})"
    )
    for rel in skipped:
        click.echo(f"  - not listed (neither built nor installed): {rel}")


# -----------------------------------------------------------------------------
# Sign manifest (verified by the iOS engine)
# -----------------------------------------------------------------------------


@click.command("sign-manifest")
@click.option(
    "--build-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Directory containing build/gazetteers/gazetteer_manifest.json.",
)
@click.option(
    "--private-key",
    type=click.Path(file_okay=True, dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Path to the Ed25519 private key: an age-encrypted file (*.age, decrypted "
        "in memory through --age-identity) or a plaintext PEM. Defaults to "
        "~/.resecta-data/manifest-private-key.pem.age when it exists, else "
        "~/.resecta-data/manifest-private-key.pem (gitignored — outside both "
        "repos so the key never enters git history)."
    ),
)
@click.option(
    "--age-identity",
    type=click.Path(file_okay=True, dir_okay=False, path_type=Path),
    default=DEFAULT_IDENTITY_PATH,
    help=(
        "age identity file used to decrypt an encrypted --private-key. "
        "Defaults to ~/.resecta-data/age-identity.txt."
    ),
)
@click.option(
    "--generate-key",
    is_flag=True,
    default=False,
    help=(
        "Generate a new Ed25519 private key at --private-key if it does "
        "not already exist. With --encrypt-to the key is born straight into "
        "its age-encrypted form and no plaintext is written. The key is "
        "rotated on the maintainer's documented schedule and on any "
        "suspicion of compromise — see KEY-MANAGEMENT.md."
    ),
)
@click.option(
    "--encrypt-to",
    "encrypt_to",
    metavar="RECIPIENT",
    default=None,
    help=(
        "age recipient for --generate-key. Required when the key path ends "
        "in .age; when --private-key is omitted the encrypted default path "
        "is used."
    ),
)
def sign_manifest_cmd(
    build_dir: Path,
    private_key: Path | None,
    age_identity: Path,
    generate_key: bool,
    encrypt_to: str | None,
) -> None:
    """Sign the shipped manifest (``gazetteer_manifest.shipped.json``) with Ed25519.

    Writes ``gazetteer_manifest.sig`` and ``manifest_public_key.pem`` next
    to the manifest. Both files are picked up by ``install-assets`` via the
    routing entries above and copied into the iOS Resources/Gazetteers/
    tree. The iOS engine verifies the signature at detector init
    (see GazetteerLoader.swift).

    Cross-boundary wire-format changes need a paired Swift PR (Ed25519;
    the signing key is rotated on the maintainer's documented schedule and
    on any suspicion of compromise — see KEY-MANAGEMENT.md).
    """
    if private_key is not None:
        key_path = private_key
    elif generate_key and encrypt_to is not None:
        key_path = DEFAULT_ENCRYPTED_KEY_PATH
    else:
        key_path = resolve_default_key_path()

    if generate_key:
        if key_path.exists():
            if encrypt_to is not None:
                raise click.UsageError(
                    f"Key already exists at {key_path}; --encrypt-to applies to a new key only. "
                    "To rotate, retire the existing key first (KEY-MANAGEMENT.md)."
                )
            click.echo(f"Key already exists at {key_path}; refusing to overwrite.")
        else:
            if is_encrypted_key_path(key_path) and encrypt_to is None:
                raise click.UsageError(
                    f"{key_path} is an encrypted key path; --generate-key needs --encrypt-to."
                )
            generate_private_key(key_path, encrypt_to=encrypt_to)
            form = "age-encrypted" if encrypt_to is not None else "plaintext"
            click.echo(f"Generated new Ed25519 private key at {key_path} ({form})")
    elif encrypt_to is not None:
        raise click.UsageError("--encrypt-to only applies with --generate-key.")

    pk = load_private_key(key_path, identity=age_identity)

    manifest_path = build_dir / "gazetteers" / SHIPPED_MANIFEST_FILE
    if not manifest_path.is_file():
        raise click.ClickException(
            f"Shipped manifest not found at {manifest_path}. Run `make manifest-assets` first."
        )

    # The signature file keeps its name (`gazetteer_manifest.sig`): the iOS
    # verifier reads the manifest as `gazetteer-manifest.json` and the
    # signature as `gazetteer_manifest.sig` regardless of the build-side stem.
    signature_path = sign_manifest_file(
        manifest_path, pk, signature_path=build_dir / "gazetteers" / "gazetteer_manifest.sig"
    )
    public_key_path = build_dir / "gazetteers" / "manifest_public_key.pem"
    export_public_key(pk, public_key_path)

    click.echo(f"Signed: {signature_path.relative_to(build_dir)}")
    click.echo(f"Public key: {public_key_path.relative_to(build_dir)}")


def register(main: click.Group, build: click.Group, calibrate: click.Group) -> None:
    """Attach this module's commands to the CLI groups."""
    main.add_command(stage_reviewed_negctx_cmd)
    main.add_command(install_assets_cmd)
    main.add_command(manifest_assets_cmd)
    main.add_command(sign_manifest_cmd)
