from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from latticeai.services.model_loading import prepare_and_load_model_stream
from latticeai.services.model_runtime import download as download_mod
from latticeai.services.model_runtime.download import (
    clear_download_progress_snapshots,
    download_hf_model,
    estimate_eta_seconds,
    estimate_speed_bps,
    get_download_progress_snapshot,
    is_gated_or_auth_error,
    model_download_progress_payload,
    resolve_download_candidates,
)


def test_speed_and_eta_estimation_and_progress_payload():
    clear_download_progress_snapshots()
    assert estimate_speed_bps(100.0, None, now=105.0) is None
    assert estimate_speed_bps(100.0, 0, now=105.0) is None
    assert estimate_speed_bps(100.0, 1024, now=100.0) is None

    speed = estimate_speed_bps(100.0, 10 * 1024 * 1024, now=102.0)
    assert speed == 5 * 1024 * 1024

    eta = estimate_eta_seconds(
        100.0,
        50.0,
        downloaded_bytes=10 * 1024 * 1024,
        total_bytes=20 * 1024 * 1024,
        speed_bps=speed,
    )
    assert eta == pytest.approx(2.0)

    payload = model_download_progress_payload(
        "download",
        "Downloading weights",
        percent=50.0,
        detail="model.safetensors",
        downloaded_bytes=10 * 1024 * 1024,
        total_bytes=20 * 1024 * 1024,
        speed_bps=speed,
        eta_seconds=eta,
        current_file="model.safetensors",
        gated_fallback=True,
        fallback_repo="mlx-community/embeddinggemma-2-4bit",
    )
    assert payload["stage"] == "download"
    assert payload["percent"] == 50.0
    assert payload["downloaded_bytes"] == 10 * 1024 * 1024
    assert payload["total_bytes"] == 20 * 1024 * 1024
    assert payload["speed_bps"] == 5 * 1024 * 1024
    assert payload["speed_mbps"] == 5.0
    assert payload["eta_seconds"] == 2
    assert payload["current_file"] == "model.safetensors"
    assert payload["file"] == "model.safetensors"
    assert payload["gated_fallback"] is True
    assert payload["fallback_repo"] == "mlx-community/embeddinggemma-2-4bit"

    snapshot = get_download_progress_snapshot("mlx-community/embeddinggemma-2-4bit")
    assert snapshot["percent"] == 50.0
    assert (
        get_download_progress_snapshot()["fallback_repo"]
        == "mlx-community/embeddinggemma-2-4bit"
    )


def test_is_gated_or_auth_error_and_resolve_download_candidates():
    assert (
        is_gated_or_auth_error(RuntimeError("401 Client Error: Unauthorized")) is True
    )
    assert (
        is_gated_or_auth_error(RuntimeError("403 Forbidden: Cannot access gated repo"))
        is True
    )
    assert (
        is_gated_or_auth_error(
            RuntimeError("Access to model google/embeddinggemma-2 is restricted")
        )
        is True
    )
    assert is_gated_or_auth_error(RuntimeError("disk full")) is False

    unauth = resolve_download_candidates("google/embeddinggemma-2", "local_mlx", env={})
    assert unauth["proactive_fallback"] is True
    assert unauth["primary_repo"] == "mlx-community/embeddinggemma-2-4bit"
    assert unauth["candidates"][0] == "mlx-community/embeddinggemma-2-4bit"
    assert "ggml-org/embeddinggemma-2-GGUF" in unauth["public_mirror_repos"]

    authed = resolve_download_candidates(
        "google/embeddinggemma-2",
        "local_mlx",
        env={"HF_TOKEN": "hf_secret", "HF_ENDPOINT": "https://hf-mirror.com/"},
    )
    assert authed["proactive_fallback"] is False
    assert authed["primary_repo"] == "google/embeddinggemma-2"
    assert authed["candidates"][0] == "google/embeddinggemma-2"
    assert authed["candidates"][1] == "mlx-community/embeddinggemma-2-4bit"
    assert authed["hf_endpoint"] == "https://hf-mirror.com"


