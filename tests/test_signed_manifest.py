"""Signed gazetteer manifest tests.

Covers:
  - Round-trip: sign + verify with the same key pair succeeds.
  - Tampered manifest: flipping one byte breaks verification.
  - Canonical JSON is byte-for-byte reproducible (the same payload signed
    twice through ``common.io.dump_canonical_json`` produces identical
    bytes, which is the determinism property the Ed25519 wire format
    relies on).

Wire-format changes need a paired Swift PR and must preserve the
canonical-form JSON invariant.
"""

from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from resecta_data.common.io import dump_canonical_json
from resecta_data.manifest_signing import (
    SIGNATURE_PEM_FOOTER,
    SIGNATURE_PEM_HEADER,
    export_public_key,
    sign_manifest_bytes,
    sign_manifest_file,
    verify_signature,
)


def _sample_manifest_payload() -> dict[str, object]:
    """A representative gazetteer-manifest shape matching the iOS
    ``GazetteerManifest`` decoder (sorted-key, semver string version).
    """
    return {
        "version": "1.0.0",
        "hashAlgorithm": "MurmurHash3_x64_128",
        "seed": 20260416,
        "filters": [
            {
                "name": "surnames",
                "type": "surname",
                "n": 14991156,
                "m": 215536664,
                "k": 10,
                "fprTarget": 0.001,
                "sources": ["census_surnames"],
                "builtAt": "2026-04-16T00:00:00Z",
            },
        ],
    }


def test_round_trip_signature_verifies(tmp_path: Path) -> None:
    """Sign a manifest with a fresh key pair; verify succeeds with the
    corresponding public key.
    """
    manifest_path = tmp_path / "gazetteer_manifest.json"
    dump_canonical_json(_sample_manifest_payload(), manifest_path)

    private_key = Ed25519PrivateKey.generate()
    signature_path = sign_manifest_file(manifest_path, private_key)

    assert signature_path.is_file()
    signature_pem = signature_path.read_bytes()
    assert signature_pem.startswith(SIGNATURE_PEM_HEADER)
    assert signature_pem.endswith(SIGNATURE_PEM_FOOTER)

    manifest_bytes = manifest_path.read_bytes()
    assert verify_signature(manifest_bytes, signature_pem, private_key.public_key())


def test_tampered_manifest_signature_fails(tmp_path: Path) -> None:
    """Flip one byte in the manifest after signing; verification fails."""
    manifest_path = tmp_path / "gazetteer_manifest.json"
    dump_canonical_json(_sample_manifest_payload(), manifest_path)

    private_key = Ed25519PrivateKey.generate()
    signature_path = sign_manifest_file(manifest_path, private_key)
    signature_pem = signature_path.read_bytes()

    # Tamper: flip the last non-newline byte of the manifest. Trailing
    # newline is preserved so canonical-shape invariants stay intact;
    # only the payload changed.
    original = manifest_path.read_bytes()
    assert original.endswith(b"\n"), "canonical JSON must end with LF"
    body = bytearray(original[:-1])
    body[-1] ^= 0x01
    tampered = bytes(body) + b"\n"
    manifest_path.write_bytes(tampered)

    tampered_bytes = manifest_path.read_bytes()
    assert tampered_bytes != original, "tamper must change the bytes"
    assert not verify_signature(tampered_bytes, signature_pem, private_key.public_key())

    # And the original (un-tampered) bytes still verify against the same signature.
    assert verify_signature(original, signature_pem, private_key.public_key())


def test_canonical_json_byte_for_byte_reproducible(tmp_path: Path) -> None:
    """The same payload serialized twice via ``dump_canonical_json``
    produces identical bytes — the determinism property the Ed25519
    signing flow depends on.
    """
    payload = _sample_manifest_payload()

    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    dump_canonical_json(payload, first_path)
    dump_canonical_json(payload, second_path)

    assert first_path.read_bytes() == second_path.read_bytes()

    # And the same key pair signing those identical bytes produces an
    # identical (deterministic) signature — Ed25519's deterministic
    # property is what lets the build's `make sign-manifest` output stay
    # in `asset_hashes.lock` without re-running per check.
    private_key = Ed25519PrivateKey.generate()
    sig_first = sign_manifest_file(first_path, private_key)
    sig_second = sign_manifest_file(second_path, private_key)
    assert sig_first.read_bytes() == sig_second.read_bytes()


def test_public_key_export_round_trips(tmp_path: Path) -> None:
    """Export a public key to PEM and load it back; same key verifies the
    same signature. (Auxiliary — gives the CLI export path a regression
    floor; primary verification covered above.)
    """
    manifest_path = tmp_path / "gazetteer_manifest.json"
    dump_canonical_json(_sample_manifest_payload(), manifest_path)

    private_key = Ed25519PrivateKey.generate()
    public_key_path = tmp_path / "manifest_public_key.pem"
    export_public_key(private_key, public_key_path)

    signature_pem = sign_manifest_bytes(manifest_path.read_bytes(), private_key)

    loaded_public = serialization.load_pem_public_key(public_key_path.read_bytes())
    assert verify_signature(manifest_path.read_bytes(), signature_pem, loaded_public)  # type: ignore[arg-type]
