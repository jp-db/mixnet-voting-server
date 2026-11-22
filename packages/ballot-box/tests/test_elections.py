"""Elections end to end: admin API, key generation on the nodes, casting, mixing, verified tally."""

from __future__ import annotations

import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import evmlr
from ballot_box.app import create_app
from ballot_box.elections import service as ops
from ballot_box.models import Election, MixnetNode, Question, Voter
from ballot_box.questions import ENCODINGS, QuestionSpec, crypto_id

from .conftest import ADMIN_TOKEN, node_token

PRESIDENT = {"title": "President", "answers": ["Alice", "Bob", "Carol"], "min_choices": 1, "max_choices": 1}
COUNCIL = {"title": "Council", "answers": ["Dan", "Eve", "Fay", "Gus"], "min_choices": 0, "max_choices": 2}


def new_election(api, questions=(PRESIDENT, COUNCIL), voters=4, **extra) -> dict:
    r = api.post(
        "/api/admin/elections", json={"title": "Board election", "questions": list(questions), **extra}
    )
    assert r.status_code == 201, r.text
    e = r.json()
    if voters:
        roll = [{"provider": "password", "external_id": f"voter{i}"} for i in range(voters)]
        r = api.post(f"/api/admin/elections/{e['id']}/voters", json={"voters": roll})
        assert r.status_code == 200, r.text
    return api.get(f"/api/admin/elections/{e['id']}").json()


def act(api, e: dict, action: str, expect: int = 200) -> dict:
    r = api.post(f"/api/admin/elections/{e['id']}/{action}")
    assert r.status_code == expect, r.text
    return r.json()


def frozen_and_open(api, queue, **kw) -> dict:
    e = new_election(api, **kw)
    assert act(api, e, "freeze")["state"] == "keygen"
    queue.drain()
    e = api.get(f"/api/admin/elections/{e['id']}").json()
    assert e["state"] == "frozen", e
    return act(api, e, "open")


def cast(services, e: dict, voter_index: int, question_index: int, coefficients: list[int]) -> str:
    """Casts as the voter, through the service the booth endpoint will call. Returns the receipt."""
    with services.db.begin() as s:
        election = s.get_one(Election, uuid.UUID(e["id"]))
        voter = next(v for v in election.voters if v.external_id == f"voter{voter_index}")
        question = election.questions[question_index]
        pp = services.blobs.get(question.params_key)
        ballot = evmlr.cast(pp, voter.voter_id, coefficients)
        return ops.cast_ballot(s, services.blobs, election, question, voter, ballot).blob_key


def encode(question: dict, answer: tuple[int, ...]) -> list[int]:
    spec = QuestionSpec.model_validate(question)
    return ENCODINGS[spec.encoding].encode(spec, answer)


# --- the whole flow -----------------------------------------------------------------------------


