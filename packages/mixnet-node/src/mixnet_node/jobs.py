"""Mix jobs: one server's step of an election, run in a worker process.

A job is identified by a hash of its inputs (election id, public parameters,
input batch and, for j > 0, the previous server's mix record), so the ballot
box can resubmit after a timeout or a crash and get the same job back.

**One mix per election.** Once this node has mixed an input batch for an
election, it refuses to mix a different one, and while one job is pending it
refuses others. Without this, a malicious ballot box could submit the batch
several times with different subsets of ballots and compare the outputs,
tracing a chosen ballot to its vote (a replay attack on the mixnet). A
failed job produces no output, so the same inputs can be resubmitted, and
until a job succeeds the ballot box may still correct its inputs. Clearing an
election's record by hand (``jobs/elections/<id>.json``) lifts the lock: do
it only once you know why a second mix is needed.

The lock lives in this process, so run the node as a single server process
(one uvicorn worker); mixing itself runs in ``mix_workers`` processes.

Not checked yet: that the previous servers' proofs of shuffle verify and
chain back to the bulletin board. ``evmlr.mix`` checks that the batch is what
the previous record says was output, but the C API has no call that
verifies a partial chain (``evmlr_verify`` needs all k records). Until it
does, a node trusts the ballot box for that, and the public verification
after the count is what catches a bad chain.
"""

from __future__ import annotations

import enum
import hashlib
import json
import multiprocessing
import os
import tempfile
import threading
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

import evmlr
from voting_common.blobstore import FilesystemBlobStore, digest

from .config import Settings
from .keystore import KeyFileError, KeyStore


class JobState(enum.StrEnum):
    PENDING = "pending"
    """Queued or running."""
    DONE = "done"
    FAILED = "failed"


class BadInput(Exception):
    """The request cannot be a mix job of this election (e.g. parameters of another election)."""


class NoKey(Exception):
    """This node has no key for the election: run keygen first."""


class MixConflict(Exception):
    """The election already has another job, pending or done, with different inputs."""

    def __init__(self, other_job_id: str) -> None:
        super().__init__(f"this election is already mixed or being mixed by job {other_job_id}")
        self.other_job_id = other_job_id


class JobNotFound(KeyError):
    pass


class JobNotDone(Exception):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Job:
    job_id: str
    election_id: str
    """Hex."""
    state: JobState
    inputs: dict[str, str | None]
    """Blob keys of ``pp``, ``batch`` and ``prev_record`` (None for server 0)."""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    outputs: dict[str, str] | None = None
    """Blob keys of ``batch`` and ``record``, once done."""
    info: dict[str, Any] | None = None
    """What the mix record says: server, n_in, n_out, rejections."""
    error: dict[str, str] | None = None
    """``status`` (an evmlr Status name, or INTERNAL) and ``message``, if failed."""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Job:
        return cls(**{**d, "state": JobState(d["state"])})


def job_id_for(election_id: bytes, pp_key: str, batch_key: str, prev_key: str | None) -> str:
    h = hashlib.sha256(b"mixnet-node job v1\x00")
    h.update(election_id)
    for key in (pp_key, batch_key, prev_key or ""):
        h.update(key.encode() + b"\x00")
    return h.hexdigest()


