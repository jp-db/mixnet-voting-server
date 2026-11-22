"""Counting methods: valid answers to a result. Versioned like encodings.

Ranked methods (IRV, STV, ...) will come with the encodings for them.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from .encodings import Answer
from .spec import QuestionSpec

Method = Callable[[QuestionSpec, Sequence[Answer]], dict[str, Any]]


def choice_count_v1(spec: QuestionSpec, answers: Sequence[Answer]) -> dict[str, Any]:
    """How many votes chose each answer. Declaring winners (and breaking ties) is left to people."""
    counts = [0] * len(spec.answers)
    for answer in answers:
        for i in answer:
            counts[i] += 1
    return {
        "counts": counts,
        "blank": sum(1 for a in answers if not a),
        "valid": len(answers),
    }


METHODS: dict[str, Method] = {"choice_count/v1": choice_count_v1}
