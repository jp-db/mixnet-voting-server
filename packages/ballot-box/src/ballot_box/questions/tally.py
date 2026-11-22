"""Decoding a question's verified votes and counting them."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .encodings import ENCODINGS, Answer, Malformed
from .methods import METHODS
from .spec import QuestionSpec


@dataclass(frozen=True)
class Discarded:
    index: int
    """Position of the vote in the verified output (``evmlr.verify``)."""
    reason: str


def tally(spec: QuestionSpec, votes: Sequence[Sequence[int]]) -> tuple[dict[str, Any], list[Discarded]]:
    """The result of the valid votes, and the malformed ones left out.

    A malformed vote never stops the count: it is discarded and reported.
    """
    encoding, method = ENCODINGS[spec.encoding], METHODS[spec.method]
    answers: list[Answer] = []
    discarded: list[Discarded] = []
    for i, vote in enumerate(votes):
        try:
            answers.append(encoding.decode(spec, vote))
        except Malformed as e:
            discarded.append(Discarded(i, e.reason))
    result = method(spec, answers)
    result["discarded"] = len(discarded)
    return result, discarded
