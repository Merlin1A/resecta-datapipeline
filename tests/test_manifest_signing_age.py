"""The age-encrypted signing-key source.

Covers:
  - A real round trip through the host ``age`` tooling with a throwaway
    x25519 identity: the key is born encrypted (no plaintext file), loads
    back through the identity, and the signature it produces verifies with
    the exported public key. Skipped where ``age``/``age-keygen`` are not
    installed (hosted CI never signs).
  - The subprocess seam with a stand-in ``age``: the argv shape, the stdin
    plumbing, the error path (a non-zero exit becomes a ``PipelineError``
    whose message carries no key bytes) and the missing-identity guard.
  - ``resolve_default_key_path`` prefers the encrypted file when present.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from resecta_data import manifest_signing
from resecta_data.common.exceptions import PipelineError
from resecta_data.manifest_signing import (
    generate_private_key,
    is_encrypted_key_path,
    load_private_key,
    resolve_default_key_path,
    sign_manifest_bytes,
    verify_signature,
)

PRIVATE_KEY_MARKER = b"PRIVATE KEY"


def _write_script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _fixture_pem(tmp_path: Path) -> tuple[Path, Ed25519PrivateKey]:
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pem_path = tmp_path / "fixture.pem"
    pem_path.write_bytes(pem)
    return pem_path, key


# --- the real tooling ---------------------------------------------------------


@pytest.mark.skipif(
    shutil.which("age") is None or shutil.which("age-keygen") is None,
    reason="age / age-keygen not installed on this host",
)
def test_age_round_trip_with_a_throwaway_identity(tmp_path: Path) -> None:
    identity = tmp_path / "identity.txt"
    keygen = shutil.which("age-keygen")
    assert keygen is not None
    result = subprocess.run(  # noqa: S603 -- fixed argv, no shell
        [keygen, "-o", str(identity)],
        check=True,
        capture_output=True,
        text=True,
    )
    recipient = result.stderr.split("Public key:", 1)[1].strip()
    assert recipient.startswith("age1")

    key_path = tmp_path / "k.pem.age"
    generated = generate_private_key(key_path, encrypt_to=recipient)

    assert key_path.is_file()
    assert not (tmp_path / "k.pem").exists()
    assert PRIVATE_KEY_MARKER not in key_path.read_bytes()
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600

    loaded = load_private_key(key_path, identity=identity)
    manifest = b'{"version": "1.0.0"}\n'
    signature = sign_manifest_bytes(manifest, loaded)
    assert verify_signature(manifest, signature, generated.public_key())
    assert not verify_signature(manifest + b"x", signature, generated.public_key())


@pytest.mark.skipif(
    shutil.which("age") is None or shutil.which("age-keygen") is None,
    reason="age / age-keygen not installed on this host",
)
def test_wrong_identity_fails_without_leaking(tmp_path: Path) -> None:
    keygen = shutil.which("age-keygen")
    assert keygen is not None
    right = tmp_path / "right.txt"
    wrong = tmp_path / "wrong.txt"
    recipient = ""
    for ident in (right, wrong):
        out = subprocess.run(  # noqa: S603 -- fixed argv, no shell
            [keygen, "-o", str(ident)],
            check=True,
            capture_output=True,
            text=True,
        )
        if ident is right:
            recipient = out.stderr.split("Public key:", 1)[1].strip()
    key_path = tmp_path / "k.pem.age"
    generate_private_key(key_path, encrypt_to=recipient)

    with pytest.raises(PipelineError) as excinfo:
        load_private_key(key_path, identity=wrong)
    assert PRIVATE_KEY_MARKER.decode() not in str(excinfo.value)
    assert "decrypt" in str(excinfo.value)


# --- the subprocess seam -------------------------------------------------------


def test_fake_age_decrypts_through_the_seam(tmp_path: Path) -> None:
    pem_path, key = _fixture_pem(tmp_path)
    argv_log = tmp_path / "argv.log"
    fake_age = _write_script(
        tmp_path / "fake-age",
        f'printf "%s\\n" "$@" > "{argv_log}"\ncat "{pem_path}"\n',
    )
    identity = tmp_path / "identity.txt"
    identity.write_text("AGE-SECRET-KEY-1FAKE\n")
    encrypted = tmp_path / "k.pem.age"
    encrypted.write_bytes(b"not really ciphertext")

    loaded = load_private_key(encrypted, identity=identity, age_bin=fake_age)

    assert loaded.public_key().public_bytes_raw() == key.public_key().public_bytes_raw()
    assert argv_log.read_text().split("\n")[:4] == ["-d", "-i", str(identity), str(encrypted)]


def test_fake_age_failure_names_the_mechanism_not_the_bytes(tmp_path: Path) -> None:
    fake_age = _write_script(
        tmp_path / "fake-age",
        'echo "age: error: no identity matched any of the recipients" >&2\nexit 1\n',
    )
    identity = tmp_path / "identity.txt"
    identity.write_text("AGE-SECRET-KEY-1FAKE\n")
    encrypted = tmp_path / "k.pem.age"
    encrypted.write_bytes(b"ciphertext")

    with pytest.raises(PipelineError) as excinfo:
        load_private_key(encrypted, identity=identity, age_bin=fake_age)
    message = str(excinfo.value)
    assert "exited 1" in message
    assert str(encrypted) in message
    assert PRIVATE_KEY_MARKER.decode() not in message
    assert "ciphertext" not in message


def test_fake_age_encrypts_from_stdin_and_writes_only_the_age_file(tmp_path: Path) -> None:
    argv_log = tmp_path / "argv.log"
    # The stand-in "encrypts" by reversing nothing: stdin → stdout, so the
    # test can prove the PEM travelled over the pipe rather than a file.
    fake_age = _write_script(
        tmp_path / "fake-age",
        f'printf "%s\\n" "$@" > "{argv_log}"\ncat\n',
    )
    key_path = tmp_path / "k.pem.age"

    generated = generate_private_key(key_path, encrypt_to="age1fakerecipient", age_bin=fake_age)

    assert argv_log.read_text().split("\n")[:2] == ["-r", "age1fakerecipient"]
    written = key_path.read_bytes()
    assert written.startswith(b"-----BEGIN PRIVATE KEY-----")
    reloaded = serialization.load_pem_private_key(written, password=None)
    assert isinstance(reloaded, Ed25519PrivateKey)
    assert reloaded.public_key().public_bytes_raw() == generated.public_key().public_bytes_raw()
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert sorted(p.name for p in tmp_path.iterdir() if p.suffix in {".pem", ".age"}) == [
        "k.pem.age"
    ]


def test_fake_age_empty_output_is_an_error(tmp_path: Path) -> None:
    fake_age = _write_script(tmp_path / "fake-age", "exit 0\n")
    key_path = tmp_path / "k.pem.age"
    with pytest.raises(PipelineError, match="no output"):
        generate_private_key(key_path, encrypt_to="age1fakerecipient", age_bin=fake_age)
    assert not key_path.exists()


def test_generate_refuses_mismatched_path_forms(tmp_path: Path) -> None:
    with pytest.raises(PipelineError, match="--encrypt-to"):
        generate_private_key(tmp_path / "k.pem.age")
    with pytest.raises(PipelineError, match=r"does not end in \.age"):
        generate_private_key(tmp_path / "k.pem", encrypt_to="age1fakerecipient")
    assert list(tmp_path.iterdir()) == []


def test_generate_refuses_to_clobber_an_encrypted_key(tmp_path: Path) -> None:
    key_path = tmp_path / "k.pem.age"
    key_path.write_bytes(b"existing")
    with pytest.raises(PipelineError, match="Refusing to overwrite"):
        generate_private_key(key_path, encrypt_to="age1fakerecipient")
    assert key_path.read_bytes() == b"existing"


def test_missing_identity_is_reported_before_age_runs(tmp_path: Path) -> None:
    fake_age = _write_script(tmp_path / "fake-age", 'echo ran > "$0.ran"\n')
    encrypted = tmp_path / "k.pem.age"
    encrypted.write_bytes(b"ciphertext")
    with pytest.raises(PipelineError, match="age identity not found"):
        load_private_key(encrypted, identity=tmp_path / "absent.txt", age_bin=fake_age)
    assert not Path(f"{fake_age}.ran").exists()


def test_missing_age_binary_is_a_pipeline_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    encrypted = tmp_path / "k.pem.age"
    encrypted.write_bytes(b"ciphertext")
    identity = tmp_path / "identity.txt"
    identity.write_text("AGE-SECRET-KEY-1FAKE\n")
    with pytest.raises(PipelineError, match="not on PATH"):
        load_private_key(encrypted, identity=identity)


def test_plaintext_pem_still_loads(tmp_path: Path) -> None:
    pem_path, key = _fixture_pem(tmp_path)
    loaded = load_private_key(pem_path)
    assert loaded.public_key().public_bytes_raw() == key.public_key().public_bytes_raw()


# --- the default path ----------------------------------------------------------


def test_resolve_default_key_path_prefers_the_encrypted_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plaintext = tmp_path / "manifest-private-key.pem"
    encrypted = tmp_path / "manifest-private-key.pem.age"
    monkeypatch.setattr(manifest_signing, "DEFAULT_PRIVATE_KEY_PATH", plaintext)
    monkeypatch.setattr(manifest_signing, "DEFAULT_ENCRYPTED_KEY_PATH", encrypted)

    assert resolve_default_key_path() == plaintext
    plaintext.write_bytes(b"pem")
    assert resolve_default_key_path() == plaintext
    encrypted.write_bytes(b"age")
    assert resolve_default_key_path() == encrypted
    plaintext.unlink()
    assert resolve_default_key_path() == encrypted


def test_is_encrypted_key_path() -> None:
    assert is_encrypted_key_path(Path("k.pem.age"))
    assert not is_encrypted_key_path(Path("k.pem"))
    assert not is_encrypted_key_path(Path("k.age.pem"))


def test_generate_at_the_encrypted_default_refuses_while_plaintext_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plaintext = tmp_path / "manifest-private-key.pem"
    encrypted = tmp_path / "manifest-private-key.pem.age"
    monkeypatch.setattr(manifest_signing, "DEFAULT_PRIVATE_KEY_PATH", plaintext)
    monkeypatch.setattr(manifest_signing, "DEFAULT_ENCRYPTED_KEY_PATH", encrypted)
    plaintext.write_bytes(b"pem")
    fake_age = _write_script(tmp_path / "fake-age", "cat\n")

    with pytest.raises(PipelineError, match="would replace it"):
        generate_private_key(encrypted, encrypt_to="age1fakerecipient", age_bin=fake_age)
    assert not encrypted.exists()

    # An explicit non-default path is not subject to the guard.
    elsewhere = tmp_path / "other.pem.age"
    generate_private_key(elsewhere, encrypt_to="age1fakerecipient", age_bin=fake_age)
    assert elsewhere.is_file()
