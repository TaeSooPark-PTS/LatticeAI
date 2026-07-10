"""Machine-global Brain Network administration must be administrator-only."""

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from latticeai.api.network import create_network_router


class _Network:
    def list_peers(self):
        return []

    def add_peer(self, **_kwargs):
        return {}

    def remove_peer(self, _peer_id):
        return {}

    def push_to_peer(self, _peer_id, *, workspace_id=None):
        return {"workspace_id": workspace_id}

    def receive(self, _headers, _body):
        return {"status": "ok"}


def _client(*, admin: bool) -> TestClient:
    app = FastAPI()

    def require_admin(_request):
        if not admin:
            raise HTTPException(status_code=403, detail="admin only")
        return ("admin@example.com", {})

    app.include_router(create_network_router(
        network=_Network(),
        identity=type("Identity", (), {"describe": lambda self: {"id": "device"}})(),
        require_user=lambda _request: "user@example.com",
        require_admin=require_admin,
    ))
    return TestClient(app)


def test_network_identity_remains_user_readable() -> None:
    response = _client(admin=False).get("/network/identity")

    assert response.status_code == 200
    assert response.json()["id"] == "device"


def test_peer_registry_and_push_require_admin() -> None:
    client = _client(admin=False)

    responses = [
        client.get("/network/peers"),
        client.post("/network/peers", json={"name": "peer", "base_url": "https://peer", "public_key": "key"}),
        client.delete("/network/peers/peer-id"),
        client.post("/network/push/peer-id", json={"workspace_id": "org:secret"}),
    ]

    assert [response.status_code for response in responses] == [403, 403, 403, 403]
