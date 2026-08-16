"""Integration tests for FastAPI endpoints.

Run against a live server:  pytest tests/integration/ --base-url http://localhost:8899
Default base URL falls back to http://localhost:8899 if flag not provided.
"""
import os

import httpx
import pytest

BASE_URL = os.environ.get("LTCAI_TEST_BASE_URL", "http://localhost:8899")


def _session_cookie() -> dict:
    """Return session cookie if LTCAI_TEST_SESSION env var is set, else empty dict."""
    sid = os.environ.get("LTCAI_TEST_SESSION", "")
    return {"session_id": sid} if sid else {}


@pytest.fixture(scope="session", autouse=True)
def _require_live_server(live_server_base_url):
    """Skip this module's tests when no live server is reachable."""
    return live_server_base_url


@pytest.fixture(scope="session")
def client():
    with httpx.Client(base_url=BASE_URL, cookies=_session_cookie(), timeout=15) as c:
        yield c


# ---------------------------------------------------------------------------
# Health / Info
# ---------------------------------------------------------------------------

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert "status" in data


def test_mode_endpoint(client):
    r = client.get("/mode")
    assert r.status_code == 200
    data = r.json()
    assert "model" in data or "mode" in data


def test_runtime_features(client):
    r = client.get("/runtime_features")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Local filesystem endpoints
# ---------------------------------------------------------------------------

def test_local_list_home(client):
    r = client.get("/local/list", params={"path": "~"})
    # 200 if authenticated, 401/403 if no session
    assert r.status_code in (200, 401, 403)


def test_local_list_requires_auth(client):
    """Without a session cookie the endpoint must reject the request."""
    r = httpx.get(f"{BASE_URL}/local/list", params={"path": "~"}, timeout=10)
    assert r.status_code in (401, 403)


def test_local_serve_missing_file(client):
    r = client.get("/local/serve", params={"path": "/nonexistent_lattice_ai_test_xyz.txt"})
    assert r.status_code in (400, 401, 403, 404)


# ---------------------------------------------------------------------------
# /chat — smoke test (no model required)
# ---------------------------------------------------------------------------

def test_chat_requires_message(client):
    r = client.post("/chat", json={})
    # Missing message → 422 validation error or 401 unauth
    assert r.status_code in (401, 403, 422)


# ---------------------------------------------------------------------------
# /agent — smoke test
# ---------------------------------------------------------------------------

def test_agent_requires_task(client):
    r = client.post("/agent", json={})
    assert r.status_code in (401, 403, 422)
