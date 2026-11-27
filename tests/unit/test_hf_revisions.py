from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

from latticeai.core import hf_revisions as hr
from latticeai.core.embedding_providers import autodetect as ad
from latticeai.models.router import local_models as lm
from latticeai.services.model_runtime import download as download_mod

PINNED_REPO = "BAAI/bge-reranker-v2-m3"
PINNED_SHA = hr.PINNED_HF_REVISIONS[PINNED_REPO]
OTHER_SHA = "a" * 40


def _complete(path: Path) -> bool:
    return (path / "config.json").is_file()


def _snapshot(root: Path, repo: str, sha: str, complete: bool = True) -> Path:
    snap = root / ("models--" + repo.replace("/", "--")) / "snapshots" / sha
    snap.mkdir(parents=True)
    if complete:
        (snap / "config.json").write_text("{}", encoding="utf-8")
        (snap / "model.safetensors").write_bytes(b"w")
    return snap


def _ref(root: Path, repo: str, name: str, value: str) -> None:
    refs = root / ("models--" + repo.replace("/", "--")) / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / name).write_text(value, encoding="utf-8")


def test_every_pin_is_a_full_commit_sha() -> None:
    assert all(hr.is_commit_sha(sha) for sha in hr.PINNED_HF_REVISIONS.values())
    assert hr.is_commit_sha(None) is False
    assert hr.is_commit_sha("main") is False
    assert hr.pinned_revision(PINNED_REPO.upper()) == PINNED_SHA
    assert hr.pinned_revision("") is None


def test_revision_overrides_parse_and_reject(monkeypatch) -> None:
    monkeypatch.setenv(hr.HF_REVISIONS_ENV, f" Org/Repo={OTHER_SHA} , ,x/y=dev")
    assert hr.revision_overrides() == {"org/repo": OTHER_SHA, "x/y": "dev"}
    assert hr.revision_overrides({}) == {}
    with pytest.raises(ValueError, match="org/repo=<revision>"):
        hr.revision_overrides({hr.HF_REVISIONS_ENV: "broken"})
    with pytest.raises(ValueError):
        hr.revision_overrides({hr.HF_REVISIONS_ENV: "a/b="})


def test_resolve_revision_sources() -> None:
    explicit = hr.resolve_revision(PINNED_REPO, override=" dev ", env={})
    assert (explicit.revision, explicit.source, explicit.explicit) == (
        "dev",
        hr.REVISION_OVERRIDE,
        True,
    )
    configured = hr.resolve_revision(
        PINNED_REPO, env={hr.HF_REVISIONS_ENV: f"{PINNED_REPO}={OTHER_SHA}"}
    )
    assert (configured.revision, configured.source) == (
        OTHER_SHA,
        hr.REVISION_OVERRIDE,
    )
    pinned = hr.resolve_revision(PINNED_REPO, env={})
    assert (pinned.revision, pinned.source, pinned.explicit) == (
        PINNED_SHA,
        hr.REVISION_PINNED,
        False,
    )
    unpinned = hr.resolve_revision("org/unknown", env={})
    assert (unpinned.revision, unpinned.source) == (None, hr.REVISION_UNPINNED)


def test_find_cached_snapshot_prefers_exact_pin(tmp_path: Path) -> None:
    _snapshot(tmp_path, PINNED_REPO, OTHER_SHA)
    exact = _snapshot(tmp_path, PINNED_REPO, PINNED_SHA)
    request = hr.resolve_revision(PINNED_REPO, env={})
    found = hr.find_cached_snapshot(request, [tmp_path], _complete)
    assert found == hr.CachedSnapshot(exact, PINNED_SHA, hr.REVISION_PINNED)


def test_find_cached_snapshot_resolves_branch_refs(tmp_path: Path) -> None:
    snap = _snapshot(tmp_path, "org/m", OTHER_SHA)
    _ref(tmp_path, "org/m", "dev", OTHER_SHA)
    request = hr.RevisionRequest("org/m", "dev", hr.REVISION_OVERRIDE)
    found = hr.find_cached_snapshot(request, [tmp_path], _complete)
    assert found == hr.CachedSnapshot(snap, OTHER_SHA, hr.REVISION_OVERRIDE)


def test_explicit_override_never_falls_back(tmp_path: Path) -> None:
    _snapshot(tmp_path, "org/m", OTHER_SHA)
    _ref(tmp_path, "org/m", "dev", "")
    request = hr.RevisionRequest("org/m", "dev", hr.REVISION_OVERRIDE)
    assert hr.find_cached_snapshot(request, [tmp_path], _complete) is None


