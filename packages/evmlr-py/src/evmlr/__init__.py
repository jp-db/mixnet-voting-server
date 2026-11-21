"""Python bindings for evmlr-shuffle, the lattice-based verifiable mixnet.

Each function maps to one party's step in ``evmlr_api.h``; every object is
``bytes`` in the library's canonical, platform-independent encoding, so a
ballot cast by the WebAssembly build in a browser verifies here.

==================  =========================  ===============================
Party               Function                   Result
==================  =========================  ===============================
mixing server j     :func:`keygen`             public key, secret key pair
election authority  :func:`params`             public parameters
voter               :func:`cast`               ballot (normally in the browser)
ballot box          :func:`check_ballot`       voter id, or EvmlrError
server 0            :func:`first_batch`        input batch from the board
server j            :func:`mix`                next batch + mix record
anyone              :func:`mix_info`           rejections of a mix record
anyone              :func:`transcript`         the k records as one transcript
anyone              :func:`verify`             the votes, if the count verifies
server k-1          :func:`results`            the votes in its output batch
==================  =========================  ===============================
"""

from ._api import (
    ELECTION_ID_BYTES,
    MODULUS,
    VOTE_LEN,
    EvmlrError,
    MixInfo,
    MixOutput,
    ParamsInfo,
    Rejection,
    RejectReason,
    ServerKeys,
    Status,
    Vote,
    cast,
    check_ballot,
    first_batch,
    is_valid_ballot,
    keygen,
    mix,
    mix_info,
    params,
    params_info,
    results,
    transcript,
    verify,
)

__all__ = [
    "ELECTION_ID_BYTES",
    "MODULUS",
    "VOTE_LEN",
    "EvmlrError",
    "MixInfo",
    "MixOutput",
    "ParamsInfo",
    "Rejection",
    "RejectReason",
    "ServerKeys",
    "Status",
    "Vote",
    "cast",
    "check_ballot",
    "first_batch",
    "is_valid_ballot",
    "keygen",
    "mix",
    "mix_info",
    "params",
    "params_info",
    "results",
    "transcript",
    "verify",
]
