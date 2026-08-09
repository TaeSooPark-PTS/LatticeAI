"""wp12 coverage — the Ollama and OpenAI-compatible providers, offline.

Both providers ``import httpx`` inside the call, which is the seam: a fake
``httpx`` module injected with ``monkeypatch.setitem(sys.modules, ...)`` is
picked up at call time and dropped again when the test ends, so no socket is
opened and the branches (batch endpoint, the ``/api/embed`` → ``/api/embeddings``
404 downgrade, transport failure, health) run identically on every platform.
The recorded requests are asserted too — a provider that embeds by calling the
wrong endpoint would still "not raise".
"""

from __future__ import annotations

import sys
import types

import pytest

from latticeai.core.embedding_providers import (
    EmbeddingUnavailable,
    OllamaEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    _RemoteConfig,
)


class _FakeResponse:
    def __init__(self, *, status_code=200, payload=None, error=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self._error = error

    def raise_for_status(self):
        if self._error is not None:
            raise self._error

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, handler, calls, **kwargs):
        self._handler = handler
        self._calls = calls
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def _record(self, method, url, payload, headers):
        self._calls.append(
            {
                "method": method,
                "url": url,
                "json": payload,
                "headers": headers,
                "timeout": self.kwargs.get("timeout"),
            }
        )

    def post(self, url, json=None, headers=None):
        self._record("POST", url, json, headers)
        return self._handler("POST", url, json)

    def get(self, url):
        self._record("GET", url, None, None)
        return self._handler("GET", url, None)


def _install_httpx(monkeypatch, handler):
    """Inject a fake ``httpx`` and return the list requests are recorded in."""
    calls = []
    module = types.ModuleType("httpx")

    def _client(**kwargs):
        return _FakeClient(handler, calls, **kwargs)

    module.Client = _client
    monkeypatch.setitem(sys.modules, "httpx", module)
    return calls


# ``build_embedding_provider`` passes ``dim=0`` when the caller did not pin one,
# which is what asks the provider to guess from the model name.
def _ollama(**kwargs):
    kwargs.setdefault("model", "nomic-embed-text")
    kwargs.setdefault("dim", 0)
    return OllamaEmbeddingProvider(_RemoteConfig(**kwargs))


def _openai(**kwargs):
    kwargs.setdefault("model", "text-embedding-3-small")
    kwargs.setdefault("dim", 0)
    return OpenAICompatibleEmbeddingProvider(_RemoteConfig(**kwargs))


# ── Ollama ───────────────────────────────────────────────────────────────────


def test_ollama_defaults_to_the_loopback_server_and_a_known_dimension():
    prov = _ollama(base_url="http://ollama.example:11434/")

    assert prov._base == "http://ollama.example:11434"
    assert prov.dim == 768
    assert prov.model_id == "ollama:nomic-embed-text:768"
    assert _ollama()._base == "http://127.0.0.1:11434"


def test_an_empty_batch_never_reaches_the_server(monkeypatch):
    def _handler(method, url, payload):
        raise AssertionError("no request should be made for an empty batch")

    calls = _install_httpx(monkeypatch, _handler)

    assert _ollama().embed_batch([]) == []
    assert calls == []


def test_ollama_batches_through_api_embed_and_locks_the_true_dimension(monkeypatch):
    def _handler(method, url, payload):
        assert url == "http://127.0.0.1:11434/api/embed"
        assert payload == {"model": "nomic-embed-text", "input": ["alpha", "beta"]}
        return _FakeResponse(payload={"embeddings": [[3.0, 4.0], [0.0, 0.0]]})

    calls = _install_httpx(monkeypatch, _handler)
    prov = _ollama()

    vectors = prov.embed_batch(["alpha", "beta"])

    assert [round(x, 6) for x in vectors[0]] == [0.6, 0.8]
    # a zero vector has no direction to normalize — it is returned as-is
    assert vectors[1] == [0.0, 0.0]
    # the server's real dimensionality overrides the guessed one
    assert prov.dim == 2
    assert len(calls) == 1


def test_ollama_accepts_a_single_embedding_key_and_truncates_giant_inputs(monkeypatch):
    def _handler(method, url, payload):
        assert len(payload["input"][0]) == 50_000
        return _FakeResponse(payload={"embedding": [1.0, 0.0, 0.0]})

    _install_httpx(monkeypatch, _handler)

    assert _ollama().embed_batch(["x" * 60_000]) == [[1.0, 0.0, 0.0]]


def test_ollama_downgrades_to_the_single_prompt_endpoint_on_404(monkeypatch):
    def _handler(method, url, payload):
        if url.endswith("/api/embed"):
            return _FakeResponse(status_code=404, payload={"error": "not found"})
        if payload["prompt"] == "alpha":
            return _FakeResponse(payload={"embedding": [0.0, 5.0]})
        return _FakeResponse(payload={})

    calls = _install_httpx(monkeypatch, _handler)
    prov = _ollama(base_url="http://ollama.example:11434")

    vectors = prov.embed_batch(["alpha", "beta"])

    assert vectors[0] == [0.0, 1.0]
    # an embedding the old endpoint declined to produce becomes a zero vector
    assert vectors[1] == [0.0, 0.0]
    assert [c["url"] for c in calls] == [
        "http://ollama.example:11434/api/embed",
        "http://ollama.example:11434/api/embeddings",
        "http://ollama.example:11434/api/embeddings",
    ]
    assert calls[1]["json"] == {"model": "nomic-embed-text", "prompt": "alpha"}


