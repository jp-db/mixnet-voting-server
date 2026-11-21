"""This node's per-election key pairs, encrypted at rest.

Each election gets its own key pair, generated here and never sent
anywhere: only the public key leaves the node. The file holds the public
key in the clear and the key pair sealed with AES-256-GCM under the node's
key-encryption key (``MIXNET_NODE_KEY_ENCRYPTION_KEY``). The election id is
the associated data, so a key file copied to another election's name does
not open.

A key is never replaced. Ballots are encrypted to it, and a new key would
make every ballot of the election undecryptable. :meth:`KeyStore.create` is
therefore idempotent, and it publishes the file with ``os.link``, which
fails if the name exists, so concurrent calls cannot overwrite each other.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import evmlr

_FORMAT_VERSION = 1
_AAD_PREFIX = b"mixnet-node key v1\x00"


class KeyNotFound(KeyError):
    pass


class KeyFileError(Exception):
    """A key file exists but cannot be read or opened (wrong KEK, tampering)."""


@dataclass(frozen=True)
class PublicKey:
    election_id: bytes
    public_key: bytes

    @property
    def fingerprint(self) -> str:
        """SHA-256 of the public key, for comparing against what the ballot box published."""
        return hashlib.sha256(self.public_key).hexdigest()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _unb64(text: str) -> bytes:
    return base64.b64decode(text, validate=True)


class KeyStore:
    def __init__(self, root: str | os.PathLike[str], key_encryption_key: bytes) -> None:
        if len(key_encryption_key) != 32:
            raise ValueError("the key-encryption key must be 32 bytes")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._aead = AESGCM(key_encryption_key)

    def _path(self, election_id: bytes) -> Path:
        if len(election_id) != evmlr.ELECTION_ID_BYTES:
            raise ValueError("election ids are 32 bytes")
        return self.root / f"{election_id.hex()}.key"

    @staticmethod
    def _aad(election_id: bytes) -> bytes:
        return _AAD_PREFIX + election_id

    def create(self, election_id: bytes) -> tuple[PublicKey, bool]:
        """Returns this election's public key, generating the key pair if there is none.

        The flag says whether the key was created by this call.
        """
        path = self._path(election_id)
        if path.exists():
            return self.public_key(election_id), False

        keys = evmlr.keygen()
        nonce = os.urandom(12)
        record = {
            "version": _FORMAT_VERSION,
            "election_id": election_id.hex(),
            "public_key": _b64(keys.public_key),
            "nonce": _b64(nonce),
            "sealed_keypair": _b64(self._aead.encrypt(nonce, keys.keypair, self._aad(election_id))),
        }
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".tmp-")  # created with mode 0600
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(record, f)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(tmp, path)  # atomic, and never replaces an existing key
            except FileExistsError:
                return self.public_key(election_id), False  # lost a race: keep the winner's
        finally:
            Path(tmp).unlink(missing_ok=True)
        return PublicKey(election_id, keys.public_key), True

    def _read(self, election_id: bytes) -> dict:
        try:
            record = json.loads(self._path(election_id).read_text())
        except FileNotFoundError:
            raise KeyNotFound(election_id.hex()) from None
        except (OSError, ValueError) as e:
            raise KeyFileError(f"unreadable key file for {election_id.hex()}") from e
        if record.get("version") != _FORMAT_VERSION or record.get("election_id") != election_id.hex():
            raise KeyFileError(f"key file for {election_id.hex()} does not match its name")
        return record

    def has(self, election_id: bytes) -> bool:
        return self._path(election_id).exists()

    def public_key(self, election_id: bytes) -> PublicKey:
        record = self._read(election_id)
        return PublicKey(election_id, _unb64(record["public_key"]))

    def keypair(self, election_id: bytes) -> bytes:
        """The secret key pair, decrypted. Only the mixing process should call this."""
        record = self._read(election_id)
        try:
            return self._aead.decrypt(
                _unb64(record["nonce"]), _unb64(record["sealed_keypair"]), self._aad(election_id)
            )
        except Exception as e:  # InvalidTag, bad base64
            raise KeyFileError(f"cannot open the key of {election_id.hex()} (wrong KEK?)") from e
