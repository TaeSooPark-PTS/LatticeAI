"""Embedding auto-detection, adoption, and the honest surface (v12.0.0).

Detection is filesystem-first and must never download, never fail a boot, and
never claim more than it found. Adoption is a separate, explicit decision —
these tests pin both halves, because the failure that matters is a machine
silently switching the identity every stored vector is filed under.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Sequence

import pytest

from latticeai.core.embedding_providers import resolve_embedder
from latticeai.core.embedding_providers.autodetect import (
    AUTO_PROVIDER,
    Detection,
    autodetect_enabled,
    detect_embedder,
    detect_local_mlx,
    detect_ollama,
    hf_cache_roots,
    resolve_auto_provider,
)
from latticeai.core.embedding_providers.text import (
    E5_PREFIXES,
    MLX_MAX_TOKENS,
    HashEmbeddingProvider,
    MLXEmbeddingProvider,
    _wants_e5_prefix,
)
from latticeai.runtime.brain_runtime import build_embedder_runtime
from latticeai.services.search_service import SearchService


def _plant(root: Path, repo_id: str, *, weights: bool = True, config: bool = True) -> None:
    """Write a snapshot that looks exactly like a finished HF download."""
    snapshot = root / ("models--" + repo_id.replace("/", "--")) / "snapshots" / "abc123"
    snapshot.mkdir(parents=True, exist_ok=True)
    if config:
        (snapshot / "config.json").write_text("{}", encoding="utf-8")
    if weights:
        (snapshot / "model.safetensors").write_bytes(b"\x00")


# ── detection ────────────────────────────────────────────────────────────────


def test_nothing_downloaded_is_a_finding_not_a_guess(tmp_path: Path) -> None:
    found = detect_local_mlx({"HF_HUB_CACHE": str(tmp_path)})
    assert not found.found
    assert found.source == "none"
    assert found.provider == ""
    # Every candidate is still listed, so a UI can offer the download.
    assert found.candidates
    assert all(not candidate["downloaded"] for candidate in found.candidates)


def test_a_downloaded_model_is_detected_without_a_network_call(tmp_path: Path) -> None:
    _plant(tmp_path, "mlx-community/multilingual-e5-small-mlx")
    found = detect_local_mlx({"HF_HUB_CACHE": str(tmp_path)})
    assert found.found
    assert found.provider == "mlx"
    assert found.model == "mlx-community/multilingual-e5-small-mlx"
    assert found.dim == 384
    assert found.source == "local_model"
    assert found.as_dict()["found"] is True


def test_a_half_finished_download_is_not_a_model(tmp_path: Path) -> None:
    """A cache directory exists as soon as *anything* is fetched."""
    _plant(tmp_path, "mlx-community/multilingual-e5-small-mlx", weights=False)
    assert not detect_local_mlx({"HF_HUB_CACHE": str(tmp_path)}).found
    _plant(tmp_path, "mlx-community/multilingual-e5-base-mlx", config=False)
    assert not detect_local_mlx({"HF_HUB_CACHE": str(tmp_path)}).found


def test_the_best_downloaded_model_wins_the_preference_order(tmp_path: Path) -> None:
    _plant(tmp_path, "mlx-community/multilingual-e5-large-mlx")
    _plant(tmp_path, "mlx-community/multilingual-e5-small-mlx")
    found = detect_local_mlx({"HF_HUB_CACHE": str(tmp_path)})
    assert found.model == "mlx-community/multilingual-e5-small-mlx"


def test_cache_roots_respect_every_hugging_face_variable(tmp_path: Path) -> None:
    roots = hf_cache_roots(
        {"HF_HUB_CACHE": str(tmp_path / "a"), "HF_HOME": str(tmp_path / "b"), "HOME": "/nope"}
    )
    assert roots[0] == tmp_path / "a"
    assert tmp_path / "b" / "hub" in roots
    assert Path("/nope/.cache/huggingface/hub") in roots


def test_an_unreachable_ollama_is_not_found_and_never_raises() -> None:
    found = detect_ollama(base_url="http://127.0.0.1:1", timeout=0.05)
    assert not found.found
    assert found.source == "none"
    assert "no Ollama server" in found.detail


class _FakeResponse:
    def __init__(self, payload: Any):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    """Enough of `httpx.Client` for the one GET the Ollama probe makes."""

    def __init__(self, payload: Any, **_: Any):
        self._payload = payload

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def get(self, _url: str) -> _FakeResponse:
        return _FakeResponse(self._payload)


def _fake_httpx(monkeypatch: pytest.MonkeyPatch, payload: Any) -> None:
    """Stand in for `httpx` at the import the probe performs."""
    import sys
    import types

    module = types.ModuleType("httpx")
    module.Client = lambda **kwargs: _FakeClient(payload, **kwargs)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "httpx", module)


def test_a_pulled_ollama_embedder_is_found(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_httpx(
        monkeypatch,
        {"models": [{"name": "llama3:8b"}, "junk", {"name": "bge-m3:latest"}]},
    )
    found = detect_ollama(base_url="http://ollama.test")
    assert found.found
    assert found.provider == "ollama"
    assert found.model == "bge-m3:latest"
    assert found.dim == 1024
    assert found.source == "ollama"
    assert "ollama.test" in found.detail


def test_an_ollama_with_only_chat_models_is_not_an_embedder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_httpx(monkeypatch, {"models": [{"name": "llama3:8b"}]})
    found = detect_ollama(base_url="http://ollama.test")
    assert not found.found
    assert "no embedding model pulled" in found.detail


def test_detect_falls_through_to_a_reachable_ollama(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_httpx(monkeypatch, {"models": [{"name": "nomic-embed-text"}]})
    found = detect_embedder("hash", "", {"HF_HUB_CACHE": str(tmp_path)})
    assert found.provider == "ollama"
    assert found.dim == 768
    assert found.candidates, "the local candidate list is still offered"


def test_a_downloaded_model_wins_before_any_ollama_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The filesystem answer is free; the network probe is not, so it is last."""

    def _explode(**_: Any) -> Detection:  # pragma: no cover - must not run
        raise AssertionError("a downloaded model must short-circuit the probe")

    monkeypatch.setattr(
        "latticeai.core.embedding_providers.autodetect.detect_ollama", _explode
    )
    _plant(tmp_path, "mlx-community/multilingual-e5-small-mlx")
    found = detect_embedder("hash", "", {"HF_HUB_CACHE": str(tmp_path)})
    assert found.source == "local_model"


