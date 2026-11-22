"""A question's specification, and the evmlr election id derived from it."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

import evmlr


class QuestionSpec(BaseModel):
    """An n-out-of-m question: choose between ``min_choices`` and ``max_choices`` answers.

    Other kinds (ranked, STV, ...) will be further models, told apart by
    ``kind``, once evmlr supports their votes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["n_of_m"] = "n_of_m"
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=5000)
    answers: list[str] = Field(min_length=1, max_length=evmlr.VOTE_LEN)
    min_choices: int = Field(default=0, ge=0)
    """0 allows a blank vote."""
    max_choices: int = Field(default=1, ge=1)
    encoding: Literal["n_of_m/v1"] = "n_of_m/v1"
    method: Literal["choice_count/v1"] = "choice_count/v1"

    @model_validator(mode="after")
    def _check(self) -> Self:
        if any(not a.strip() for a in self.answers):
            raise ValueError("answers must not be empty")
        if len(set(self.answers)) != len(self.answers):
            raise ValueError("answers must be distinct")
        if self.min_choices > self.max_choices:
            raise ValueError("min_choices must not exceed max_choices")
        if self.max_choices > len(self.answers):
            raise ValueError("max_choices must not exceed the number of answers")
        return self

    def canonical(self) -> bytes:
        """The spec as canonical JSON: what :func:`crypto_id` hashes."""
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()


_DOMAIN = b"mixnet-voting question v1\x00"


def crypto_id(election_id: uuid.UUID, index: int, spec: QuestionSpec) -> bytes:
    """evmlr's election id for question ``index`` of an election.

    It binds the election, the question's position and its whole spec
    (answers, encoding, counting method), so a ballot is valid only for the
    question its voter saw.
    """
    h = hashlib.sha256(_DOMAIN)
    h.update(election_id.bytes)
    h.update(index.to_bytes(4, "big"))
    h.update(spec.canonical())
    return h.digest()