def test_an_ollama_transport_failure_is_reported_as_unavailable(monkeypatch):
    def _handler(method, url, payload):
        raise ConnectionError("connection refused")

    _install_httpx(monkeypatch, _handler)

    with pytest.raises(EmbeddingUnavailable, match="Ollama embedding failed: connection refused"):
        _ollama().embed_batch(["alpha"])


def test_a_reachable_ollama_server_is_healthy(monkeypatch):
    def _handler(method, url, payload):
        assert method == "GET"
        assert url == "http://127.0.0.1:11434/api/tags"
        return _FakeResponse(payload={"models": []})

    calls = _install_httpx(monkeypatch, _handler)

    assert _ollama(timeout=30.0).health() == {
        "status": "ok",
        "detail": "Ollama reachable at http://127.0.0.1:11434",
    }
    # the health probe caps the wait rather than inheriting the embed timeout
    assert len(calls) == 1
    assert calls[0]["timeout"] == 5.0


def test_an_ollama_server_that_answers_with_an_error_is_unavailable(monkeypatch):
    def _handler(method, url, payload):
        return _FakeResponse(status_code=500, error=RuntimeError("server error 500"))

    _install_httpx(monkeypatch, _handler)

    health = _ollama().health()

    assert health["status"] == "unavailable"
    assert "Ollama unreachable: server error 500" in health["detail"]


# ── OpenAI-compatible ────────────────────────────────────────────────────────


def test_the_openai_provider_defaults_to_the_public_endpoint_and_a_known_dimension():
    prov = _openai()

    assert prov._base == "https://api.openai.com/v1"
    assert prov.dim == 1536
    assert prov.model_id == "openai:text-embedding-3-small:1536"

    pinned = _openai(model="mystery-model", base_url="http://lm-studio.local:1234/v1/", dim=64)
    assert pinned._base == "http://lm-studio.local:1234/v1"
    assert pinned.dim == 64
    assert pinned.model_id == "openai:mystery-model:64"


def test_the_authorization_header_appears_only_when_a_key_is_configured():
    assert _openai()._headers() == {"Content-Type": "application/json"}
    assert _openai(api_key="sk-test")._headers() == {
        "Content-Type": "application/json",
        "Authorization": "Bearer sk-test",
    }


def test_openai_rows_are_reordered_by_index_before_they_are_returned(monkeypatch):
    def _handler(method, url, payload):
        assert url == "https://api.openai.com/v1/embeddings"
        assert payload == {"model": "text-embedding-3-small", "input": ["alpha", "beta"]}
        return _FakeResponse(
            payload={
                "data": [
                    {"index": 1, "embedding": [0.0, 2.0]},
                    {"index": 0, "embedding": [2.0, 0.0]},
                ]
            }
        )

    calls = _install_httpx(monkeypatch, _handler)
    prov = _openai(api_key="sk-test")

    vectors = prov.embed_batch(["alpha", "beta"])

    assert vectors == [[1.0, 0.0], [0.0, 1.0]]
    assert calls[0]["headers"]["Authorization"] == "Bearer sk-test"
    assert prov.dim == 2


def test_a_row_without_an_embedding_becomes_a_zero_vector(monkeypatch):
    def _handler(method, url, payload):
        return _FakeResponse(payload={"data": [{"index": 0}]})

    _install_httpx(monkeypatch, _handler)
    prov = _openai(dim=3)

    assert prov.embed_batch(["alpha"]) == [[0.0, 0.0, 0.0]]


def test_an_openai_transport_failure_is_reported_as_unavailable(monkeypatch):
    def _handler(method, url, payload):
        return _FakeResponse(status_code=401, error=RuntimeError("401 Unauthorized"))

    _install_httpx(monkeypatch, _handler)

    with pytest.raises(EmbeddingUnavailable, match="OpenAI-compatible embedding failed"):
        _openai().embed_batch(["alpha"])


def test_openai_health_pings_the_endpoint_with_a_real_embedding_call(monkeypatch):
    def _handler(method, url, payload):
        assert payload["input"] == ["ping"]
        return _FakeResponse(payload={"data": [{"index": 0, "embedding": [1.0]}]})

    calls = _install_httpx(monkeypatch, _handler)

    assert _openai(base_url="http://vllm.local/v1").health() == {
        "status": "ok",
        "detail": "http://vllm.local/v1 reachable",
    }
    assert calls[0]["url"] == "http://vllm.local/v1/embeddings"


def test_openai_health_surfaces_the_failure_detail(monkeypatch):
    def _handler(method, url, payload):
        raise ConnectionError("name or service not known")

    _install_httpx(monkeypatch, _handler)

    health = _openai().health()

    assert health["status"] == "unavailable"
    assert "name or service not known" in health["detail"]
