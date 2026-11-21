"""The mixnet node's API, with real keys, ballots and mixing (in the node's process pool).

Two nodes play servers 0 and 1. Each test that changes an election's state
uses an election of its own, since the nodes outlive a single test.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import evmlr
from mixnet_node.app import create_app
from mixnet_node.config import Settings
from mixnet_node.jobs import Job, JobState, MixService
from mixnet_node.keystore import KeyFileError, KeyStore

TOKEN = "t" * 40


def make_settings(data_dir: Path, name: str) -> Settings:
    return Settings(
        node_name=name,
        api_token=TOKEN,
        key_encryption_key=base64.b64encode(os.urandom(32)).decode(),
        data_dir=data_dir,
    )


@pytest.fixture(scope="module")
def nodes(tmp_path_factory: pytest.TempPathFactory) -> Iterator[list[TestClient]]:
    clients = []
    for j in range(2):
        settings = make_settings(tmp_path_factory.mktemp(f"node{j}"), f"node-{j}")
        client = TestClient(create_app(settings), headers={"Authorization": f"Bearer {TOKEN}"})
        client.__enter__()  # runs the lifespan: starts the mix service
        clients.append(client)
    yield clients
    for client in clients:
        client.__exit__(None, None, None)


@dataclass
class Election:
    eid: bytes
    pp: bytes
    ballots: list[bytes]

    @property
    def hex(self) -> str:
        return self.eid.hex()


def new_election(nodes: list[TestClient], label: str, votes: list[int] = [10, 20, 30]) -> Election:  # noqa: B006
    eid = hashlib.sha256(label.encode()).digest()
    pks = []
    for node in nodes:
        r = node.post(f"/elections/{eid.hex()}/key")
        assert r.status_code == 201, r.text
        pks.append(base64.b64decode(r.json()["public_key"]))
    pp = evmlr.params(eid, pks)
    ballots = [evmlr.cast(pp, voter, [v]) for voter, v in enumerate(votes, start=1)]
    return Election(eid, pp, ballots)


def submit(node: TestClient, e: Election, batch: bytes, prev: bytes | None = None, pp: bytes | None = None):
    files = {"pp": pp if pp is not None else e.pp, "batch": batch}
    if prev is not None:
        files["prev_record"] = prev
    return node.post(f"/elections/{e.hex}/mix-jobs", files=files)


def wait(node: TestClient, job_id: str, timeout: float = 60) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = node.get(f"/mix-jobs/{job_id}").json()
        if job["state"] != "pending":
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} still pending after {timeout}s")


def mix_through(nodes: list[TestClient], e: Election) -> tuple[list[bytes], bytes]:
    """Runs both servers; returns the records and the final batch."""
    batch, records = evmlr.first_batch(e.pp, e.ballots), []
    for j, node in enumerate(nodes):
        r = submit(node, e, batch, records[-1] if j else None)
        assert r.status_code == 202, r.text
        job = wait(node, r.json()["job_id"])
        assert job["state"] == "done", job
        batch = node.get(f"/mix-jobs/{job['job_id']}/batch").content
        records.append(node.get(f"/mix-jobs/{job['job_id']}/record").content)
    return records, batch


# --- ops and auth --------------------------------------------------------------


def test_health_needs_no_token(nodes):
    r = nodes[0].get("/health", headers={"Authorization": ""})
    assert r.json() == {"status": "ok", "node": "node-0"}


@pytest.mark.parametrize("header", [None, "Bearer wrong-token", f"Basic {TOKEN}"])
def test_routes_need_the_token(nodes, header):
    headers = {"Authorization": header} if header else {"Authorization": ""}
    r = nodes[0].post(f"/elections/{'00' * 32}/key", headers=headers)
    assert r.status_code == 401


# --- keys ----------------------------------------------------------------------


def test_keygen_is_idempotent(nodes):
    eid = hashlib.sha256(b"keygen").hexdigest()
    first = nodes[0].post(f"/elections/{eid}/key")
    assert first.status_code == 201
    again = nodes[0].post(f"/elections/{eid}/key")
    assert again.status_code == 200
    assert again.json() == first.json()
    assert nodes[0].get(f"/elections/{eid}/key").json() == first.json()
    pk = base64.b64decode(first.json()["public_key"])
    assert first.json()["fingerprint"] == hashlib.sha256(pk).hexdigest()


def test_keys_are_per_node_and_per_election(nodes):
    a, b = hashlib.sha256(b"a").hexdigest(), hashlib.sha256(b"b").hexdigest()
    keys = {nodes[i].post(f"/elections/{e}/key").json()["public_key"] for i in (0, 1) for e in (a, b)}
    assert len(keys) == 4


def test_unknown_key_and_malformed_id(nodes):
    assert nodes[0].get(f"/elections/{'ab' * 32}/key").status_code == 404
    assert nodes[0].post("/elections/not-hex/key").status_code == 422
    assert nodes[0].post(f"/elections/{'AB' * 32}/key").status_code == 422  # lowercase only


def test_key_file_is_sealed(tmp_path):
    kek = os.urandom(32)
    store = KeyStore(tmp_path, kek)
    eid, other = os.urandom(32), os.urandom(32)
    store.create(eid)
    keypair = store.keypair(eid)
    path = tmp_path / f"{eid.hex()}.key"
    assert keypair not in path.read_bytes()
    assert base64.b64encode(keypair) not in path.read_bytes()
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(KeyFileError):
        KeyStore(tmp_path, os.urandom(32)).keypair(eid)  # wrong KEK
    # A key file renamed to another election does not open there.
    record = json.loads(path.read_text())
    record["election_id"] = other.hex()
    (tmp_path / f"{other.hex()}.key").write_text(json.dumps(record))
    with pytest.raises(KeyFileError):
        store.keypair(other)


def test_key_is_never_replaced(tmp_path):
    store = KeyStore(tmp_path, os.urandom(32))
    eid = os.urandom(32)
    pk, created = store.create(eid)
    assert created
    again, created = store.create(eid)
    assert not created and again == pk


# --- mixing --------------------------------------------------------------------


def test_full_count_through_two_nodes(nodes):
    e = new_election(nodes, "full count")
    records, final = mix_through(nodes, e)
    votes = evmlr.verify(e.pp, e.ballots, evmlr.transcript(e.pp, records))
    assert sorted(v[0] for v in votes) == [10, 20, 30]
    assert votes == evmlr.results(e.pp, final)


def test_job_reports_its_mix(nodes):
    e = new_election(nodes, "job info")
    batch = evmlr.first_batch(e.pp, e.ballots)
    job = wait(nodes[0], submit(nodes[0], e, batch).json()["job_id"])
    assert job["info"] == {"server": 0, "n_in": 3, "n_out": 3, "rejections": []}
    assert job["inputs"]["batch"] == hashlib.sha256(batch).hexdigest()
    assert job["inputs"]["prev_record"] is None
    record = nodes[0].get(f"/mix-jobs/{job['job_id']}/record").content
    assert job["outputs"]["record"] == hashlib.sha256(record).hexdigest()


def test_resubmitting_returns_the_same_job(nodes):
    e = new_election(nodes, "resubmit")
    batch = evmlr.first_batch(e.pp, e.ballots)
    first = submit(nodes[0], e, batch)
    assert first.status_code == 202
    wait(nodes[0], first.json()["job_id"])
    again = submit(nodes[0], e, batch)
    assert again.status_code == 200
    assert again.json()["job_id"] == first.json()["job_id"]
    assert again.json()["state"] == "done"


def test_an_election_is_mixed_once(nodes):
    """A second, different input (here a subset of the board) must be refused."""
    e = new_election(nodes, "replay", votes=[1, 2, 3, 4])
    job = wait(nodes[0], submit(nodes[0], e, evmlr.first_batch(e.pp, e.ballots)).json()["job_id"])
    assert job["state"] == "done"
    subset = evmlr.first_batch(e.pp, e.ballots[:2])
    r = submit(nodes[0], e, subset)
    assert r.status_code == 409
    assert job["job_id"] in r.json()["detail"]


def test_failed_jobs_do_not_lock_the_election(nodes):
    e = new_election(nodes, "too few")
    single = evmlr.first_batch(e.pp, e.ballots[:1])
    job = wait(nodes[0], submit(nodes[0], e, single).json()["job_id"])
    assert job["state"] == "failed"
    assert job["error"]["status"] == "TOO_FEW"
    assert nodes[0].get(f"/mix-jobs/{job['job_id']}/batch").status_code == 409
    r = submit(nodes[0], e, evmlr.first_batch(e.pp, e.ballots))
    assert r.status_code == 202
    assert wait(nodes[0], r.json()["job_id"])["state"] == "done"


def test_node_refuses_to_mix_another_servers_layer(nodes):
    e = new_election(nodes, "wrong layer")
    job = wait(nodes[1], submit(nodes[1], e, evmlr.first_batch(e.pp, e.ballots)).json()["job_id"])
    assert job["state"] == "failed"
    assert job["error"]["status"] == "KEY"  # the batch is for server 0's key


def test_parameters_of_another_election_are_refused(nodes):
    e = new_election(nodes, "params mismatch")
    other = new_election(nodes, "params mismatch, other")
    r = submit(nodes[0], e, evmlr.first_batch(e.pp, e.ballots), pp=other.pp)
    assert r.status_code == 422
    r = submit(nodes[0], e, b"x", pp=b"not parameters")
    assert r.status_code == 422


def test_no_key_for_the_election(nodes):
    eid = hashlib.sha256(b"never generated").digest()
    pp = evmlr.params(eid, [evmlr.keygen().public_key, evmlr.keygen().public_key])
    e = Election(eid, pp, [])
    r = submit(nodes[0], e, evmlr.first_batch(pp, []))
    assert r.status_code == 404


def test_unknown_job(nodes):
    assert nodes[0].get(f"/mix-jobs/{'0' * 64}").status_code == 404
    assert nodes[0].get(f"/mix-jobs/{'0' * 64}/record").status_code == 404
    assert nodes[0].get("/mix-jobs/nope").status_code == 422


def test_restart_fails_interrupted_jobs(tmp_path):
    settings = make_settings(tmp_path, "restart")
    service = MixService(settings)
    eid = "cd" * 32
    service.store.save(Job(job_id="ef" * 32, election_id=eid, state=JobState.PENDING, inputs={}))
    service.store.save_election(eid, {"pending": "ef" * 32, "done": None})
    service.shutdown()

    restarted = MixService(settings)
    job = restarted.get("ef" * 32)
    assert job.state is JobState.FAILED
    assert job.error is not None and job.error["status"] == "INTERRUPTED"
    assert restarted.store.election(eid)["pending"] is None
    restarted.shutdown()
