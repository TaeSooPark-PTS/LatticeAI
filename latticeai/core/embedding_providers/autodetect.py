from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from latticeai.core.hf_revisions import (
    REVISION_UNPINNED,
    CachedSnapshot,
    RevisionRequest,
    fetch_pinned_snapshot,
    find_cached_snapshot,
    require_pinned_revision,
    resolve_revision,
)

AUTO_PROVIDER = "auto"
AUTODETECT_ENV = "LATTICEAI_EMBEDDING_AUTODETECT"
OLLAMA_BASE_ENV = "LATTICEAI_EMBEDDING_BASE_URL"
DEFAULT_OLLAMA_BASE = "http://127.0.0.1:11434"

LOCAL_HF_MODELS: Tuple[Tuple[str, int, bool], ...] = (
    ("google/embeddinggemma-2", 768, False),
    ("google/embeddinggemma-300m", 768, False),
)

LOCAL_MLX_MODELS: Tuple[Tuple[str, int, bool], ...] = (
    ("mlx-community/embeddinggemma-2-4bit", 768, False),
    ("mlx-community/embeddinggemma-300m-4bit", 768, False),
    ("mlx-community/multilingual-e5-small-mlx", 384, True),
    ("mlx-community/multilingual-e5-base-mlx", 768, True),
    ("mlx-community/multilingual-e5-large-mlx", 1024, True),
    ("mlx-community/snowflake-arctic-embed-l-v2.0-8bit", 1024, False),
)

OLLAMA_EMBEDDING_MODELS: Tuple[Tuple[str, int], ...] = (
    ("bge-m3", 1024),
    ("mxbai-embed-large", 1024),
    ("nomic-embed-text", 768),
    ("all-minilm", 384),
)


@dataclass
class Detection:
    provider: str = ""
    model: str = ""
    dim: int = 0
    source: str = "none"
    detail: str = ""
    candidates: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return bool(self.provider)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "found": self.found,
            "provider": self.provider,
            "model": self.model,
            "dim": self.dim,
            "source": self.source,
            "detail": self.detail,
            "candidates": list(self.candidates),
        }


def autodetect_enabled(env: Optional[Dict[str, str]] = None) -> bool:
    raw = (env or dict(os.environ)).get(AUTODETECT_ENV, "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def hf_cache_roots(env: Optional[Dict[str, str]] = None) -> List[Path]:
    values = env if env is not None else dict(os.environ)
    roots: List[Path] = []
    for key in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "LATTICEAI_MODELS_DIR"):
        raw = values.get(key, "").strip()
        if raw:
            roots.append(Path(raw))
    home = values.get("HF_HOME", "").strip()
    if home:
        roots.append(Path(home) / "hub")
    if env is None or "HOME" in values or not roots:
        home_dir = Path(values.get("HOME", "~")).expanduser()
        roots.append(home_dir / ".cache/huggingface/hub")
        roots.append(home_dir / ".latticeai/models")
    seen: List[Path] = []
    for root in roots:
        if root not in seen:
            seen.append(root)
    return seen


def _has_model_files(directory: Path) -> bool:
    if not directory.is_dir():
        return False
    if not (directory / "config.json").exists():
        return False
    return (
        any(
            (directory / name).exists()
            for name in (
                "model.safetensors",
                "weights.safetensors",
                "pytorch_model.bin",
            )
        )
        or any(directory.glob("*.safetensors"))
        or any(directory.glob("*.bin"))
    )


def cached_snapshot(
    request: RevisionRequest, roots: List[Path]
) -> Optional[CachedSnapshot]:
    return find_cached_snapshot(request, roots, _has_model_files)


def _snapshot_dir(
    repo_id: str, roots: List[Path], env: Optional[Mapping[str, str]] = None
) -> Optional[Path]:
    found = cached_snapshot(resolve_revision(repo_id, env=env), roots)
    return found.path if found is not None else None


def revision_report(
    repo_id: str, roots: List[Path], env: Optional[Mapping[str, str]] = None
) -> Dict[str, Any]:
    request = resolve_revision(repo_id, env=env)
    found = cached_snapshot(request, roots)
    if found is None:
        return {
            "snapshot": None,
            "revision": request.revision,
            "revision_source": request.source,
        }
    return {
        "snapshot": found.path,
        "revision": found.revision,
        "revision_source": found.source,
    }


def _is_hub_repo_id(model: str) -> bool:
    parts = model.split("/")
    return len(parts) == 2 and all(parts) and not model.startswith((".", "~"))


def pinned_model_target(
    model: str,
    env: Optional[Dict[str, str]] = None,
    override: Optional[str] = None,
) -> str:
    raw = str(model or "").strip()
    if not _is_hub_repo_id(raw) or Path(raw).expanduser().exists():
        return raw
    request = require_pinned_revision(
        resolve_revision(raw, override=override, env=env), env=env
    )
    if request.source == REVISION_UNPINNED:
        return raw
    found = cached_snapshot(request, hf_cache_roots(env))
    if found is not None:
        return str(found.path)
    return str(fetch_pinned_snapshot(request, env=env).path)


