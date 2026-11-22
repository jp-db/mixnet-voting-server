"""Sealing secrets the ballot box stores, such as mixnet node tokens.

AES-256-GCM under a key derived from ``BALLOT_BOX_SECRET_KEY`` with HKDF.
The associated data names what the secret is for (e.g. the node), so a
sealed value moved to another row does not open there.
"""

from __future__ import annotations

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_NONCE = 12


class UnsealError(Exception):
    """Wrong key (the secret key changed?) or tampered data."""


class Sealer:
    def __init__(self, secret_key: str, purpose: bytes) -> None:
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=purpose).derive(secret_key.encode())
        self._aead = AESGCM(key)

    def seal(self, plaintext: bytes, context: bytes) -> bytes:
        nonce = os.urandom(_NONCE)
        return nonce + self._aead.encrypt(nonce, plaintext, context)

    def unseal(self, sealed: bytes, context: bytes) -> bytes:
        try:
            return self._aead.decrypt(sealed[:_NONCE], sealed[_NONCE:], context)
        except (InvalidTag, ValueError) as e:
            raise UnsealError("cannot unseal: wrong secret key or tampered data") from e


def node_token_sealer(secret_key: str) -> Sealer:
    return Sealer(secret_key, b"ballot-box mixnet node token v1")
