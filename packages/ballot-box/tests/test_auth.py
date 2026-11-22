"""Logging in (password, CAS), sessions, and who may use the admin API."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from ballot_box import accounts as accounts_cli
from ballot_box.app import create_app
from ballot_box.models import Account

from .conftest import ADMIN_TOKEN
from .test_elections import PRESIDENT
from .test_voting import PUBLIC, booth

CAS_SERVER = "https://cas.example.edu/cas/"
PASSWORD = "correct horse battery"


class FakeCasHttp:
    """Stands in for the requests session python-cas validates tickets with."""

    def __init__(self) -> None:
        self.tickets: dict[str, str] = {}  # ticket -> user
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None, verify=True, **_):
        self.calls.append((url, params))
        user = self.tickets.pop(params["ticket"], None)
        if user is None:
            body = (
                '<cas:serviceResponse xmlns:cas="http://www.yale.edu/tp/cas">'
                '<cas:authenticationFailure code="INVALID_TICKET">no</cas:authenticationFailure>'
                "</cas:serviceResponse>"
            )
        else:
            body = (
                '<cas:serviceResponse xmlns:cas="http://www.yale.edu/tp/cas"><cas:authenticationSuccess>'
                f"<cas:user>{user}</cas:user><cas:attributes><cas:mail>{user}@example.edu</cas:mail>"
                f"<cas:displayName>{user.title()}</cas:displayName></cas:attributes>"
                "</cas:authenticationSuccess></cas:serviceResponse>"
            )
        return type("Resp", (), {"content": body.encode(), "close": lambda self: None})()


@pytest.fixture
def auth_app(settings, services, queue):
    settings = settings.model_copy(
        update={
            "auth_providers": ["password", "cas"],
            "cas_server_url": CAS_SERVER,
            "admin_identities": ["cas:boss"],
        }
    )
    app = create_app(settings, services=services, queue=queue)
    with TestClient(app, base_url=PUBLIC):  # runs the lifespan once
        app.state.cas.http_session = FakeCasHttp()
        yield app


@pytest.fixture
def admin(auth_app) -> TestClient:
    return TestClient(auth_app, base_url=PUBLIC, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})


def browser(app) -> TestClient:
    return TestClient(app, base_url=PUBLIC, follow_redirects=False)


def add_password_login(admin: TestClient, username: str, password: str = PASSWORD) -> None:
    r = admin.put("/api/admin/password-logins", json={"username": username, "password": password})
    assert r.status_code == 204, r.text


def password_login(client: TestClient, username: str, password: str = PASSWORD):
    return client.post("/auth/password/login", json={"username": username, "password": password})


def cas_login(app, client: TestClient, user: str, next_path: str = "/"):
    r = client.get("/auth/cas/login", params={"next": next_path})
    service = parse_qs(urlsplit(r.headers["location"]).query)["service"][0]
    app.state.cas.http_session.tickets[f"ST-{user}"] = user
    callback = urlsplit(service)
    return client.get(f"{callback.path}?{callback.query}&ticket=ST-{user}")


# --- password -----------------------------------------------------------------------------------


def test_password_login_and_logout(auth_app, admin):
    add_password_login(admin, "alice")
    web = browser(auth_app)
    assert web.get("/auth/me").status_code == 401
    wrong = password_login(web, "alice", "wrong password!")
    unknown = password_login(web, "nobody")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()  # the same answer: usernames are not revealed

    assert password_login(web, "alice").status_code == 204
    me = web.get("/auth/me").json()
    assert me["provider"] == "password" and me["external_id"] == "alice"
    assert not me["is_admin"] and not me["can_manage"]
    cookie = web.cookies.get("ballot_box_session")
    assert cookie and "alice" not in cookie.split(".")[-1]  # signed, not readable-and-forgeable

    web.post("/auth/logout")
    assert web.get("/auth/me").status_code == 401


def test_weak_passwords_are_refused(admin):
    r = admin.put("/api/admin/password-logins", json={"username": "bob", "password": "short"})
    assert r.status_code == 422


def test_a_tampered_cookie_is_ignored(auth_app, admin):
    add_password_login(admin, "alice")
    web = browser(auth_app)
    password_login(web, "alice")
    value = web.cookies["ballot_box_session"]
    web.cookies.set("ballot_box_session", value[:-2] + ("AA" if value[-2:] != "AA" else "BB"))
    assert web.get("/auth/me").status_code == 401


def test_providers_and_disabled_logins(auth_app, settings, services, queue):
    web = browser(auth_app)
    assert [p["name"] for p in web.get("/auth/providers").json()] == ["password", "cas"]
    only_password = create_app(settings, services=services, queue=queue)
    with TestClient(only_password, base_url=PUBLIC) as client:
        assert client.get("/auth/cas/login").status_code == 404


def test_cross_site_login_post_is_refused(auth_app, admin):
    add_password_login(admin, "alice")
    r = browser(auth_app).post(
        "/auth/password/login",
        json={"username": "alice", "password": PASSWORD},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403


# --- CAS ----------------------------------------------------------------------------------------


def test_cas_login(auth_app):
    web = browser(auth_app)
    r = web.get("/auth/cas/login", params={"next": "/elections/1"})
    assert r.status_code == 303
    location = urlsplit(r.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == CAS_SERVER + "login"
    service = parse_qs(location.query)["service"][0]
    assert service == f"{PUBLIC}/auth/cas/callback?next=%2Felections%2F1"

    r = cas_login(auth_app, web, "jdoe", "/elections/1")
    assert r.status_code == 303 and r.headers["location"] == "/elections/1"
    me = web.get("/auth/me").json()
    assert (me["provider"], me["external_id"], me["email"]) == ("cas", "jdoe", "jdoe@example.edu")
    url, params = auth_app.state.cas.http_session.calls[-1]
    assert url == CAS_SERVER + "p3/serviceValidate" and params["service"] == service


def test_cas_rejects_unknown_tickets(auth_app):
    web = browser(auth_app)
    assert web.get("/auth/cas/callback", params={"ticket": "ST-forged", "next": "/"}).status_code == 401
    assert web.get("/auth/me").status_code == 401


@pytest.mark.parametrize("bad", ["//evil.example/x", "https://evil.example", "/\\evil.example", "x"])
def test_no_open_redirect(auth_app, bad):
    r = cas_login(auth_app, browser(auth_app), "jdoe", bad)
    assert r.headers["location"] == "/"


# --- roles --------------------------------------------------------------------------------------


def test_a_plain_account_cannot_manage(auth_app, admin):
    add_password_login(admin, "alice")
    web = browser(auth_app)
    password_login(web, "alice")
    assert web.get("/api/admin/elections").status_code == 403
    assert web.get("/api/admin/elections", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_configured_admins_and_granted_managers(auth_app, admin):
    boss = browser(auth_app)
    cas_login(auth_app, boss, "boss")
    assert boss.get("/auth/me").json()["is_admin"]
    accounts = {a["identity"]: a for a in boss.get("/api/admin/accounts").json()}
    assert accounts["cas:boss"]["is_admin"]

    for name in ("mallory", "carol"):
        add_password_login(admin, name)
    carol, mallory = browser(auth_app), browser(auth_app)
    password_login(carol, "carol")
    password_login(mallory, "mallory")
    ids = {a["identity"]: a["id"] for a in boss.get("/api/admin/accounts").json()}
    for who in ("password:carol", "password:mallory"):
        r = boss.patch(f"/api/admin/accounts/{ids[who]}", json={"can_manage": True})
        assert r.json()["can_manage"]

    # A manager creates an election and is its manager; another manager does not see it.
    e = carol.post("/api/admin/elections", json={"title": "Club", "questions": [PRESIDENT]}).json()
    assert carol.get(f"/api/admin/elections/{e['id']}/managers").json() == ["password:carol"]
    assert [x["id"] for x in carol.get("/api/admin/elections").json()] == [e["id"]]
    assert mallory.get("/api/admin/elections").json() == []
    assert mallory.get(f"/api/admin/elections/{e['id']}").status_code == 404
    assert mallory.post(f"/api/admin/elections/{e['id']}/freeze").status_code == 404
    assert e["id"] in [x["id"] for x in boss.get("/api/admin/elections").json()]

    # Managers can share an election; only administrators touch nodes and accounts.
    r = carol.put(
        f"/api/admin/elections/{e['id']}/managers", json={"managers": ["password:carol", "password:mallory"]}
    )
    assert r.json() == ["password:carol", "password:mallory"]
    assert mallory.get(f"/api/admin/elections/{e['id']}").status_code == 200
    for bad in (["ghost"], ["ldap:ghost"], []):
        r = carol.put(f"/api/admin/elections/{e['id']}/managers", json={"managers": bad})
        assert r.status_code == 422, bad
    assert carol.get("/api/admin/accounts").status_code == 403
    node = {"name": "x", "url": "http://x", "token": "t" * 40}
    assert carol.post("/api/admin/mixnet-nodes", json=node).status_code == 403
    assert carol.get("/api/admin/mixnet-nodes").status_code == 200  # to pick the mix servers
    assert (
        carol.put("/api/admin/password-logins", json={"username": "z", "password": PASSWORD}).status_code
        == 403
    )

    r = boss.patch(f"/api/admin/accounts/{ids['cas:boss']}", json={"is_admin": False})
    assert r.status_code == 409


def test_election_managers_need_no_role_and_may_not_have_logged_in(auth_app, admin):
    e = admin.post("/api/admin/elections", json={"title": "Club"}).json()
    r = admin.post(
        f"/api/admin/elections/{e['id']}/managers/csv",
        content="identity\ncas:dora\npassword:erin\n",
        headers={"Content-Type": "text/csv"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == ["cas:dora", "password:erin"]

    # Dora has never logged in; when she does, she manages that election but may not create others.
    dora = browser(auth_app)
    cas_login(auth_app, dora, "dora")
    me = dora.get("/auth/me").json()
    assert me["can_manage"] and not me["can_create"] and not me["is_admin"]
    assert [x["id"] for x in dora.get("/api/admin/elections").json()] == [e["id"]]
    assert dora.patch(f"/api/admin/elections/{e['id']}", json={"title": "Club 2"}).status_code == 200
    assert dora.post("/api/admin/elections", json={"title": "Mine"}).status_code == 403
    assert dora.post(f"/api/admin/elections/{e['id']}/clone").status_code == 403

    r = admin.post(
        f"/api/admin/elections/{e['id']}/managers/csv",
        content="nobody\n",
        headers={"Content-Type": "text/csv"},
    )
    assert r.status_code == 422 and "line 1" in r.json()["detail"]


def test_cross_site_admin_post_with_a_cookie_is_refused(auth_app):
    boss = browser(auth_app)
    cas_login(auth_app, boss, "boss")
    r = boss.post(
        "/api/admin/elections",
        json={"title": "x", "questions": [PRESIDENT]},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403


# --- a voter, end to end ------------------------------------------------------------------------


def test_voter_logs_in_with_a_password_and_casts(auth_app, admin, queue):
    add_password_login(admin, "voter0")
    e = admin.post("/api/admin/elections", json={"title": "E", "questions": [PRESIDENT]}).json()
    roll = [{"provider": "password", "external_id": f"voter{i}"} for i in range(2)]
    admin.post(f"/api/admin/elections/{e['id']}/voters", json={"voters": roll})
    admin.post(f"/api/admin/elections/{e['id']}/freeze")
    queue.drain()
    admin.post(f"/api/admin/elections/{e['id']}/open")

    web = browser(auth_app)
    assert password_login(web, "voter0").status_code == 204
    r = booth(web, e, {0: (1,)})
    assert r.status_code == 201, r.text
    assert web.get(f"/api/elections/{e['id']}/me").json()["cast"][0]["tracker"] == r.json()[0]["tracker"]


# --- the command line ---------------------------------------------------------------------------


def test_accounts_cli(monkeypatch, settings, services):
    monkeypatch.setattr(accounts_cli, "get_settings", lambda: settings)
    monkeypatch.setattr(accounts_cli.getpass, "getpass", lambda prompt="": PASSWORD)
    assert accounts_cli.main(["set-password", "root", "--admin"]) == 0
    assert accounts_cli.main(["grant", "cas:newboss", "manager"]) == 0
    assert accounts_cli.main(["grant", "nonsense", "admin"]) == 1
    with services.db() as s:
        rows = {(a.provider, a.external_id): a for a in s.query(Account)}
    assert rows["password", "root"].is_admin
    assert rows["cas", "newboss"].can_manage and not rows["cas", "newboss"].is_admin
    monkeypatch.setattr(accounts_cli.getpass, "getpass", lambda prompt="": "short")
    assert accounts_cli.main(["set-password", "weak"]) == 1