def test_election_from_draft_to_published(api, queue, services, node_apps):
    e = new_election(api)
    assert e["state"] == "draft"
    assert [m["node"] for m in e["mix_servers"]] == ["node-0", "node-1", "node-2"]  # the defaults
    assert e["voter_count"] == 4

    e = act(api, e, "freeze")
    queue.drain()
    e = api.get(f"/api/admin/elections/{e['id']}").json()
    assert e["state"] == "frozen"

    # Each question has its own evmlr election and its own key on every node.
    crypto_ids = [q["crypto_id"] for q in e["questions"]]
    assert len(set(crypto_ids)) == 2
    for q in e["questions"]:
        assert [k["position"] for k in q["keys"]] == [0, 1, 2]
        for k, name in zip(q["keys"], ["node-0", "node-1", "node-2"], strict=True):
            node = node_apps[f"http://{name}"]
            node.headers["Authorization"] = f"Bearer {node_token(name)}"
            on_node = node.get(f"/elections/{q['crypto_id']}/key").json()
            assert on_node["fingerprint"] == k["fingerprint"]
        pp = services.blobs.get(q["params_key"])
        info = evmlr.params_info(pp)
        assert info.k == 3 and info.election_id.hex() == q["crypto_id"]

    e = act(api, e, "open")
    cast(services, e, 0, 0, encode(PRESIDENT, (0,)))
    cast(services, e, 1, 0, encode(PRESIDENT, (2,)))  # replaced below: the last ballot counts
    cast(services, e, 1, 0, encode(PRESIDENT, (1,)))
    cast(services, e, 2, 0, encode(PRESIDENT, (0,)))
    cast(services, e, 3, 0, [1, 1])  # two choices where one is allowed: malformed
    cast(services, e, 0, 1, encode(COUNCIL, (0, 3)))
    cast(services, e, 1, 1, encode(COUNCIL, ()))  # blank
    cast(services, e, 2, 1, encode(COUNCIL, (3,)))
    cast(services, e, 3, 1, [0, 0, 0, 0, 7])  # outside the answers: malformed

    act(api, e, "close")
    assert act(api, e, "count")["state"] == "mixing"
    queue.drain()
    e = api.get(f"/api/admin/elections/{e['id']}").json()
    assert e["state"] == "tallied", e

    president, council = (q["tally"] for q in e["questions"])
    assert president["state"] == "done"
    assert president["result"] == {"counts": [2, 1, 0], "blank": 0, "valid": 3, "discarded": 1}
    assert [d["reason"] for d in president["discarded"]] == ["too_many_choices"]
    assert council["result"] == {"counts": [1, 0, 0, 2], "blank": 1, "valid": 3, "discarded": 1}
    assert [d["reason"] for d in council["discarded"]] == ["nonzero_padding"]
    assert [s["position"] for s in president["steps"]] == [0, 1, 2]
    assert president["steps"][0]["info"]["n_in"] == 4  # one ballot per voter

    # Anyone can recheck the count from the published objects.
    q = e["questions"][0]
    with services.db() as s:
        board = [services.blobs.get(k) for k in ops.board_ballots(s, uuid.UUID(q["id"]))]
    votes = evmlr.verify(
        services.blobs.get(q["params_key"]), board, services.blobs.get(president["transcript_key"])
    )
    assert len(votes) == 4

    assert act(api, e, "publish")["state"] == "published"


# --- mix servers --------------------------------------------------------------------------------


def test_manager_picks_the_mix_servers_and_their_order(api, queue, services):
    r = api.post(
        "/api/admin/mixnet-nodes", json={"name": "spare", "url": "http://spare", "token": node_token("spare")}
    )
    assert r.status_code == 201 and r.json()["is_default"] is False
    e = frozen_and_open(api, queue, questions=(PRESIDENT,), mix_servers=["spare", "node-1"])
    assert [m["node"] for m in e["mix_servers"]] == ["spare", "node-1"]
    for voter, choice in enumerate([0, 1, 1]):
        cast(services, e, voter, 0, encode(PRESIDENT, (choice,)))
    act(api, e, "close")
    act(api, e, "count")
    queue.drain()
    e = api.get(f"/api/admin/elections/{e['id']}").json()
    assert e["state"] == "tallied", e
    assert e["questions"][0]["tally"]["result"]["counts"] == [1, 2, 0]


def test_mix_servers_can_be_changed_in_draft(api):
    e = new_election(api, voters=0)
    r = api.patch(f"/api/admin/elections/{e['id']}", json={"mix_servers": ["node-2", "node-0"]})
    assert [m["node"] for m in r.json()["mix_servers"]] == ["node-2", "node-0"]
    r = api.patch(f"/api/admin/elections/{e['id']}", json={"use_default_mix_servers": True})
    assert [m["node"] for m in r.json()["mix_servers"]] == ["node-0", "node-1", "node-2"]


@pytest.mark.parametrize(
    ("servers", "detail"),
    [
        (["node-0", "nowhere"], "unknown mixnet nodes: nowhere"),
        (["node-0", "node-0"], "only once"),
        ([], "at least one"),
    ],
)
def test_bad_mix_servers(api, servers, detail):
    r = api.post(
        "/api/admin/elections", json={"title": "x", "questions": [PRESIDENT], "mix_servers": servers}
    )
    assert r.status_code == 422
    assert detail in r.json()["detail"]


def test_node_tokens_are_sealed(api, services):
    api.post(
        "/api/admin/mixnet-nodes", json={"name": "spare", "url": "http://spare", "token": node_token("spare")}
    )
    with services.db() as s:
        node = s.scalars(select(MixnetNode).where(MixnetNode.name == "spare")).one()
        assert node_token("spare").encode() not in node.token_sealed
    assert "token" not in api.get("/api/admin/mixnet-nodes").json()[0]


