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
from ..models import (
    Account,
    Ballot,
    Election,
    ElectionManager,
    ElectionMixServer,
    MixnetNode,
    PasswordCredential,
    Question,
    Tally,
    TallyState,
    Voter,
)
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


def utc(when: datetime | None) -> datetime | None:
    """``when`` in UTC. A naive time (SQLite gives them back) is taken as UTC already."""
    if when is None:
        return None
    return when.replace(tzinfo=UTC) if when.tzinfo is None else when.astimezone(UTC)


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


def _resolve_nodes(
    session: Session, names: Sequence[str] | None, *, allow_none: bool = False
) -> list[MixnetNode]:
    if names is None:
        nodes = default_nodes(session)
        if not nodes and not allow_none:
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


def set_mix_servers(
    session: Session, election: Election, names: Sequence[str] | None, *, allow_none: bool = False
) -> None:
    """The mix chain, in order. ``None`` picks the default nodes in their default order
    (``allow_none``: an empty chain if there are none, to be set before freezing)."""
    _require(election, ElectionState.DRAFT)
    nodes = _resolve_nodes(session, names, allow_none=allow_none)
    election.mix_servers.clear()
    session.flush()  # the old rows' primary keys are reused
    election.mix_servers.extend(ElectionMixServer(position=j, node=n) for j, n in enumerate(nodes))


def set_questions(election: Election, specs: Sequence[QuestionSpec]) -> None:
    """Replaces the questions. A draft may have none yet; freezing needs at least one."""
    _require(election, ElectionState.DRAFT)
    election.questions.clear()
    election.questions.extend(
        Question(index=i, spec=spec.model_dump(mode="json")) for i, spec in enumerate(specs)
    )


def create_election(
    session: Session,
    title: str,
    description: str,
    questions: Sequence[QuestionSpec] = (),
    mix_servers: Sequence[str] | None = None,
) -> Election:
    """A new draft. Questions can come later; the mix chain defaults to the default nodes."""
    election = Election(title=title, description=description, state=ElectionState.DRAFT)
    session.add(election)
    set_questions(election, questions)
    set_mix_servers(session, election, mix_servers, allow_none=True)
    session.flush()
    return election


def clone_election(session: Session, source: Election, title: str | None = None) -> Election:
    """A new draft with ``source``'s configuration, in any state of ``source``.

    It copies the description, questions, mix chain, managers, the open-to-all
    setting and the roll as entered (voters added at freeze for an election
    open to all are not copied: the clone adds the accounts registered at its
    own freeze). Voters get new random ids. The schedule, keys, ballots and
    results are not copied.
    """
    clone = Election(
        title=title or f"Copy of {source.title}"[:200],
        description=source.description,
        state=ElectionState.DRAFT,
        open_to_all=source.open_to_all,
    )
    session.add(clone)
    clone.questions.extend(Question(index=q.index, spec=dict(q.spec)) for q in source.questions)
    clone.mix_servers.extend(
        ElectionMixServer(position=m.position, node_id=m.node_id) for m in source.mix_servers
    )
    clone.managers.extend(ElectionManager(account_id=m.account_id) for m in source.managers)
    session.flush()
    add_voters(
        session,
        clone,
        [
            VoterEntry(v.provider, v.external_id, v.email, v.display_name)
            for v in source.voters
            if not v.from_registry
        ],
    )
    return clone


def update_election(
    session: Session,
    election: Election,
    *,
    title: str | None = None,
    description: str | None = None,
    questions: Sequence[QuestionSpec] | None = None,
    mix_servers: Sequence[str] | None = None,
    default_mix_servers: bool = False,
    open_to_all: bool | None = None,
) -> None:
    _require(election, ElectionState.DRAFT)
    if open_to_all is not None:
        election.open_to_all = open_to_all
    if title is not None:
        election.title = title
    if description is not None:
        election.description = description
    if questions is not None:
        set_questions(election, questions)
    if mix_servers is not None or default_mix_servers:
        set_mix_servers(session, election, mix_servers)


# --- the voting schedule --------------------------------------------------------------------


def set_schedule(
    election: Election, opens_at: datetime | None, closes_at: datetime | None, now: datetime | None = None
) -> None:
    """When the scheduler opens and closes the election (None: by hand).

    In DRAFT and KEYGEN anything goes. Once frozen the times are public, so
    they may only move in the voters' favour or to end early: the opening
    can be brought forward (not postponed or removed), and until the
    election closes the closing can be moved to any future time (an
    extension, or an earlier end) or removed. Opening or closing at once
    is the ``open`` and ``close`` actions.
    """
    now = now or _now()
    opens_at, closes_at = utc(opens_at), utc(closes_at)
    state = election.state
    if state not in (ElectionState.DRAFT, ElectionState.KEYGEN, ElectionState.FROZEN, ElectionState.OPEN):
        raise WrongState(f"the election is {state.value}: its voting period is over")
    current_open = utc(election.opens_at)
    if opens_at != current_open:
        if state is ElectionState.OPEN:
            raise WrongState("the election is already open")
        if state is ElectionState.FROZEN and (
            opens_at is None or (current_open is not None and opens_at > current_open)
        ):
            raise Invalid("once frozen, the opening can only be brought forward")
    if closes_at != utc(election.closes_at) and closes_at is not None and closes_at <= now:
        raise Invalid("the closing time must be in the future: to close now, close the election")
    start = opens_at if state is not ElectionState.OPEN else utc(election.opened_at)
    if closes_at is not None and start is not None and closes_at <= start:
        raise Invalid("the election must close after it opens")
    election.opens_at, election.closes_at = opens_at, closes_at


