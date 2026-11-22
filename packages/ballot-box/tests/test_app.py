from fastapi.testclient import TestClient

from ballot_box.app import create_app
from ballot_box.config import Settings


def test_health(services, queue):
    settings = Settings(secret_key="test", database_url=services.settings.database_url)
    with TestClient(create_app(settings, services=services, queue=queue)) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["evmlr"]["modulus"] == 3109
