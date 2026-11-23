"""Smoke test of a running stack: one election from login to verified, published results.

Talks HTTP only, as the browser does, so run it against the address voters
use (the Vite dev server, or the deployment):

    BALLOT_BOX_ADMIN_TOKEN=... uv run python scripts/smoke_test.py [BASE_URL]

The ballot box's public URL must be BASE_URL (it checks the Origin header),
and it needs its Celery worker and mixnet nodes running. Creates password
logins manager1 and voter0..voter3, and an election titled "Smoke election".
"""

import hashlib
import os
import sys
import time

import httpx

import evmlr

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5173"
HDR = {"Origin": BASE}
admin = httpx.Client(
    base_url=BASE, headers={"Authorization": f"Bearer {os.environ['BALLOT_BOX_ADMIN_TOKEN']}"}
)
PW = "smoke test password"


def ok(r, code=None):
    assert r.status_code < 400 and (code is None or r.status_code == code), (r.status_code, r.text)
    return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else r


def browser(user):
    c = httpx.Client(base_url=BASE, headers=HDR)
    ok(c.post("/auth/password/login", json={"username": user, "password": PW}), 204)
    return c


def wait(eid, state, timeout=120):
    t = time.monotonic()
    while time.monotonic() - t < timeout:
        e = ok(manager.get(f"/api/admin/elections/{eid}"))
        if e["state"] == state:
            return e, time.monotonic() - t
        if e["error"] or any(q["tally"] and q["tally"]["state"] == "failed" for q in e["questions"]):
            raise SystemExit(f"failed: {e['error']} {[q['tally'] for q in e['questions']]}")
        time.sleep(0.5)
    raise SystemExit(f"timeout waiting for {state}: {e['state']}")


for u in ["manager1"] + [f"voter{i}" for i in range(4)]:
    ok(admin.put("/api/admin/password-logins", json={"username": u, "password": PW}), 204)
fresh = all(a["identity"] != "password:manager1" for a in ok(admin.get("/api/admin/accounts")))
manager = browser("manager1")
if fresh:  # on a reused database manager1 may already manage elections
    assert manager.get("/api/admin/elections").status_code == 403
acct = next(a for a in ok(admin.get("/api/admin/accounts")) if a["identity"] == "password:manager1")
ok(admin.patch(f"/api/admin/accounts/{acct['id']}", json={"can_manage": True}))

questions = [
    {"title": "Chair", "answers": ["Ana", "Bruno", "Carla"], "min_choices": 1, "max_choices": 1},
    {"title": "Board", "answers": ["D", "E", "F", "G"], "min_choices": 0, "max_choices": 2},
]
e = ok(manager.post("/api/admin/elections", json={"title": "Smoke election", "questions": questions}), 201)
eid = e["id"]
ok(
    manager.post(
        f"/api/admin/elections/{eid}/voters",
        json={"voters": [{"provider": "password", "external_id": f"voter{i}"} for i in range(4)]},
    )
)
ok(manager.post(f"/api/admin/elections/{eid}/freeze"))
e, dt = wait(eid, "frozen")
print(f"keygen via Celery: {dt:.1f}s, keys {[len(q['keys']) for q in e['questions']]}")
ok(manager.post(f"/api/admin/elections/{eid}/open"))

ballots = {
    "voter0": {0: [1, 0, 0], 1: [1, 1]},
    "voter1": {0: [0, 1, 0], 1: []},
    "voter2": {0: [0, 1, 0], 1: [0, 0, 0, 1]},
    "voter3": {0: [1, 1, 0]},
}  # voter3 malformed
for user, answers in ballots.items():
    c = browser(user)
    me = ok(c.get(f"/api/elections/{eid}/me"))
    files = {}
    for i, coeffs in answers.items():
        pp = c.get(f"/api/elections/{eid}/questions/{i}/params").content
        files[f"q{i}"] = (f"q{i}", evmlr.cast(pp, int(me["voter_id"]), coeffs))
    receipts = ok(c.post(f"/api/elections/{eid}/ballots", files=files), 201)
    assert all(
        r["tracker"] == hashlib.sha256(files[f"q{r['question_index']}"][1]).hexdigest() for r in receipts
    )
print("cast 7 ballots through the proxy")

ok(manager.post(f"/api/admin/elections/{eid}/close"))
ok(manager.post(f"/api/admin/elections/{eid}/count"))
e, dt = wait(eid, "tallied")
print(f"count via Celery (2 questions x 3 nodes): {dt:.1f}s")
ok(manager.post(f"/api/admin/elections/{eid}/publish"))

web = httpx.Client(base_url=BASE)
res = ok(web.get(f"/api/elections/{eid}/results"))["questions"]
for r in res:
    print(f"q{r['index']}: {r['result']} discarded={r['discarded']}")
    base = f"/api/elections/{eid}/questions/{r['index']}"
    pp = web.get(f"{base}/params").content
    board = ok(web.get(f"{base}/board"))["entries"]
    votes = evmlr.verify(
        pp,
        [web.get(f"{base}/ballots/{b['tracker']}").content for b in board],
        web.get(f"{base}/transcript").content,
    )
    print(f"   public verification OK: {len(votes)} votes from {len(board)} ballots")
assert res[0]["result"]["counts"] == [1, 2, 0] and res[0]["result"]["discarded"] == 1
assert res[1]["result"]["counts"] == [1, 1, 0, 1] and res[1]["result"]["blank"] == 1
print("SMOKE OK")
