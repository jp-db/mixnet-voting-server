"""Election operations, on a database session. The caller commits.

Each operation checks the election's state first: configuration changes only
in DRAFT, ballots only while OPEN, and so on (see :mod:`.state`).
"""

from __future__ import annotations

import secrets
import struct
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

import evmlr
from voting_common.blobstore import BlobStore

from ..mixnet.nodes import default_nodes
from ..models import Ballot, Election, ElectionMixServer, MixnetNode, Question, Tally, TallyState, Voter
from ..questions import QuestionSpec, crypto_id
from .state import ElectionState, InvalidTransition, transition


class NotFound(LookupError):
    pass


class WrongState(Exception):
    """The election's state does not allow this operation now."""


class Invalid(ValueError):
    """The request itself is wrong (unknown node, duplicate server, bad ballot, ...)."""


def _now() -> datetime:
    return datetime.now(UTC)


def _require(election: Election, *states: ElectionState) -> None:
    if election.state not in states:
        allowed = " or ".join(s.value for s in states)
        raise WrongState(f"the election is {election.state.value}; this needs it {allowed}")


def _move(election: Election, target: ElectionState) -> None:
    try:
        election.state = transition(election.state, target)
    except InvalidTransition as e:
        raise WrongState(str(e)) from e


def get_election(
    session: Session, election_id: uuid.UUID, *, lock: bool = False, shared: bool = False
) -> Election:
    """``lock`` takes the row exclusively (state changes); ``shared`` lets casts run side by side."""
    with_for_update: bool | dict[str, bool] = {"read": True} if shared and not lock else lock
    election = session.get(Election, election_id, with_for_update=with_for_update)
    if election is None:
        raise NotFound("no such election")
    return election


# --- configuration (DRAFT) ---------------------------------------------------------------


def _resolve_nodes(session: Session, names: Sequence[str] | None) -> list[MixnetNode]:
    if names is None:
        nodes = default_nodes(session)
        if not nodes:
            raise Invalid("no default mixnet nodes are configured: name the mix servers")
        return nodes
    if not names:
        raise Invalid("an election needs at least one mix server")
    if len(set(names)) != len(names):
        raise Invalid("a node can appear only once in the mix chain")
    found = {n.name: n for n in session.scalars(select(MixnetNode).where(MixnetNode.name.in_(names)))}
    missing = [n for n in names if n not in found]
    if missing:
        raise Invalid(f"unknown mixnet nodes: {', '.join(missing)}")
    return [found[n] for n in names]


def set_mix_servers(session: Session, election: Election, names: Sequence[str] | None) -> None:
    """The mix chain, in order. ``None`` picks the default nodes in their default order."""
    _require(election, ElectionState.DRAFT)
    nodes = _resolve_nodes(session, names)
    election.mix_servers.clear()
    session.flush()  # the old rows' primary keys are reused
    election.mix_servers.extend(ElectionMixServer(position=j, node=n) for j, n in enumerate(nodes))


def set_questions(election: Election, specs: Sequence[QuestionSpec]) -> None:
    _require(election, ElectionState.DRAFT)
    if not specs:
        raise Invalid("an election needs at least one question")
    election.questions.clear()
    election.questions.extend(
        Question(index=i, spec=spec.model_dump(mode="json")) for i, spec in enumerate(specs)
    )


def create_election(
    session: Session,
    title: str,
    description: str,
    questions: Sequence[QuestionSpec],
    mix_servers: Sequence[str] | None = None,
) -> Election:
    election = Election(title=title, description=description, state=ElectionState.DRAFT)
    session.add(election)
    set_questions(election, questions)
    set_mix_servers(session, election, mix_servers)
    session.flush()
    return election


def update_election(
    session: Session,
    election: Election,
    *,
    title: str | None = None,
    description: str | None = None,
    questions: Sequence[QuestionSpec] | None = None,
    mix_servers: Sequence[str] | None = None,
    default_mix_servers: bool = False,
) -> None:
    _require(election, ElectionState.DRAFT)
    if title is not None:
        election.title = title
    if description is not None:
        election.description = description
    if questions is not None:
        set_questions(election, questions)
    if mix_servers is not None or default_mix_servers:
        set_mix_servers(session, election, mix_servers)


@dataclass(frozen=True)
class VoterEntry:
    provider: str
    external_id: str
    email: str | None = None
    display_name: str | None = None


MAX_VOTER_ID = 2**31 - 1
"""evmlr's voter ids are FLINT ``slong``: 64 bits natively but 32 on wasm32,
where ``evmlr_cast`` refuses larger ids. Ids must fit both, or voters could not
cast from a browser."""


def _new_voter_id(taken: set[int]) -> int:
    while True:
        vid = secrets.randbelow(MAX_VOTER_ID) + 1
        if vid not in taken:
            taken.add(vid)
            return vid


def add_voters(session: Session, election: Election, entries: Sequence[VoterEntry]) -> list[Voter]:
    """Adds voters to the roll; an entry already on it is returned unchanged.

    Each new voter gets a random evmlr voter id: ids carry no information
    about the voter or the order of registration.
    """
    _require(election, ElectionState.DRAFT)
    existing = {(v.provider, v.external_id): v for v in election.voters}
    taken = {v.voter_id for v in election.voters}
    out: list[Voter] = []
    for entry in entries:
        key = (entry.provider, entry.external_id)
        voter = existing.get(key)
        if voter is None:
            voter = Voter(
                voter_id=_new_voter_id(taken),
                provider=entry.provider,
                external_id=entry.external_id,
                email=entry.email,
                display_name=entry.display_name,
            )
            election.voters.append(voter)
            existing[key] = voter
        out.append(voter)
    session.flush()
    return out


