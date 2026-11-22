"""Client for one mixnet node's API (see ``mixnet_node.api``)."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Any

import httpx


class MixnetError(Exception):
    """The node refused the request (4xx) or answered something unexpected. Retrying will not help."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class MixnetUnavailable(Exception):
    """The node could not be reached, or failed (5xx). Retrying later may help."""


@dataclass(frozen=True)
class NodeKey:
    public_key: bytes
    fingerprint: str


@dataclass(frozen=True)
class NodeJob:
    job_id: str
    state: str
    """``pending``, ``done`` or ``failed``."""
    outputs: dict[str, str] | None
    info: dict[str, Any] | None
    error: dict[str, str] | None


class MixnetClient:
    """Wraps an ``httpx.Client`` whose base URL and bearer token are the node's."""

    def __init__(self, http: httpx.Client, name: str = "mixnet node") -> None:
        self.http = http
        self.name = name

    def _call(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            r = self.http.request(method, url, **kwargs)
        except httpx.TransportError as e:
            raise MixnetUnavailable(f"{self.name}: {e!r}") from e
        if r.status_code >= 500:
            raise MixnetUnavailable(f"{self.name}: HTTP {r.status_code}")
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise MixnetError(f"{self.name}: HTTP {r.status_code}: {detail}", r.status_code)
        return r

    def create_key(self, crypto_id: bytes) -> NodeKey:
        """Asks the node for its key for ``crypto_id``, generating it the first time."""
        body = self._call("POST", f"/elections/{crypto_id.hex()}/key").json()
        if body.get("election_id") != crypto_id.hex():
            raise MixnetError(f"{self.name}: returned a key for another election")
        pk = base64.b64decode(body["public_key"], validate=True)
        if hashlib.sha256(pk).hexdigest() != body.get("fingerprint"):
            raise MixnetError(f"{self.name}: the key's fingerprint does not match it")
        return NodeKey(pk, body["fingerprint"])

    def submit_mix(self, crypto_id: bytes, pp: bytes, batch: bytes, prev_record: bytes | None) -> NodeJob:
        """Queues the node's mix; returns the existing job if these inputs were already submitted."""
        files: dict[str, Any] = {
            "pp": ("pp", pp, "application/octet-stream"),
            "batch": ("batch", batch, "application/octet-stream"),
        }
        if prev_record is not None:
            files["prev_record"] = ("prev_record", prev_record, "application/octet-stream")
        return self._job(self._call("POST", f"/elections/{crypto_id.hex()}/mix-jobs", files=files).json())

    def get_job(self, job_id: str) -> NodeJob:
        return self._job(self._call("GET", f"/mix-jobs/{job_id}").json())

    def job_output(self, job: NodeJob, which: str) -> bytes:
        """``which`` is ``batch`` or ``record``; checked against the hash the job reported."""
        data = self._call("GET", f"/mix-jobs/{job.job_id}/{which}").content
        if job.outputs is None or hashlib.sha256(data).hexdigest() != job.outputs.get(which):
            raise MixnetError(f"{self.name}: the job's {which} does not match its hash")
        return data

    @staticmethod
    def _job(body: dict[str, Any]) -> NodeJob:
        return NodeJob(
            job_id=body["job_id"],
            state=body["state"],
            outputs=body.get("outputs"),
            info=body.get("info"),
            error=body.get("error"),
        )