# --- lifecycle rules ----------------------------------------------------------------------------


def test_configuration_is_fixed_once_frozen(api, queue):
    e = new_election(api)
    act(api, e, "freeze")
    r = api.patch(f"/api/admin/elections/{e['id']}", json={"title": "changed"})
    assert r.status_code == 409
    r = api.post(
        f"/api/admin/elections/{e['id']}/voters", json={"voters": [{"provider": "p", "external_id": "x"}]}
    )
    assert r.status_code == 409
    act(api, e, "open", expect=409)  # keys not in yet


def test_unfreeze_and_freeze_again(api, queue):
    e = act(api, new_election(api), "freeze")
    ids = [q["crypto_id"] for q in e["questions"]]
    e = act(api, e, "unfreeze")
    assert e["state"] == "draft" and all(q["crypto_id"] is None for q in e["questions"])
    e = act(api, e, "freeze")
    assert [q["crypto_id"] for q in e["questions"]] == ids  # same spec, same election ids
    queue.drain()
    assert api.get(f"/api/admin/elections/{e['id']}").json()["state"] == "frozen"


def test_keygen_waits_for_an_unreachable_node(api, queue, services):
    e = act(api, new_election(api), "freeze")
    services.connect.down.add("node-1")
    queue.drain()
    assert queue.unavailable
    assert api.get(f"/api/admin/elections/{e['id']}").json()["state"] == "keygen"
    services.connect.down.clear()
    act(api, e, "freeze")  # in KEYGEN, retries
    queue.drain()
    assert api.get(f"/api/admin/elections/{e['id']}").json()["state"] == "frozen"


def test_questions_change_the_election_ids():
    eid = uuid.uuid4()
    a = QuestionSpec.model_validate(PRESIDENT)
    b = QuestionSpec.model_validate({**PRESIDENT, "answers": ["Alice", "Bob", "Dave"]})
    assert crypto_id(eid, 0, a) != crypto_id(eid, 0, b)
    assert crypto_id(eid, 0, a) != crypto_id(eid, 1, a)
    assert crypto_id(eid, 0, a) != crypto_id(uuid.uuid4(), 0, a)
    assert (
        crypto_id(eid, 0, a)
        == hashlib.sha256(
            b"mixnet-voting question v1\x00" + eid.bytes + (0).to_bytes(4, "big") + a.canonical()
        ).digest()
    )


# --- casting ------------------------------------------------------------------------------------


def test_casting_rules(api, queue, services):
    e = frozen_and_open(api, queue)
    with services.db.begin() as s:
        election = s.get_one(Election, uuid.UUID(e["id"]))
        alice, bob = election.voters[0], election.voters[1]
        q0, q1 = election.questions
        pp0 = services.blobs.get(q0.params_key)
        alices = evmlr.cast(pp0, alice.voter_id, [1])
        with pytest.raises(ops.Invalid, match="another voter"):
            ops.cast_ballot(s, services.blobs, election, q0, bob, alices)
        with pytest.raises(ops.Invalid, match="does not verify"):
            ops.cast_ballot(s, services.blobs, election, q1, alice, alices)  # the other question
        tampered = bytearray(alices)
        tampered[100] ^= 1  # in the part check_ballot covers; see test_inner_layer_tampering
        with pytest.raises(ops.Invalid, match="does not verify"):
            ops.cast_ballot(s, services.blobs, election, q0, alice, bytes(tampered))
        receipt = ops.cast_ballot(s, services.blobs, election, q0, alice, alices).blob_key
        assert receipt == hashlib.sha256(alices).hexdigest()

    act(api, e, "close")
    with services.db.begin() as s:
        election = s.get_one(Election, uuid.UUID(e["id"]))
        voter: Voter = election.voters[0]
        question: Question = election.questions[0]
        with pytest.raises(ops.WrongState):
            ops.cast_ballot(s, services.blobs, election, question, voter, alices)


