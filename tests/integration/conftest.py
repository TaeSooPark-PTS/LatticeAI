"""Shared fixtures for integration tests.

These tests exercise a *live* FastAPI server (started by
``scripts/run_integration_tests.mjs``). When the suite is run with a plain
``pytest tests/`` and no server is listening, the HTTP calls previously raised
``httpx.ConnectError`` and reported hard failures — noise that masks real
regressions in CI and local runs. Probe the target once per session and skip
the live-server tests when the server is unreachable instead of failing.
"""
import os

import httpx
import pytest

BASE_URL = os.environ.get("LTCAI_TEST_BASE_URL", "http://localhost:8899")


@pytest.fixture(scope="session")
def live_server_base_url() -> str:
    """Return the base URL of a reachable server or skip the requesting test."""
    try:
        httpx.get(f"{BASE_URL}/health", timeout=3)
    except httpx.HTTPError as exc:
        pytest.skip(f"live server not reachable at {BASE_URL}: {exc}")
    return BASE_URL
