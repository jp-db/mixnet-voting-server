"""Prepares scripts/browser_test.mjs: logins manager1 and voter0..2, and an open election.

BALLOT_BOX_ADMIN_TOKEN=... uv run python scripts/browser_setup.py > election-id
"""

import os
import time

import httpx

B = "http://localhost:5173"
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
e = m.post("/api/admin/elections", json={"title": "Browser election", "questions": qs}).json()
voters = [{"provider": "password", "external_id": f"voter{i}"} for i in range(3)]
m.post(f"/api/admin/elections/{e['id']}/voters", json={"voters": voters})
m.post(f"/api/admin/elections/{e['id']}/freeze")
for _ in range(100):
    if m.get(f"/api/admin/elections/{e['id']}").json()["state"] == "frozen":
        break
    time.sleep(0.3)
assert m.post(f"/api/admin/elections/{e['id']}/open").json()["state"] == "open"
print(e["id"])
