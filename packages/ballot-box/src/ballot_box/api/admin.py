"""Election management API, under ``/api/admin``.

Until user accounts and roles exist, every route needs the bearer token in
``BALLOT_BOX_ADMIN_TOKEN``; without one configured the API refuses all
requests.

Lifecycle actions are ``POST /api/admin/elections/{id}/<action>``:
``freeze`` (DRAFT -> KEYGEN, queues key generation; in KEYGEN, retries it),
``unfreeze`` (KEYGEN -> DRAFT), ``open``, ``close``, ``count`` (CLOSED ->
MIXING, queues one count per question), ``retry-count`` and ``publish``.
"""

from __future__ import annotations

import hmac
import uuid
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..elections import service as svc_ops
from ..elections.service import Invalid, NotFound, VoterEntry, WrongState
from ..mixnet.nodes import register_node
from ..models import Election, MixnetNode, Question, Tally, Voter
from ..questions import QuestionSpec
from ..services import Services
from ..tasks import TaskQueue

_bearer = HTTPBearer(auto_error=False)


def require_admin(
    request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
) -> None:
    expected = request.app.state.settings.admin_token
    if expected is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "the admin API is disabled (no admin token set)")
    given = credentials.credentials.encode() if credentials is not None else b""
    if not hmac.compare_digest(given, expected.get_secret_value().encode()):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "missing or wrong bearer token", {"WWW-Authenticate": "Bearer"}
        )


router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)], tags=["admin"])


def _services(request: Request) -> Services:
    return request.app.state.services


def _queue(request: Request) -> TaskQueue:
    return request.app.state.queue


def _session(request: Request) -> Iterator[Session]:
    with request.app.state.services.db() as session:
        yield session


Svc = Annotated[Services, Depends(_services)]
Queue = Annotated[TaskQueue, Depends(_queue)]
Db = Annotated[Session, Depends(_session)]


def _errors[T](fn: Callable[[], T]) -> T:
    try:
        return fn()
    except NotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from None
    except WrongState as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    except Invalid as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None


# --- schemas --------------------------------------------------------------------------------


class MixnetNodeIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(pattern=r"^https?://", max_length=500)
    token: str = Field(min_length=32)


class MixnetNodeOut(BaseModel):
    id: uuid.UUID
    name: str
    url: str
    is_default: bool
    default_position: int | None

    @classmethod
    def of(cls, n: MixnetNode) -> MixnetNodeOut:
        return cls(
            id=n.id, name=n.name, url=n.url, is_default=n.is_default, default_position=n.default_position
        )


class ElectionIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    questions: list[QuestionSpec] = Field(min_length=1)
    mix_servers: list[str] | None = None
    """Node names in mixing order. Omitted: the default nodes."""


class ElectionPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    questions: list[QuestionSpec] | None = Field(default=None, min_length=1)
    mix_servers: list[str] | None = None
    use_default_mix_servers: bool = False
    """Replace the mix chain with the default nodes."""


class VoterIn(BaseModel):
    provider: str = Field(min_length=1, max_length=50)
    external_id: str = Field(min_length=1, max_length=255)
    email: str | None = None
    display_name: str | None = None


class VotersIn(BaseModel):
    voters: list[VoterIn] = Field(min_length=1)


class VoterOut(BaseModel):
    id: uuid.UUID
    voter_id: int
    provider: str
    external_id: str
    email: str | None
    display_name: str | None

    @classmethod
    def of(cls, v: Voter) -> VoterOut:
        return cls(
            id=v.id,
            voter_id=v.voter_id,
            provider=v.provider,
            external_id=v.external_id,
            email=v.email,
            display_name=v.display_name,
        )


class MixServerOut(BaseModel):
    position: int
    node_id: uuid.UUID
    node: str


class KeyOut(BaseModel):
    position: int
    fingerprint: str


class MixStepOut(BaseModel):
    position: int
    job_id: str | None
    record_key: str | None
    output_batch_key: str | None
    info: dict[str, Any] | None


