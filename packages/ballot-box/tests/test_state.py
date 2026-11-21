import pytest

from ballot_box.elections.state import ElectionState as S
from ballot_box.elections.state import InvalidTransition, can_transition, transition


def test_happy_path():
    path = [S.DRAFT, S.KEYGEN, S.FROZEN, S.OPEN, S.CLOSED, S.MIXING, S.TALLIED, S.PUBLISHED]
    for current, target in zip(path, path[1:], strict=False):
        assert transition(current, target) is target


def test_no_reopening_or_skipping():
    assert not can_transition(S.CLOSED, S.OPEN)
    assert not can_transition(S.OPEN, S.TALLIED)
    assert not can_transition(S.FROZEN, S.DRAFT)
    with pytest.raises(InvalidTransition):
        transition(S.PUBLISHED, S.DRAFT)


def test_every_state_has_transitions_defined():
    for state in S:
        can_transition(state, S.DRAFT)  # raises KeyError if a state is missing
