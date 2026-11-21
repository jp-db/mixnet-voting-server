"""The election lifecycle as an explicit state machine.

Every action is allowed only in some states (adding voters before FROZEN,
casting only while OPEN, and so on). Transitions should happen under a row
lock on the election, and each one is recorded in the audit log.
"""

from __future__ import annotations

import enum


class ElectionState(enum.StrEnum):
    DRAFT = "draft"
    """Being configured: questions, voter roll, mixnet nodes."""
    KEYGEN = "keygen"
    """Waiting for each mixnet node to generate its key pair and send its public key."""
    FROZEN = "frozen"
    """Public parameters built and published; configuration can no longer change."""
    OPEN = "open"
    """Accepting ballots."""
    CLOSED = "closed"
    """No more ballots; the board is final."""
    MIXING = "mixing"
    """Mixnet nodes running in turn (a chain of queued tasks)."""
    TALLIED = "tallied"
    """Votes decoded and the transcript verified; awaiting the admin's release."""
    PUBLISHED = "published"
    """Results and the full transcript are public."""


_TRANSITIONS: dict[ElectionState, frozenset[ElectionState]] = {
    ElectionState.DRAFT: frozenset({ElectionState.KEYGEN}),
    ElectionState.KEYGEN: frozenset({ElectionState.FROZEN, ElectionState.DRAFT}),
    ElectionState.FROZEN: frozenset({ElectionState.OPEN}),
    ElectionState.OPEN: frozenset({ElectionState.CLOSED}),
    ElectionState.CLOSED: frozenset({ElectionState.MIXING}),
    ElectionState.MIXING: frozenset({ElectionState.TALLIED}),
    ElectionState.TALLIED: frozenset({ElectionState.PUBLISHED}),
    ElectionState.PUBLISHED: frozenset(),
}


class InvalidTransition(Exception):
    def __init__(self, current: ElectionState, target: ElectionState) -> None:
        super().__init__(f"cannot go from {current.value} to {target.value}")
        self.current = current
        self.target = target


def can_transition(current: ElectionState, target: ElectionState) -> bool:
    return target in _TRANSITIONS[current]


def transition(current: ElectionState, target: ElectionState) -> ElectionState:
    """Returns ``target`` if the move is allowed; raises :class:`InvalidTransition` otherwise."""
    if not can_transition(current, target):
        raise InvalidTransition(current, target)
    return target
