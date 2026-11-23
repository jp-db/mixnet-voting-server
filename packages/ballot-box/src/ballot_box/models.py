"""The ballot box's database schema (SQLAlchemy 2.0, typed).

Large protocol objects (parameters, ballots, batches, mix records,
transcripts) live in the blob store; rows keep their SHA-256 keys.

An :class:`Election` is what voters and managers see. Each of its
:class:`Question` s is a separate election for evmlr, with its own election id
(``crypto_id``), parameters, node keys, mix chain and tally.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .elections.state import ElectionState


def _now() -> datetime:
    return datetime.now(UTC)


def _enum(cls: type[enum.Enum]) -> Enum:
    # Stored as their string values, not as a native enum type: adding a state
    # then needs no ALTER TYPE.
    return Enum(cls, native_enum=False, length=20, values_callable=lambda e: [m.value for m in e])


class Base(DeclarativeBase):
    type_annotation_map = {
        dict[str, Any]: JSON,
        list[Any]: JSON,
        bytes: LargeBinary,
        datetime: DateTime(timezone=True),
    }


# SQLite only auto-increments an INTEGER PRIMARY KEY; Postgres gets a bigint.
_BigIntPK = BigInteger().with_variant(Integer, "sqlite")


class MixnetNode(Base):
    """A mixnet node the ballot box can talk to."""

    __tablename__ = "mixnet_nodes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    url: Mapped[str] = mapped_column(String(500))
    token_sealed: Mapped[bytes]
    """The node's bearer token, sealed (see :mod:`ballot_box.sealing`)."""
    is_default: Mapped[bool] = mapped_column(default=False)
    default_position: Mapped[int | None]
    """Position in the default mixing order, for default nodes."""
    created_at: Mapped[datetime] = mapped_column(default=_now)


class Account(Base):
    """Someone who has logged in, with what they may manage. Voting needs no account role:
    the voter roll decides who votes."""

    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("provider", "external_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(50))
    external_id: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(320))
    display_name: Mapped[str | None] = mapped_column(String(200))
    is_admin: Mapped[bool] = mapped_column(default=False)
    """Everything: nodes, accounts, every election."""
    can_manage: Mapped[bool] = mapped_column(default=False)
    """May create elections, and manage those they are a manager of."""
    created_at: Mapped[datetime] = mapped_column(default=_now)
    last_login_at: Mapped[datetime | None]


class PasswordCredential(Base):
    """A login of the ``password`` provider. The username is the identity's external id."""

    __tablename__ = "password_credentials"

    username: Mapped[str] = mapped_column(String(150), primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    """argon2id, in PHC string format."""
    email: Mapped[str | None] = mapped_column(String(320))
    display_name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class ElectionManager(Base):
    __tablename__ = "election_managers"

    election_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("elections.id"), primary_key=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), primary_key=True)


class Election(Base):
    __tablename__ = "elections"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[ElectionState] = mapped_column(_enum(ElectionState), default=ElectionState.DRAFT)
    error: Mapped[str | None] = mapped_column(Text)
    """The last background failure (key generation, counting), for the manager."""
    created_at: Mapped[datetime] = mapped_column(default=_now)
    frozen_at: Mapped[datetime | None]
    opened_at: Mapped[datetime | None]
    closed_at: Mapped[datetime | None]
    tallied_at: Mapped[datetime | None]
    published_at: Mapped[datetime | None]

    opens_at: Mapped[datetime | None]
    """When the scheduler opens the election (once frozen). None: the manager opens it."""
    closes_at: Mapped[datetime | None]
    """When the scheduler closes it; casting stops then even if the scheduler is late."""
    open_to: Mapped[list[Any]] = mapped_column(default=list)
    """Login methods (``password``, a CAS service name) whose every user may vote,
    besides the roll: they join it themselves, even after the freeze."""

    questions: Mapped[list[Question]] = relationship(
        back_populates="election", order_by="Question.index", cascade="all, delete-orphan"
    )
    mix_servers: Mapped[list[ElectionMixServer]] = relationship(
        order_by="ElectionMixServer.position", cascade="all, delete-orphan"
    )
    managers: Mapped[list[ElectionManager]] = relationship(cascade="all, delete-orphan")
    voters: Mapped[list[Voter]] = relationship(
        back_populates="election",
        order_by="(Voter.provider, Voter.external_id)",
        cascade="all, delete-orphan",
    )


