import pytest
from pydantic import ValidationError

import evmlr
from ballot_box.questions import ENCODINGS, QuestionSpec, tally

SPEC = QuestionSpec(title="Q", answers=["a", "b", "c", "d"], min_choices=1, max_choices=2)
ENC = ENCODINGS["n_of_m/v1"]


def padded(coeffs: list[int]) -> list[int]:
    return coeffs + [0] * (evmlr.VOTE_LEN - len(coeffs))


@pytest.mark.parametrize("answer", [(0,), (3,), (1, 2), (0, 3)])
def test_round_trip(answer):
    assert ENC.decode(SPEC, padded(ENC.encode(SPEC, answer))) == answer


@pytest.mark.parametrize("answer", [(), (0, 1, 2), (4,), (1, 1)])
def test_encode_refuses_invalid_answers(answer):
    with pytest.raises(ValueError):
        ENC.encode(SPEC, answer)


@pytest.mark.parametrize(
    ("coeffs", "reason"),
    [
        ([2], "coefficient_not_0_or_1"),
        ([evmlr.MODULUS - 1], "coefficient_not_0_or_1"),
        ([0, 0, 0, 0, 1], "nonzero_padding"),
        ([1, 1, 1], "too_many_choices"),
        ([], "too_few_choices"),
    ],
)
def test_malformed_votes(coeffs, reason):
    result, discarded = tally(SPEC, [padded(coeffs), padded([1])])
    assert [(d.index, d.reason) for d in discarded] == [(0, reason)]
    assert result == {"counts": [1, 0, 0, 0], "blank": 0, "valid": 1, "discarded": 1}


def test_wrong_length_is_malformed():
    _, discarded = tally(SPEC, [[1]])
    assert discarded[0].reason == "wrong_length"


@pytest.mark.parametrize(
    "bad",
    [
        {"answers": []},
        {"answers": ["a", "a"]},
        {"answers": ["a", " "]},
        {"answers": ["a"], "max_choices": 2},
        {"answers": ["a", "b"], "min_choices": 2, "max_choices": 1},
        {"answers": [str(i) for i in range(evmlr.VOTE_LEN + 1)]},
        {"answers": ["a"], "encoding": "n_of_m/v9"},
        {"answers": ["a"], "extra": 1},
    ],
)
def test_spec_validation(bad):
    with pytest.raises(ValidationError):
        QuestionSpec(title="Q", **bad)


def test_vectors_file_is_current():
    """test-vectors/encodings.json is what the encoders produce (the booth's tests read it)."""
    from ballot_box.questions import vectors

    assert vectors.PATH.read_text() == vectors.render(), "run: python -m ballot_box.questions.vectors"