class TallyOut(BaseModel):
    state: str
    error: str | None
    result: dict[str, Any] | None
    discarded: list[Any] | None
    first_batch_key: str | None
    transcript_key: str | None
    steps: list[MixStepOut]

    @classmethod
    def of(cls, t: Tally) -> TallyOut:
        return cls(
            state=t.state.value,
            error=t.error,
            result=t.result,
            discarded=t.discarded,
            first_batch_key=t.first_batch_key,
            transcript_key=t.transcript_key,
            steps=[
                MixStepOut(
                    position=s.position,
                    job_id=s.job_id,
                    record_key=s.record_key,
                    output_batch_key=s.output_batch_key,
                    info=s.info,
                )
                for s in t.steps
            ],
        )


class QuestionOut(BaseModel):
    id: uuid.UUID
    index: int
    spec: QuestionSpec
    crypto_id: str | None
    """evmlr's election id for the question, hex."""
    params_key: str | None
    keys: list[KeyOut]
    tally: TallyOut | None

    @classmethod
    def of(cls, q: Question) -> QuestionOut:
        return cls(
            id=q.id,
            index=q.index,
            spec=QuestionSpec.model_validate(q.spec),
            crypto_id=q.crypto_id.hex() if q.crypto_id else None,
            params_key=q.params_key,
            keys=[KeyOut(position=k.position, fingerprint=k.fingerprint) for k in q.keys],
            tally=TallyOut.of(q.tally) if q.tally else None,
        )