def test_download_hf_model_unauthenticated_proactive_mirror_fallback(
    monkeypatch, tmp_path: Path
):
    import huggingface_hub

    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
    monkeypatch.setattr(
        download_mod.importlib.util, "find_spec", lambda _name: object()
    )
    monkeypatch.setattr(
        download_mod, "hf_model_dir", lambda repo: tmp_path / repo.replace("/", "__")
    )

    downloaded_repos: list[str] = []

    def fake_ready(repo: str, _provider: str = "local_mlx") -> bool:
        return repo in downloaded_repos

    monkeypatch.setattr(download_mod, "hf_model_ready", fake_ready)
    monkeypatch.setattr(
        download_mod,
        "hf_repo_files_with_sizes",
        lambda repo: [
            {"name": "model.safetensors", "size": 4096},
            {"name": "config.json", "size": 512},
        ],
    )

    def fake_hf_download(
        *, repo_id, filename, local_dir, tqdm_class=None, revision=None
    ):
        p = Path(local_dir) / filename
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 512)
        if repo_id not in downloaded_repos:
            downloaded_repos.append(repo_id)
        return str(p)

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_hf_download)

    events: list[dict] = []
    result = download_hf_model(
        "google/embeddinggemma-2", "local_mlx", progress_emit=events.append
    )

    assert result["model"] == "mlx-community/embeddinggemma-2-4bit"
    assert result["requested_model"] == "google/embeddinggemma-2"
    assert result["gated_fallback"] is True
    assert result["fallback_repo"] == "mlx-community/embeddinggemma-2-4bit"
    assert any(ev.get("gated_fallback") is True for ev in events)
    assert events[-1]["percent"] == 100


def test_download_hf_model_gated_403_falls_back_to_public_mirror(
    monkeypatch, tmp_path: Path
):
    import huggingface_hub

    monkeypatch.setenv("HF_TOKEN", "hf_expired_or_unaccepted_gate")
    monkeypatch.setattr(
        download_mod.importlib.util, "find_spec", lambda _name: object()
    )
    monkeypatch.setattr(
        download_mod, "hf_model_dir", lambda repo: tmp_path / repo.replace("/", "__")
    )

    ready_repos: set[str] = set()
    monkeypatch.setattr(
        download_mod, "hf_model_ready", lambda repo, _p="local_mlx": repo in ready_repos
    )

    def fake_files(repo: str):
        if repo == "google/embeddinggemma-2":
            raise RuntimeError(
                "403 Forbidden: Cannot access gated repo for url https://huggingface.co/google/embeddinggemma-2"
            )
        return [{"name": "model.safetensors", "size": 2048}]

    monkeypatch.setattr(download_mod, "hf_repo_files_with_sizes", fake_files)

    def fake_hf_download(*, repo_id, filename, local_dir, tqdm_class=None, **_kwargs):
        p = Path(local_dir) / filename
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"w" * 2048)
        ready_repos.add(repo_id)
        return str(p)

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_hf_download)

    events: list[dict] = []
    result = download_hf_model(
        "google/embeddinggemma-2", "local_mlx", progress_emit=events.append
    )
    assert result["model"] == "mlx-community/embeddinggemma-2-4bit"
    assert result["gated_fallback"] is True
    assert result["fallback_repo"] == "mlx-community/embeddinggemma-2-4bit"
    assert any(ev.get("current_file") == "model.safetensors" for ev in events)


