"""The voter-facing API and the public board, through HTTP only (casting included).

Logging in goes through the real signed session cookie: the tests add a
login route that calls ``log_in``, as the password and CAS routes will.
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

import evmlr
from ballot_box.auth import Identity, log_in, log_out

from .conftest import ADMIN_TOKEN
from .test_elections import act, encode, new_election

PUBLIC = "http://localhost:8000"  # Settings.public_url's default


@pytest.fixture
def web(api: TestClient) -> TestClient:
    """A browser: no admin token, its own cookie jar."""
    app = api.app

    @app.post("/test/login/{external_id}")
    def _login(external_id: str, request: Request) -> None:
        log_in(request, Identity(provider="password", external_id=external_id))

    @app.post("/test/logout")
    def _logout(request: Request) -> None:
        log_out(request)

    return TestClient(app, base_url=PUBLIC)


def login(web: TestClient, external_id: str) -> None:
    assert web.post(f"/test/login/{external_id}").status_code == 200


def opened(api, queue) -> dict:
    e = new_election(api)
    act(api, e, "freeze")
    queue.drain()
    return act(api, e, "open")


def booth(web: TestClient, e: dict, answers: dict[int, tuple[int, ...] | list[int]], raw: bool = False):
    """What the booth does: fetch the parameters, encode, cast, send. ``raw`` sends coefficients as given."""
    me = web.get(f"/api/elections/{e['id']}/me").json()
    election = web.get(f"/api/elections/{e['id']}").json()
    files = {}
    for index, answer in answers.items():
        spec = election["questions"][index]["spec"]
        pp = web.get(f"/api/elections/{e['id']}/questions/{index}/params").content
        coefficients = list(answer) if raw else encode(spec, tuple(answer))
        files[f"q{index}"] = (f"q{index}", evmlr.cast(pp, int(me["voter_id"]), coefficients))
    return web.post(f"/api/elections/{e['id']}/ballots", files=files)


# --- the public election ------------------------------------------------------------------------


def test_drafts_are_not_public(api, web):
    e = new_election(api)
    assert web.get(f"/api/elections/{e['id']}").status_code == 404


def test_public_election_and_parameters(api, queue, web):
    e = opened(api, queue)
    pub = web.get(f"/api/elections/{e['id']}").json()
    assert pub["state"] == "open" and pub["voters"] == 4
    assert [m["node"] for m in pub["mix_servers"]] == ["node-0", "node-1", "node-2"]
    for q, admin_q in zip(pub["questions"], e["questions"], strict=True):
        assert q["crypto_id"] == admin_q["crypto_id"]
        assert q["keys"] == admin_q["keys"]
        r = web.get(f"/api/elections/{e['id']}/questions/{q['index']}/params")
        assert "immutable" in r.headers["cache-control"]
        assert hashlib.sha256(r.content).hexdigest() == q["params_key"]
        assert evmlr.params_info(r.content).election_id.hex() == q["crypto_id"]
    assert web.get(f"/api/elections/{e['id']}/questions/2/params").status_code == 404


# --- the voter ----------------------------------------------------------------------------------


def test_me(api, queue, web):
    e = opened(api, queue)
    assert web.get(f"/api/elections/{e['id']}/me").status_code == 401
    login(web, "stranger")
    assert web.get(f"/api/elections/{e['id']}/me").json() == {"eligible": False, "voter_id": None, "cast": []}
    login(web, "voter1")
    me = web.get(f"/api/elections/{e['id']}/me").json()
    roll = api.get(f"/api/admin/elections/{e['id']}/voters").json()
    assert me["eligible"] and me["voter_id"] == next(
        v["voter_id"] for v in roll if v["external_id"] == "voter1"
    )
    assert isinstance(me["voter_id"], str)


def test_cast_revote_and_trackers(api, queue, web):
    e = opened(api, queue)
    login(web, "voter0")
    r = booth(web, e, {0: (0,), 1: (1, 2)})
    assert r.status_code == 201, r.text
    first = r.json()
    assert [c["question_index"] for c in first] == [0, 1]

    board = web.get(f"/api/elections/{e['id']}/questions/0/board").json()
    me = web.get(f"/api/elections/{e['id']}/me").json()
    assert board["entries"][0]["tracker"] == first[0]["tracker"]
    assert board["entries"][0]["voter_id"] == me["voter_id"]
    ballot = web.get(f"/api/elections/{e['id']}/questions/0/ballots/{first[0]['tracker']}").content
    assert hashlib.sha256(ballot).hexdigest() == first[0]["tracker"]
    assert web.get(f"/api/elections/{e['id']}/questions/1/ballots/{first[0]['tracker']}").status_code == 404

    again = booth(web, e, {0: (2,)}).json()  # revote on the first question only
    old = web.get(f"/api/elections/{e['id']}/trackers/{first[0]['tracker']}").json()
    new = web.get(f"/api/elections/{e['id']}/trackers/{again[0]['tracker']}").json()
    assert old["superseded"] and not new["superseded"]
    assert web.get(f"/api/elections/{e['id']}/trackers/{first[1]['tracker']}").json()["superseded"] is False
    latest = web.get(f"/api/elections/{e['id']}/me").json()["cast"]
    assert [c["tracker"] for c in latest] == [again[0]["tracker"], first[1]["tracker"]]
    assert web.get(f"/api/elections/{e['id']}").json()["questions"][0]["ballots"] == 2


def test_board_pages(api, queue, web):
    e = opened(api, queue)
    for i in range(3):
        login(web, f"voter{i}")
        booth(web, e, {0: (i,)})
    page = web.get(f"/api/elections/{e['id']}/questions/0/board", params={"limit": 2}).json()
    assert len(page["entries"]) == 2 and page["next_after"] == page["entries"][-1]["seq"]
    rest = web.get(
        f"/api/elections/{e['id']}/questions/0/board", params={"limit": 2, "after": page["next_after"]}
    ).json()
    assert len(rest["entries"]) == 1 and rest["next_after"] is None


# --- casting refusals ---------------------------------------------------------------------------


def _board_size(web, e) -> int:
    return sum(q["ballots"] for q in web.get(f"/api/elections/{e['id']}").json()["questions"])


def test_cast_refusals(api, queue, web):
    e = opened(api, queue)
    url = f"/api/elections/{e['id']}/ballots"
    assert web.post(url, files={"q0": ("q0", b"x")}).status_code == 401

    login(web, "stranger")
    assert web.post(url, files={"q0": ("q0", b"x" * 100)}).status_code == 403

    login(web, "voter0")
    pp0 = web.get(f"/api/elections/{e['id']}/questions/0/params").content
    pp1 = web.get(f"/api/elections/{e['id']}/questions/1/params").content
    me = web.get(f"/api/elections/{e['id']}/me").json()
    mine0 = evmlr.cast(pp0, int(me["voter_id"]), [1])
    roll = api.get(f"/api/admin/elections/{e['id']}/voters").json()
    other = int(next(v["voter_id"] for v in roll if v["external_id"] == "voter1"))
    theirs1 = evmlr.cast(pp1, other, [1])

    # All or nothing: one good ballot and one made for someone else store neither.
    r = web.post(url, files={"q0": ("q0", mine0), "q1": ("q1", theirs1)})
    assert r.status_code == 422 and "another voter" in r.json()["detail"]
    assert _board_size(web, e) == 0

    assert web.post(url, files={"q1": ("q1", mine0)}).status_code == 422  # the other question's parameters
    assert web.post(url, files={"q7": ("q7", mine0)}).status_code == 422
    assert web.post(url, files={"ballot": ("b", mine0)}).status_code == 422
    assert web.post(url, data={"q0": "text"}).status_code in (400, 422)
    assert (
        web.post(url, files={"q0": ("q0", mine0)}, headers={"Origin": "https://evil.example"}).status_code
        == 403
    )
    assert _board_size(web, e) == 0

    assert web.post(url, files={"q0": ("q0", mine0)}, headers={"Origin": PUBLIC}).status_code == 201
    act(api, e, "close")
    assert web.post(url, files={"q0": ("q0", mine0)}).status_code == 409


def test_oversized_ballot(api, queue, web, settings):
    e = opened(api, queue)
    login(web, "voter0")
    big = b"x" * (settings.max_ballot_bytes + 1)
    assert web.post(f"/api/elections/{e['id']}/ballots", files={"q0": ("q0", big)}).status_code == 413


def test_logout(api, queue, web):
    e = opened(api, queue)
    login(web, "voter0")
    web.post("/test/logout")
    assert web.get(f"/api/elections/{e['id']}/me").status_code == 401


# --- results and public verification ------------------------------------------------------------


def test_results_are_hidden_until_published_then_verifiable(api, queue, web):
    e = opened(api, queue)
    votes = {"voter0": {0: (0,), 1: (0, 1)}, "voter1": {0: (1,), 1: ()}, "voter2": {0: (1,), 1: (3,)}}
    for voter, answers in votes.items():
        login(web, voter)
        assert booth(web, e, answers).status_code == 201
    login(web, "voter3")
    assert booth(web, e, {0: [0, 1, 1]}, raw=True).status_code == 201  # malformed: three choices
    act(api, e, "close")
    act(api, e, "count")
    queue.drain()
    for path in ("results", "questions/0/transcript", "questions/0/mix-records/0"):
        assert web.get(f"/api/elections/{e['id']}/{path}").status_code == 404, path

    act(api, e, "publish")
    results = web.get(f"/api/elections/{e['id']}/results").json()["questions"]
    assert results[0]["result"] == {"counts": [1, 2, 0], "blank": 0, "valid": 3, "discarded": 1}
    assert results[1]["result"] == {"counts": [1, 1, 0, 1], "blank": 1, "valid": 3, "discarded": 0}
    assert [d["reason"] for d in results[0]["discarded"]] == ["too_many_choices"]

    # Anyone can redo the count's verification with what the API publishes.
    base = f"/api/elections/{e['id']}/questions/0"
    pp = web.get(f"{base}/params").content
    board = web.get(f"{base}/board").json()["entries"]
    ballots = [web.get(f"{base}/ballots/{b['tracker']}").content for b in board]
    transcript = web.get(f"{base}/transcript").content
    assert hashlib.sha256(transcript).hexdigest() == results[0]["transcript_key"]
    verified = evmlr.verify(pp, ballots, transcript)
    assert sorted(v[:3] for v in verified) == sorted([(1, 0, 0), (0, 1, 0), (0, 1, 0), (0, 1, 1)])
    records = [web.get(f"{base}/mix-records/{j}").content for j in range(3)]
    assert evmlr.transcript(pp, records) == transcript


def test_admin_token_does_not_log_in(api, queue):
    e = opened(api, queue)
    r = api.get(f"/api/elections/{e['id']}/me", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    assert r.status_code == 401