def test_voter_ids_are_random_and_registration_idempotent(api):
    e = new_election(api, voters=3)
    roll = api.get(f"/api/admin/elections/{e['id']}/voters").json()
    again = api.post(
        f"/api/admin/elections/{e['id']}/voters",
        json={"voters": [{"provider": "password", "external_id": "voter0"}]},
    ).json()
    assert again[0]["voter_id"] == roll[0]["voter_id"]
    assert len({v["voter_id"] for v in roll}) == 3
    assert all(0 < int(v["voter_id"]) < 2**63 for v in roll)


def test_inner_layer_tampering_is_caught_only_by_the_mix(api, queue, services):
    """evmlr gap: ``check_ballot`` covers only the start of a ballot.

    A ballot changed further in still goes on the board; the mix server whose
    layer was changed rejects it, the rejection is in its step's info, and it
    does not count. Integrity holds, but the voter is not told at cast time.
    """
    e = frozen_and_open(api, queue, questions=(PRESIDENT,))
    for voter, choice in enumerate([0, 1, 2]):
        cast(services, e, voter, 0, encode(PRESIDENT, (choice,)))
    with services.db.begin() as s:
        election = s.get_one(Election, uuid.UUID(e["id"]))
        voter = next(v for v in election.voters if v.external_id == "voter3")
        question = election.questions[0]
        ballot = bytearray(evmlr.cast(services.blobs.get(question.params_key), voter.voter_id, [1, 0, 0]))
        ballot[len(ballot) // 2] ^= 1
        ops.cast_ballot(s, services.blobs, election, question, voter, bytes(ballot))  # accepted
    act(api, e, "close")
    act(api, e, "count")
    queue.drain()
    tally = api.get(f"/api/admin/elections/{e['id']}").json()["questions"][0]["tally"]
    assert tally["state"] == "done"
    assert tally["result"]["counts"] == [1, 1, 1]
    assert sum(len(s["info"]["rejections"]) for s in tally["steps"]) == 1


# --- counting failures --------------------------------------------------------------------------


def test_a_failed_count_is_reported_and_can_be_retried(api, queue, services):
    e = frozen_and_open(api, queue, questions=(PRESIDENT,))
    cast(services, e, 0, 0, encode(PRESIDENT, (0,)))  # one ballot: evmlr cannot mix it yet
    act(api, e, "close")
    act(api, e, "count")
    queue.drain()
    e = api.get(f"/api/admin/elections/{e['id']}").json()
    tally = e["questions"][0]["tally"]
    assert e["state"] == "mixing"
    assert tally["state"] == "failed" and "TOO_FEW" in tally["error"]
    e = act(api, e, "retry-count")
    assert e["questions"][0]["tally"]["state"] == "pending"
    queue.drain()
    assert api.get(f"/api/admin/elections/{e['id']}").json()["questions"][0]["tally"]["state"] == "failed"


def test_count_survives_an_unreachable_node(api, queue, services):
    e = frozen_and_open(api, queue, questions=(PRESIDENT,))
    for voter, choice in enumerate([0, 2, 2]):
        cast(services, e, voter, 0, encode(PRESIDENT, (choice,)))
    act(api, e, "close")
    act(api, e, "count")
    services.connect.down.add("node-2")
    queue.drain()
    e = api.get(f"/api/admin/elections/{e['id']}").json()
    tally = e["questions"][0]["tally"]
    assert tally["state"] == "pending" and len([s for s in tally["steps"] if s["record_key"]]) == 2
    services.connect.down.clear()
    act(api, e, "retry-count")  # what the worker's retry would do
    queue.drain()
    e = api.get(f"/api/admin/elections/{e['id']}").json()
    assert e["state"] == "tallied"
    assert e["questions"][0]["tally"]["result"]["counts"] == [1, 0, 2]


# --- admin API access ---------------------------------------------------------------------------


def test_admin_api_needs_the_token(api):
    assert api.get("/api/admin/elections", headers={"Authorization": ""}).status_code == 401
    assert api.get("/api/admin/elections", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_bearer_access_is_off_without_a_token(settings, services, queue):
    settings = settings.model_copy(update={"admin_token": None})
    with TestClient(create_app(settings, services=services, queue=queue)) as client:
        r = client.get("/api/admin/elections", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    assert r.status_code == 401


def test_unknown_election(api):
    assert api.get(f"/api/admin/elections/{uuid.uuid4()}").status_code == 404