def _write_json(path: Path, data: dict[str, Any]) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class JobStore:
    """Job records and the per-election lock, as JSON files."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "elections").mkdir(parents=True, exist_ok=True)

    def get(self, job_id: str) -> Job | None:
        if len(job_id) != 64 or any(c not in "0123456789abcdef" for c in job_id):
            return None
        try:
            return Job.from_dict(json.loads((self.root / f"{job_id}.json").read_text()))
        except FileNotFoundError:
            return None

    def save(self, job: Job) -> None:
        job.updated_at = _now()
        _write_json(self.root / f"{job.job_id}.json", asdict(job))

    def all(self) -> list[Job]:
        return [Job.from_dict(json.loads(p.read_text())) for p in self.root.glob("*.json")]

    def election(self, election_id_hex: str) -> dict[str, str | None]:
        try:
            return json.loads((self.root / "elections" / f"{election_id_hex}.json").read_text())
        except FileNotFoundError:
            return {"pending": None, "done": None}

    def save_election(self, election_id_hex: str, record: dict[str, str | None]) -> None:
        _write_json(self.root / "elections" / f"{election_id_hex}.json", record)


def run_mix(
    data_dir: str, kek: bytes, election_id_hex: str, pp_key: str, batch_key: str, prev_key: str | None
) -> dict[str, Any]:
    """The job itself, in a worker process. Returns a plain dict (it crosses a pipe).

    The worker decrypts the key pair itself, so the web process never holds it.
    """
    root = Path(data_dir)
    blobs = FilesystemBlobStore(root / "blobs")
    keys = KeyStore(root / "keys", kek)
    try:
        pp = blobs.get(pp_key)
        out = evmlr.mix(
            pp,
            keys.keypair(bytes.fromhex(election_id_hex)),
            blobs.get(batch_key),
            blobs.get(prev_key) if prev_key else None,
        )
        info = evmlr.mix_info(pp, out.record)
    except evmlr.EvmlrError as e:
        return {"ok": False, "error": {"status": e.status.name, "message": str(e)}}
    except KeyFileError as e:
        return {"ok": False, "error": {"status": "KEY_FILE", "message": str(e)}}
    return {
        "ok": True,
        "outputs": {"batch": blobs.put(out.batch), "record": blobs.put(out.record)},
        "info": {
            "server": info.server,
            "n_in": info.n_in,
            "n_out": info.n_out,
            "rejections": [{"index": r.index, "reason": r.reason.name} for r in info.rejections],
        },
    }


class MixService:
    """Accepts mix jobs, runs them in a process pool and records their outcome."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.blobs = FilesystemBlobStore(settings.blob_dir)
        self.keys = KeyStore(settings.key_dir, settings.kek_bytes)
        self.store = JobStore(settings.job_dir)
        self._lock = threading.Lock()
        self._executor: ProcessPoolExecutor | None = None
        self._recover()

    def _recover(self) -> None:
        """Jobs pending when the node stopped will never finish: mark them failed."""
        for job in self.store.all():
            if job.state is JobState.PENDING:
                job.state = JobState.FAILED
                job.error = {"status": "INTERRUPTED", "message": "the node restarted while the job ran"}
                self.store.save(job)
                record = self.store.election(job.election_id)
                if record.get("pending") == job.job_id:
                    record["pending"] = None
                    self.store.save_election(job.election_id, record)

    def _pool(self) -> ProcessPoolExecutor:
        if self._executor is None:
            # forkserver: workers start from a clean process, not a fork of a threaded server.
            ctx = multiprocessing.get_context("forkserver")
            self._executor = ProcessPoolExecutor(max_workers=self.settings.mix_workers, mp_context=ctx)
        return self._executor

    def shutdown(self) -> None:
        with self._lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)

    def submit(
        self, election_id: bytes, pp: bytes, batch: bytes, prev_record: bytes | None
    ) -> tuple[Job, bool]:
        """Returns the job for these inputs, and whether this call created (or restarted) it."""
        try:
            info = evmlr.params_info(pp)
        except evmlr.EvmlrError as e:
            raise BadInput(f"the public parameters do not decode: {e}") from e
        if info.election_id != election_id:
            raise BadInput("the public parameters are for another election")
        if not self.keys.has(election_id):
            raise NoKey(election_id.hex())

        eid = election_id.hex()
        pp_key, batch_key = digest(pp), digest(batch)
        prev_key = digest(prev_record) if prev_record is not None else None
        job_id = job_id_for(election_id, pp_key, batch_key, prev_key)

        with self._lock:
            record = self.store.election(eid)
            for other in (record.get("done"), record.get("pending")):
                if other is not None and other != job_id:
                    raise MixConflict(other)
            job = self.store.get(job_id)
            if job is not None and job.state is not JobState.FAILED:
                return job, False

            self.blobs.put(pp)
            self.blobs.put(batch)
            if prev_record is not None:
                self.blobs.put(prev_record)
            job = Job(
                job_id=job_id,
                election_id=eid,
                state=JobState.PENDING,
                inputs={"pp": pp_key, "batch": batch_key, "prev_record": prev_key},
            )
            self.store.save(job)
            record["pending"] = job_id
            self.store.save_election(eid, record)

            future = self._pool().submit(
                run_mix,
                str(self.settings.data_dir),
                self.settings.kek_bytes,
                eid,
                pp_key,
                batch_key,
                prev_key,
            )
        future.add_done_callback(partial(self._finish, job_id))
        return job, True

    def _finish(self, job_id: str, future: Future[dict[str, Any]]) -> None:
        try:
            result = future.result()
        except BrokenProcessPool as e:
            result = {"ok": False, "error": {"status": "INTERNAL", "message": f"worker crashed: {e}"}}
            with self._lock:
                self._executor = None  # a broken pool accepts no more work; start a new one
        except BaseException as e:  # noqa: BLE001 - any failure becomes the job's error
            result = {"ok": False, "error": {"status": "INTERNAL", "message": repr(e)}}

        with self._lock:
            job = self.store.get(job_id)
            if job is None:
                return
            if result["ok"]:
                job.state, job.outputs, job.info = JobState.DONE, result["outputs"], result["info"]
            else:
                job.state, job.error = JobState.FAILED, result["error"]
            self.store.save(job)
            record = self.store.election(job.election_id)
            if record.get("pending") == job_id:
                record["pending"] = None
            if result["ok"]:
                record["done"] = job_id
            self.store.save_election(job.election_id, record)

    def get(self, job_id: str) -> Job:
        job = self.store.get(job_id)
        if job is None:
            raise JobNotFound(job_id)
        return job

    def output(self, job_id: str, which: str) -> bytes:
        """``which`` is ``batch`` or ``record``."""
        job = self.get(job_id)
        if job.state is not JobState.DONE or job.outputs is None:
            raise JobNotDone(job_id)
        return self.blobs.get(job.outputs[which])
