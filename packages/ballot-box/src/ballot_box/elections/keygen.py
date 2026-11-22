"""Key generation: every mix server's key for every question, then the parameters.

Runs in a worker (:func:`ballot_box.worker.keygen_task`). Asking a node for
a key is idempotent, so the task can be retried at any point. Network calls
happen outside any transaction; the result is applied only if the election
is still in KEYGEN with the same questions and servers.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import evmlr

from ..mixnet import MixnetError, NodeKey
from ..models import Election, QuestionKey
from ..services import Services
from .state import ElectionState


def _snapshot(election: Election) -> tuple[list[tuple[uuid.UUID, bytes | None]], list[uuid.UUID]]:
    return (
        [(q.id, q.crypto_id) for q in election.questions],
        [ms.node_id for ms in election.mix_servers],
    )


def run_keygen(svc: Services, election_id: uuid.UUID) -> bool:
    """Returns whether the election is now FROZEN.

    Raises :class:`ballot_box.mixnet.MixnetUnavailable` when a node cannot be
    reached (retry later). A node that refuses is recorded in
    ``election.error`` and the election stays in KEYGEN.
    """
    with svc.db() as s:
        election = s.get(Election, election_id)
        if election is None or election.state is not ElectionState.KEYGEN:
            return False
        before = _snapshot(election)
        servers = [(ms.position, ms.node) for ms in election.mix_servers]
        questions = [(q.id, q.crypto_id) for q in election.questions]

    keys: dict[tuple[uuid.UUID, int], NodeKey] = {}
    try:
        for qid, cid in questions:
            if cid is None:
                raise MixnetError("a question has no election id")
            for position, node in servers:
                keys[qid, position] = svc.connect(node).create_key(cid)
    except MixnetError as e:
        with svc.db.begin() as s:
            election = s.get(Election, election_id, with_for_update=True)
            if election is not None and election.state is ElectionState.KEYGEN:
                election.error = f"key generation failed: {e}"
        return False

    with svc.db.begin() as s:
        election = s.get(Election, election_id, with_for_update=True)
        if election is None or election.state is not ElectionState.KEYGEN or _snapshot(election) != before:
            return False  # cancelled or reconfigured meanwhile
        nodes = {ms.position: ms.node_id for ms in election.mix_servers}
        for q in election.questions:
            assert q.crypto_id is not None  # noqa: S101 - checked above, same snapshot
            q.keys.clear()
            s.flush()
            row_keys = [keys[q.id, j] for j in range(len(servers))]
            q.keys.extend(
                QuestionKey(position=j, node_id=nodes[j], public_key=k.public_key, fingerprint=k.fingerprint)
                for j, k in enumerate(row_keys)
            )
            q.params_key = svc.blobs.put(evmlr.params(q.crypto_id, [k.public_key for k in row_keys]))
        election.state = ElectionState.FROZEN
        election.frozen_at = datetime.now(UTC)
        election.error = None
    return True