def test_a_stray_file_in_the_snapshots_directory_is_not_a_model(tmp_path: Path) -> None:
    root = tmp_path / "models--mlx-community--multilingual-e5-small-mlx" / "snapshots"
    root.mkdir(parents=True)
    (root / ".DS_Store").write_text("junk", encoding="utf-8")
    assert not detect_local_mlx({"HF_HUB_CACHE": str(tmp_path)}).found


def test_an_explicit_provider_is_never_second_guessed(tmp_path: Path) -> None:
    _plant(tmp_path, "mlx-community/multilingual-e5-small-mlx")
    found = detect_embedder("openai", "text-embedding-3-small", {"HF_HUB_CACHE": str(tmp_path)})
    assert found.source == "configured"
    assert found.provider == "openai"
    assert found.model == "text-embedding-3-small"


def test_detection_can_be_switched_off_entirely(tmp_path: Path) -> None:
    _plant(tmp_path, "mlx-community/multilingual-e5-small-mlx")
    env = {"HF_HUB_CACHE": str(tmp_path), "LATTICEAI_EMBEDDING_AUTODETECT": "0"}
    assert not autodetect_enabled(env)
    assert not detect_embedder("hash", "", env).found
    assert autodetect_enabled({})


def test_detect_falls_through_to_ollama_and_keeps_the_local_candidates(tmp_path: Path) -> None:
    env = {"HF_HUB_CACHE": str(tmp_path), "LATTICEAI_EMBEDDING_BASE_URL": "http://127.0.0.1:1"}
    found = detect_embedder("hash", "", env, probe_ollama=True)
    assert not found.found
    assert found.candidates, "the local candidate list survives a failed Ollama probe"


# ── adoption ─────────────────────────────────────────────────────────────────


def test_only_auto_changes_the_resolution() -> None:
    found = Detection(provider="mlx", model="m", dim=384, source="local_model")
    assert resolve_auto_provider("hash", "", 0, found) == ("hash", "", 0)
    assert resolve_auto_provider("openai", "x", 1536, found) == ("openai", "x", 1536)
    assert resolve_auto_provider(AUTO_PROVIDER, "", 0, found) == ("mlx", "m", 384)


def test_auto_with_nothing_found_resolves_to_hash_rather_than_to_a_failure() -> None:
    assert resolve_auto_provider(AUTO_PROVIDER, "", 0, Detection()) == ("hash", "", 0)


def test_explicit_model_and_dim_still_win_under_auto() -> None:
    found = Detection(provider="mlx", model="detected", dim=384, source="local_model")
    assert resolve_auto_provider(AUTO_PROVIDER, "mine", 768, found) == ("mlx", "mine", 768)


class _Config:
    embedding_provider = "hash"
    embedding_profile = ""
    embedding_model = ""
    embedding_base_url = ""
    embedding_api_key = ""
    embedding_dim = 0
    embedding_timeout = 30
    embedding_custom_target = ""


def _resolver(seen: List[Dict[str, Any]]):
    def resolve(provider: str, **kwargs: Any) -> Any:
        seen.append({"provider": provider, **kwargs})
        return resolve_embedder("hash")

    return resolve


def test_the_runtime_reports_what_it_found_even_when_it_stays_on_hash() -> None:
    seen: List[Dict[str, Any]] = []
    found = Detection(provider="mlx", model="m", dim=384, source="local_model")
    resolved = build_embedder_runtime(
        config=_Config(),
        profile={},
        resolve_embedder=_resolver(seen),
        detect=lambda **_: found,
    )
    assert seen[0]["provider"] == "hash", "detection alone never switches the provider"
    assert resolved.detected is found
    assert resolved.as_dict()["detected"]["model"] == "m"


