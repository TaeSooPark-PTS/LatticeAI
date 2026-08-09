"""Remote MCP registry + plugin-directory fetchers.

Every network seam is a fake ``httpx`` module injected into
``latticeai.core.mcp_registry`` via ``monkeypatch``, so no socket is opened and
the module-level caches are restored after each test.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from latticeai.core import mcp_registry
from latticeai.core.mcp_catalog import MCP_REGISTRY


class FakeResponse:
    """Minimal stand-in for ``httpx.Response``."""

    def __init__(self, payload=None, *, text="", status_code=200, error=None):
        self._payload = payload
        self.text = text
        self.status_code = status_code
        self._error = error

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self._error is not None:
            raise self._error


class FakeAsyncClient:
    """Async context manager exposing only the ``get`` the registry uses."""

    def __init__(self, handler, calls, kwargs):
        self._handler = handler
        self.calls = calls
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        return self._handler(url, params)


class FakeHttpx:
    """Stands in for the ``httpx`` module inside ``mcp_registry``."""

    def __init__(self, handler):
        self._handler = handler
        self.calls: list = []
        self.client_kwargs: list = []

    def AsyncClient(self, **kwargs):
        self.client_kwargs.append(kwargs)
        return FakeAsyncClient(self._handler, self.calls, kwargs)


def _reset_remote(monkeypatch, *, cache=None, fetched_at=None):
    monkeypatch.setattr(mcp_registry, "_REMOTE_REGISTRY_CACHE", [] if cache is None else cache)
    monkeypatch.setattr(mcp_registry, "_REMOTE_REGISTRY_FETCHED_AT", fetched_at)


def _reset_directory(monkeypatch, *, cache=None, fetched_at=None):
    monkeypatch.setattr(mcp_registry, "_PLUGIN_DIRECTORY_CACHE", [] if cache is None else cache)
    monkeypatch.setattr(mcp_registry, "_PLUGIN_DIRECTORY_FETCHED_AT", fetched_at)
    monkeypatch.setattr(mcp_registry, "_REPO_LICENSE_CACHE", {})


_PAGE_ONE = {
    "servers": [
        {
            "server": {
                "name": "io.example/good",
                "title": "Good Server",
                "description": "does good things",
                "packages": [
                    {
                        "transport": {"type": "http"},
                        "registryType": "npm",
                        "identifier": "ignored-http",
                    },
                    {
                        "transport": {"type": "stdio"},
                        "registryType": "npm",
                        "identifier": "good-pkg",
                        "version": "1.4.0",
                    },
                ],
                "repository": {"url": "https://github.com/example/good"},
            },
            "_meta": {"io.modelcontextprotocol.registry/official": {"isLatest": True}},
        },
        {
            "server": {"name": "io.example/stale", "packages": []},
            "_meta": {"io.modelcontextprotocol.registry/official": {"isLatest": False}},
        },
    ],
    "nextCursor": "page-2",
}

_PAGE_TWO = {
    "servers": [
        {
            "server": {
                "name": "io.example/http-only",
                "packages": [
                    {"transport": {"type": "http"}, "registryType": "npm", "identifier": "x"},
                ],
            },
            "_meta": {},
        },
        {
            # ``documents`` already ships in the built-in catalog: the remote copy
            # must be dropped rather than shadow it.
            "server": {
                "name": "documents",
                "packages": [
                    {
                        "transport": {"type": "stdio"},
                        "registryType": "pypi",
                        "identifier": "documents-remote",
                    },
                ],
            },
            "_meta": {},
        },
    ],
}


def test_remote_registry_paginates_and_keeps_only_new_stdio_servers(monkeypatch):
    # An expired stamp must still trigger a refetch.
    _reset_remote(monkeypatch, fetched_at=datetime.now() - timedelta(hours=2))

    def handler(url, params):
        assert url == mcp_registry._REMOTE_REGISTRY_URL
        assert params["limit"] == 100
        if params.get("cursor") == "page-2":
            return FakeResponse(_PAGE_TWO)
        return FakeResponse(_PAGE_ONE)

    fake = FakeHttpx(handler)
    monkeypatch.setattr(mcp_registry, "httpx", fake)

    result = asyncio.run(mcp_registry._fetch_remote_mcp_registry())

    assert result == [{
        "id": "io-example-good",
        "name": "Good Server",
        "category": "MCP Registry",
        "install_mode": "npm",
        "package": "good-pkg",
        "package_version": "1.4.0",
        "description": "does good things",
        "keywords": [],
        "capabilities": [],
        "source": "registry",
        "homepage": "https://github.com/example/good",
    }]
    assert [call[1].get("cursor") for call in fake.calls] == [None, "page-2"]
    assert mcp_registry._REMOTE_REGISTRY_CACHE == result
    assert mcp_registry._REMOTE_REGISTRY_FETCHED_AT is not None


def test_remote_registry_serves_cache_inside_ttl_without_network(monkeypatch):
    cached = [{"id": "cached-entry"}]
    _reset_remote(monkeypatch, cache=cached, fetched_at=datetime.now())

    def explode(url, params):
        raise AssertionError("a cache hit must not reach the network")

    monkeypatch.setattr(mcp_registry, "httpx", FakeHttpx(explode))

    assert asyncio.run(mcp_registry._fetch_remote_mcp_registry()) == cached


def test_remote_registry_failure_keeps_the_previous_cache(monkeypatch):
    previous = [{"id": "previous"}]
    _reset_remote(monkeypatch, cache=previous)

    def handler(url, params):
        return FakeResponse({}, status_code=503, error=RuntimeError("503 upstream"))

    monkeypatch.setattr(mcp_registry, "httpx", FakeHttpx(handler))

    assert asyncio.run(mcp_registry._fetch_remote_mcp_registry()) == previous
    # A failed refresh must not stamp the cache as fresh.
    assert mcp_registry._REMOTE_REGISTRY_FETCHED_AT is None


def test_combined_registry_appends_remote_entries_after_builtins(monkeypatch):
    async def fake_remote():
        return [{"id": "remote-only"}]

    monkeypatch.setattr(mcp_registry, "_fetch_remote_mcp_registry", fake_remote)

    combined = asyncio.run(mcp_registry._get_combined_registry())

    assert combined[: len(MCP_REGISTRY)] == MCP_REGISTRY
    assert combined[len(MCP_REGISTRY):] == [{"id": "remote-only"}]


def test_repo_license_prefers_cache_then_the_known_map(monkeypatch):
    monkeypatch.setattr(mcp_registry, "_REPO_LICENSE_CACHE", {"cached/repo": "MIT"})

    def explode(url, params):
        raise AssertionError("no GitHub lookup expected")

    client = FakeAsyncClient(explode, [], {})

    assert asyncio.run(mcp_registry._get_repo_license(client, "cached/repo")) == "MIT"
    assert asyncio.run(mcp_registry._get_repo_license(client, "adobe/skills")) == "Apache-2.0"
    assert mcp_registry._REPO_LICENSE_CACHE["adobe/skills"] == "Apache-2.0"


def test_repo_license_reads_github_and_caches_empty_results(monkeypatch):
    monkeypatch.setattr(mcp_registry, "_REPO_LICENSE_CACHE", {})

    def handler(url, params):
        if url.endswith("/ok/repo"):
            return FakeResponse({"license": {"spdx_id": "BSD-3-Clause"}})
        if url.endswith("/missing/repo"):
            return FakeResponse({}, status_code=404)
        raise RuntimeError("network down")

    client = FakeAsyncClient(handler, [], {})

    assert asyncio.run(mcp_registry._get_repo_license(client, "ok/repo")) == "BSD-3-Clause"
    assert asyncio.run(mcp_registry._get_repo_license(client, "missing/repo")) == ""
    assert asyncio.run(mcp_registry._get_repo_license(client, "boom/repo")) == ""
    assert mcp_registry._REPO_LICENSE_CACHE == {
        "ok/repo": "BSD-3-Clause",
        "missing/repo": "",
        "boom/repo": "",
    }


_DIRECTORY_PAYLOAD = {
    "plugins": [
        {
            "name": "official",
            "source": "./plugins/official",
            "author": {"name": "Anthropic"},
            "description": "first party",
            "category": "core",
        },
        # Same-repo layout but not Anthropic: falls through to the dict branch and
        # is dropped because a bare string is not a repo reference.
        {"name": "string-source", "source": "./plugins/other", "author": {"name": "Someone"}},
        {"name": "no-repo", "source": {"url": ""}},
        {"name": "closed-source", "source": {"url": "https://github.com/closed/repo.git"}},
        {
            "name": "open-source",
            "source": {"url": "https://github.com/Airtable/skills/tree/main"},
            "author": {"name": ""},
            "description": "airtable",
            "category": "productivity",
        },
    ]
}


def test_plugin_directory_keeps_anthropic_and_open_licensed_third_parties(monkeypatch):
    _reset_directory(monkeypatch)

    def handler(url, params):
        if url.endswith("marketplace.json"):
            return FakeResponse(_DIRECTORY_PAYLOAD)
        if url.endswith("/closed/repo"):
            return FakeResponse({"license": {"spdx_id": "GPL-3.0"}})
        raise AssertionError("unexpected url " + url)

    monkeypatch.setattr(mcp_registry, "httpx", FakeHttpx(handler))

    result = asyncio.run(mcp_registry._fetch_plugin_directory())

    assert [p["name"] for p in result] == ["official", "open-source"]
    assert result[0] == {
        "name": "official",
        "description": "first party",
        "category": "core",
        "author": "Anthropic",
        "license": "Apache-2.0",
        "homepage": (
            "https://github.com/anthropics/claude-plugins-official/tree/main/plugins/official"
        ),
        "source_type": "anthropic",
    }
    assert result[1] == {
        "name": "open-source",
        "description": "airtable",
        "category": "productivity",
        "author": "Airtable",
        "license": "MIT",
        "homepage": "https://github.com/Airtable/skills",
        "source_type": "third-party",
    }
    assert mcp_registry._PLUGIN_DIRECTORY_CACHE == result
    assert mcp_registry._PLUGIN_DIRECTORY_FETCHED_AT is not None


def test_plugin_directory_serves_cache_inside_ttl(monkeypatch):
    cached = [{"name": "cached-plugin"}]
    _reset_directory(monkeypatch, cache=cached, fetched_at=datetime.now())

    def explode(url, params):
        raise AssertionError("a cache hit must not reach the network")

    monkeypatch.setattr(mcp_registry, "httpx", FakeHttpx(explode))

    assert asyncio.run(mcp_registry._fetch_plugin_directory()) == cached


def test_plugin_directory_failure_keeps_the_previous_cache(monkeypatch):
    previous = [{"name": "previous-plugin"}]
    _reset_directory(monkeypatch, cache=previous)

    def handler(url, params):
        return FakeResponse({}, status_code=500, error=RuntimeError("500 marketplace"))

    monkeypatch.setattr(mcp_registry, "httpx", FakeHttpx(handler))

    assert asyncio.run(mcp_registry._fetch_plugin_directory()) == previous
    assert mcp_registry._PLUGIN_DIRECTORY_FETCHED_AT is None