def test_prepare_and_load_model_stream_sse_emits_speed_eta_and_fallback(
    monkeypatch, tmp_path: Path
):
    from latticeai.services import model_loading as ml_mod

    fake_state = SimpleNamespace(
        get_current_user=lambda _req: "user@example.com",
        get_user_api_key=lambda _u, _p: None,
        router=SimpleNamespace(
            load_model=lambda *_a, **_kw: asyncio.sleep(0, result="loaded"),
            current_model_id="mlx-community/embeddinggemma-2-4bit",
        ),
    )

    def fake_download(repo_id: str, provider: str = "local_mlx", progress_emit=None):
        if progress_emit:
            progress_emit(
                model_download_progress_payload(
                    "download",
                    "모델 다운로드 중입니다.",
                    percent=42.5,
                    detail="model.safetensors",
                    downloaded_bytes=42_500_000,
                    total_bytes=100_000_000,
                    speed_bps=12_400_000.0,
                    eta_seconds=5,
                    current_file="model.safetensors",
                    gated_fallback=True,
                    fallback_repo="mlx-community/embeddinggemma-2-4bit",
                )
            )
        return {
            "model": "mlx-community/embeddinggemma-2-4bit",
            "requested_model": repo_id,
            "path": str(tmp_path),
            "cached": False,
            "gated_fallback": True,
            "fallback_repo": "mlx-community/embeddinggemma-2-4bit",
        }

    class FakeResolution:
        load_id = "mlx-community/embeddinggemma-2-4bit"

        @classmethod
        def from_request(cls, *_a, **_kw):
            return cls()

        def update_after_load(self, **_kw):
            pass

        def to_dict(self):
            return {"load_id": self.load_id}

    monkeypatch.setattr(
        ml_mod,
        "_get_model_runtime_deps",
        lambda _state: {
            "normalize_local_model_request": lambda m, _e: m,
            "parse_model_ref": lambda m: ("local_mlx", m),
            "_model_runtime_compatibility": lambda _m, engine=None: {"supported": True},
            "model_download_progress_payload": model_download_progress_payload,
            "engine_installed": lambda _p: True,
            "_download_allowed": lambda allow: bool(allow),
            "_engine_install_block": lambda _p: None,
            "ensure_engine_ready": lambda _p: {"installed_now": False},
            "hf_model_ready": lambda _m, _p="local_mlx": False,
            "_download_block": lambda _p, _m: None,
            "download_hf_model": fake_download,
            "hf_model_dir": lambda _m: tmp_path,
            "_friendly_model_runtime_error": lambda exc, **_kw: str(exc),
            "get_current_user": fake_state.get_current_user,
            "get_user_api_key": fake_state.get_user_api_key,
            "router": fake_state.router,
            "_ModelResolution": FakeResolution,
            "MODEL_ENGINE_ALIASES": {},
            "_smoke_test_loaded_model": lambda *_a, **_kw: asyncio.sleep(
                0, result={"ok": True, "status": "ok"}
            ),
        },
    )

    async def collect():
        chunks: list[str] = []
        async for chunk in prepare_and_load_model_stream(
            "google/embeddinggemma-2",
            request=SimpleNamespace(),
            engine="local_mlx",
            allow_download=True,
            runtime_state=fake_state,
        ):
            chunks.append(chunk)
        return chunks

    frames = asyncio.run(collect())
    parsed_events = []
    for frame in frames:
        lines = [
            line for line in frame.strip().splitlines() if line.startswith("data: ")
        ]
        if lines:
            parsed_events.append(json.loads(lines[0][6:]))

    download_frames = [
        ev
        for ev in parsed_events
        if ev.get("stage") == "download"
        and ev.get("current_file") == "model.safetensors"
    ]
    assert len(download_frames) == 1
    df = download_frames[0]
    assert df["percent"] == 42.5
    assert df["speed_bps"] == 12_400_000.0
    assert df["eta_seconds"] == 5
    assert df["gated_fallback"] is True
    assert df["fallback_repo"] == "mlx-community/embeddinggemma-2-4bit"
    done_event = parsed_events[-1]
    assert done_event["status"] == "ok"
    assert done_event["model"] == "mlx-community/embeddinggemma-2-4bit"