class ElectionOut(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    state: str
    error: str | None
    created_at: datetime
    frozen_at: datetime | None
    opened_at: datetime | None
    closed_at: datetime | None
    tallied_at: datetime | None
    published_at: datetime | None
    mix_servers: list[MixServerOut]
    questions: list[QuestionOut]
    voter_count: int

    @classmethod
    def of(cls, session: Session, e: Election) -> ElectionOut:
        voters = session.scalar(select(func.count()).select_from(Voter).where(Voter.election_id == e.id))
        return cls(
            id=e.id,
            title=e.title,
            description=e.description,
            state=e.state.value,
            error=e.error,
            created_at=e.created_at,
            frozen_at=e.frozen_at,
            opened_at=e.opened_at,
            closed_at=e.closed_at,
            tallied_at=e.tallied_at,
            published_at=e.published_at,
            mix_servers=[
                MixServerOut(position=m.position, node_id=m.node_id, node=m.node.name) for m in e.mix_servers
            ],
            questions=[QuestionOut.of(q) for q in e.questions],
            voter_count=voters or 0,
        )


class ElectionSummary(BaseModel):
    id: uuid.UUID
    title: str
    state: str
    created_at: datetime


# --- mixnet nodes ---------------------------------------------------------------------------


@router.get("/mixnet-nodes", response_model=list[MixnetNodeOut])
def list_nodes(db: Db) -> list[MixnetNodeOut]:
    nodes = db.scalars(select(MixnetNode).order_by(MixnetNode.default_position, MixnetNode.name))
    return [MixnetNodeOut.of(n) for n in nodes]


@router.post("/mixnet-nodes", response_model=MixnetNodeOut, status_code=status.HTTP_201_CREATED)
def add_node(body: MixnetNodeIn, db: Db, svc: Svc) -> MixnetNodeOut:
    """Registers a node (or updates the URL and token of the node with that name)."""
    node = register_node(db, svc.sealer, body.name, body.url, body.token)
    db.commit()
    return MixnetNodeOut.of(node)


# --- elections ------------------------------------------------------------------------------


@router.get("/elections", response_model=list[ElectionSummary])
def list_elections(db: Db) -> list[ElectionSummary]:
    rows = db.scalars(select(Election).order_by(Election.created_at.desc()))
    return [
        ElectionSummary(id=e.id, title=e.title, state=e.state.value, created_at=e.created_at) for e in rows
    ]


@router.post("/elections", response_model=ElectionOut, status_code=status.HTTP_201_CREATED)
def create_election(body: ElectionIn, db: Db) -> ElectionOut:
    election = _errors(
        lambda: svc_ops.create_election(db, body.title, body.description, body.questions, body.mix_servers)
    )
    db.commit()
    return ElectionOut.of(db, election)


@router.get("/elections/{election_id}", response_model=ElectionOut)
def get_election(election_id: uuid.UUID, db: Db) -> ElectionOut:
    return ElectionOut.of(db, _errors(lambda: svc_ops.get_election(db, election_id)))


@router.patch("/elections/{election_id}", response_model=ElectionOut)
def update_election(election_id: uuid.UUID, body: ElectionPatch, db: Db) -> ElectionOut:
    def run() -> Election:
        election = svc_ops.get_election(db, election_id, lock=True)
        svc_ops.update_election(
            db,
            election,
            title=body.title,
            description=body.description,
            questions=body.questions,
            mix_servers=body.mix_servers,
            default_mix_servers=body.use_default_mix_servers,
        )
        return election

    election = _errors(run)
    db.commit()
    return ElectionOut.of(db, election)


@router.get("/elections/{election_id}/voters", response_model=list[VoterOut])
def list_voters(election_id: uuid.UUID, db: Db) -> list[VoterOut]:
    election = _errors(lambda: svc_ops.get_election(db, election_id))
    return [VoterOut.of(v) for v in election.voters]


@router.post("/elections/{election_id}/voters", response_model=list[VoterOut])
def add_voters(election_id: uuid.UUID, body: VotersIn, db: Db) -> list[VoterOut]:
    """Adds voters to the roll (DRAFT only). Entries already on it come back unchanged."""
    entries = [VoterEntry(**v.model_dump()) for v in body.voters]
    voters = _errors(
        lambda: svc_ops.add_voters(db, svc_ops.get_election(db, election_id, lock=True), entries)
    )
    db.commit()
    return [VoterOut.of(v) for v in voters]


def _action(db: Db, election_id: uuid.UUID, op: Callable[[Election], Any]) -> tuple[Election, Any]:
    def run() -> tuple[Election, Any]:
        election = svc_ops.get_election(db, election_id, lock=True)
        return election, op(election)

    election, out = _errors(run)
    db.commit()
    return election, out


@router.post("/elections/{election_id}/freeze", response_model=ElectionOut)
def freeze(election_id: uuid.UUID, db: Db, queue: Queue) -> ElectionOut:
    election, _ = _action(db, election_id, svc_ops.freeze)
    queue.keygen(election.id)
    return ElectionOut.of(db, election)


@router.post("/elections/{election_id}/unfreeze", response_model=ElectionOut)
def unfreeze(election_id: uuid.UUID, db: Db) -> ElectionOut:
    return ElectionOut.of(db, _action(db, election_id, svc_ops.unfreeze)[0])


@router.post("/elections/{election_id}/open", response_model=ElectionOut)
def open_election(election_id: uuid.UUID, db: Db) -> ElectionOut:
    return ElectionOut.of(db, _action(db, election_id, svc_ops.open_election)[0])


@router.post("/elections/{election_id}/close", response_model=ElectionOut)
def close_election(election_id: uuid.UUID, db: Db) -> ElectionOut:
    return ElectionOut.of(db, _action(db, election_id, svc_ops.close_election)[0])


@router.post("/elections/{election_id}/count", response_model=ElectionOut)
def count(election_id: uuid.UUID, db: Db, queue: Queue) -> ElectionOut:
    election, question_ids = _action(db, election_id, svc_ops.start_count)
    for qid in question_ids:
        queue.count(qid)
    return ElectionOut.of(db, election)


@router.post("/elections/{election_id}/retry-count", response_model=ElectionOut)
def retry_count(election_id: uuid.UUID, db: Db, queue: Queue) -> ElectionOut:
    election, question_ids = _action(db, election_id, svc_ops.retry_count)
    for qid in question_ids:
        queue.count(qid)
    return ElectionOut.of(db, election)


@router.post("/elections/{election_id}/publish", response_model=ElectionOut)
def publish(election_id: uuid.UUID, db: Db) -> ElectionOut:
    return ElectionOut.of(db, _action(db, election_id, svc_ops.publish)[0])
