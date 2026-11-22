"""Managing elections: drafts without questions, the voting schedule, cloning,
elections open to all registered accounts, CSV rolls and the booth simulation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

import evmlr
from ballot_box.auth import password as password_auth
from ballot_box.auth.accounts import record_login
from ballot_box.auth.base import Identity
from ballot_box.elections import rolls
from ballot_box.elections import service as ops
from ballot_box.models import Election

from .conftest import node_token
from .test_elections import COUNCIL, PRESIDENT, act, cast, encode, new_election


def admin_election(api, e: dict) -> dict:
    return api.get(f"/api/admin/elections/{e['id']}").json()


def schedule(api, e: dict, opens_at: datetime | None, closes_at: datetime | None, expect: int = 200) -> dict:
    body = {
        "opens_at": opens_at.isoformat() if opens_at else None,
        "closes_at": closes_at.isoformat() if closes_at else None,
    }
    r = api.put(f"/api/admin/elections/{e['id']}/schedule", json=body)
    assert r.status_code == expect, r.text
    return r.json()


def frozen(api, queue, **kw) -> dict:
    e = new_election(api, **kw)
    act(api, e, "freeze")
    queue.drain()
    e = admin_election(api, e)
    assert e["state"] == "frozen", e
    return e


def tick(services, now: datetime | None = None) -> ops.ScheduleRun:
    with services.db.begin() as s:
        return ops.run_schedule(s, now)


def set_times(services, e: dict, **times) -> None:
    """Writes schedule times directly, bypassing the rules (e.g. a closing time in the past)."""
    with services.db.begin() as s:
        election = s.get_one(Election, uuid.UUID(e["id"]))
        for k, v in times.items():
            setattr(election, k, v)


def now() -> datetime:
    return datetime.now(UTC)


# --- drafts -------------------------------------------------------------------------------------


def test_a_draft_starts_without_questions_but_cannot_freeze_without_them(api):
    r = api.post("/api/admin/elections", json={"title": "Later"})
    assert r.status_code == 201, r.text
    e = r.json()
    assert e["questions"] == [] and [m["node"] for m in e["mix_servers"]] == ["node-0", "node-1", "node-2"]
    api.post(
        f"/api/admin/elections/{e['id']}/voters", json={"voters": [{"provider": "cas", "external_id": "a"}]}
    )
    r = api.post(f"/api/admin/elections/{e['id']}/freeze")
    assert r.status_code == 422 and "question" in r.json()["detail"]

    e = api.patch(f"/api/admin/elections/{e['id']}", json={"questions": [PRESIDENT]}).json()
    assert len(e["questions"]) == 1
    e = api.patch(f"/api/admin/elections/{e['id']}", json={"questions": []}).json()
    assert e["questions"] == []


def test_freezing_needs_voters(api):
    e = new_election(api, voters=0)
    r = api.post(f"/api/admin/elections/{e['id']}/freeze")
    assert r.status_code == 422 and "roll" in r.json()["detail"]


# --- the schedule -------------------------------------------------------------------------------


def test_the_scheduler_opens_and_closes(api, queue, services):
    opens, closes = now() + timedelta(hours=1), now() + timedelta(hours=2)
    draft = new_election(api)
    schedule(api, draft, closes, opens, expect=422)  # closes before it opens
    schedule(api, draft, opens, now() - timedelta(minutes=1), expect=422)  # closes in the past
    draft = schedule(api, draft, opens, closes)
    assert datetime.fromisoformat(draft["opens_at"]) == opens

    e = frozen(api, queue)
    schedule(api, e, opens, closes)
    assert tick(services) == ops.ScheduleRun(opened=[], closed=[])  # not due yet

    run = tick(services, opens + timedelta(seconds=1))
    assert run.opened == [uuid.UUID(e["id"])] and run.closed == []  # the draft is not opened
    assert admin_election(api, e)["state"] == "open"
    assert admin_election(api, draft)["state"] == "draft"
    assert tick(services, opens + timedelta(seconds=2)).opened == []  # idempotent

    assert tick(services, closes).closed == [uuid.UUID(e["id"])]
    assert admin_election(api, e)["state"] == "closed"


def test_after_freezing_the_opening_only_moves_earlier(api, queue):
    e = frozen(api, queue)
    opens, closes = now() + timedelta(hours=1), now() + timedelta(hours=3)
    schedule(api, e, opens, closes)
    schedule(api, e, opens + timedelta(minutes=5), closes, expect=422)  # postponed
    schedule(api, e, None, closes, expect=422)  # removed
    schedule(api, e, opens - timedelta(minutes=30), closes)  # brought forward
    schedule(api, e, opens - timedelta(minutes=30), closes + timedelta(days=1))  # extended
    schedule(api, e, opens - timedelta(minutes=30), opens)  # shortened

    e = act(api, e, "open")  # early, by hand
    assert e["state"] == "open"
    schedule(api, e, now() - timedelta(hours=1), closes, expect=409)  # already open
    e = schedule(api, e, datetime.fromisoformat(e["opens_at"]), None)  # closes by hand now
    assert e["closes_at"] is None
    schedule(api, e, datetime.fromisoformat(e["opens_at"]), now() - timedelta(minutes=1), expect=422)
    act(api, e, "close")
    schedule(api, e, None, None, expect=409)


def test_ballots_are_refused_once_the_closing_time_passes(api, queue, services):
    e = frozen(api, queue, questions=(PRESIDENT,))
    act(api, e, "open")
    cast(services, e, 0, 0, encode(PRESIDENT, (0,)))
    set_times(services, e, closes_at=now() - timedelta(seconds=1))  # the scheduler has not run yet
    with pytest.raises(ops.WrongState, match="closed"):
        cast(services, e, 1, 0, encode(PRESIDENT, (1,)))
    assert tick(services).closed == [uuid.UUID(e["id"])]


def test_a_closing_time_that_passed_during_keygen_blocks_the_opening(api, queue, services):
    e = new_election(api)
    schedule(api, e, now() + timedelta(minutes=1), now() + timedelta(minutes=2))
    act(api, e, "freeze")
    queue.drain()
    set_times(services, e, opens_at=now() - timedelta(minutes=2), closes_at=now() - timedelta(minutes=1))
    assert tick(services).opened == []
    e = admin_election(api, e)
    assert e["state"] == "frozen" and "reschedule" in e["error"]
    schedule(api, e, datetime.fromisoformat(e["opens_at"]), now() + timedelta(hours=1))
    assert tick(services).opened == [uuid.UUID(e["id"])]


def test_the_public_page_shows_the_schedule(api, queue, web):
    e = frozen(api, queue)
    closes = now() + timedelta(days=1)
    schedule(api, e, now() + timedelta(hours=1), closes)
    pub = web.get(f"/api/elections/{e['id']}").json()
    assert datetime.fromisoformat(pub["closes_at"]) == closes


# --- cloning ------------------------------------------------------------------------------------


def test_clone_copies_the_configuration_not_the_vote(api, queue, services):
    spare = {"name": "spare", "url": "http://spare", "token": node_token("spare")}
    assert api.post("/api/admin/mixnet-nodes", json=spare).status_code == 201
    e = frozen(api, queue, questions=(PRESIDENT, COUNCIL), mix_servers=["spare", "node-1"])
    schedule(api, e, now() + timedelta(hours=1), now() + timedelta(hours=2))
    act(api, e, "open")
    cast(services, e, 0, 0, encode(PRESIDENT, (0,)))

    r = api.post(f"/api/admin/elections/{e['id']}/clone", json={"title": "Next year"})
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["id"] != e["id"] and c["title"] == "Next year" and c["state"] == "draft"
    assert [q["spec"] for q in c["questions"]] == [q["spec"] for q in e["questions"]]
    assert all(q["crypto_id"] is None and q["keys"] == [] for q in c["questions"])
    assert [m["node"] for m in c["mix_servers"]] == ["spare", "node-1"]
    assert c["opens_at"] is None and c["closes_at"] is None
    old = {v["external_id"]: v["voter_id"] for v in api.get(f"/api/admin/elections/{e['id']}/voters").json()}
    new = {v["external_id"]: v["voter_id"] for v in api.get(f"/api/admin/elections/{c['id']}/voters").json()}
    assert old.keys() == new.keys() and not set(old.values()) & set(new.values())

    assert api.post(f"/api/admin/elections/{e['id']}/clone").json()["title"] == "Copy of Board election"
    act(api, c, "freeze")
    queue.drain()
    assert admin_election(api, c)["state"] == "frozen"


# --- open to all registered accounts ------------------------------------------------------------


def test_an_election_open_to_all_takes_the_registered_accounts_at_freeze(api, queue, services):
    with services.db.begin() as s:
        record_login(s, Identity("cas", "jdoe", email="jdoe@example.edu"), [])
        password_auth.set_password(s, "alice", "correct horse battery")
    e = new_election(api, voters=1, open_to_all=True)
    assert e["open_to_all"] and e["voter_count"] == 1

    e = act(api, e, "freeze")
    roll = {
        (v["provider"], v["external_id"]) for v in api.get(f"/api/admin/elections/{e['id']}/voters").json()
    }
    assert roll == {("password", "voter0"), ("cas", "jdoe"), ("password", "alice")}

    # Back to draft: the registry's entries leave the roll, the manager's stay.
    e = act(api, e, "unfreeze")
    assert e["voter_count"] == 1
    assert api.post(f"/api/admin/elections/{e['id']}/clone").json()["voter_count"] == 1

    with services.db.begin() as s:
        record_login(s, Identity("cas", "late"), [])
    e = act(api, e, "freeze")
    queue.drain()
    assert admin_election(api, e)["voter_count"] == 4
    with services.db.begin() as s:
        record_login(s, Identity("cas", "too-late"), [])  # registered after the freeze: not on the roll
    assert admin_election(api, e)["voter_count"] == 4


# --- CSV ----------------------------------------------------------------------------------------


def test_voters_from_csv(api):
    e = new_election(api, voters=0)
    text = '﻿Identity,Name,Email\ncas:jdoe,"Doe, Jane",jdoe@example.edu\n\n# a comment\npassword:bob,,\n'
    r = api.post(
        f"/api/admin/elections/{e['id']}/voters/csv", content=text, headers={"Content-Type": "text/csv"}
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"rows": 2, "added": 2}
    voters = {v["external_id"]: v for v in api.get(f"/api/admin/elections/{e['id']}/voters").json()}
    assert voters["jdoe"]["display_name"] == "Doe, Jane" and voters["jdoe"]["email"] == "jdoe@example.edu"
    assert voters["bob"]["email"] is None

    r = api.post(
        f"/api/admin/elections/{e['id']}/voters/csv",
        content="cas:jdoe\ncas:new\n",
        headers={"Content-Type": "text/csv"},
    )
    assert r.json() == {"rows": 2, "added": 1}


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("jdoe\n", "line 1"),
        ("cas:a\nldap:b\n", "line 2: unknown login method"),
        ("cas:a,a@x,A,extra\n", "too many columns"),
        ("", "no voters"),
    ],
)
def test_bad_voter_csv(text, error):
    with pytest.raises(ops.Invalid, match=error):
        rolls.parse_voters(text)


def test_csv_dialects_and_split_identity_columns():
    assert rolls.parse_voters("provider;username;email\ncas;jdoe;j@x\n") == [
        ops.VoterEntry("cas", "jdoe", "j@x")
    ]
    assert rolls.parse_identities("cas:a\npassword:b\n") == [("cas", "a"), ("password", "b")]
    assert rolls.parse_identities("name,identity\nAnn,cas:ann\n") == [("cas", "ann")]


def test_csv_needs_a_draft(api, queue):
    e = frozen(api, queue)
    r = api.post(
        f"/api/admin/elections/{e['id']}/voters/csv", content="cas:x\n", headers={"Content-Type": "text/csv"}
    )
    assert r.status_code == 409


# --- the booth simulation -----------------------------------------------------------------------


def test_test_ballots_are_checked_and_not_kept(api, queue, web):
    e = new_election(api, questions=(PRESIDENT,))
    r = api.post(f"/api/admin/elections/{e['id']}/test-ballots", files={"q0": ("q0", b"x" * 100)})
    assert r.status_code == 409  # no parameters before the keys are in

    e = frozen(api, queue, questions=(PRESIDENT,))
    pp = web.get(f"/api/elections/{e['id']}/questions/0/params").content
    ballot = evmlr.cast(pp, 12345, encode(PRESIDENT, (2,)))
    r = api.post(f"/api/admin/elections/{e['id']}/test-ballots", files={"q0": ("q0", ballot)})
    assert r.status_code == 200, r.text
    assert r.json() == [
        {
            "question_index": 0,
            "voter_id": "12345",
            "bytes": len(ballot),
            "tracker": sha256(ballot).hexdigest(),
        }
    ]

    bad = bytearray(ballot)
    bad[100] ^= 1
    r = api.post(f"/api/admin/elections/{e['id']}/test-ballots", files={"q0": ("q0", bytes(bad))})
    assert r.status_code == 422
    r = api.post(f"/api/admin/elections/{e['id']}/test-ballots", files={"q1": ("q1", ballot)})
    assert r.status_code == 422
    assert web.get(f"/api/elections/{e['id']}/questions/0/board").json()["entries"] == []
