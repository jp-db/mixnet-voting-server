from fastapi.testclient import TestClient

from mixnet_node.app import create_app
from mixnet_node.config import Settings


def test_health():
    client = TestClient(create_app(Settings(node_name="node-0")))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "node": "node-0"}
