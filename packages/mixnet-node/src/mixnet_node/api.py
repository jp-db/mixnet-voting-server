"""HTTP API of a mixnet node. Every route except ``/health`` needs the bearer token.

Keys
    ``POST /elections/{election_id}/key``   generate this node's key (idempotent)
    ``GET  /elections/{election_id}/key``   its public key

Mixing (asynchronous: submit, poll, download)
    ``POST /elections/{election_id}/mix-jobs``   multipart: ``pp``, ``batch``, ``prev_record``
    ``GET  /mix-jobs/{job_id}``                  state, rejections, error
    ``GET  /mix-jobs/{job_id}/batch``            output batch, for the next server
    ``GET  /mix-jobs/{job_id}/record``           mix record, to publish

``election_id`` is the 32-byte election id in lowercase hex. Binary objects
travel as raw bytes (``application/octet-stream`` parts and responses); the
public key is small and comes back base64 in JSON.
"""

from __future__ import annotations

import base64
import hmac
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Path, Request, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from .jobs import BadInput, Job, JobNotDone, JobNotFound, MixConflict, MixService, NoKey
from .keystore import KeyNotFound, KeyStore, PublicKey

_bearer = HTTPBearer(auto_error=False)


def require_token(
    request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
) -> None:
    expected = request.app.state.settings.api_token.get_secret_value().encode()
    given = credentials.credentials.encode() if credentials is not None else b""
    if not hmac.compare_digest(given, expected):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "missing or wrong bearer token", {"WWW-Authenticate": "Bearer"}
        )


router = APIRouter(dependencies=[Depends(require_token)])

ElectionIdHex = Annotated[str, Path(pattern=r"^[0-9a-f]{64}$", description="32-byte election id, hex")]
JobId = Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")]


def _service(request: Request) -> MixService:
    return request.app.state.mix_service


def _keys(request: Request) -> KeyStore:
    return request.app.state.mix_service.keys


Service = Annotated[MixService, Depends(_service)]
Keys = Annotated[KeyStore, Depends(_keys)]


class PublicKeyOut(BaseModel):
    election_id: str
    public_key: str
    """Base64."""
    fingerprint: str
    """SHA-256 of the public key, hex."""

    @classmethod
    def of(cls, pk: PublicKey) -> PublicKeyOut:
        return cls(
            election_id=pk.election_id.hex(),
            public_key=base64.b64encode(pk.public_key).decode(),
            fingerprint=pk.fingerprint,
        )


class JobOut(BaseModel):
    job_id: str
    election_id: str
    state: str
    created_at: str
    updated_at: str
    inputs: dict[str, str | None]
    outputs: dict[str, str] | None
    info: dict[str, Any] | None
    error: dict[str, str] | None

    @classmethod
    def of(cls, job: Job) -> JobOut:
        return cls(**{**job.__dict__, "state": job.state.value})


@router.post("/elections/{election_id}/key", response_model=PublicKeyOut, tags=["keys"])
def create_key(election_id: ElectionIdHex, response: Response, keys: Keys) -> PublicKeyOut:
    """Generates this node's key pair for the election, or returns the existing one.

    201 when created, 200 when it already existed: a key is never replaced.
    """
    pk, created = keys.create(bytes.fromhex(election_id))
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return PublicKeyOut.of(pk)


@router.get("/elections/{election_id}/key", response_model=PublicKeyOut, tags=["keys"])
def get_key(election_id: ElectionIdHex, keys: Keys) -> PublicKeyOut:
    try:
        return PublicKeyOut.of(keys.public_key(bytes.fromhex(election_id)))
    except KeyNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no key for this election") from None


@router.post("/elections/{election_id}/mix-jobs", response_model=JobOut, tags=["mixing"])
async def submit_mix(
    election_id: ElectionIdHex,
    response: Response,
    pp: Annotated[UploadFile, File(description="public parameters")],
    batch: Annotated[UploadFile, File(description="input batch")],
    service: Service,
    prev_record: Annotated[
        UploadFile | None, File(description="server j-1's mix record; omit for j = 0")
    ] = None,
) -> JobOut:
    """Queues this node's mix of ``batch``. 202 when queued, 200 when the same job exists.

    409 if the election was already mixed (or is being mixed) with other inputs.
    """
    # Batches are large (about 100 KB per ballot); evmlr needs them in memory anyway.
    pp_bytes, batch_bytes = await pp.read(), await batch.read()
    prev_bytes = await prev_record.read() if prev_record is not None else None
    try:
        job, created = await run_in_threadpool(
            service.submit, bytes.fromhex(election_id), pp_bytes, batch_bytes, prev_bytes
        )
    except BadInput as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    except NoKey:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no key for this election") from None
    except MixConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    response.status_code = status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK
    return JobOut.of(job)


@router.get("/mix-jobs/{job_id}", response_model=JobOut, tags=["mixing"])
def get_job(job_id: JobId, service: Service) -> JobOut:
    try:
        return JobOut.of(service.get(job_id))
    except JobNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such job") from None


def _output(service: MixService, job_id: str, which: str) -> Response:
    try:
        data = service.output(job_id, which)
    except JobNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such job") from None
    except JobNotDone:
        raise HTTPException(status.HTTP_409_CONFLICT, "the job has not finished successfully") from None
    return Response(content=data, media_type="application/octet-stream")


@router.get("/mix-jobs/{job_id}/batch", tags=["mixing"], response_class=Response)
def get_job_batch(job_id: JobId, service: Service) -> Response:
    return _output(service, job_id, "batch")


@router.get("/mix-jobs/{job_id}/record", tags=["mixing"], response_class=Response)
def get_job_record(job_id: JobId, service: Service) -> Response:
    return _output(service, job_id, "record")
