"""Election management API, under ``/api/admin``.

Callers are logged-in accounts with a role, or scripts with the bearer
token ``BALLOT_BOX_ADMIN_TOKEN`` (administrator rights). Administrators may
do everything; managers (``can_manage``) may create elections and manage
those they are a manager of. Someone else's election answers 404, as if it
did not exist.

Lifecycle actions are ``POST /api/admin/elections/{id}/<action>``:
``freeze`` (DRAFT -> KEYGEN, queues key generation; in KEYGEN, retries it),
``unfreeze`` (KEYGEN -> DRAFT), ``open``, ``close``, ``count`` (CLOSED ->
MIXING, queues one count per question), ``retry-count`` and ``publish``.
"""

from __future__ import annotations

import hmac
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import current_identity, same_origin
from ..auth import password as password_auth
from ..auth.accounts import Principal, find_account, identity_key
from ..elections import service as svc_ops
from ..elections.service import VoterEntry
from ..mixnet.nodes import register_node
from ..models import Account, Election, ElectionManager, MixnetNode, Question, Tally, Voter
from ..questions import QuestionSpec
from .deps import Db, Queue, Svc, errors

_bearer = HTTPBearer(auto_error=False)


def principal(
    request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)], db: Db
) -> Principal:
    if credentials is not None:
        expected = request.app.state.settings.admin_token
        if expected is not None and hmac.compare_digest(
            credentials.credentials.encode(), expected.get_secret_value().encode()
        ):
            return Principal(account=None, is_admin=True, can_manage=True)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "wrong bearer token", {"WWW-Authenticate": "Bearer"}
        )
    identity = current_identity(request)
    if identity is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "log in first")
    account = find_account(db, identity)
    if account is None or not (account.is_admin or account.can_manage):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "this account may not manage elections")
    return Principal(account=account, is_admin=account.is_admin, can_manage=True)


Caller = Annotated[Principal, Depends(principal)]


def require_admin(caller: Caller) -> None:
    if not caller.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "administrators only")


