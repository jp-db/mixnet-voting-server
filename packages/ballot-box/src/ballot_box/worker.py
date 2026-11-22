"""Celery application and tasks: ``celery -A ballot_box.worker worker``.

``keygen_task`` collects every mix server's key for every question of an
election and builds the parameters. ``count_task`` counts one question: it
does one step (:func:`ballot_box.elections.counting.advance_count`) and
enqueues itself again, at once or after ``mix_poll_seconds`` while a node
mixes. The questions of an election are counted in parallel.

Steps are idempotent and the state lives in the database, so a task that is
lost, redelivered or run twice does no harm. evmlr calls are serialised
within a process (see ``evmlr._api``), so scale with worker processes
(``--concurrency``), not threads.
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import Celery, Task

from .config import get_settings
from .elections.counting import Step, advance_count, fail_count
from .elections.keygen import run_keygen
from .mixnet import MixnetUnavailable
from .models import Election
from .services import Services

MAX_RETRIES = 30
"""Attempts to reach an unavailable node, with exponential backoff capped at 5 minutes."""


def make_celery() -> Celery:
    settings = get_settings()
    app = Celery("ballot_box", broker=settings.redis_url, backend=settings.redis_url)
    app.conf.update(
        task_acks_late=True,  # a crashed worker's task is redelivered
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,  # counting tasks are long; do not hoard them
        task_serializer="json",
        accept_content=["json"],
        task_ignore_result=True,  # state lives in the database
    )
    return app


app = make_celery()

_services: Services | None = None


def services() -> Services:
    global _services
    if _services is None:
        _services = Services.from_settings(get_settings())
    return _services


def _backoff(retries: int) -> float:
    return min(2.0**retries, 300.0)


@app.task(bind=True, max_retries=MAX_RETRIES)
def keygen_task(self: Task[Any, Any], election_id: str) -> None:
    svc = services()
    try:
        run_keygen(svc, uuid.UUID(election_id))
    except MixnetUnavailable as e:
        if self.request.retries >= MAX_RETRIES:
            with svc.db.begin() as s:
                election = s.get(Election, uuid.UUID(election_id), with_for_update=True)
                if election is not None:
                    election.error = f"key generation gave up: {e}"
            return
        raise self.retry(exc=e, countdown=_backoff(self.request.retries)) from e


@app.task(bind=True, max_retries=MAX_RETRIES)
def count_task(self: Task[Any, Any], question_id: str) -> None:
    svc = services()
    try:
        step = advance_count(svc, uuid.UUID(question_id))
    except MixnetUnavailable as e:
        if self.request.retries >= MAX_RETRIES:
            fail_count(svc, uuid.UUID(question_id), f"gave up reaching a mixnet node: {e}")
            return
        raise self.retry(exc=e, countdown=_backoff(self.request.retries)) from e
    if step is Step.CONTINUE:
        count_task.delay(question_id)
    elif step is Step.WAIT:
        count_task.apply_async((question_id,), countdown=svc.settings.mix_poll_seconds)
