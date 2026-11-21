"""Celery application: ``celery -A ballot_box.worker worker``.

Counting is a chain of idempotent tasks, each keyed by
``(election_id, step, input_hash)`` so a retry stores the same blobs:

    build_first_batch -> mix(j=0) -> mix(1) -> ... -> mix(k-1) -> decode_and_tally -> verify

``mix(j)`` sends the batch to mixnet node j and waits for its output batch
and mix record; the ballot box checks each record before passing the batch
on. evmlr calls are serialised within a process (see ``evmlr._api``), so
scale with worker processes (``--concurrency``), not threads.
"""

from __future__ import annotations

from celery import Celery

from .config import get_settings


def make_celery() -> Celery:
    settings = get_settings()
    app = Celery("ballot_box", broker=settings.redis_url, backend=settings.redis_url)
    app.conf.update(
        task_acks_late=True,  # a crashed worker's task is redelivered
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,  # counting tasks are long; do not hoard them
        task_serializer="json",
        accept_content=["json"],
    )
    return app


app = make_celery()
