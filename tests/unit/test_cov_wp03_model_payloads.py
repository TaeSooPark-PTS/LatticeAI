"""Model payload builders: health/engine summaries, recommendation edges, registry lookup.

These three modules are pure — no FastAPI, no runtime, no hardware — so the
tests feed them the exact inputs the routers feed them and assert the shape
that reaches the client.
"""

from __future__ import annotations

import types

import pytest

from latticeai.services import model_capability_registry as registry
from latticeai.services import model_recommendation as mr
from latticeai.services.model_service import ModelService


def _router(*, current="mlx-community/gemma-4-12b-it-4bit", loaded=("a", "b"), providers=()):
    return types.SimpleNamespace(
        current_model_id=current,
        loaded_model_ids=list(loaded),
        detected_cloud_models=lambda: list(providers),
    )


# ── ModelService ─────────────────────────────────────────────────────────────


def test_runtime_payload_is_whatever_the_injected_feature_probe_returns():
    calls = []

    def _features():
        calls.append(1)
        return {"mlx": True, "browser": False}

    service = ModelService(model_router=_router(), runtime_features=_features, is_public=False)

    assert service.runtime() == {"mlx": True, "browser": False}
    assert calls == [1]  # probed once, not cached behind the caller's back


def test_health_base_is_the_shared_prefix_of_every_health_response():
    service = ModelService(model_router=_router(), runtime_features=dict, is_public=False)

    assert service.health_base(version="10.10.0", mode="local") == {
        "status": "ok",
        "version": "10.10.0",
        "mode": "local",
        "platform": "AI Workspace OS",
    }


def test_full_health_merges_the_base_with_live_router_state():
    service = ModelService(
        model_router=_router(providers=[{"provider": "openai"}]),
        runtime_features=lambda: {"mlx": True},
        is_public=False,
    )
    base = service.health_base(version="10.10.0", mode="local")

    full = service.health_full(base, [{"engine": "ollama", "installed": True}])

    assert full["status"] == "ok"
    assert full["version"] == "10.10.0"
    assert full["current_model"] == "mlx-community/gemma-4-12b-it-4bit"
    assert full["loaded_models"] == ["a", "b"]
    assert full["device"] == "Apple Silicon MLX"
    assert full["features"] == {"mlx": True}
    assert full["providers"] == [{"provider": "openai"}]
    assert full["engines"] == [{"engine": "ollama", "installed": True}]


def test_a_public_deployment_does_not_claim_apple_silicon():
    service = ModelService(model_router=_router(), runtime_features=dict, is_public=True)

    full = service.health_full(service.health_base(version="1", mode="public"), [])

    assert full["device"] == "Public cloud/API runtime"


def test_engines_payload_names_the_model_the_engines_are_serving():
    service = ModelService(model_router=_router(current=None), runtime_features=dict, is_public=False)

    assert service.engines_payload([{"engine": "vllm"}]) == {
        "engines": [{"engine": "vllm"}],
        "current": None,
    }


# ── model_recommendation edges ───────────────────────────────────────────────


@pytest.fixture
def runtime_supported(monkeypatch):
    """Pin runtime compatibility so sizing, not the local MLX install, decides."""
    monkeypatch.setattr(
        mr,
        "model_runtime_compatibility",
        lambda model_id, *, engine=None: {
            "model_id": model_id,
            "engine": engine,
            "status": "supported",
            "supported": True,
        },
    )


def test_unreadable_memory_downgrades_every_model_to_compatible(runtime_supported):
    """``ram_mb`` we cannot parse must not be read as "0 GB, nothing fits"."""
    profile = {"os": "darwin", "arch": "arm64", "ram_mb": "unknown", "gpu": {"vendor": "apple"}}

    result = mr.recommend_catalog(profile, engine="local_mlx")

    assert result["ram_gb"] == 0.0
    assert result["counts"][mr.RECOMMENDED] == 0
    assert result["counts"][mr.NOT_RECOMMENDED] == 0
    assert result["top_pick"] is None
    sized = [m for m in result["models"] if m["required_ram_gb"] is not None]
    assert sized
    assert all(m["status"] == mr.COMPATIBLE for m in sized)
    assert all("메모리 정보를 확인하지 못했습니다" in m["reason"] for m in sized)


def test_a_family_the_display_order_does_not_know_sorts_last(runtime_supported, monkeypatch):
    monkeypatch.setattr(
        mr,
        "ENGINE_MODEL_CATALOG",
        {
            "local_mlx": [
                {
                    "id": "fake/brand-new-9b",
                    "name": "Brand New 9B",
                    "family": "Brand New",
                    "modality": "multimodal",
                    "size": "5GB",
                },
                {
                    "id": "fake/gemma-4-12b",
                    "name": "Gemma 4 12B",
                    "family": "Gemma 4",
                    "modality": "multimodal",
                    "size": "7GB",
                },
                {
                    "id": "fake/text-only",
                    "name": "Text only",
                    "family": "Gemma 4",
                    "modality": "text",
                    "size": "1GB",
                },
            ]
        },
    )
    profile = {"os": "darwin", "arch": "arm64", "ram_mb": 64 * 1024, "gpu": {"vendor": "apple"}}

    result = mr.recommend_catalog(profile, engine="local_mlx")

    assert [f["family"] for f in result["families"]] == ["Gemma 4", "Brand New"]
    # Text-only rows are classified too, they just carry their own modality.
    assert [m["id"] for m in result["models"]] == [
        "fake/brand-new-9b", "fake/gemma-4-12b", "fake/text-only",
    ]
    assert result["top_pick"]["id"] == "fake/gemma-4-12b"


# ── model_capability_registry lookup + catalog projection ────────────────────


def test_capability_lookup_answers_none_for_an_unknown_model():
    assert registry.get_capability("nobody/does-not-exist") is None


def test_capability_lookup_accepts_either_the_id_or_the_repo_id():
    known = registry.get_all_capabilities()[0]

    assert registry.get_capability(known.id) is known
    assert registry.get_capability(known.hf_repo_id) is known


def test_the_real_registry_projects_an_mlx_catalog_without_the_fallback():
    """Every recommended entry hints local_mlx, so the projection is enough.

    11.2.0 deleted the "backfill local_mlx when the projection produced none"
    guard: no entry can reach the registry without a provider hint, so the
    branch was unreachable and only made the builder harder to read.
    """
    catalog = registry.build_engine_model_catalog()

    assert catalog["local_mlx"]
    assert all(entry.get("id") for entry in catalog["local_mlx"])
    assert len(catalog["local_mlx"]) == len(registry.get_recommended_capabilities())
