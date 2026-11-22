"""The voter-facing API and the public bulletin board, under ``/api/elections``.

Public, once an election is frozen (earlier it does not exist here):

    ``GET /``                                          every public election, newest first
    ``GET /{id}``                                      the election, its questions and mix servers
    ``GET /{id}/questions/{i}/params``                 question i's public parameters (bytes)
    ``GET /{id}/questions/{i}/board``                  the board: every ballot cast, in order
    ``GET /{id}/questions/{i}/ballots/{tracker}``      one ballot (bytes)
    ``GET /{id}/trackers/{tracker}``                   find a ballot by its tracker

Public once the results are published:

    ``GET /{id}/results``                              each question's result, discards, mix steps
    ``GET /{id}/questions/{i}/transcript``             the counting transcript (bytes)
    ``GET /{id}/questions/{i}/mix-records/{j}``        server j's mix record (bytes)

For the logged-in voter:

    ``GET  /{id}/me``        whether they are on the roll, their voter id, what they cast
    ``POST /{id}/ballots``   cast: multipart, one part ``q<i>`` per question answered

Voter ids travel as decimal strings, as evmlr's 64-bit ids would need in
JavaScript; the ballot box keeps them below 2**31 (the WebAssembly limit).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import RequiredIdentity, same_origin
from ..auth.base import Identity
from ..elections import service as ops
from ..elections.state import ElectionState
from ..models import Ballot, Election, Question, Tally, TallyState, Voter
from ..questions import QuestionSpec
from ..services import Services
from .deps import Db, Svc, errors

router = APIRouter(prefix="/api/elections", tags=["voting"])

ElectionId = Annotated[uuid.UUID, Path()]
Index = Annotated[int, Path(ge=0)]
Tracker = Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")]

_PUBLIC = frozenset(ElectionState) - {ElectionState.DRAFT, ElectionState.KEYGEN}
_MAX_QUESTIONS_PER_CAST = 100
_IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}


def _election(db: Session, election_id: uuid.UUID) -> Election:
    election = db.get(Election, election_id)
    if election is None or election.state not in _PUBLIC:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such election")
    return election


def _question(election: Election, index: int) -> Question:
    if index >= len(election.questions):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such question")
    return election.questions[index]


def _published(election: Election) -> None:
    if election.state is not ElectionState.PUBLISHED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "the results are not published yet")


def _bytes(data: bytes, immutable: bool = True) -> Response:
    return Response(data, media_type="application/octet-stream", headers=_IMMUTABLE if immutable else None)


def _voter(db: Session, election: Election, identity: Identity) -> Voter | None:
    return db.scalar(
        select(Voter).where(
            Voter.election_id == election.id,
            Voter.provider == identity.provider,
            Voter.external_id == identity.external_id,
        )
    )


# --- schemas --------------------------------------------------------------------------------


class PublicKeyOut(BaseModel):
    position: int
    fingerprint: str


class PublicQuestion(BaseModel):
    index: int
    spec: QuestionSpec
    crypto_id: str
    params_key: str
    keys: list[PublicKeyOut]
    ballots: int
    """Ballots on the board, revotes included."""


class PublicMixServer(BaseModel):
    position: int
    node: str


class PublicElection(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    state: str
    opens_at: datetime | None
    """When voting is scheduled to open (it may open earlier)."""
    closes_at: datetime | None
    """When voting is scheduled to close; ballots are refused from then on."""
    opened_at: datetime | None
    closed_at: datetime | None
    published_at: datetime | None
    mix_servers: list[PublicMixServer]
    questions: list[PublicQuestion]
    voters: int


class BoardEntry(BaseModel):
    seq: int
    """Board order; the last valid ballot of a voter counts."""
    voter_id: str
    tracker: str
    cast_at: datetime


class BoardPage(BaseModel):
    entries: list[BoardEntry]
    next_after: int | None
    """Pass as ``after`` for the next page; null on the last page."""


class TrackerOut(BaseModel):
    question_index: int
    seq: int
    cast_at: datetime
    superseded: bool
    """A later ballot of the same voter for the same question replaces this one."""


class CastOut(BaseModel):
    question_index: int
    seq: int
    tracker: str
    cast_at: datetime


class MeOut(BaseModel):
    eligible: bool
    voter_id: str | None
    cast: list[CastOut]
    """The voter's latest ballot of each question."""


class QuestionResult(BaseModel):
    index: int
    state: str
    result: dict[str, Any] | None
    discarded: list[Any] | None
    transcript_key: str | None
    mix_steps: list[dict[str, Any]]


class ResultsOut(BaseModel):
    election_id: uuid.UUID
    questions: list[QuestionResult]


# --- public ---------------------------------------------------------------------------------


class ElectionListItem(BaseModel):
    id: uuid.UUID
    title: str
    state: str
    opens_at: datetime | None
    closes_at: datetime | None
    opened_at: datetime | None
    closed_at: datetime | None