def detect_local_hf(env: Optional[Dict[str, str]] = None) -> Detection:
    roots = hf_cache_roots(env)
    candidates: List[Dict[str, Any]] = []
    for repo_id, dim, prefixed in LOCAL_HF_MODELS:
        report = revision_report(repo_id, roots, env)
        candidates.append(
            {
                "provider": "huggingface",
                "model": repo_id,
                "dim": dim,
                "downloaded": report["snapshot"] is not None,
                "revision": report["revision"],
                "revision_source": report["revision_source"],
                "e5_prefixes": prefixed,
            }
        )
    for candidate in candidates:
        if candidate["downloaded"]:
            return Detection(
                provider="huggingface",
                model=str(candidate["model"]),
                dim=int(candidate["dim"]),
                source="local_model",
                detail=f"{candidate['model']} is already in the local model cache",
                candidates=candidates,
            )
    return Detection(
        source="none",
        detail="no known Hugging Face embedding model is downloaded yet",
        candidates=candidates,
    )


def detect_local_mlx(env: Optional[Dict[str, str]] = None) -> Detection:
    roots = hf_cache_roots(env)
    candidates: List[Dict[str, Any]] = []
    for repo_id, dim, prefixed in LOCAL_MLX_MODELS:
        report = revision_report(repo_id, roots, env)
        candidates.append(
            {
                "provider": "mlx",
                "model": repo_id,
                "dim": dim,
                "downloaded": report["snapshot"] is not None,
                "revision": report["revision"],
                "revision_source": report["revision_source"],
                "e5_prefixes": prefixed,
            }
        )
    for candidate in candidates:
        if candidate["downloaded"]:
            return Detection(
                provider="mlx",
                model=str(candidate["model"]),
                dim=int(candidate["dim"]),
                source="local_model",
                detail=f"{candidate['model']} is already in the local model cache",
                candidates=candidates,
            )
    return Detection(
        source="none",
        detail="no known embedding model is downloaded yet",
        candidates=candidates,
    )


def detect_ollama(
    base_url: str = "",
    timeout: float = 1.5,
    env: Optional[Dict[str, str]] = None,
) -> Detection:
    values = env or dict(os.environ)
    base = (base_url or values.get(OLLAMA_BASE_ENV, "") or DEFAULT_OLLAMA_BASE).rstrip(
        "/"
    )
    try:
        import httpx
    except ImportError as exc:
        return Detection(source="none", detail=f"no Ollama server at {base}: {exc}")
    _http_error: type[Exception] = getattr(httpx, "HTTPError", OSError)
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(f"{base}/api/tags")
            response.raise_for_status()
            payload = response.json()
    except (
        _http_error,
        OSError,
        ValueError,
        TypeError,
        RuntimeError,
        AttributeError,
    ) as exc:
        return Detection(source="none", detail=f"no Ollama server at {base}: {exc}")
    names = [
        str(model.get("name") or "")
        for model in (payload.get("models") or [])
        if isinstance(model, dict)
    ]
    for known, dim in OLLAMA_EMBEDDING_MODELS:
        for name in names:
            if name.split(":")[0] == known:
                return Detection(
                    provider="ollama",
                    model=name,
                    dim=dim,
                    source="ollama",
                    detail=f"{name} is pulled on the Ollama server at {base}",
                )
    return Detection(
        source="none",
        detail=f"Ollama at {base} has no embedding model pulled",
    )


def detect_embedder(
    configured_provider: str = "",
    configured_model: str = "",
    env: Optional[Dict[str, str]] = None,
    probe_ollama: bool = True,
) -> Detection:
    values = env if env is not None else dict(os.environ)
    configured = str(configured_provider or "").strip().lower()
    if configured in {"hf", "sentence-transformers", "sentence_transformers"}:
        configured = "huggingface"
    if configured and configured not in {"hash", "local", "fallback", AUTO_PROVIDER}:
        resolved_model = configured_model or (
            "google/embeddinggemma-2" if configured == "huggingface" else ""
        )
        dim = (
            768
            if configured == "huggingface"
            and "embeddinggemma" in resolved_model.lower()
            else 0
        )
        return Detection(
            provider=configured,
            model=resolved_model,
            dim=dim,
            source="configured",
            detail=f"{configured} is configured explicitly",
        )
    if not autodetect_enabled(values):
        return Detection(source="none", detail=f"{AUTODETECT_ENV} is off")

    local_hf = detect_local_hf(values)
    local_mlx = detect_local_mlx(values)
    combined_candidates = list(local_hf.candidates) + list(local_mlx.candidates)
    if local_hf.found:
        local_hf.candidates = combined_candidates
        return local_hf
    if local_mlx.found:
        local_mlx.candidates = combined_candidates
        return local_mlx
    if probe_ollama:
        ollama = detect_ollama(env=values)
        if ollama.found:
            ollama.candidates = combined_candidates
            return ollama
    local_mlx.candidates = combined_candidates
    return local_mlx


def resolve_auto_provider(
    configured_provider: str,
    configured_model: str,
    configured_dim: int,
    detection: Detection,
) -> Tuple[str, str, int]:
    requested = str(configured_provider or "").strip().lower()
    if requested != AUTO_PROVIDER:
        return configured_provider, configured_model, configured_dim
    if not detection.found:
        return "hash", "", configured_dim
    return (
        detection.provider,
        configured_model or detection.model,
        configured_dim or detection.dim,
    )


__all__ = [
    "AUTODETECT_ENV",
    "AUTO_PROVIDER",
    "DEFAULT_OLLAMA_BASE",
    "LOCAL_HF_MODELS",
    "LOCAL_MLX_MODELS",
    "OLLAMA_EMBEDDING_MODELS",
    "Detection",
    "autodetect_enabled",
    "cached_snapshot",
    "detect_embedder",
    "detect_local_hf",
    "detect_local_mlx",
    "pinned_model_target",
    "revision_report",
    "detect_ollama",
    "hf_cache_roots",
    "resolve_auto_provider",
]
