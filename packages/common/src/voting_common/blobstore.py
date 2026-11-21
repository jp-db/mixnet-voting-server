"""Content-addressed storage for large protocol objects.

Ballots (~100 KB each) and mix records and transcripts (hundreds of KB per
ballot) are too large for database rows. They are stored once, keyed by
their SHA-256, and the database keeps only the hex digest. Content
addressing makes writes idempotent (a retried task stores the same key) and
lets anyone check a published object against the hash on the bulletin board.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Protocol


def digest(data: bytes) -> str:
    """The key of ``data``: its SHA-256, in lowercase hex."""
    return hashlib.sha256(data).hexdigest()


class BlobStore(Protocol):
    def put(self, data: bytes) -> str:
        """Stores ``data`` and returns its key. Storing the same bytes twice is a no-op."""
        ...

    def get(self, key: str) -> bytes:
        """Returns the bytes stored under ``key``; raises ``KeyError`` if absent."""
        ...

    def exists(self, key: str) -> bool: ...


class FilesystemBlobStore:
    """A blob store in a local directory, for development and single-host deployments.

    Objects live at ``root/ab/cdef...``. Writes go through a temporary file
    and an atomic rename, so a reader never sees a partial object.
    An S3/MinIO implementation of :class:`BlobStore` is the production target.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError(f"not a SHA-256 hex digest: {key!r}")
        return self.root / key[:2] / key[2:]

    def put(self, data: bytes) -> str:
        key = digest(data)
        path = self._path(key)
        if path.exists():
            return key
        path.parent.mkdir(exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return key

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError:
            raise KeyError(key) from None

    def exists(self, key: str) -> bool:
        return self._path(key).exists()