@router.get("", response_model=list[ElectionListItem])
def list_elections(db: Db) -> list[ElectionListItem]:
    rows = db.scalars(
        select(Election).where(Election.state.in_(_PUBLIC)).order_by(Election.created_at.desc())
    )
    return [
        ElectionListItem(
            id=e.id,
            title=e.title,
            state=e.state.value,
            opens_at=ops.utc(e.opens_at),
            closes_at=ops.utc(e.closes_at),
            opened_at=e.opened_at,
            closed_at=e.closed_at,
        )
        for e in rows
    ]


@router.get("/{election_id}", response_model=PublicElection)
def get_election(election_id: ElectionId, db: Db) -> PublicElection:
    election = _election(db, election_id)
    counts = dict(
        db.execute(
            select(Ballot.question_id, func.count())
            .where(Ballot.question_id.in_([q.id for q in election.questions]))
            .group_by(Ballot.question_id)
        ).all()
    )
    voters = db.scalar(select(func.count()).select_from(Voter).where(Voter.election_id == election.id))
    return PublicElection(
        id=election.id,
        title=election.title,
        description=election.description,
        state=election.state.value,
        opens_at=ops.utc(election.opens_at),
        closes_at=ops.utc(election.closes_at),
        opened_at=election.opened_at,
        closed_at=election.closed_at,
        published_at=election.published_at,
        mix_servers=[PublicMixServer(position=m.position, node=m.node.name) for m in election.mix_servers],
        questions=[
            PublicQuestion(
                index=q.index,
                spec=QuestionSpec.model_validate(q.spec),
                crypto_id=(q.crypto_id or b"").hex(),
                params_key=q.params_key or "",
                keys=[PublicKeyOut(position=k.position, fingerprint=k.fingerprint) for k in q.keys],
                ballots=counts.get(q.id, 0),
            )
            for q in election.questions
        ],
        voters=voters or 0,
    )


@router.get("/{election_id}/questions/{index}/params", response_class=Response)
def get_params(election_id: ElectionId, index: Index, db: Db, svc: Svc) -> Response:
    question = _question(_election(db, election_id), index)
    if question.params_key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no parameters")
    return _bytes(svc.blobs.get(question.params_key))