class ElectionMixServer(Base):
    """Server ``position`` of the election's mix chain (0 mixes first)."""

    __tablename__ = "election_mix_servers"
    __table_args__ = (UniqueConstraint("election_id", "node_id"),)

    election_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("elections.id"), primary_key=True)
    position: Mapped[int] = mapped_column(primary_key=True)
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mixnet_nodes.id"))

    node: Mapped[MixnetNode] = relationship(lazy="joined")


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (UniqueConstraint("election_id", "index"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    election_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("elections.id"))
    index: Mapped[int]
    spec: Mapped[dict[str, Any]]
    """A :class:`ballot_box.questions.QuestionSpec`, as JSON."""
    crypto_id: Mapped[bytes | None] = mapped_column(LargeBinary(32), unique=True)
    """evmlr's election id for this question; set when the election freezes."""
    params_key: Mapped[str | None] = mapped_column(String(64))
    """Blob key of the public parameters, once every node's key is in."""

    election: Mapped[Election] = relationship(back_populates="questions")
    keys: Mapped[list[QuestionKey]] = relationship(
        order_by="QuestionKey.position", cascade="all, delete-orphan"
    )
    tally: Mapped[Tally | None] = relationship(cascade="all, delete-orphan")


class QuestionKey(Base):
    """Mix server ``position``'s public key for one question."""

    __tablename__ = "question_keys"

    question_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("questions.id"), primary_key=True)
    position: Mapped[int] = mapped_column(primary_key=True)
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mixnet_nodes.id"))
    public_key: Mapped[bytes]
    fingerprint: Mapped[str] = mapped_column(String(64))
    """SHA-256 of the public key, hex: what the node and the board both show."""


class Voter(Base):
    """An entry of an election's voter roll."""

    __tablename__ = "voters"
    __table_args__ = (
        UniqueConstraint("election_id", "voter_id"),
        UniqueConstraint("election_id", "provider", "external_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    election_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("elections.id"))
    voter_id: Mapped[int] = mapped_column(BigInteger)
    """The id evmlr binds into this voter's ballots (1 to 2**31 - 1: the WebAssembly build's limit). Public:
    the board shows who cast, never how."""
    provider: Mapped[str] = mapped_column(String(50))
    external_id: Mapped[str] = mapped_column(String(255))
    """Matched against the :class:`ballot_box.auth.Identity` of whoever logs in."""
    email: Mapped[str | None] = mapped_column(String(320))
    display_name: Mapped[str | None] = mapped_column(String(200))
    self_enrolled: Mapped[bool] = mapped_column(default=False)
    """Joined by logging in, because the election is open to their login method
    (not put on the roll by a manager)."""

    election: Mapped[Election] = relationship(back_populates="voters")


class Ballot(Base):
    """A ballot on the bulletin board. ``id`` order is board order.

    Every accepted ballot stays on the board; evmlr counts the last valid one
    of each voter (``evmlr.first_batch``).
    """

    __tablename__ = "ballots"

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    question_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("questions.id"), index=True)
    voter_pk: Mapped[uuid.UUID] = mapped_column(ForeignKey("voters.id"))
    blob_key: Mapped[str] = mapped_column(String(64))
    """SHA-256 of the ballot: also the voter's receipt."""
    cast_at: Mapped[datetime] = mapped_column(default=_now)


class TallyState(enum.StrEnum):
    PENDING = "pending"
    """Counting: building server 0's batch, mixing, or verifying."""
    DONE = "done"
    FAILED = "failed"
    """Stopped; ``error`` says why. The manager can retry."""


class Tally(Base):
    """One question's count: the mix chain, its verification and the decoded result."""

    __tablename__ = "tallies"

    question_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("questions.id"), primary_key=True)
    state: Mapped[TallyState] = mapped_column(_enum(TallyState), default=TallyState.PENDING)
    error: Mapped[str | None] = mapped_column(Text)
    first_batch_key: Mapped[str | None] = mapped_column(String(64))
    transcript_key: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[dict[str, Any] | None]
    """What the question's counting method computed from the valid votes."""
    discarded: Mapped[list[Any] | None]
    """Malformed votes left out of the result: ``[{"index", "reason"}]``, where
    ``index`` is the vote's position in the verified output."""
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    steps: Mapped[list[MixStep]] = relationship(order_by="MixStep.position", cascade="all, delete-orphan")


class MixStep(Base):
    """Mix server ``position``'s step of one question's count."""

    __tablename__ = "mix_steps"

    question_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tallies.question_id"), primary_key=True)
    position: Mapped[int] = mapped_column(primary_key=True)
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mixnet_nodes.id"))
    job_id: Mapped[str | None] = mapped_column(String(64))
    """The node's job id; recomputable from the inputs, so a lost one is harmless."""
    input_batch_key: Mapped[str] = mapped_column(String(64))
    prev_record_key: Mapped[str | None] = mapped_column(String(64))
    output_batch_key: Mapped[str | None] = mapped_column(String(64))
    record_key: Mapped[str | None] = mapped_column(String(64))
    info: Mapped[dict[str, Any] | None]
    """What the mix record says: n_in, n_out, rejections."""
