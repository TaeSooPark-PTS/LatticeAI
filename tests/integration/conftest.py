import os

import httpx
import pytest

BASE_URL = os.environ.get("LTCAI_TEST_BASE_URL", "http://localhost:8899")


@pytest.fixture(scope="session")
def live_server_base_url() -> str:
    try:
        httpx.get(f"{BASE_URL}/health", timeout=3)
    except httpx.HTTPError as exc:
        pytest.skip(f"live server not reachable at {BASE_URL}: {exc}")
    return BASE_URL
