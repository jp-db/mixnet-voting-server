"""Test vectors for the encodings, shared with the booth's TypeScript encoder.

``python -m ballot_box.questions.vectors`` rewrites ``test-vectors/encodings.json``
at the repository root. Both test suites check their encoder against that file,
so the two stay identical. Regenerate it only when adding an encoding or cases:
a released encoding never changes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import evmlr

from .encodings import ENCODINGS
from .spec import QuestionSpec

PATH = Path(__file__).resolve().parents[5] / "test-vectors" / "encodings.json"

_SPECS: list[dict[str, Any]] = [
    {"title": "One of three", "answers": ["a", "b", "c"], "min_choices": 1, "max_choices": 1},
    {"title": "Up to two of four", "answers": ["a", "b", "c", "d"], "min_choices": 0, "max_choices": 2},
    {"title": "Two or three of five", "answers": list("abcde"), "min_choices": 2, "max_choices": 3},
]
_ANSWERS: list[tuple[int, ...]] = [(), (0,), (2,), (0, 1), (1, 3), (0, 2, 4), (4, 3), (1, 1), (5,), (-1,)]
_DECODE: list[list[int]] = [
    [1],
    [0, 1, 0, 1],
    [1, 1, 1],
    [2],
    [0, 0, 0, 0, 0, 1],
    [evmlr.MODULUS - 1],
    [],
]


def _pad(coefficients: list[int]) -> list[int]:
    return coefficients + [0] * (evmlr.VOTE_LEN - len(coefficients))


def generate() -> dict[str, Any]:
    out: dict[str, Any] = {"vote_len": evmlr.VOTE_LEN, "modulus": evmlr.MODULUS, "encodings": {}}
    for name, encoding in ENCODINGS.items():
        cases = []
        for raw in _SPECS:
            spec = QuestionSpec.model_validate({**raw, "encoding": name})
            encode = []
            for answer in _ANSWERS:
                try:
                    encode.append({"answer": list(answer), "coefficients": encoding.encode(spec, answer)})
                except ValueError:
                    encode.append({"answer": list(answer), "error": True})
            decode = []
            for coefficients in _DECODE:
                try:
                    decode.append(
                        {
                            "coefficients": coefficients,
                            "answer": list(encoding.decode(spec, _pad(coefficients))),
                        }
                    )
                except ValueError as e:
                    decode.append({"coefficients": coefficients, "malformed": getattr(e, "reason", str(e))})
            cases.append({"spec": spec.model_dump(mode="json"), "encode": encode, "decode": decode})
        out["encodings"][name] = cases
    return out


def render() -> str:
    return json.dumps(generate(), indent=1) + "\n"


if __name__ == "__main__":
    PATH.parent.mkdir(exist_ok=True)
    PATH.write_text(render())
    print(f"wrote {PATH}")