# --- lifecycle ------------------------------------------------------------------------------


def freeze(election: Election) -> None:
    """DRAFT -> KEYGEN: fixes each question's evmlr election id. Then enqueue key generation.

    Also accepted in KEYGEN, to retry a key generation that failed.
    """
    if election.state is ElectionState.KEYGEN:
        election.error = None
        return
    _require(election, ElectionState.DRAFT)
    if not election.questions:
        raise Invalid("an election needs at least one question")
    if not election.mix_servers:
        raise Invalid("an election needs at least one mix server")
    for q in election.questions:
        q.crypto_id = crypto_id(election.id, q.index, QuestionSpec.model_validate(q.spec))
        q.params_key = None
        q.keys.clear()
    election.error = None
    _move(election, ElectionState.KEYGEN)


def unfreeze(election: Election) -> None:
    """KEYGEN -> DRAFT, e.g. to replace a node that cannot generate its key.

    Keys the nodes already generated stay on them, unused.
    """
    _move(election, ElectionState.DRAFT)
    for q in election.questions:
        q.crypto_id = None
        q.params_key = None
        q.keys.clear()


def open_election(election: Election) -> None:
    _move(election, ElectionState.OPEN)
    election.opened_at = _now()


def close_election(election: Election) -> None:
    _move(election, ElectionState.CLOSED)
    election.closed_at = _now()


def start_count(election: Election) -> list[uuid.UUID]:
    """CLOSED -> MIXING. Returns the questions to enqueue a count for."""
    _move(election, ElectionState.MIXING)
    election.error = None
    for q in election.questions:
        q.tally = Tally(state=TallyState.PENDING)
    return [q.id for q in election.questions]


def retry_count(election: Election) -> list[uuid.UUID]:
    """Restarts the failed counts, and re-enqueues the pending ones. Returns both.

    A failed step is resubmitted to its node: the node keeps no output of a
    failed job, so a second try mixes the same input, which its one-mix-per-
    election rule allows.
    """
    _require(election, ElectionState.MIXING)
    election.error = None
    out = []
    for q in election.questions:
        t = q.tally
        if t is None or t.state is TallyState.DONE:
            continue
        if t.state is TallyState.FAILED:
            t.state, t.error = TallyState.PENDING, None
            for step in t.steps:
                if step.record_key is None:
                    step.job_id = None
        out.append(q.id)
    return out


def publish(election: Election) -> None:
    _move(election, ElectionState.PUBLISHED)
    election.published_at = _now()


# --- casting --------------------------------------------------------------------------------

_VOTER_ID = struct.Struct("<q")
_VOTER_ID_OFFSET = 20
"""Where evmlr's ballot encoding keeps the voter id (int64, little endian)."""


def ballot_voter_id(ballot: bytes) -> int:
    """The voter id a ballot claims, read without the library (for a quick early check)."""
    if len(ballot) < _VOTER_ID_OFFSET + _VOTER_ID.size:
        raise Invalid("not a ballot")
    return _VOTER_ID.unpack_from(ballot, _VOTER_ID_OFFSET)[0]


def _check_ballot(blobs: BlobStore, question: Question, voter: Voter, ballot: bytes) -> None:
    if ballot_voter_id(ballot) != voter.voter_id:
        raise Invalid(f"question {question.index}: the ballot was made for another voter")
    if question.params_key is None:  # cannot happen for an open election
        raise WrongState("the question has no public parameters")
    try:
        claimed = evmlr.check_ballot(blobs.get(question.params_key), ballot)
    except evmlr.EvmlrError as e:
        raise Invalid(f"question {question.index}: the ballot does not verify: {e.status.name}") from e
    if claimed != voter.voter_id:
        raise Invalid(f"question {question.index}: the ballot was made for another voter")


def cast_ballots(
    session: Session, blobs: BlobStore, election: Election, voter: Voter, ballots: Mapping[int, bytes]
) -> list[Ballot]:
    """Puts ``voter``'s ballots on the board, keyed by question index: all of them or none.

    The caller has authenticated ``voter`` and holds at least a shared lock on
    the election row, so the election cannot close in the middle. A voter may
    cast again: the last valid ballot of each question counts. Returns the
    board entries; a ballot's blob key (its SHA-256) is the voter's tracker.
    """
    _require(election, ElectionState.OPEN)
    if voter.election_id != election.id:
        raise Invalid("the voter belongs to another election")
    if not ballots:
        raise Invalid("no ballots")
    questions = {q.index: q for q in election.questions}
    unknown = sorted(set(ballots) - set(questions))
    if unknown:
        raise Invalid(f"no such question: {unknown[0]}")
    for index, ballot in sorted(ballots.items()):
        _check_ballot(blobs, questions[index], voter, ballot)
    rows = [
        Ballot(question_id=questions[index].id, voter_pk=voter.id, blob_key=blobs.put(ballot))
        for index, ballot in sorted(ballots.items())
    ]
    session.add_all(rows)
    session.flush()
    return rows


def cast_ballot(
    session: Session, blobs: BlobStore, election: Election, question: Question, voter: Voter, ballot: bytes
) -> Ballot:
    """:func:`cast_ballots` for one question."""
    if question.election_id != election.id:
        raise Invalid("the question belongs to another election")
    return cast_ballots(session, blobs, election, voter, {question.index: ballot})[0]


def board_ballots(session: Session, question_id: uuid.UUID) -> list[str]:
    """Blob keys of every ballot on the question's board, in board order."""
    return list(
        session.scalars(select(Ballot.blob_key).where(Ballot.question_id == question_id).order_by(Ballot.id))
    )
