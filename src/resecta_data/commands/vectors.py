"""Test-vector builders: build vectors, build fuzz, build adversarial."""

from __future__ import annotations

from pathlib import Path

import click

from resecta_data.adversarial import build as build_adversarial_patterns
from resecta_data.common.determinism import CANONICAL_SEED, assert_hash_seed_pinned
from resecta_data.common.io import atomic_write_bytes, dump_canonical_json
from resecta_data.fuzz import DEFAULT_MUTATION_COUNT, MUTATIONS_DIRNAME, build_pdf_mutations
from resecta_data.fuzz import build as build_fuzz_redos
from resecta_data.vectors import VECTOR_FAMILIES, VectorBuilder

# Views over the one vector-family config (``vectors/__init__.py``): the CLI
# ``kind`` names, their builders and their output filenames come from one tuple.
_VECTOR_BUILDERS: dict[str, VectorBuilder] = {f.kind: f.builder for f in VECTOR_FAMILIES}
_VECTOR_OUTPUT_FILENAMES: dict[str, str] = {f.kind: f.output_filename for f in VECTOR_FAMILIES}
_VECTOR_KINDS: tuple[str, ...] = tuple(f.kind for f in VECTOR_FAMILIES)


@click.command("vectors")
@click.argument(
    "kind",
    type=click.Choice([*_VECTOR_KINDS, "all"]),
)
@click.option(
    "--build-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--seed",
    type=int,
    default=CANONICAL_SEED,
    show_default=True,
    help="PRNG seed. Default is the canonical seed; vary only for ablation.",
)
def build_vectors_cmd(kind: str, build_dir: Path, seed: int) -> None:
    """Build Phase 1 test vectors (checksummed + structural PII detectors)."""
    assert_hash_seed_pinned()
    selected = list(_VECTOR_KINDS) if kind == "all" else [kind]
    for name in selected:
        payload = _VECTOR_BUILDERS[name](seed)
        dest = build_dir / "vectors" / _VECTOR_OUTPUT_FILENAMES[name]
        dump_canonical_json(payload, dest)
        click.echo(f"Wrote {dest} ({len(payload['vectors'])} vectors)")


@click.command("fuzz")
@click.argument("kind", type=click.Choice(["redos", "pdf-mutations"]))
@click.option(
    "--build-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--seed",
    type=int,
    default=CANONICAL_SEED,
    show_default=True,
)
@click.option(
    "--packet",
    "packet_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Source PDF the pdf-mutations kind damages (a sample-doc checkout's "
        "packet.pdf). Required for that kind; ignored otherwise. Nothing binary "
        "is committed here -- the base is read at build time and its sha256 is "
        "recorded in the manifest."
    ),
)
@click.option(
    "--count",
    type=int,
    default=DEFAULT_MUTATION_COUNT,
    show_default=True,
    help="How many pdf-mutations fixtures to emit, split evenly across the four families.",
)
def build_fuzz_cmd(
    kind: str,
    build_dir: Path,
    seed: int,
    packet_path: Path | None,
    count: int,
) -> None:
    """Build fuzz payload catalogs."""
    assert_hash_seed_pinned()
    if kind == "redos":
        payload = build_fuzz_redos(seed)
        dest = build_dir / "fuzz" / "redos_payloads.json"
        dump_canonical_json(payload, dest)
        click.echo(f"Wrote {dest} ({len(payload['payloads'])} payloads)")
        return

    if packet_path is None:
        raise click.UsageError("--packet is required for `build fuzz pdf-mutations`.")
    mutation_set = build_pdf_mutations(seed, source=packet_path.read_bytes(), count=count)
    fuzz_dir = build_dir / "fuzz"
    for rel, data in mutation_set.files:
        atomic_write_bytes(fuzz_dir / rel, data)
    dest = fuzz_dir / "pdf_mutations.json"
    dump_canonical_json(mutation_set.manifest, dest)
    click.echo(
        f"Wrote {dest} ({len(mutation_set.files)} fixtures under "
        f"{fuzz_dir / MUTATIONS_DIRNAME}/; base sha256 "
        f"{mutation_set.manifest['base_sha256'][:12]}...)"
    )


@click.command("adversarial")
@click.argument("kind", type=click.Choice(["patterns"]))
@click.option(
    "--build-dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--seed",
    type=int,
    default=CANONICAL_SEED,
    show_default=True,
)
def build_adversarial_cmd(kind: str, build_dir: Path, seed: int) -> None:
    """Build adversarial pattern fixtures."""
    assert_hash_seed_pinned()
    if kind == "patterns":
        payload = build_adversarial_patterns(seed)
        dest = build_dir / "adversarial" / "adversarial_patterns.json"
        dump_canonical_json(payload, dest)
        click.echo(f"Wrote {dest} ({len(payload['patterns'])} patterns)")


def register(main: click.Group, build: click.Group, calibrate: click.Group) -> None:
    """Attach this module's commands to the CLI groups."""
    build.add_command(build_vectors_cmd)
    build.add_command(build_fuzz_cmd)
    build.add_command(build_adversarial_cmd)
