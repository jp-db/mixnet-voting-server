from fastapi.testclient import TestClient

from ballot_box.app import create_app
from ballot_box.config import Settings


def test_health():
    client = TestClient(create_app(Settings(secret_key="test")))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["evmlr"]["modulus"] == 3109
