"""End-to-end tests of the bindings: an election run through bytes only.

The fixture runs one election (2 servers, 4 voters, one revote) once per
session; the tests then check its pieces and the failure paths.
"""

from __future__ import annotations

import hashlib
import struct
import threading
from dataclasses import dataclass

import pytest

import evmlr
from evmlr import EvmlrError, Status

K = 2
ELECTION_ID = hashlib.sha256(b"evmlr-py test election").digest()
OTHER_ELECTION_ID = hashlib.sha256(b"another election").digest()

# (voter_id, candidate) in cast order; voter 2 revotes, so 20 must not count.
CAST = [(1, 10), (2, 20), (3, 30), (2, 21), (4, 40)]
EXPECTED = sorted([10, 30, 21, 40])

# The submission encoding: a 20-byte header, then the voter id as a
# little-endian u64 (evmlr_serialize.c, evmlr_serialize_submission).
VOTER_ID_OFFSET = 20


@dataclass
class Election:
    keys: list[evmlr.ServerKeys]
    pp: bytes
    ballots: list[bytes]
    batches: list[bytes]
    records: list[bytes]
    transcript: bytes


def run_mixnet(pp: bytes, keys: list[evmlr.ServerKeys], ballots: list[bytes]):
    batches = [evmlr.first_batch(pp, ballots)]
    records: list[bytes] = []
    for j, key in enumerate(keys):
        out = evmlr.mix(pp, key.keypair, batches[-1], records[-1] if j else None)
        batches.append(out.batch)
        records.append(out.record)
    return batches, records


@pytest.fixture(scope="session")
def election() -> Election:
    keys = [evmlr.keygen() for _ in range(K)]
    pp = evmlr.params(ELECTION_ID, [k.public_key for k in keys])
    ballots = [evmlr.cast(pp, vid, [c]) for vid, c in CAST]
    batches, records = run_mixnet(pp, keys, ballots)
    tr = evmlr.transcript(pp, records)
    return Election(keys, pp, ballots, batches, records, tr)


def with_voter_id(ballot: bytes, voter_id: int) -> bytes:
    patched = bytearray(ballot)
    struct.pack_into("<q", patched, VOTER_ID_OFFSET, voter_id)
    return bytes(patched)


# --- constants and parameters ----------------------------------------------


def test_constants():
    assert evmlr.ELECTION_ID_BYTES == 32
    assert evmlr.MODULUS == 3109
    assert evmlr.VOTE_LEN == 512


def test_keys_hide_secret_in_repr():
    keys = evmlr.keygen()
    assert keys.public_key and keys.keypair
    assert "keypair" not in repr(keys)


def test_params_info(election: Election):
    info = evmlr.params_info(election.pp)
    assert info.k == K
    assert info.election_id == ELECTION_ID


def test_params_are_deterministic(election: Election):
    again = evmlr.params(ELECTION_ID, [k.public_key for k in election.keys])
    assert again == election.pp


def test_params_validates_arguments(election: Election):
    with pytest.raises(ValueError):
        evmlr.params(b"short", [election.keys[0].public_key])
    with pytest.raises(ValueError):
        evmlr.params(ELECTION_ID, [])
    with pytest.raises(EvmlrError) as exc:
        evmlr.params(ELECTION_ID, [b"not a key"])
    assert exc.value.status in (Status.TRUNCATED, Status.MAGIC)


# --- casting and the board ---------------------------------------------------


def test_check_ballot_returns_voter_id(election: Election):
    for ballot, (vid, _) in zip(election.ballots, CAST, strict=True):
        assert evmlr.check_ballot(election.pp, ballot) == vid
        assert evmlr.is_valid_ballot(election.pp, ballot)


def test_ballot_copied_under_another_voter_is_refused(election: Election):
    copied = with_voter_id(election.ballots[0], 99)
    with pytest.raises(EvmlrError) as exc:
        evmlr.check_ballot(election.pp, copied)
    assert exc.value.status == Status.INVALID
    assert not evmlr.is_valid_ballot(election.pp, copied)


