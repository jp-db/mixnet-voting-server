"""Python API over ``evmlr_api.h``.

Every object crosses the boundary as ``bytes`` in the library's canonical
encoding, so nothing here exposes C memory: each function copies its outputs
into Python ``bytes`` and releases the C buffers before returning.

Thread safety: the C library keeps one global random stream and is not
thread-safe, so every call takes a module-wide lock. cffi releases the GIL
while C code runs, so other Python threads (an event loop, for instance) keep
running; but only one evmlr call runs at a time per process. For parallel
work, such as verifying many ballots or counting several elections, use
several processes (Celery workers, ``ProcessPoolExecutor``).
"""

from __future__ import annotations

import enum
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field

from ._evmlr_cffi import ffi, lib

ELECTION_ID_BYTES: int = lib.EVMLR_ELECTION_ID_BYTES
MODULUS: int = lib.evmlr_modulus()
VOTE_LEN: int = lib.evmlr_vote_len()

_INT64_MIN, _INT64_MAX = -(2**63), 2**63 - 1

_LOCK = threading.Lock()

Vote = tuple[int, ...]
"""A decrypted vote: ``VOTE_LEN`` coefficients mod ``MODULUS``. Most
encodings use only ``vote[0]``, for instance as a candidate number."""


class Status(enum.IntEnum):
    """Mirror of ``evmlr_status``; cffi checks the values at build time."""

    OK = lib.EVMLR_OK
    TRUNCATED = lib.EVMLR_ERR_TRUNCATED
    MAGIC = lib.EVMLR_ERR_MAGIC
    VERSION = lib.EVMLR_ERR_VERSION
    TYPE = lib.EVMLR_ERR_TYPE
    PARAMS = lib.EVMLR_ERR_PARAMS
    SHAPE = lib.EVMLR_ERR_SHAPE
    VALUE = lib.EVMLR_ERR_VALUE
    TRAILING = lib.EVMLR_ERR_TRAILING
    NOMEM = lib.EVMLR_ERR_NOMEM
    ARG = lib.EVMLR_ERR_ARG
    KEY = lib.EVMLR_ERR_KEY
    INVALID = lib.EVMLR_ERR_INVALID
    TOO_FEW = lib.EVMLR_ERR_TOO_FEW
    SPACE = lib.EVMLR_ERR_SPACE


class RejectReason(enum.IntEnum):
    """Why a mixing server dropped a ciphertext."""

    HPKE = 1
    """The ML-PKE half of its proof of encryption fails."""
    OTSE = 2
    """The OTSE half fails against the decryption (not publicly checkable yet)."""


class EvmlrError(Exception):
    """A call into the C library failed. ``status`` says why."""

    def __init__(self, status: int, operation: str) -> None:
        self.status = Status(status)
        self.operation = operation
        message = ffi.string(lib.evmlr_strerror(status)).decode()
        super().__init__(f"{operation}: {message} ({self.status.name})")


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServerKeys:
    """A mixing server's keys. Publish ``public_key``; keep ``keypair`` secret."""

    public_key: bytes
    keypair: bytes = field(repr=False)


@dataclass(frozen=True)
class ParamsInfo:
    k: int
    """Number of mixing servers."""
    election_id: bytes


@dataclass(frozen=True)
class MixOutput:
    batch: bytes
    """Input for the next server; for the last server, the votes (see :func:`results`)."""
    record: bytes
    """The mix record to publish: rejections, shuffle statement and proof."""


@dataclass(frozen=True)
class Rejection:
    index: int
    """Index into the server's input batch."""
    reason: RejectReason


@dataclass(frozen=True)
class MixInfo:
    server: int
    n_in: int
    n_out: int
    rejections: tuple[Rejection, ...]


# ---------------------------------------------------------------------------
# Marshalling helpers
# ---------------------------------------------------------------------------


def _check(status: int, operation: str) -> None:
    if status != lib.EVMLR_OK:
        raise EvmlrError(status, operation)


def _buf(data: bytes | bytearray | memoryview) -> object:
    """A ``uint8_t *`` view of a Python buffer (no copy). Keep ``data`` alive."""
    return ffi.from_buffer("uint8_t[]", data)


def _take(out: object) -> bytes:
    """Copies an ``evmlr_bytes`` into Python and frees the C buffer."""
    try:
        if out.data == ffi.NULL:
            return b""
        return bytes(ffi.buffer(out.data, out.len))
    finally:
        lib.evmlr_bytes_free(out)


class _ByteList:
    """``const uint8_t *const *`` plus ``size_t *`` lengths for a list of buffers."""

    def __init__(self, items: Sequence[bytes]) -> None:
        self._bufs = [_buf(item) for item in items]  # keeps the views alive
        self.ptrs = ffi.new("const uint8_t *[]", self._bufs)
        self.lens = ffi.new("size_t[]", [len(item) for item in items])
        self.n = len(items)


