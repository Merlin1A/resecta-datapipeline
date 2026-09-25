"""Ed25519 signing for the gazetteer manifest.

This module signs ``build/gazetteers/gazetteer_manifest.json`` with an
Ed25519 private key held under ``~/.resecta-data/`` (gitignored — it never
enters either repo's tree). The key source is either the age-encrypted
``manifest-private-key.pem.age`` (preferred; decrypted in memory through an
age identity for the duration of the signing run) or the plaintext
``manifest-private-key.pem`` (transitional). The detached signature is
written next to the manifest as ``gazetteer_manifest.sig``; the public key
is exported to ``manifest_public_key.pem`` so the iOS engine can bundle it
under ``Resources/Gazetteers/`` and verify at detector init.

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

Rotation cadence: the key is rotated on the maintainer's documented
schedule and on any suspicion of compromise — see KEY-MANAGEMENT.md. This
module does not implement automatic rotation; it provides the primitives
that a release-prep step will call.

Cross-boundary wire-format changes need a paired Swift PR and must
preserve the canonical-form JSON invariant.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
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

# Key locations. Outside both repos so the private key never enters git
# history. The release process supplies its own path via ``--private-key``
# when rotating.
#
# ``DEFAULT_PRIVATE_KEY_PATH`` is the plaintext PEM (transitional form);
# ``DEFAULT_ENCRYPTED_KEY_PATH`` is the same PEM encrypted with ``age`` to a
# recipient whose identity lives at ``DEFAULT_IDENTITY_PATH``. When the
# encrypted file exists it is preferred (see ``resolve_default_key_path``).
KEY_DIR: Final[Path] = Path.home() / ".resecta-data"
DEFAULT_PRIVATE_KEY_PATH: Final[Path] = KEY_DIR / "manifest-private-key.pem"
ENCRYPTED_KEY_SUFFIX: Final[str] = ".age"
DEFAULT_ENCRYPTED_KEY_PATH: Final[Path] = KEY_DIR / (
    DEFAULT_PRIVATE_KEY_PATH.name + ENCRYPTED_KEY_SUFFIX
)
DEFAULT_IDENTITY_PATH: Final[Path] = KEY_DIR / "age-identity.txt"

# The ``age`` binary is a host tool (never a Python dependency); the
# ``age_bin`` parameters below exist so tests can substitute a stand-in.
AGE_BINARY_NAME: Final[str] = "age"


def is_encrypted_key_path(path: Path) -> bool:
    """Return True iff ``path`` names an age-encrypted key (``*.age``)."""
    return path.suffix == ENCRYPTED_KEY_SUFFIX


def resolve_default_key_path() -> Path:
    """Pick the default signing-key path: the encrypted file when present.

    Transition-safe: a host that has not yet moved its key into the
    encrypted form keeps signing from the plaintext PEM; once the ``.age``
    file exists it wins, so retiring the plaintext copy is a separate,
    deliberate operator step.
    """
    if DEFAULT_ENCRYPTED_KEY_PATH.exists():
        return DEFAULT_ENCRYPTED_KEY_PATH
    return DEFAULT_PRIVATE_KEY_PATH


def _resolve_age_binary(age_bin: Path | str | None) -> str:
    """Return the absolute ``age`` executable, or raise a ``PipelineError``."""
    if age_bin is not None:
        return str(age_bin)
    found = shutil.which(AGE_BINARY_NAME)
    if found is None:
        raise PipelineError(
            f"`{AGE_BINARY_NAME}` is not on PATH; it is required to read or write an "
            "encrypted signing key. Install it (https://age-encryption.org) or pass "
            "--private-key with a plaintext PEM."
        )
    return found


def _run_age(
    argv: list[str],
    *,
    stdin: bytes | None,
    what: str,
) -> bytes:
    """Run ``age`` with a fixed argv and return its stdout bytes.

    stderr is inherited so an age plugin's interactive lines (a hardware
    prompt, a passphrase request) reach the operator's terminal. A non-zero
    exit raises a ``PipelineError`` that names the mechanism only — never
    the bytes on either side of the pipe.
    """
    try:
        result = subprocess.run(  # noqa: S603 -- fixed argv, no shell
            argv,
            input=stdin,
            stdout=subprocess.PIPE,
            stderr=None,
            check=False,
        )
    except OSError as exc:
        raise PipelineError(f"Failed to run `{argv[0]}` to {what}: {exc}") from exc
    if result.returncode != 0:
        raise PipelineError(
            f"`{argv[0]}` exited {result.returncode} while trying to {what}; "
            "see its messages above."
        )
    return result.stdout


def generate_private_key(
    path: Path = DEFAULT_PRIVATE_KEY_PATH,
    *,
    encrypt_to: str | None = None,
    age_bin: Path | str | None = None,
) -> Ed25519PrivateKey:
    """Generate a new Ed25519 private key and write it to ``path``.

    With ``encrypt_to`` (an age recipient string) the key is generated in
    memory and only its age-encrypted form is written; no plaintext key
    bytes touch the disk. ``path`` must then end in ``.age``. Without it the
    key is written as an unencrypted PKCS#8 PEM (the transitional form).

    The parent directory is created with mode 0o700 if it does not exist.
    The key file is written with mode 0o600.

    Raises:
        PipelineError: If the key file already exists (rotation is an
            explicit operator step; this function refuses to clobber), if
            the path form does not match the requested encryption, or if
            ``age`` is unavailable or fails.
    """
    if path.exists():
        raise PipelineError(
            f"Refusing to overwrite existing private key at {path}. "
            "Delete it manually if you intend to rotate."
        )
    encrypted = is_encrypted_key_path(path)
    if encrypted and encrypt_to is None:
        raise PipelineError(
            f"{path} names an encrypted key but no recipient was given; "
            "pass --encrypt-to RECIPIENT."
        )
    if encrypt_to is not None and not encrypted:
        raise PipelineError(
            f"--encrypt-to was given but {path} does not end in {ENCRYPTED_KEY_SUFFIX}."
        )
    private_key = Ed25519PrivateKey.generate()
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if encrypt_to is not None:
        age = _resolve_age_binary(age_bin)
        ciphertext = _run_age(
            [age, "-r", encrypt_to],
            stdin=pem,
            what=f"encrypt the new signing key to {encrypt_to}",
        )
        if not ciphertext:
            raise PipelineError("`age` produced no output while encrypting the new signing key.")
        atomic_write_bytes(path, ciphertext)
    else:
        atomic_write_bytes(path, pem)
    path.chmod(0o600)
    return private_key


def load_private_key(
    path: Path | None = None,
    *,
    identity: Path = DEFAULT_IDENTITY_PATH,
    age_bin: Path | str | None = None,
) -> Ed25519PrivateKey:
    """Load the Ed25519 private key from ``path`` (default: the resolved key).

    A ``.age`` path is decrypted through ``age -d -i IDENTITY`` with the
    plaintext held only in this process's memory; any other path is read
    as an unencrypted PEM.

    Raises:
        PipelineError: If the file or the identity is missing, ``age``
            fails, or the bytes are not a valid unencrypted PEM-encoded
            Ed25519 private key. Messages never include key bytes.
    """
    if path is None:
        path = resolve_default_key_path()
    if not path.is_file():
        raise PipelineError(
            f"Private key not found at {path}. "
            "Run `resecta-data sign-manifest --generate-key` to create one."
        )
    if is_encrypted_key_path(path):
        if not identity.is_file():
            raise PipelineError(
                f"age identity not found at {identity}; it is required to decrypt {path} "
                "(pass --age-identity)."
            )
        age = _resolve_age_binary(age_bin)
        pem = _run_age(
            [age, "-d", "-i", str(identity), str(path)],
            stdin=None,
            what=f"decrypt the signing key at {path}",
        )
    else:
        try:
            pem = path.read_bytes()
        except OSError as exc:
            raise PipelineError(f"Failed to read private key at {path}: {exc}") from exc
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except (ValueError, TypeError) as exc:
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