def test_ballot_replayed_in_another_election_is_refused(election: Election):
    other_pp = evmlr.params(OTHER_ELECTION_ID, [k.public_key for k in election.keys])
    assert not evmlr.is_valid_ballot(other_pp, election.ballots[0])


def test_truncated_ballot_is_refused(election: Election):
    with pytest.raises(EvmlrError) as exc:
        evmlr.check_ballot(election.pp, election.ballots[0][:-1])
    assert exc.value.status == Status.TRUNCATED


def test_cast_validates_vote(election: Election):
    with pytest.raises(ValueError):
        evmlr.cast(election.pp, 1, [evmlr.MODULUS])
    with pytest.raises(ValueError):
        evmlr.cast(election.pp, 1, [0] * (evmlr.VOTE_LEN + 1))
    with pytest.raises(ValueError):
        evmlr.cast(election.pp, 2**63, [1])


# --- mixing and counting -----------------------------------------------------


def test_results_hold_the_votes_after_revote(election: Election):
    votes = evmlr.results(election.pp, election.batches[-1])
    assert len(votes) == len(EXPECTED)
    assert all(len(v) == evmlr.VOTE_LEN for v in votes)
    assert sorted(v[0] for v in votes) == EXPECTED
    assert all(not any(v[1:]) for v in votes)


def test_mix_info(election: Election):
    for j, record in enumerate(election.records):
        info = evmlr.mix_info(election.pp, record)
        assert info.server == j
        assert info.n_in == info.n_out == len(EXPECTED)
        assert info.rejections == ()


def test_verify_matches_results(election: Election):
    verified = evmlr.verify(election.pp, election.ballots, election.transcript)
    assert verified == evmlr.results(election.pp, election.batches[-1])


def test_verify_rejects_a_different_board(election: Election):
    with pytest.raises(EvmlrError) as exc:
        evmlr.verify(election.pp, election.ballots[:-1], election.transcript)
    assert exc.value.status == Status.INVALID


def test_mix_refuses_another_servers_key(election: Election):
    with pytest.raises(EvmlrError) as exc:
        evmlr.mix(election.pp, election.keys[1].keypair, election.batches[0])
    assert exc.value.status == Status.KEY


def test_mix_requires_the_previous_record(election: Election):
    with pytest.raises(EvmlrError) as exc:
        evmlr.mix(election.pp, election.keys[1].keypair, election.batches[1])
    assert exc.value.status == Status.ARG
    with pytest.raises(EvmlrError) as exc:
        evmlr.mix(election.pp, election.keys[0].keypair, election.batches[0], election.records[0])
    assert exc.value.status == Status.ARG


def test_transcript_requires_server_order(election: Election):
    with pytest.raises(EvmlrError) as exc:
        evmlr.transcript(election.pp, list(reversed(election.records)))
    assert exc.value.status == Status.ARG


def test_empty_board(election: Election):
    batches, records = run_mixnet(election.pp, election.keys, [])
    assert evmlr.results(election.pp, batches[-1]) == []
    tr = evmlr.transcript(election.pp, records)
    assert evmlr.verify(election.pp, [], tr) == []


def test_single_ballot_is_not_mixed(election: Election):
    batch = evmlr.first_batch(election.pp, election.ballots[:1])
    with pytest.raises(EvmlrError) as exc:
        evmlr.mix(election.pp, election.keys[0].keypair, batch)
    assert exc.value.status == Status.TOO_FEW


# --- concurrency -------------------------------------------------------------


def test_concurrent_calls_are_serialised(election: Election):
    """The C library is not thread-safe; the module lock must make this safe."""
    errors: list[BaseException] = []
    out: list[int] = []

    def worker(vid: int) -> None:
        try:
            ballot = evmlr.cast(election.pp, vid, [vid])
            out.append(evmlr.check_ballot(election.pp, ballot))
        except BaseException as e:  # noqa: BLE001 - reported below
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(100 + i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert sorted(out) == [100, 101, 102, 103]
