"""Coverage for SearchService (wp34).

``SearchService`` now only reports the resolved embedder. Retrieval lives in
``lattice-retrieval``; these tests drive ``embeddings_status`` over fake
providers so the grade / state / health mapping stays honest.
"""

from __future__ import annotations

from latticeai.services.search_service import SearchService


class _Provider:
    def __init__(self, *, metadata_error=None, health_value=None):
        self.metadata_error = metadata_error
        self.health_value = health_value

    def metadata(self):
        if self.metadata_error:
            raise self.metadata_error
        return {"provider": "local", "model": "m", "model_id": "m", "dim": 8, "grade": "production"}

    def health(self):
        return self.health_value


class _Resolved:
    def __init__(self, provider):
        self.provider = provider


class _LegacyProvider:
    model_id = "lattice-local-hash-v1"
    dim = 384


def test_embedding_metadata_failure_degrades_to_an_empty_descriptor():
    service = SearchService(embedder=_Resolved(_Provider(metadata_error=RuntimeError("provider gone"))))

    status = service.embeddings_status(resolved={"fell_back": True})

    assert status["provider"] is None
    assert status["state"] == "unavailable"
    assert status["last_indexed_at"] is None
    assert status["index"] == {}


def test_legacy_hash_embedder_reports_the_fallback_grade():
    service = SearchService(embedder=_Resolved(_LegacyProvider()))

    status = service.embeddings_status()

    assert status["provider"] == "hash"
    assert status["dimensions"] == 384
    assert status["grade"] == "fallback"
    assert status["state"] == "fallback"


def test_refresh_asks_the_embedder_for_live_health():
    provider = _Provider(health_value={"status": "ok", "detail": "reachable"})
    service = SearchService(embedder=_Resolved(provider))

    status = service.embeddings_status(resolved={"health": {"status": "unknown"}}, refresh=True)

    assert status["health"] == {"status": "ok", "detail": "reachable"}
    assert status["state"] == "production"


def test_a_missing_embedder_reports_the_hash_fallback():
    status = SearchService(embedder=None).embeddings_status()

    assert status["provider"] == "hash"
    assert status["model_id"] == "lattice-local-hash-v1"
    assert status["dimensions"] == 384
    assert status["state"] == "fallback"
