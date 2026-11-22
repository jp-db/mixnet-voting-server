"""Encodings: answers to evmlr vote coefficients and back.

Each encoding is versioned (``name/vN``) and never changes once released:
a counted election must decode the same way forever. The booth's TypeScript
encoder must produce exactly what :meth:`Encoding.encode` produces here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import evmlr

from .spec import QuestionSpec

Answer = tuple[int, ...]
"""For n-out-of-m questions: the chosen answer indices, ascending."""


class Malformed(ValueError):
    """A decrypted vote that no voter using the booth could have produced."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Encoding(Protocol):
    name: str

    def encode(self, spec: QuestionSpec, answer: Answer) -> list[int]:
        """Raises ``ValueError`` if ``answer`` is not a valid answer to ``spec``."""
        ...

    def decode(self, spec: QuestionSpec, vote: Sequence[int]) -> Answer:
        """Raises :class:`Malformed` if ``vote`` is not an encoded answer to ``spec``."""
        ...


class NOfMV1:
    """Coefficient ``i`` is 1 if answer ``i`` is chosen, else 0; the rest are 0.

    Up to ``evmlr.VOTE_LEN`` (512) answers. A blank vote is all zeros.
    """

    name = "n_of_m/v1"

    def encode(self, spec: QuestionSpec, answer: Answer) -> list[int]:
        chosen = set(answer)
        if len(chosen) != len(answer):
            raise ValueError("an answer is chosen twice")
        if any(not 0 <= i < len(spec.answers) for i in chosen):
            raise ValueError("no such answer")
        if not spec.min_choices <= len(chosen) <= spec.max_choices:
            raise ValueError(f"choose between {spec.min_choices} and {spec.max_choices} answers")
        return [1 if i in chosen else 0 for i in range(len(spec.answers))]

    def decode(self, spec: QuestionSpec, vote: Sequence[int]) -> Answer:
        if len(vote) != evmlr.VOTE_LEN:
            raise Malformed("wrong_length")
        m = len(spec.answers)
        if any(c not in (0, 1) for c in vote[:m]):
            raise Malformed("coefficient_not_0_or_1")
        if any(vote[m:]):
            raise Malformed("nonzero_padding")
        answer = tuple(i for i in range(m) if vote[i])
        if len(answer) > spec.max_choices:
            raise Malformed("too_many_choices")
        if len(answer) < spec.min_choices:
            raise Malformed("too_few_choices")
        return answer


ENCODINGS: dict[str, Encoding] = {e.name: e for e in (NOfMV1(),)}
