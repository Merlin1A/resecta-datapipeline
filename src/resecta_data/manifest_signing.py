"""Ed25519 signing for the gazetteer manifest.

This module signs ``build/gazetteers/gazetteer_manifest.json`` with an
Ed25519 private key held at ``~/.resecta-data/manifest-private-key.pem``
(gitignored — it never enters either repo's tree). The detached signature
is written next to the manifest as ``gazetteer_manifest.sig``; the public
key is exported to ``manifest_public_key.pem`` so the iOS engine can bundle
it under ``Resources/Gazetteers/`` and verify at detector init.

Wire contract (paired with iOS CryptoKit ``Curve25519.Signing``):

- Signing input is the canonical-form JSON bytes produced by
  ``common.io.dump_canonical_json`` — sorted keys, ``indent=2``,
  ``ensure_ascii=False``, trailing LF. The Swift side
  recomputes those bytes from the same on-disk JSON and verifies against
  the bundled public key.
- Signature is detached, 64 bytes raw, PEM-wrapped with the standard
  ``BEGIN SIGNATURE`` ``END SIGNATURE`` markers. The raw form keeps the
  byte-for-byte invariant simple for the Swift loader.
- Determinism: Ed25519 signing is deterministic for a fixed
  ``(private_key, message)`` pair so the artifact bytes reproduce from
  the same private key + same canonical manifest. This matches the
  pipeline's reproducibility invariants.

Rotation cadence: per major release. This module does not
implement automatic rotation; it provides the
primitives that a release-prep step will call.

Cross-boundary wire-format changes need a paired Swift PR and must
preserve the canonical-form JSON invariant.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .common.exceptions import PipelineError
from .common.io import atomic_write_bytes

# PEM markers for the detached signature. The body is base64 of the 64-byte
# Ed25519 signature; the marker pair lets a human glance distinguish the
# signature file from the public key in the same directory.
SIGNATURE_PEM_HEADER: Final[bytes] = b"-----BEGIN ED25519 SIGNATURE-----\n"
SIGNATURE_PEM_FOOTER: Final[bytes] = b"-----END ED25519 SIGNATURE-----\n"

# Default key path. Outside both repos so the private key never enters
# git history. The release process supplies its own path via
# ``--private-key`` when rotating.
DEFAULT_PRIVATE_KEY_PATH: Final[Path] = Path.home() / ".resecta-data" / "manifest-private-key.pem"


def generate_private_key(path: Path = DEFAULT_PRIVATE_KEY_PATH) -> Ed25519PrivateKey:
    """Generate a new Ed25519 private key and write it to ``path``.

    The key is PEM-encoded, unencrypted (the host filesystem is the trust
    boundary; encrypting with a passphrase only adds friction for the
    release operator).

    The parent directory is created with mode 0o700 if it does not exist.
    The key file is written with mode 0o600.

    Raises:
        PipelineError: If the key file already exists. Rotation is an
            explicit operator step; this function refuses to clobber.
    """
    if path.exists():
        raise PipelineError(
            f"Refusing to overwrite existing private key at {path}. "
            "Delete it manually if you intend to rotate."
        )
    private_key = Ed25519PrivateKey.generate()
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write_bytes(path, pem)
    path.chmod(0o600)
    return private_key


def load_private_key(path: Path = DEFAULT_PRIVATE_KEY_PATH) -> Ed25519PrivateKey:
    """Load the Ed25519 private key from ``path``.

    Raises:
        PipelineError: If the file is missing, unreadable, or not a valid
            unencrypted PEM-encoded Ed25519 private key.
    """
    if not path.is_file():
        raise PipelineError(
            f"Private key not found at {path}. "
            "Run `resecta-data sign-manifest --generate-key` to create one."
        )
    try:
        pem = path.read_bytes()
        key = serialization.load_pem_private_key(pem, password=None)
    except (ValueError, OSError) as exc:
        raise PipelineError(f"Failed to load private key at {path}: {exc}") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise PipelineError(
            f"Key at {path} is not an Ed25519 private key (got {type(key).__name__})"
        )
    return key


def export_public_key(private_key: Ed25519PrivateKey, path: Path) -> None:
    """Write the corresponding public key as a PEM file at ``path``.

    The PEM form is the same shape that ``cryptography.hazmat.primitives``
    produces by default. The iOS side strips the PEM envelope and feeds
    the raw 32-byte public key to ``Curve25519.Signing.PublicKey(rawRepresentation:)``.
    """
    public_key = private_key.public_key()
    pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    atomic_write_bytes(path, pem)


def sign_manifest_bytes(
    manifest_bytes: bytes,
    private_key: Ed25519PrivateKey,
) -> bytes:
    """Sign ``manifest_bytes`` and return the detached signature in PEM form.

    The PEM body is base64 of the raw 64-byte Ed25519 signature. Wrapping
    is fixed at 64 characters per line to match the standard PEM layout
    other tooling produces.
    """
    raw_signature = private_key.sign(manifest_bytes)
    encoded = base64.b64encode(raw_signature)
    # Wrap to 64-char lines for PEM-style layout.
    wrapped = b"\n".join(encoded[i : i + 64] for i in range(0, len(encoded), 64))
    return SIGNATURE_PEM_HEADER + wrapped + b"\n" + SIGNATURE_PEM_FOOTER


def parse_signature_pem(signature_pem: bytes) -> bytes:
    """Extract the raw 64-byte Ed25519 signature from PEM form.

    Raises:
        PipelineError: If the input is not a valid signature PEM.
    """
    if not signature_pem.startswith(SIGNATURE_PEM_HEADER):
        raise PipelineError("Signature PEM missing BEGIN marker")
    if SIGNATURE_PEM_FOOTER not in signature_pem:
        raise PipelineError("Signature PEM missing END marker")
    body_start = len(SIGNATURE_PEM_HEADER)
    body_end = signature_pem.index(SIGNATURE_PEM_FOOTER)
    body = signature_pem[body_start:body_end].replace(b"\n", b"").replace(b"\r", b"")
    try:
        raw = base64.b64decode(body, validate=True)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise PipelineError(f"Signature PEM body is not valid base64: {exc}") from exc
    # Ed25519 signatures are exactly 64 bytes.
    expected_length = 64
    if len(raw) != expected_length:
        raise PipelineError(
            f"Signature is {len(raw)} bytes; expected {expected_length} for Ed25519"
        )
    return raw


def verify_signature(
    manifest_bytes: bytes,
    signature_pem: bytes,
    public_key: Ed25519PublicKey,
) -> bool:
    """Return True iff ``signature_pem`` is a valid Ed25519 signature over
    ``manifest_bytes`` for ``public_key``.

    Returns False on any signature failure (invalid signature, parse error,
    length mismatch). Never raises.
    """
    try:
        raw_signature = parse_signature_pem(signature_pem)
        public_key.verify(raw_signature, manifest_bytes)
    except (InvalidSignature, PipelineError):
        return False
    return True


def sign_manifest_file(
    manifest_path: Path,
    private_key: Ed25519PrivateKey,
    signature_path: Path | None = None,
) -> Path:
    """Sign the manifest at ``manifest_path`` and write the detached signature.

    The manifest bytes are read as-is (the file is expected to already be
    canonical-form JSON from ``common.io.dump_canonical_json``; this module
    does not re-serialize it).

    Args:
        manifest_path: Path to the canonical-form JSON manifest.
        private_key: The Ed25519 private key to sign with.
        signature_path: Optional explicit signature output path. Defaults
            to ``<manifest_stem>.sig`` next to the manifest.

    Returns:
        The path that the signature was written to.

    Raises:
        PipelineError: If the manifest cannot be read or the signature
            cannot be written.
    """
    if not manifest_path.is_file():
        raise PipelineError(f"Manifest not found: {manifest_path}")
    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError as exc:
        raise PipelineError(f"Failed to read manifest {manifest_path}: {exc}") from exc

    if signature_path is None:
        signature_path = manifest_path.with_suffix(".sig")

    signature_pem = sign_manifest_bytes(manifest_bytes, private_key)
    atomic_write_bytes(signature_path, signature_pem)
    return signature_path