def test_the_runtime_adopts_the_detected_provider_under_auto() -> None:
    seen: List[Dict[str, Any]] = []
    config = _Config()
    config.embedding_provider = AUTO_PROVIDER
    build_embedder_runtime(
        config=config,
        profile={},
        resolve_embedder=_resolver(seen),
        detect=lambda **_: Detection(
            provider="mlx", model="m", dim=384, source="local_model"
        ),
    )
    assert seen[0]["provider"] == "mlx"
    assert seen[0]["model"] == "m"
    assert seen[0]["dim"] == 384
    assert seen[0]["probe"] is True


def test_a_named_profile_still_beats_auto() -> None:
    seen: List[Dict[str, Any]] = []
    config = _Config()
    config.embedding_provider = AUTO_PROVIDER
    config.embedding_profile = "local:multilingual-e5-small"
    build_embedder_runtime(
        config=config,
        profile={"provider": "ollama", "model": "bge-m3", "dimensions": 1024},
        resolve_embedder=_resolver(seen),
        detect=lambda **_: Detection(provider="mlx", model="m", dim=384),
    )
    assert seen[0]["provider"] == "ollama"
    assert seen[0]["dim"] == 1024


# ── the provider itself ──────────────────────────────────────────────────────


class _Recorder(MLXEmbeddingProvider):
    """An MLX provider with the model swapped out for a recorder."""

    def __init__(self, model: str):
        from latticeai.core.embedding_providers.base import _RemoteConfig

        super().__init__(_RemoteConfig(model=model, dim=4))
        self.seen: List[str] = []

    def _embed_raw(self, texts: Sequence[str]) -> List[List[float]]:
        self.seen.extend(texts)
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


def test_an_e5_model_is_told_which_role_it_is_embedding_for() -> None:
    provider = _Recorder("mlx-community/multilingual-e5-small-mlx")
    provider.embed_batch_for(["문장"], "query")
    provider.embed_batch_for(["문장"], "passage")
    assert provider.seen == ["query: 문장", "passage: 문장"]
    # An unknown role is a passage — the safer half of the asymmetry.
    provider.seen.clear()
    provider.embed_batch_for(["문장"], "sideways")
    assert provider.seen == ["passage: 문장"]
    assert provider.metadata()["instruction_prefixes"] is True
    assert provider.metadata()["max_tokens"] == MLX_MAX_TOKENS


def test_a_symmetric_model_sees_the_text_unchanged() -> None:
    provider = _Recorder("mlx-community/snowflake-arctic-embed-l-v2.0-8bit")
    provider.embed_batch_for(["문장"], "query")
    assert provider.seen == ["문장"]
    assert provider.metadata()["instruction_prefixes"] is False
    assert set(E5_PREFIXES) == {"query", "passage"}
    assert _wants_e5_prefix("e5-large") and not _wants_e5_prefix("bge-m3")


def test_the_default_provider_ignores_the_role() -> None:
    provider = HashEmbeddingProvider(dim=8)
    assert provider.embed_batch_for(["x"], "query") == provider.embed_batch_for(
        ["x"], "passage"
    )


def test_the_mlx_dim_guess_knows_the_new_local_models() -> None:
    from latticeai.core.embedding_providers.base import _guess_dim

    assert _guess_dim("mlx-community/multilingual-e5-small-mlx", 999) == 384
    assert _guess_dim("mlx-community/multilingual-e5-large-mlx", 999) == 1024
    assert _guess_dim("mlx-community/snowflake-arctic-embed-l-v2.0-8bit", 999) == 1024


# ── the honest surface ───────────────────────────────────────────────────────


def test_the_status_route_says_whether_the_vectors_mean_anything() -> None:
    resolved = resolve_embedder("hash")
    resolved.detected = Detection(
        provider="mlx", model="m", dim=384, source="local_model", detail="already here"
    )
    report = SearchService(embedder=resolved).embeddings_status(
        resolved=resolved.as_dict()
    )
    assert report["semantic"] is False, "the hash fallback is not semantic"
    assert report["state"] == "fallback"
    assert report["detected"]["found"] is True
    assert report["detected"]["model"] == "m"
    assert report["identity"] == "lattice-local-hash-v1:384:384"


def test_the_status_route_reports_an_undetected_machine_as_empty_not_missing() -> None:
    resolved = resolve_embedder("hash")
    report = SearchService(embedder=resolved).embeddings_status(
        resolved=resolved.as_dict()
    )
    assert report["detected"] == {}


@pytest.mark.parametrize("profile_id", ["local:multilingual-e5-small", "local:arctic-embed-l-v2"])
def test_a_one_click_profile_carries_what_a_download_needs(profile_id: str) -> None:
    from latticeai.core.embedding_providers import resolve_embedding_profile

    profile = resolve_embedding_profile(profile_id)
    assert profile["hf_repo_id"] == profile["model"], "offer, fetch and detect agree"
    assert 0 < profile["download_gb"] < 1.0
    assert profile["provider"] == "mlx"
