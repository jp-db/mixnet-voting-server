"""How the web app hands work to the workers.

Enqueue only after the transaction that makes the work valid has committed:
a task that starts first would find nothing to do. If enqueuing fails after
the commit, the manager's retry endpoints enqueue again.
"""

from __future__ import annotations

import uuid
from typing import Protocol


class TaskQueue(Protocol):
    def keygen(self, election_id: uuid.UUID) -> None: ...

    def count(self, question_id: uuid.UUID) -> None: ...


class CeleryQueue:
    def keygen(self, election_id: uuid.UUID) -> None:
        from .worker import keygen_task

        keygen_task.delay(str(election_id))

    def count(self, question_id: uuid.UUID) -> None:
        from .worker import count_task

        count_task.delay(str(question_id))
