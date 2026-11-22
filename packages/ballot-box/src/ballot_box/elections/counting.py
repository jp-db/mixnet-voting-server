"""Counting one question: server 0's batch, each node's mix in turn, then verification.

:func:`advance_count` does one step and says what should happen next; the
worker task (:func:`ballot_box.worker.count_task`) calls it again, at once
or after a delay, until the count is done or failed. Every step is safe to
repeat: blobs are content-addressed, and a node returns the existing job
when the same inputs are submitted again.

Before the result is accepted the ballot box runs the public verification
(``evmlr.verify``) over the whole board and every mix record, exactly as any
observer can afterwards. It cannot check each node's proof as soon as it
arrives yet: the C API only verifies the full chain. A bad record therefore
shows up at the end, as a failed count, rather than at the next node.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

import evmlr

from ..mixnet import MixnetError
from ..models import Election, ElectionMixServer, MixStep, Question, Tally, TallyState
from ..questions import QuestionSpec, tally
from ..services import Services
from .service import board_ballots
from .state import ElectionState


class Step(enum.Enum):
    CONTINUE = "continue"
    """Call again now."""
    WAIT = "wait"
    """A node is mixing: call again after a while."""
    DONE = "done"
    FAILED = "failed"
    IDLE = "idle"
    """Nothing to do (no pending count for this question)."""


def _fail(tally_row: Tally, message: str) -> Step:
    tally_row.state, tally_row.error = TallyState.FAILED, message
    return Step.FAILED


def fail_count(svc: Services, question_id: uuid.UUID, message: str) -> None:
    """Marks a pending count failed (e.g. a node stayed unreachable)."""
    with svc.db.begin() as s:
        t = s.get(Tally, question_id, with_for_update=True)
        if t is not None and t.state is TallyState.PENDING:
            _fail(t, message)


def advance_count(svc: Services, question_id: uuid.UUID) -> Step:
    """One step of the question's count.

    Raises :class:`ballot_box.mixnet.MixnetUnavailable` when a node cannot be
    reached; nothing is changed then, so the caller retries later.
    """
    with svc.db.begin() as s:
        t = s.get(Tally, question_id, with_for_update=True)
        if t is None or t.state is not TallyState.PENDING:
            return Step.IDLE
        q = s.get_one(Question, question_id)
        if q.params_key is None or q.crypto_id is None:
            return _fail(t, "the question has no public parameters")
        pp = svc.blobs.get(q.params_key)
        try:
            if t.first_batch_key is None:
                ballots = [svc.blobs.get(k) for k in board_ballots(s, q.id)]
                t.first_batch_key = svc.blobs.put(evmlr.first_batch(pp, ballots))
                return Step.CONTINUE
            servers = q.election.mix_servers
            done = [st for st in t.steps if st.record_key is not None]
            if len(done) < len(servers):
                return _mix_step(svc, t, q, pp, servers[len(done)])
            step = _verify_and_tally(s, svc, t, q, pp)
        except evmlr.EvmlrError as e:
            return _fail(t, f"evmlr {e.operation}: {e.status.name}")
        except MixnetError as e:
            return _fail(t, str(e))
        _finish_election_if_counted(s, q.election_id)
        return step


def _mix_step(svc: Services, t: Tally, q: Question, pp: bytes, server: ElectionMixServer) -> Step:
    j = server.position
    step = next((st for st in t.steps if st.position == j), None)
    if step is None:
        prev = t.steps[j - 1] if j > 0 else None
        input_key = prev.output_batch_key if prev is not None else t.first_batch_key
        assert input_key is not None  # noqa: S101 - the previous step is done
        step = MixStep(
            position=j,
            node_id=server.node_id,
            input_batch_key=input_key,
            prev_record_key=prev.record_key if prev is not None else None,
        )
        t.steps.append(step)

    client = svc.connect(server.node)
    assert q.crypto_id is not None  # noqa: S101 - checked by the caller
    if step.job_id is None:
        job = client.submit_mix(
            q.crypto_id,
            pp,
            svc.blobs.get(step.input_batch_key),
            svc.blobs.get(step.prev_record_key) if step.prev_record_key else None,
        )
        step.job_id = job.job_id
    else:
        job = client.get_job(step.job_id)

    if job.state == "pending":
        return Step.WAIT
    if job.state == "failed":
        error = job.error or {}
        if error.get("status") == "INTERRUPTED":
            step.job_id = None  # the node restarted mid-job: submit again
            return Step.CONTINUE
        return _fail(
            t, f"mix server {j} ({server.node.name}) failed: {error.get('status')}: {error.get('message')}"
        )

    batch, record = client.job_output(job, "batch"), client.job_output(job, "record")
    info = evmlr.mix_info(pp, record)
    if info.server != j:
        raise MixnetError(f"{server.node.name} returned the mix record of server {info.server}, not {j}")
    step.output_batch_key = svc.blobs.put(batch)
    step.record_key = svc.blobs.put(record)
    step.info = {
        "n_in": info.n_in,
        "n_out": info.n_out,
        "rejections": [{"index": r.index, "reason": r.reason.name} for r in info.rejections],
    }
    return Step.CONTINUE


def _verify_and_tally(s: Session, svc: Services, t: Tally, q: Question, pp: bytes) -> Step:
    records = [svc.blobs.get(st.record_key) for st in t.steps if st.record_key is not None]
    transcript = evmlr.transcript(pp, records)
    ballots = [svc.blobs.get(k) for k in board_ballots(s, q.id)]
    votes = evmlr.verify(pp, ballots, transcript)  # the public check, over the whole board
    result, discarded = tally(QuestionSpec.model_validate(q.spec), votes)
    t.transcript_key = svc.blobs.put(transcript)
    t.result = result
    t.discarded = [asdict(d) for d in discarded]
    t.state, t.error = TallyState.DONE, None
    return Step.DONE


def _finish_election_if_counted(s: Session, election_id: uuid.UUID) -> None:
    # Lock the election before looking at the tallies: two questions finishing
    # at once then take turns, and the second sees the first one's result.
    election = s.get_one(Election, election_id, with_for_update=True)
    if election.state is not ElectionState.MIXING:
        return
    states = s.scalars(
        select(Tally.state)
        .join(Question, Question.id == Tally.question_id)
        .where(Question.election_id == election_id)
    ).all()
    if len(states) == len(election.questions) and all(st is TallyState.DONE for st in states):
        election.state = ElectionState.TALLIED
        election.tallied_at = datetime.now(UTC)
