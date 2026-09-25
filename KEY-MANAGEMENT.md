# Key management

The detection data that ships inside the Resecta iOS app is listed in a signed
manifest. This page describes the key behind that signature: what it signs,
what a valid signature does and does not prove, how the private key is held,
and what happens when the key is rotated or suspected to be exposed.

## What is signed

`make sign-manifest` signs `build/gazetteers/gazetteer_manifest.shipped.json`
with an Ed25519 key. That manifest lists every detection asset the pipeline
installs into the app bundle, with each file's SHA-256 and byte count. The
detached signature (`gazetteer_manifest.sig`) and the public key
(`manifest_public_key.pem`) are installed beside the manifest under the
engine's `Resources/Gazetteers/` directory by `make install-assets`.

Signing is a maintainer step on the maintainer's machine. Hosted continuous
integration never signs: the runners hold no key, and the signed files are
committed to the app repository like any other asset.

## What the signature proves

At first load, the app verifies the signature over the manifest bytes with the
public key it bundles, then checks each gated asset against its manifest entry
before reading it. A valid signature means the detection data the app reads is
the data this pipeline produced and signed — pipeline-to-bundle provenance.

It is not the mechanism that protects an installed app from tampering. That is
the app-bundle code signature, which seals these same files. On any
verification failure the app withholds the gated loaders and shows a banner;
it does not fail silently.

## The current public key

The public key bundled in the app since 2026-07-11 has this fingerprint
(SHA-256 over the DER-encoded SubjectPublicKeyInfo):

```
d471e66bb6d3b6682c3ab3e5baf7679d7e58b2059f359da997bbb0cded9d20d1
```

To recompute it from a checkout of the app repository:

```sh
openssl pkey -pubin \
  -in Packages/RedactionEngine/Sources/RedactionEngine/Resources/Gazetteers/manifest_public_key.pem \
  -outform DER | openssl dgst -sha256
```

## How the private key is held

The private key does not enter either repository and is not published.

- The working copy is held encrypted with [age](https://age-encryption.org)
  to a hardware-bound key on the maintainer's build machine. `sign-manifest`
  decrypts it in memory, through the age identity named by `--age-identity`,
  only for the duration of a signing run; no plaintext copy is written.
- An offline recovery copy of the same key, encrypted to a passphrase that
  exists only on paper, is kept apart from the machine. Loss of the machine
  is a restore from that copy, not a forced rotation.
- The paper copy's location is not published.

`make doctor` reports which form of the key is present and whether the tools
needed to read it are on the path.

### Moving an existing key into the encrypted form

Encrypting a key that already exists is not a generation step: encrypt the
PEM in place with `age -r RECIPIENT -o manifest-private-key.pem.age
manifest-private-key.pem`, check that the encrypted file decrypts to the
bundled public key without writing private bytes anywhere —

```sh
age -d -i age-identity.txt manifest-private-key.pem.age \
  | openssl pkey -pubout | diff - manifest_public_key.pem
```

— and only then delete the plaintext file. `sign-manifest` refuses to
generate a new key at the encrypted default path while a plaintext key is
still present, because the encrypted file would take precedence and every
later signing run would use the new key.

## Rotation

The key is rotated on the maintainer's documented schedule and on any
suspicion of compromise. A rotation retires the existing key (an encrypted
copy is kept until the release that carries the new key is out), generates a
new key straight into its encrypted form (`sign-manifest --generate-key
--encrypt-to RECIPIENT`; no plaintext is written, and the same run signs
through the identity, which proves the new file decrypts), re-signs the
manifest, and ships the new public key inside the next app update. The app
verifies against the one public key it bundles.

There is no revocation list, by design: replacing the key means shipping a new
app version, and older versions keep verifying against the key they shipped
with.

| Public key fingerprint (SHA-256 of the SPKI DER) | In the app since |
|---|---|
| `d471e66bb6d3b6682c3ab3e5baf7679d7e58b2059f359da997bbb0cded9d20d1` | 2026-07-11 (current) |

## If the key is suspected to be exposed

1. The key is rotated as above and the affected app versions (every version
   bundling the old public key) are identified.
2. A new app version with the new public key and freshly signed assets is
   released.
3. The event is recorded in this file and in the changelog.

An exposed signing key does not by itself let anyone alter an installed app:
the app bundle's own code signature still seals the files. It would let
someone who can also replace files inside a bundle produce detection data the
app would accept, which is why exposure is treated as a rotation trigger.

Report suspected exposure through the channels in `SECURITY.md`.
