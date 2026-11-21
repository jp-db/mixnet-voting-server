import pytest

from voting_common.blobstore import FilesystemBlobStore, digest


def test_put_get_roundtrip(tmp_path):
    store = FilesystemBlobStore(tmp_path)
    key = store.put(b"ballot bytes")
    assert key == digest(b"ballot bytes")
    assert store.exists(key)
    assert store.get(key) == b"ballot bytes"
    assert store.put(b"ballot bytes") == key  # idempotent


def test_missing_and_malformed_keys(tmp_path):
    store = FilesystemBlobStore(tmp_path)
    with pytest.raises(KeyError):
        store.get(digest(b"never stored"))
    with pytest.raises(ValueError):
        store.get("../../etc/passwd")
