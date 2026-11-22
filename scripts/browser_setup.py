"""Prepares scripts/browser_test.mjs: logins manager1 and voter0..2, and an election
that the scheduler (celery beat) opens a few seconds after its keys are in.

BALLOT_BOX_ADMIN_TOKEN=... uv run python scripts/browser_setup.py > election-id
"""

import os
import time
from datetime import UTC, datetime, timedelta

import httpx

B = os.environ.get("E2E_BASE_URL", "http://localhost:5173")
PW = "browser test password"
admin = httpx.Client(base_url=B, headers={"Authorization": f"Bearer {os.environ['BALLOT_BOX_ADMIN_TOKEN']}"})
for u in ["manager1", "voter0", "voter1", "voter2"]:
    assert admin.put("/api/admin/password-logins", json={"username": u, "password": PW}).status_code == 204
m = httpx.Client(base_url=B, headers={"Origin": B})
assert m.post("/auth/password/login", json={"username": "manager1", "password": PW}).status_code == 204
acct = next(a for a in admin.get("/api/admin/accounts").json() if a["identity"] == "password:manager1")
admin.patch(f"/api/admin/accounts/{acct['id']}", json={"can_manage": True})
qs = [
    {"title": "Chair", "answers": ["Ana", "Bruno", "Carla"], "min_choices": 1, "max_choices": 1},
    {"title": "Board", "answers": ["Dora", "Ezra", "Fran", "Gil"], "min_choices": 0, "max_choices": 2},
]
# Created without questions; they come later, as in the admin pages.
e = m.post("/api/admin/elections", json={"title": "Browser election"}).json()
assert m.patch(f"/api/admin/elections/{e['id']}", json={"questions": qs}).status_code == 200
csv = "identity\n" + "".join(f"password:voter{i}\n" for i in range(3))
r = m.post(f"/api/admin/elections/{e['id']}/voters/csv", content=csv, headers={"Content-Type": "text/csv"})
assert r.json() == {"rows": 3, "added": 3}, r.text
opens = datetime.now(UTC) + timedelta(seconds=5)
r = m.put(f"/api/admin/elections/{e['id']}/schedule", json={"opens_at": opens.isoformat(), "closes_at": None})
assert r.status_code == 200, r.text
m.post(f"/api/admin/elections/{e['id']}/freeze")
state = None
for _ in range(200):  # keygen, then the scheduler opens it
    state = m.get(f"/api/admin/elections/{e['id']}").json()["state"]
    if state == "open":
        break
    time.sleep(0.3)
assert state == "open", f"the scheduler did not open the election: {state}"
print(e["id"])