def test_fallback_uses_main_ref_then_newest_complete(tmp_path: Path) -> None:
    main_root = tmp_path / "main"
    main_snap = _snapshot(main_root, PINNED_REPO, OTHER_SHA)
    _ref(main_root, PINNED_REPO, "main", OTHER_SHA)
    request = hr.resolve_revision(PINNED_REPO, env={})
    assert hr.find_cached_snapshot(request, [main_root], _complete) == (
        hr.CachedSnapshot(main_snap, OTHER_SHA, hr.REVISION_CACHED_FALLBACK)
    )

    newest_root = tmp_path / "newest"
    complete = _snapshot(newest_root, PINNED_REPO, "b" * 40)
    incomplete = _snapshot(newest_root, PINNED_REPO, "c" * 40, complete=False)
    (incomplete.parent / "stray.txt").write_text("x", encoding="utf-8")
    os.utime(complete, (1, 1))
    _ref(newest_root, PINNED_REPO, "main", "d" * 40)
    found = hr.find_cached_snapshot(request, [newest_root], _complete)
    assert found == hr.CachedSnapshot(complete, "b" * 40, hr.REVISION_CACHED_FALLBACK)


def test_fallback_reports_local_paths_and_misses(tmp_path: Path) -> None:
    request = hr.resolve_revision("org/m", env={})
    assert hr.find_cached_snapshot(request, [tmp_path], _complete) is None
    _snapshot(tmp_path, "org/m", OTHER_SHA, complete=False)
    assert hr.find_cached_snapshot(request, [tmp_path], _complete) is None
    direct = tmp_path / "org" / "m"
    direct.mkdir(parents=True)
    (direct / "config.json").write_text("{}", encoding="utf-8")
    assert hr.find_cached_snapshot(request, [tmp_path], _complete) == (
        hr.CachedSnapshot(direct, None, hr.REVISION_LOCAL_PATH)
    )


def _fake_hub(monkeypatch, result: str, seen: list) -> None:
    hub = types.ModuleType("huggingface_hub")

    def snapshot_download(**kwargs):
        seen.append(kwargs)
        return result

    hub.snapshot_download = snapshot_download
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)


def test_fetch_pinned_snapshot_passes_revision(monkeypatch, tmp_path: Path) -> None:
    seen: list = []
    _fake_hub(monkeypatch, str(tmp_path / PINNED_SHA), seen)
    request = hr.resolve_revision(PINNED_REPO, env={})
    fetched = hr.fetch_pinned_snapshot(request)
    assert seen == [{"repo_id": PINNED_REPO, "revision": PINNED_SHA}]
    assert fetched == hr.CachedSnapshot(
        tmp_path / PINNED_SHA, PINNED_SHA, hr.REVISION_PINNED
    )
    _fake_hub(monkeypatch, str(tmp_path / "local"), seen)
    dev = hr.fetch_pinned_snapshot(
        hr.RevisionRequest("org/m", "dev", hr.REVISION_OVERRIDE)
    )
    assert (dev.revision, dev.source) == ("dev", hr.REVISION_OVERRIDE)


def test_revision_report_states_source(tmp_path: Path) -> None:
    env = {"HF_HUB_CACHE": str(tmp_path)}
    missing = ad.revision_report(PINNED_REPO, [tmp_path], env)
    assert missing == {
        "snapshot": None,
        "revision": PINNED_SHA,
        "revision_source": hr.REVISION_PINNED,
    }
    snap = _snapshot(tmp_path, PINNED_REPO, PINNED_SHA)
    assert ad.revision_report(PINNED_REPO, [tmp_path], env) == {
        "snapshot": snap,
        "revision": PINNED_SHA,
        "revision_source": hr.REVISION_PINNED,
    }


def test_detect_candidates_carry_revision_source(tmp_path: Path) -> None:
    env = {"HF_HUB_CACHE": str(tmp_path)}
    detection = ad.detect_local_hf(env)
    sources = {item["revision_source"] for item in detection.candidates}
    assert sources <= {hr.REVISION_PINNED, hr.REVISION_UNPINNED}
    mlx = ad.detect_local_mlx(env)
    assert all("revision" in item for item in mlx.candidates)


def test_pinned_model_target(monkeypatch, tmp_path: Path) -> None:
    env = {"HF_HUB_CACHE": str(tmp_path)}
    assert ad.pinned_model_target("", env) == ""
    assert ad.pinned_model_target("single", env) == "single"
    assert ad.pinned_model_target("./org/m", env) == "./org/m"
    assert ad.pinned_model_target("org/", env) == "org/"
    local = tmp_path / "org" / "x"
    local.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    assert ad.pinned_model_target("org/x", env) == "org/x"
    assert ad.pinned_model_target("org/unpinned", env) == "org/unpinned"
    seen: list = []
    _fake_hub(monkeypatch, str(tmp_path / "fetched" / PINNED_SHA), seen)
    assert ad.pinned_model_target(PINNED_REPO, env) == str(
        tmp_path / "fetched" / PINNED_SHA
    )
    assert seen == [{"repo_id": PINNED_REPO, "revision": PINNED_SHA}]
    snap = _snapshot(tmp_path, PINNED_REPO, PINNED_SHA)
    assert ad.pinned_model_target(PINNED_REPO, env) == str(snap)