def voting_closed(election: Election, now: datetime | None = None) -> bool:
    """The scheduled closing time has passed (whether or not the scheduler has closed it yet)."""
    closes_at = utc(election.closes_at)
    return closes_at is not None and (now or _now()) >= closes_at


@dataclass(frozen=True)
class ScheduleRun:
    opened: list[uuid.UUID]
    closed: list[uuid.UUID]


def run_schedule(session: Session, now: datetime | None = None) -> ScheduleRun:
    """Opens the frozen elections whose opening time has come and closes the
    open ones whose closing time has. Idempotent: the scheduler calls it every
    few seconds. A frozen election whose closing time passed before it could
    open (a long key generation) stays frozen, with an error for its manager.
    """
    now = now or _now()
    opened, closed = [], []
    due = select(Election.id).where(
        ((Election.state == ElectionState.FROZEN) & (Election.opens_at <= now))
        | ((Election.state == ElectionState.OPEN) & (Election.closes_at <= now))
    )
    for election_id in list(session.scalars(due)):
        election = get_election(session, election_id, lock=True)  # the state may have moved meanwhile
        if election.state is ElectionState.FROZEN and (utc(election.opens_at) or now) <= now:
            if voting_closed(election, now):
                election.error = "the closing time passed before the election could open: reschedule it"
                continue
            open_election(election)
            opened.append(election.id)
        if election.state is ElectionState.OPEN and voting_closed(election, now):
            close_election(election)
            closed.append(election.id)
    session.flush()
    return ScheduleRun(opened=opened, closed=closed)


# --- the voter roll -------------------------------------------------------------------------


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


def add_voters(
    session: Session, election: Election, entries: Sequence[VoterEntry], *, from_registry: bool = False
) -> list[Voter]:
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
                from_registry=from_registry,
            )
            election.voters.append(voter)
            existing[key] = voter
        out.append(voter)
    session.flush()
    return out


def registered_identities(session: Session) -> list[VoterEntry]:
    """Everyone registered: every account (whoever has logged in, or was named a
    manager) and every password login, even one not used yet."""
    entries = {
        (a.provider, a.external_id): VoterEntry(a.provider, a.external_id, a.email, a.display_name)
        for a in session.scalars(select(Account))
    }
    for c in session.scalars(select(PasswordCredential)):
        entries.setdefault(
            ("password", c.username), VoterEntry("password", c.username, c.email, c.display_name)
        )
    return [entries[k] for k in sorted(entries)]


# --- lifecycle ------------------------------------------------------------------------------


def freeze(session: Session, election: Election, now: datetime | None = None) -> None:
    """DRAFT -> KEYGEN: fixes each question's evmlr election id and, for an
    election open to all, adds every registered identity to the roll. Then
    enqueue key generation.

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
    if voting_closed(election, now):
        raise Invalid("the closing time has passed: reschedule it first")
    if election.open_to_all:
        add_voters(session, election, registered_identities(session), from_registry=True)
    if not election.voters:
        raise Invalid("the voter roll is empty")
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
    election.voters[:] = [v for v in election.voters if not v.from_registry]


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
    if voting_closed(election):
        raise WrongState("voting has closed")
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


def check_test_ballots(blobs: BlobStore, election: Election, ballots: Mapping[int, bytes]) -> dict[int, int]:
    """Checks ballots as casting would, without putting them anywhere: for the
    manager's booth simulation. Returns the voter id each one claims."""
    if election.state in (ElectionState.DRAFT, ElectionState.KEYGEN):
        raise WrongState("the election has no public parameters until its keys are in")
    questions = {q.index: q for q in election.questions}
    out = {}
    for index, ballot in sorted(ballots.items()):
        question = questions.get(index)
        if question is None:
            raise Invalid(f"no such question: {index}")
        try:
            out[index] = evmlr.check_ballot(blobs.get(question.params_key or ""), ballot)
        except evmlr.EvmlrError as e:
            raise Invalid(f"question {index}: the ballot does not verify: {e.status.name}") from e
    return out


def board_ballots(session: Session, question_id: uuid.UUID) -> list[str]:
    """Blob keys of every ballot on the question's board, in board order."""
    return list(
        session.scalars(select(Ballot.blob_key).where(Ballot.question_id == question_id).order_by(Ballot.id))
    )