def _election_id(election_id: bytes) -> bytes:
    if len(election_id) != ELECTION_ID_BYTES:
        raise ValueError(f"election_id must be {ELECTION_ID_BYTES} bytes, got {len(election_id)}")
    return bytes(election_id)


def _voter_id(voter_id: int) -> int:
    if not _INT64_MIN <= voter_id <= _INT64_MAX:
        raise ValueError("voter_id must fit in a signed 64-bit integer")
    return voter_id


def _votes_out(votes: object, n: int) -> list[Vote]:
    return [tuple(votes[i * VOTE_LEN : (i + 1) * VOTE_LEN]) for i in range(n)]


# ---------------------------------------------------------------------------
# Keys and parameters
# ---------------------------------------------------------------------------


def keygen() -> ServerKeys:
    """Generates a mixing server's key pair (each server runs this itself)."""
    pk = ffi.new("evmlr_bytes *")
    kp = ffi.new("evmlr_bytes *")
    with _LOCK:
        status = lib.evmlr_keygen(pk, kp)
    _check(status, "keygen")
    return ServerKeys(public_key=_take(pk), keypair=_take(kp))


def params(election_id: bytes, public_keys: Sequence[bytes]) -> bytes:
    """Public parameters for an election, from the servers' public keys in mixing order.

    ``election_id`` (32 bytes, e.g. a hash of the election's description) is
    bound into every ballot: ballots of one election do not verify in another.
    """
    eid = _election_id(election_id)
    if not public_keys:
        raise ValueError("at least one public key is required")
    pks = _ByteList(public_keys)
    out = ffi.new("evmlr_bytes *")
    with _LOCK:
        status = lib.evmlr_params(out, _buf(eid), pks.n, pks.ptrs, pks.lens)
    _check(status, "params")
    return _take(out)


def params_info(pp: bytes) -> ParamsInfo:
    """Reads the number of servers and the election id out of public parameters."""
    k = ffi.new("uint32_t *")
    eid = ffi.new("uint8_t[]", ELECTION_ID_BYTES)
    with _LOCK:
        status = lib.evmlr_params_info(_buf(pp), len(pp), k, eid)
    _check(status, "params_info")
    return ParamsInfo(k=k[0], election_id=bytes(ffi.buffer(eid, ELECTION_ID_BYTES)))


# ---------------------------------------------------------------------------
# Casting and the bulletin board
# ---------------------------------------------------------------------------


def cast(pp: bytes, voter_id: int, vote: Sequence[int]) -> bytes:
    """Encrypts and proves a vote for ``voter_id``.

    Voters normally cast in the browser through the WebAssembly build; this is
    for tests, tooling and load generation. ``vote`` holds at most ``VOTE_LEN``
    coefficients, each in ``[0, MODULUS)``; shorter votes are zero-padded.
    """
    vid = _voter_id(voter_id)
    if len(vote) > VOTE_LEN:
        raise ValueError(f"a vote has at most {VOTE_LEN} coefficients")
    if any(not 0 <= c < MODULUS for c in vote):
        raise ValueError(f"vote coefficients must be in [0, {MODULUS})")
    coeffs = ffi.new("uint16_t[]", list(vote) or [0])
    out = ffi.new("evmlr_bytes *")
    with _LOCK:
        status = lib.evmlr_cast(out, _buf(pp), len(pp), vid, coeffs, len(vote))
    _check(status, "cast")
    return _take(out)


def check_ballot(pp: bytes, ballot: bytes) -> int:
    """What the ballot box runs before accepting a ballot. Returns its voter id.

    Raises :class:`EvmlrError` if the ballot does not decode for this election
    or its public proof fails (``Status.INVALID``, e.g. a ballot copied under
    another voter id). The caller must still check that the returned voter id
    is the authenticated voter's.
    """
    vid = ffi.new("int64_t *")
    with _LOCK:
        status = lib.evmlr_check_ballot(_buf(pp), len(pp), _buf(ballot), len(ballot), vid)
    _check(status, "check_ballot")
    return vid[0]


def is_valid_ballot(pp: bytes, ballot: bytes) -> bool:
    """:func:`check_ballot` as a boolean."""
    try:
        check_ballot(pp, ballot)
    except EvmlrError:
        return False
    return True


# ---------------------------------------------------------------------------
# Mixing
# ---------------------------------------------------------------------------


