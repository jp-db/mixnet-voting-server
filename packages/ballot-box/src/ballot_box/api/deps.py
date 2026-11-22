"""Dependencies shared by the routers."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..elections.service import Invalid, NotFound, WrongState
from ..services import Services
from ..tasks import TaskQueue


def _services(request: Request) -> Services:
    return request.app.state.services


def _queue(request: Request) -> TaskQueue:
    return request.app.state.queue


def _session(request: Request) -> Iterator[Session]:
    with request.app.state.services.db() as session:
        yield session


Svc = Annotated[Services, Depends(_services)]
Queue = Annotated[TaskQueue, Depends(_queue)]
Db = Annotated[Session, Depends(_session)]


def errors[T](fn: Callable[[], T]) -> T:
    """Runs ``fn``, turning the election service's errors into HTTP errors."""
    try:
        return fn()
    except NotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from None
    except WrongState as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    except Invalid as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