@router.get("/{election_id}/questions/{index}/board", response_model=BoardPage)
def get_board(
    election_id: ElectionId,
    index: Index,
    db: Db,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> BoardPage:
    question = _question(_election(db, election_id), index)
    rows = db.execute(
        select(Ballot.id, Voter.voter_id, Ballot.blob_key, Ballot.cast_at)
        .join(Voter, Voter.id == Ballot.voter_pk)
        .where(Ballot.question_id == question.id, Ballot.id > after)
        .order_by(Ballot.id)
        .limit(limit + 1)
    ).all()
    entries = [BoardEntry(seq=r[0], voter_id=str(r[1]), tracker=r[2], cast_at=r[3]) for r in rows[:limit]]
    return BoardPage(entries=entries, next_after=entries[-1].seq if len(rows) > limit else None)


@router.get("/{election_id}/questions/{index}/ballots/{tracker}", response_class=Response)
def get_ballot(election_id: ElectionId, index: Index, tracker: Tracker, db: Db, svc: Svc) -> Response:
    question = _question(_election(db, election_id), index)
    found = db.scalar(
        select(Ballot.id).where(Ballot.question_id == question.id, Ballot.blob_key == tracker).limit(1)
    )
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such ballot on this board")
    return _bytes(svc.blobs.get(tracker))


@router.get("/{election_id}/trackers/{tracker}", response_model=TrackerOut)
def find_tracker(election_id: ElectionId, tracker: Tracker, db: Db) -> TrackerOut:
    election = _election(db, election_id)
    by_id = {q.id: q.index for q in election.questions}
    ballot = db.scalar(
        select(Ballot)
        .where(Ballot.question_id.in_(by_id), Ballot.blob_key == tracker)
        .order_by(Ballot.id.desc())
        .limit(1)
    )
    if ballot is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no ballot with this tracker")
    later = db.scalar(
        select(func.count())
        .select_from(Ballot)
        .where(
            Ballot.question_id == ballot.question_id,
            Ballot.voter_pk == ballot.voter_pk,
            Ballot.id > ballot.id,
        )
    )
    return TrackerOut(
        question_index=by_id[ballot.question_id],
        seq=ballot.id,
        cast_at=ballot.cast_at,
        superseded=bool(later),
    )


# --- published results ----------------------------------------------------------------------


@router.get("/{election_id}/results", response_model=ResultsOut)
def get_results(election_id: ElectionId, db: Db) -> ResultsOut:
    election = _election(db, election_id)
    _published(election)
    out = []
    for q in election.questions:
        t: Tally | None = q.tally
        out.append(
            QuestionResult(
                index=q.index,
                state=t.state.value if t else TallyState.PENDING.value,
                result=t.result if t else None,
                discarded=t.discarded if t else None,
                transcript_key=t.transcript_key if t else None,
                mix_steps=[
                    {"position": s.position, "record_key": s.record_key, "info": s.info}
                    for s in (t.steps if t else [])
                ],
            )
        )
    return ResultsOut(election_id=election.id, questions=out)


@router.get("/{election_id}/questions/{index}/transcript", response_class=Response)
def get_transcript(election_id: ElectionId, index: Index, db: Db, svc: Svc) -> Response:
    election = _election(db, election_id)
    _published(election)
    t = _question(election, index).tally
    if t is None or t.transcript_key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no transcript")
    return _bytes(svc.blobs.get(t.transcript_key))


@router.get("/{election_id}/questions/{index}/mix-records/{position}", response_class=Response)
def get_mix_record(
    election_id: ElectionId, index: Index, position: Annotated[int, Path(ge=0)], db: Db, svc: Svc
) -> Response:
    election = _election(db, election_id)
    _published(election)
    t = _question(election, index).tally
    step = next((s for s in (t.steps if t else []) if s.position == position), None)
    if step is None or step.record_key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such mix record")
    return _bytes(svc.blobs.get(step.record_key))


# --- the voter ------------------------------------------------------------------------------


def _latest_casts(db: Session, election: Election, voter: Voter) -> list[CastOut]:
    index_of = {q.id: q.index for q in election.questions}
    latest = (
        select(Ballot.question_id, func.max(Ballot.id).label("last"))
        .where(Ballot.voter_pk == voter.id)
        .group_by(Ballot.question_id)
        .subquery()
    )
    rows = db.scalars(select(Ballot).join(latest, Ballot.id == latest.c.last).order_by(Ballot.id))
    return sorted(
        (
            CastOut(question_index=index_of[b.question_id], seq=b.id, tracker=b.blob_key, cast_at=b.cast_at)
            for b in rows
        ),
        key=lambda c: c.question_index,
    )


@router.get("/{election_id}/me", response_model=MeOut)
def me(election_id: ElectionId, identity: RequiredIdentity, db: Db) -> MeOut:
    election = _election(db, election_id)
    voter = _voter(db, election, identity)
    if voter is None:
        return MeOut(eligible=False, voter_id=None, cast=[])
    return MeOut(eligible=True, voter_id=str(voter.voter_id), cast=_latest_casts(db, election, voter))


async def read_ballots(request: Request, limit: int) -> dict[int, bytes]:
    """The ``q<i>`` parts, each at most ``limit`` bytes.

    Also put a request size limit in the reverse proxy: this checks the
    declared length, but a chunked upload has none.
    """
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit * _MAX_QUESTIONS_PER_CAST:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "request too large")
    form = await request.form(max_files=_MAX_QUESTIONS_PER_CAST, max_fields=0)
    ballots: dict[int, bytes] = {}
    for name, value in form.multi_items():
        if not (name.startswith("q") and name[1:].isdigit()) or isinstance(value, str):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"unexpected part {name!r}")
        index = int(name[1:])
        if index in ballots:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"two ballots for question {index}")
        data = await value.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE, f"the ballot for question {index} is too large"
            )
        ballots[index] = data
    return ballots


@router.post(
    "/{election_id}/ballots",
    response_model=list[CastOut],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(same_origin)],
)
async def cast(election_id: ElectionId, request: Request, identity: RequiredIdentity) -> list[CastOut]:
    """Casts the voter's ballots, one multipart part ``q<i>`` per question: all or none.

    Answering only some questions is allowed. Casting again replaces the
    earlier ballot of each question sent (the last valid one counts).
    """
    svc: Services = request.app.state.services
    ballots = await read_ballots(request, svc.settings.max_ballot_bytes)
    return await run_in_threadpool(_cast, svc, election_id, identity, ballots)


def _cast(
    svc: Services, election_id: uuid.UUID, identity: Identity, ballots: dict[int, bytes]
) -> list[CastOut]:
    with svc.db() as db:

        def run() -> list[Ballot]:
            # A shared lock: casts run side by side, but the election cannot close meanwhile.
            election = ops.get_election(db, election_id, shared=True)
            if election.state not in _PUBLIC:
                raise ops.NotFound("no such election")
            voter = _voter(db, election, identity)
            if voter is None:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "you are not on this election's voter roll")
            return ops.cast_ballots(db, svc.blobs, election, voter, ballots)

        rows = errors(run)
        db.commit()
        index_of = {r.question_id: i for i, r in zip(sorted(ballots), rows, strict=True)}
        return [
            CastOut(question_index=index_of[r.question_id], seq=r.id, tracker=r.blob_key, cast_at=r.cast_at)
            for r in rows
        ]