def first_batch(pp: bytes, ballots: Sequence[bytes]) -> bytes:
    """Server 0's input: the last valid ballot of each voter, in board order.

    ``ballots`` must be every ballot on the board in cast order: the revote
    policy is applied here, not by the caller.
    """
    lst = _ByteList(ballots)
    out = ffi.new("evmlr_bytes *")
    with _LOCK:
        status = lib.evmlr_first_batch(out, _buf(pp), len(pp), lst.ptrs, lst.lens, lst.n)
    _check(status, "first_batch")
    return _take(out)


def mix(pp: bytes, keypair: bytes, batch: bytes, prev_record: bytes | None = None) -> MixOutput:
    """One server's step: decrypt its layer, drop invalid ciphertexts, shuffle, prove.

    ``prev_record`` is server j-1's mix record (``None`` for server 0); the
    library checks that ``batch`` is what that record says was output.
    """
    out_batch = ffi.new("evmlr_bytes *")
    out_record = ffi.new("evmlr_bytes *")
    prev = _buf(prev_record) if prev_record is not None else ffi.NULL
    prev_len = len(prev_record) if prev_record is not None else 0
    with _LOCK:
        status = lib.evmlr_mix(
            out_batch,
            out_record,
            _buf(pp),
            len(pp),
            _buf(keypair),
            len(keypair),
            _buf(batch),
            len(batch),
            prev,
            prev_len,
        )
    _check(status, "mix")
    return MixOutput(batch=_take(out_batch), record=_take(out_record))


def mix_info(pp: bytes, record: bytes) -> MixInfo:
    """What a mix record says: the server, its counts and its rejections."""
    server, n_in, n_out, n_rej = (ffi.new("uint32_t *") for _ in range(4))
    cap = 0
    rejected = reasons = ffi.NULL
    while True:
        with _LOCK:
            status = lib.evmlr_mix_info(
                _buf(pp), len(pp), _buf(record), len(record),
                server, n_in, n_out, n_rej, rejected, reasons, cap,
            )  # fmt: skip
        if status == lib.EVMLR_ERR_SPACE and cap < n_rej[0]:
            cap = n_rej[0]
            rejected = ffi.new("uint32_t[]", cap)
            reasons = ffi.new("uint8_t[]", cap)
            continue
        _check(status, "mix_info")
        break
    rejections = tuple(Rejection(index=rejected[i], reason=RejectReason(reasons[i])) for i in range(n_rej[0]))
    return MixInfo(server=server[0], n_in=n_in[0], n_out=n_out[0], rejections=rejections)


# ---------------------------------------------------------------------------
# The count
# ---------------------------------------------------------------------------


def transcript(pp: bytes, records: Sequence[bytes]) -> bytes:
    """Assembles the k mix records (in server order) into one counting transcript."""
    lst = _ByteList(records)
    out = ffi.new("evmlr_bytes *")
    with _LOCK:
        status = lib.evmlr_transcript(out, _buf(pp), len(pp), lst.ptrs, lst.lens, lst.n)
    _check(status, "transcript")
    return _take(out)


def verify(pp: bytes, ballots: Sequence[bytes], transcript_bytes: bytes) -> list[Vote]:
    """Checks a count from public data and returns the votes.

    ``ballots`` is every ballot on the board, in board order. Raises
    :class:`EvmlrError` with ``Status.INVALID`` if the count does not verify.
    """
    lst = _ByteList(ballots)
    # The revote policy never yields more votes than ballots, so one call suffices.
    cap = max(lst.n, 1)
    votes = ffi.new("uint16_t[]", cap * VOTE_LEN)
    n_votes = ffi.new("size_t *")
    with _LOCK:
        status = lib.evmlr_verify(
            votes, cap, n_votes, _buf(pp), len(pp), lst.ptrs, lst.lens, lst.n,
            _buf(transcript_bytes), len(transcript_bytes),
        )  # fmt: skip
    _check(status, "verify")
    return _votes_out(votes, n_votes[0])


def results(pp: bytes, final_batch: bytes) -> list[Vote]:
    """The votes in the last server's output batch."""
    n_votes = ffi.new("size_t *")
    pp_buf, batch_buf = _buf(pp), _buf(final_batch)
    with _LOCK:
        # First call with no room, only to learn the count (decoding is cheap).
        status = lib.evmlr_results(ffi.NULL, 0, n_votes, pp_buf, len(pp), batch_buf, len(final_batch))
        if status == lib.EVMLR_ERR_SPACE:
            cap = n_votes[0]
            votes = ffi.new("uint16_t[]", cap * VOTE_LEN)
            status = lib.evmlr_results(votes, cap, n_votes, pp_buf, len(pp), batch_buf, len(final_batch))
        else:
            votes = ffi.NULL
    _check(status, "results")
    return _votes_out(votes, n_votes[0]) if n_votes[0] else []