def test_router_resolves_pinned_llm_revision(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(lm.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(lm, "HF_MODELS_ROOT", tmp_path / "hf-models")
    repo = "mlx-community/Qwen3.6-27B-4bit"
    sha = hr.PINNED_HF_REVISIONS[repo]
    seen: list = []
    _fake_hub(monkeypatch, str(tmp_path / "fetched" / sha), seen)
    assert lm._resolve_local_hf_model(repo) == str(tmp_path / "fetched" / sha)
    assert seen == [{"repo_id": repo, "revision": sha}]
    assert lm._resolve_local_hf_model("org/unpinned") == "org/unpinned"
    assert lm.hf_cache_model_dir(repo) is None


def test_download_passes_revision_endpoint_and_token(monkeypatch) -> None:
    monkeypatch.setenv("HF_ENDPOINT", "https://mirror.example/")
    monkeypatch.setenv("HF_TOKEN", "tok")
    calls: list = []
    download_mod._call_hf_hub_download(
        lambda **kw: calls.append(kw) or "p",
        repo_id=PINNED_REPO,
        filename="f",
        local_dir="d",
        tqdm_class=None,
        revision=PINNED_SHA,
    )
    assert calls[0]["revision"] == PINNED_SHA
    assert calls[0]["endpoint"] == "https://mirror.example"
    assert calls[0]["token"] == "tok"

    def strict(*, repo_id, filename, local_dir, tqdm_class):
        return "never"

    with pytest.raises(TypeError):
        download_mod._call_hf_hub_download(
            strict,
            repo_id=PINNED_REPO,
            filename="f",
            local_dir="d",
            tqdm_class=None,
        )


def test_hf_api_does_not_drop_credentials(monkeypatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "tok")
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    monkeypatch.delenv("HUGGINGFACE_HUB_ENDPOINT", raising=False)
    hub = types.ModuleType("huggingface_hub")

    def strict_api():
        return object()

    hub.HfApi = strict_api
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    with pytest.raises(TypeError):
        download_mod._build_hf_api()


def test_repo_listing_is_pinned(monkeypatch) -> None:
    seen: list = []

    class Sib:
        rfilename = "a.bin"
        size = 3

    class Api:
        def model_info(self, repo_id, revision=None, files_metadata=True):
            seen.append(("info", revision))
            return types.SimpleNamespace(siblings=[Sib()])

    monkeypatch.setattr(download_mod, "_build_hf_api", lambda: Api())
    assert download_mod.hf_repo_files_with_sizes(PINNED_REPO) == [
        {"name": "a.bin", "size": 3}
    ]
    assert seen == [("info", PINNED_SHA)]


def test_hf_mlx_embedding_loads_pinned_snapshot(monkeypatch, tmp_path: Path) -> None:
    from latticeai.core.embedding_providers.base import _RemoteConfig
    from latticeai.core.embedding_providers.text import HuggingFaceEmbeddingProvider

    repo = "mlx-community/multilingual-e5-small-mlx"
    snap = _snapshot(tmp_path, repo, hr.PINNED_HF_REVISIONS[repo])
    loaded: list = []
    utils = types.ModuleType("mlx_embeddings.utils")
    utils.load = lambda target: loaded.append(target) or ("model", "tok")
    monkeypatch.setitem(sys.modules, "mlx_embeddings", types.ModuleType("x"))
    monkeypatch.setitem(sys.modules, "mlx_embeddings.utils", utils)
    env = {"HF_HUB_CACHE": str(tmp_path)}
    provider = HuggingFaceEmbeddingProvider(
        _RemoteConfig(model=repo, dim=4, extra={"env": env})
    )
    assert provider._open_encoder() == ("mlx_embeddings", "model", "tok")
    assert loaded == [str(snap)]

    seen: list = []
    _fake_hub(monkeypatch, str(tmp_path / "dev"), seen)
    override = HuggingFaceEmbeddingProvider(
        _RemoteConfig(model=repo, dim=4, extra={"env": env, "revision": "dev"})
    )
    override._open_encoder()
    assert seen == [{"repo_id": repo, "revision": "dev"}]
    assert loaded[-1] == str(tmp_path / "dev")