def _managed(election_id: uuid.UUID, caller: Caller, db: Db) -> uuid.UUID:
    if not caller.manages(db, election_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such election")
    return election_id


ManagedId = Annotated[uuid.UUID, Depends(_managed)]
AdminOnly = [Depends(require_admin)]

router = APIRouter(
    prefix="/api/admin", dependencies=[Depends(principal), Depends(same_origin)], tags=["admin"]
)


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
    voter_id: str
    """Decimal: a 63-bit integer loses precision as a JSON number in JavaScript."""
    provider: str
    external_id: str
    email: str | None
    display_name: str | None

    @classmethod
    def of(cls, v: Voter) -> VoterOut:
        return cls(
            id=v.id,
            voter_id=str(v.voter_id),
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


@router.post(
    "/mixnet-nodes", response_model=MixnetNodeOut, status_code=status.HTTP_201_CREATED, dependencies=AdminOnly
)
def add_node(body: MixnetNodeIn, db: Db, svc: Svc) -> MixnetNodeOut:
    """Registers a node (or updates the URL and token of the node with that name)."""
    node = register_node(db, svc.sealer, body.name, body.url, body.token)
    db.commit()
    return MixnetNodeOut.of(node)


# --- elections ------------------------------------------------------------------------------


@router.get("/elections", response_model=list[ElectionSummary])
def list_elections(db: Db, caller: Caller) -> list[ElectionSummary]:
    query = select(Election).order_by(Election.created_at.desc())
    if not caller.is_admin:
        assert caller.account is not None  # noqa: S101 - only the token is an admin without an account
        query = query.join(ElectionManager).where(ElectionManager.account_id == caller.account.id)
    rows = db.scalars(query)
    return [
        ElectionSummary(id=e.id, title=e.title, state=e.state.value, created_at=e.created_at) for e in rows
    ]


@router.post("/elections", response_model=ElectionOut, status_code=status.HTTP_201_CREATED)
def create_election(body: ElectionIn, db: Db, caller: Caller) -> ElectionOut:
    election = errors(
        lambda: svc_ops.create_election(db, body.title, body.description, body.questions, body.mix_servers)
    )
    if caller.account is not None:
        election.managers.append(ElectionManager(account_id=caller.account.id))
    db.commit()
    return ElectionOut.of(db, election)


@router.get("/elections/{election_id}", response_model=ElectionOut)
def get_election(election_id: ManagedId, db: Db) -> ElectionOut:
    return ElectionOut.of(db, errors(lambda: svc_ops.get_election(db, election_id)))


@router.patch("/elections/{election_id}", response_model=ElectionOut)
def update_election(election_id: ManagedId, body: ElectionPatch, db: Db) -> ElectionOut:
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

    election = errors(run)
    db.commit()
    return ElectionOut.of(db, election)


@router.get("/elections/{election_id}/voters", response_model=list[VoterOut])
def list_voters(election_id: ManagedId, db: Db) -> list[VoterOut]:
    election = errors(lambda: svc_ops.get_election(db, election_id))
    return [VoterOut.of(v) for v in election.voters]


@router.post("/elections/{election_id}/voters", response_model=list[VoterOut])
def add_voters(election_id: ManagedId, body: VotersIn, db: Db) -> list[VoterOut]:
    """Adds voters to the roll (DRAFT only). Entries already on it come back unchanged."""
    entries = [VoterEntry(**v.model_dump()) for v in body.voters]
    voters = errors(lambda: svc_ops.add_voters(db, svc_ops.get_election(db, election_id, lock=True), entries))
    db.commit()
    return [VoterOut.of(v) for v in voters]


def _action(db: Db, election_id: uuid.UUID, op: Callable[[Election], Any]) -> tuple[Election, Any]:
    def run() -> tuple[Election, Any]:
        election = svc_ops.get_election(db, election_id, lock=True)
        return election, op(election)

    election, out = errors(run)
    db.commit()
    return election, out


@router.post("/elections/{election_id}/freeze", response_model=ElectionOut)
def freeze(election_id: ManagedId, db: Db, queue: Queue) -> ElectionOut:
    election, _ = _action(db, election_id, svc_ops.freeze)
    queue.keygen(election.id)
    return ElectionOut.of(db, election)


@router.post("/elections/{election_id}/unfreeze", response_model=ElectionOut)
def unfreeze(election_id: ManagedId, db: Db) -> ElectionOut:
    return ElectionOut.of(db, _action(db, election_id, svc_ops.unfreeze)[0])


@router.post("/elections/{election_id}/open", response_model=ElectionOut)
def open_election(election_id: ManagedId, db: Db) -> ElectionOut:
    return ElectionOut.of(db, _action(db, election_id, svc_ops.open_election)[0])


@router.post("/elections/{election_id}/close", response_model=ElectionOut)
def close_election(election_id: ManagedId, db: Db) -> ElectionOut:
    return ElectionOut.of(db, _action(db, election_id, svc_ops.close_election)[0])


@router.post("/elections/{election_id}/count", response_model=ElectionOut)
def count(election_id: ManagedId, db: Db, queue: Queue) -> ElectionOut:
    election, question_ids = _action(db, election_id, svc_ops.start_count)
    for qid in question_ids:
        queue.count(qid)
    return ElectionOut.of(db, election)


@router.post("/elections/{election_id}/retry-count", response_model=ElectionOut)
def retry_count(election_id: ManagedId, db: Db, queue: Queue) -> ElectionOut:
    election, question_ids = _action(db, election_id, svc_ops.retry_count)
    for qid in question_ids:
        queue.count(qid)
    return ElectionOut.of(db, election)


@router.post("/elections/{election_id}/publish", response_model=ElectionOut)
def publish(election_id: ManagedId, db: Db) -> ElectionOut:
    return ElectionOut.of(db, _action(db, election_id, svc_ops.publish)[0])


# --- managers, accounts, password logins ----------------------------------------------------


class ManagersIn(BaseModel):
    managers: list[str]
    """Identities, ``provider:external_id``. Each must have logged in once."""


@router.get("/elections/{election_id}/managers", response_model=list[str])
def list_managers(election_id: ManagedId, db: Db) -> list[str]:
    rows = db.scalars(select(Account).join(ElectionManager).where(ElectionManager.election_id == election_id))
    return sorted(identity_key(a.provider, a.external_id) for a in rows)


@router.put("/elections/{election_id}/managers", response_model=list[str])
def set_managers(election_id: ManagedId, body: ManagersIn, db: Db) -> list[str]:
    """Replaces the election's managers. Allowed in any state: it changes who administers, not the vote."""
    election = errors(lambda: svc_ops.get_election(db, election_id, lock=True))
    accounts = []
    for key in body.managers:
        provider, _, external_id = key.partition(":")
        account = db.scalar(
            select(Account).where(Account.provider == provider, Account.external_id == external_id)
        )
        if account is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{key} has never logged in")
        accounts.append(account)
    election.managers.clear()
    db.flush()
    election.managers.extend(ElectionManager(account_id=a.id) for a in {a.id: a for a in accounts}.values())
    db.commit()
    return list_managers(election_id, db)


class AccountOut(BaseModel):
    id: uuid.UUID
    identity: str
    email: str | None
    display_name: str | None
    is_admin: bool
    can_manage: bool
    last_login_at: datetime | None

    @classmethod
    def of(cls, a: Account) -> AccountOut:
        return cls(
            id=a.id,
            identity=identity_key(a.provider, a.external_id),
            email=a.email,
            display_name=a.display_name,
            is_admin=a.is_admin,
            can_manage=a.can_manage,
            last_login_at=a.last_login_at,
        )


class AccountPatch(BaseModel):
    is_admin: bool | None = None
    can_manage: bool | None = None


@router.get("/accounts", response_model=list[AccountOut], dependencies=AdminOnly)
def list_accounts(db: Db) -> list[AccountOut]:
    return [
        AccountOut.of(a) for a in db.scalars(select(Account).order_by(Account.provider, Account.external_id))
    ]


@router.patch("/accounts/{account_id}", response_model=AccountOut, dependencies=AdminOnly)
def update_account(account_id: uuid.UUID, body: AccountPatch, db: Db, caller: Caller) -> AccountOut:
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such account")
    if body.is_admin is False and caller.account is not None and caller.account.id == account.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "you cannot remove your own administrator role")
    if body.is_admin is not None:
        account.is_admin = body.is_admin
    if body.can_manage is not None:
        account.can_manage = body.can_manage
    db.commit()
    return AccountOut.of(account)


class PasswordLoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=150, pattern=r"^[^:\s]+$")
    password: str = Field(min_length=password_auth.MIN_LENGTH, max_length=1000)
    email: str | None = None
    display_name: str | None = None


@router.put("/password-logins", status_code=status.HTTP_204_NO_CONTENT, dependencies=AdminOnly)
def set_password_login(body: PasswordLoginIn, db: Db) -> None:
    """Creates a username-and-password login, or sets a new password for it."""
    password_auth.set_password(
        db, body.username, body.password, email=body.email, display_name=body.display_name
    )
    db.commit()
