from __future__ import annotations

import importlib
import os
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from lattice_brain.embeddings import DEFAULT_EMBEDDING_DIM
from latticeai.core.hf_revisions import resolve_revision

from .autodetect import (
    AUTO_PROVIDER,
    _snapshot_dir,
    detect_embedder,
    hf_cache_roots,
    pinned_model_target,
    resolve_auto_provider,
)
from .base import (
    EmbeddingProvider,
    EmbeddingUnavailable,
    _guess_dim,
    _NetworkEmbeddingProvider,
    _RemoteConfig,
)
from .fallback import (
    DEFAULT_FALLBACK_DIM as DEFAULT_FALLBACK_DIM,
)
from .fallback import (
    FallbackTextEmbedder as FallbackTextEmbedder,
)
from .fallback import (
    HashEmbeddingProvider,
)
from .fallback import (
    _extract_fallback_features as _extract_fallback_features,
)
from .profiles import RECOMMENDED_EMBEDDING_MODEL, RECOMMENDED_EMBEDDING_PROFILE


def _as_float_list(value: Any) -> List[Any]:
    if isinstance(value, (int, float)):
        raise EmbeddingUnavailable("MLX embedding produced a scalar, not a vector")
    return list(value)


MLX_MAX_TOKENS = 512

E5_PREFIXES: Dict[str, str] = {"query": "query: ", "passage": "passage: "}

DEFAULT_HF_EMBEDDING_MODEL = "google/embeddinggemma-2"

EMBEDDINGGEMMA_PREFIXES: Dict[str, str] = {
    "query": "task: search result | query: ",
    "passage": "title: none | text: ",
}


def _wants_e5_prefix(model: str) -> bool:
    return "e5" in str(model or "").lower()


def _wants_embeddinggemma_prefix(model: str) -> bool:
    return "embeddinggemma" in str(model or "").lower()


EMBEDDINGGEMMA_2_RUNTIME: Tuple[Tuple[str, Tuple[int, int]], ...] = (
    ("sentence-transformers", (6, 1)),
    ("transformers", (5, 18)),
    ("torchvision", (0, 20)),
)

EMBEDDINGGEMMA_2_TEXT_ONLY: Dict[str, Any] = {
    "vision_config": None,
    "audio_config": None,
}


def _is_embeddinggemma_2(model: str) -> bool:
    lowered = str(model or "").lower()
    return "embeddinggemma-2" in lowered and not lowered.startswith("mlx-community/")


def _version_tuple(raw: str) -> Tuple[int, int]:
    parts: List[int] = []
    for piece in str(raw or "").split(".")[:2]:
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 2:
        parts.append(0)
    return parts[0], parts[1]


def embeddinggemma_2_runtime_gap(
    version_of: Optional[Callable[[str], str]] = None,
) -> str:
    if version_of is None:
        from importlib.metadata import PackageNotFoundError, version

        def version_of(name: str) -> str:
            try:
                return version(name)
            except PackageNotFoundError:
                return ""

    gaps: List[str] = []
    for package, floor in EMBEDDINGGEMMA_2_RUNTIME:
        found = version_of(package)
        if not found or _version_tuple(found) < floor:
            gaps.append(f"{package}>={floor[0]}.{floor[1]} (found {found or 'none'})")
    if not gaps:
        return ""
    return (
        "EmbeddingGemma 2 needs "
        + ", ".join(gaps)
        + "; install it with pip install 'ltcai[rag]'"
    )


def _mean_pool_transformers(outputs: Any, encoded: Any) -> List[List[float]]:
    for attr in ("sentence_embedding", "text_embeds", "pooler_output"):
        pooled = getattr(outputs, attr, None)
        if pooled is not None:
            rows = pooled.tolist() if hasattr(pooled, "tolist") else list(pooled)
            return [[float(x) for x in row] for row in rows]
    hidden = getattr(outputs, "last_hidden_state", None)
    if hidden is None:
        hidden = outputs[0] if isinstance(outputs, (tuple, list)) else outputs
    data = hidden.tolist() if hasattr(hidden, "tolist") else list(hidden)
    if not data:
        return []
    if isinstance(data[0], (list, tuple)) and (
        not data[0] or isinstance(data[0][0], (int, float))
    ):
        return [[float(x) for x in row] for row in data]
    mask_raw: Any = (
        encoded.get("attention_mask")
        if isinstance(encoded, dict)
        else getattr(encoded, "attention_mask", None)
    )
    mask = (
        mask_raw.tolist()
        if mask_raw is not None and hasattr(mask_raw, "tolist")
        else mask_raw
    )
    out: List[List[float]] = []
    for batch_idx, token_rows in enumerate(data):
        if not token_rows:
            out.append([])
            continue
        width = len(token_rows[0])
        if mask is not None and batch_idx < len(mask):
            weights = [float(w) for w in mask[batch_idx]]
            denom = sum(weights) or 1.0
            vec = [
                sum(
                    float(token_rows[t][d]) * weights[t] for t in range(len(token_rows))
                )
                / denom
                for d in range(width)
            ]
        else:
            denom = float(len(token_rows)) or 1.0
            vec = [
                sum(float(token_rows[t][d]) for t in range(len(token_rows))) / denom
                for d in range(width)
            ]
        out.append(vec)
    return out


class HuggingFaceEmbeddingProvider(_NetworkEmbeddingProvider):
    provider = "huggingface"

    def __init__(self, cfg: _RemoteConfig):
        model_name = (
            str(cfg.model or DEFAULT_HF_EMBEDDING_MODEL).strip()
            or DEFAULT_HF_EMBEDDING_MODEL
        )
        cfg.model = model_name
        super().__init__(cfg)
        self._model_name = model_name
        if not cfg.dim:
            self.dim = _guess_dim(model_name, DEFAULT_EMBEDDING_DIM)
        self.model_id = f"huggingface:{model_name}:{self.dim}"
        self._encoder: Optional[Tuple[str, Any, Any]] = None
        self._load_lock = threading.Lock()
        self._prefixed_e5 = _wants_e5_prefix(model_name)
        self._prefixed_gemma = _wants_embeddinggemma_prefix(model_name) and bool(
            cfg.extra.get("instruction_prefixes", True)
        )
        rev = cfg.extra.get("model_revision") or cfg.extra.get("revision")
        self.model_revision = str(rev).strip() if rev else None
        if self._prefixed_e5:
            self.passage_mode = "e5_passage"
            self.query_mode = "e5_query"
        elif self._prefixed_gemma:
            self.passage_mode = "embeddinggemma_passage"
            self.query_mode = "embeddinggemma_query"
        else:
            self.passage_mode = "raw"
            self.query_mode = "raw"
        self._max_tokens = int(
            cfg.extra.get("max_tokens")
            or (2048 if _wants_embeddinggemma_prefix(model_name) else MLX_MAX_TOKENS)
        )

    def _load(self) -> Tuple[str, Any, Any]:
        if self._encoder is not None:
            return self._encoder
        with self._load_lock:
            if self._encoder is None:
                self._encoder = self._open_encoder()
            return self._encoder

    def _open_encoder(self) -> Tuple[str, Any, Any]:
        is_mlx_repo = self._model_name.startswith(
            "mlx-community/"
        ) or self._model_name.endswith("-mlx")
        env_map = (
            self._cfg.extra.get("env") if isinstance(self._cfg.extra, dict) else None
        )
        if is_mlx_repo:
            try:
                from mlx_embeddings.utils import load as mlx_load

                model, tokenizer = mlx_load(
                    pinned_model_target(self._model_name, env_map, self.model_revision)
                )
                self._encoder = ("mlx_embeddings", model, tokenizer)
                return self._encoder
            except ImportError:
                pass
            except Exception as exc:
                raise EmbeddingUnavailable(
                    f"Hugging Face MLX embedding model unavailable: {exc}"
                ) from exc

        snapshot = _snapshot_dir(self._model_name, hf_cache_roots(env_map), env_map)
        revision = resolve_revision(
            self._model_name, override=self.model_revision, env=env_map
        ).revision

        gemma_2 = _is_embeddinggemma_2(self._model_name)
        try:
            from sentence_transformers import SentenceTransformer

            is_real_st = getattr(SentenceTransformer, "__module__", "").startswith(
                "sentence_transformers."
            )
            if is_real_st:
                if gemma_2:
                    gap = embeddinggemma_2_runtime_gap()
                    if gap:
                        raise EmbeddingUnavailable(gap)
                target = str(snapshot) if snapshot is not None else self._model_name
                options: Dict[str, Any] = {"local_files_only": True}
                if snapshot is None:
                    options["revision"] = revision
                if gemma_2:
                    options["config_kwargs"] = dict(EMBEDDINGGEMMA_2_TEXT_ONLY)
                encoder = SentenceTransformer(target, **options)
            else:
                encoder = SentenceTransformer(self._model_name)
            self._encoder = ("sentence_transformers", encoder, None)
            return self._encoder
        except ImportError as exc:
            if gemma_2:
                raise EmbeddingUnavailable(
                    embeddinggemma_2_runtime_gap()
                    or f"EmbeddingGemma 2 could not be imported: {exc}"
                ) from exc
        except EmbeddingUnavailable:
            raise
        except Exception as exc:
            raise EmbeddingUnavailable(
                f"Hugging Face embedding model unavailable: {exc}"
            ) from exc

        try:
            from transformers import AutoModel, AutoTokenizer

            is_real_tf = getattr(AutoModel, "__module__", "").startswith(
                "transformers."
            )
            if is_real_tf:
                import importlib.util

                if importlib.util.find_spec("torch") is None:
                    raise ImportError("torch is required for local transformers")

                target = str(snapshot) if snapshot is not None else self._model_name
                pinned = {"revision": revision} if snapshot is None else {}
                tokenizer = AutoTokenizer.from_pretrained(
                    target, local_files_only=True, **pinned
                )
                model = AutoModel.from_pretrained(
                    target, local_files_only=True, **pinned
                )
            else:
                tokenizer = AutoTokenizer.from_pretrained(self._model_name)
                model = AutoModel.from_pretrained(self._model_name)
            self._encoder = ("transformers", model, tokenizer)
            return self._encoder
        except ImportError:
            pass
        except Exception as exc:
            raise EmbeddingUnavailable(
                f"Hugging Face embedding model unavailable: {exc}"
            ) from exc

        if not is_mlx_repo:
            try:
                from mlx_embeddings.utils import load as mlx_load

                model, tokenizer = mlx_load(
                    str(snapshot)
                    if snapshot is not None
                    else pinned_model_target(
                        self._model_name, env_map, self.model_revision
                    )
                )
                self._encoder = ("mlx_embeddings", model, tokenizer)
                return self._encoder
            except ImportError:
                pass
            except Exception as exc:
                raise EmbeddingUnavailable(
                    f"Hugging Face embedding model unavailable: {exc}"
                ) from exc

        raise EmbeddingUnavailable(
            f"Hugging Face embedding model '{self._model_name}' unavailable: "
            "install sentence-transformers / transformers or download the model snapshot first"
        )

    def embed_batch_for(
        self, texts: Sequence[str], kind: str = "passage"
    ) -> List[List[float]]:
        role = (
            "query" if str(kind or "passage").strip().lower() == "query" else "passage"
        )
        if self._prefixed_e5:
            prefix = E5_PREFIXES[role]
            return self.embed_batch(
                [
                    text if str(text).startswith(prefix) else f"{prefix}{text}"
                    for text in texts
                ]
            )
        if self._prefixed_gemma:
            prefix = EMBEDDINGGEMMA_PREFIXES[role]
            return self.embed_batch(
                [
                    text
                    if str(text).startswith(("task:", "title:"))
                    else f"{prefix}{text}"
                    for text in texts
                ]
            )
        return self.embed_batch(texts)

    def _embed_raw(self, texts: Sequence[str]) -> List[List[float]]:
        backend, model, tokenizer = self._load()
        try:
            if backend == "sentence_transformers":
                try:
                    raw = model.encode(list(texts), normalize_embeddings=True)
                except TypeError:
                    raw = model.encode(list(texts))
                rows = raw.tolist() if hasattr(raw, "tolist") else list(raw)
                return [
                    [
                        float(x)
                        for x in (row.tolist() if hasattr(row, "tolist") else list(row))
                    ]
                    for row in rows
                ]
            if backend == "transformers":
                encoded = tokenizer(
                    list(texts),
                    padding=True,
                    truncation=True,
                    max_length=self._max_tokens,
                    return_tensors="pt",
                )
                try:
                    import torch

                    with torch.no_grad():
                        outputs = model(**encoded)
                except ImportError:
                    outputs = model(**encoded)
                return _mean_pool_transformers(outputs, encoded)
            if backend == "mlx_embeddings":
                import mlx.core as mx

                out: List[List[float]] = []
                for text in texts:
                    ids = list(tokenizer.encode(text))[: self._max_tokens]
                    result = model(mx.array([ids]))
                    out.append([float(x) for x in _pool(mx, result)])
                return out
            raise EmbeddingUnavailable(f"unsupported Hugging Face backend: {backend}")
        except EmbeddingUnavailable:
            raise
        except Exception as exc:
            raise EmbeddingUnavailable(f"Hugging Face embedding failed: {exc}") from exc

    def metadata(self) -> Dict[str, Any]:
        info = super().metadata()
        info["hf_repo_id"] = self._model_name
        info["instruction_prefixes"] = bool(self._prefixed_e5 or self._prefixed_gemma)
        info["max_tokens"] = self._max_tokens
        return info

    def health(self) -> Dict[str, Any]:
        try:
            backend, _, _ = self._load()
            return {
                "status": "ok",
                "detail": f"Hugging Face model {self._model_name} loaded ({backend})",
            }
        except (
            EmbeddingUnavailable,
            ImportError,
            OSError,
            RuntimeError,
            ValueError,
            TypeError,
            AttributeError,
        ) as exc:
            return {"status": "unavailable", "detail": str(exc)}


class MLXEmbeddingProvider(_NetworkEmbeddingProvider):
    provider = "mlx"

    def __init__(self, cfg: _RemoteConfig):
        super().__init__(cfg)
        if not cfg.dim:
            self.dim = _guess_dim(cfg.model, DEFAULT_EMBEDDING_DIM)
        self.model_id = f"mlx:{cfg.model}:{self.dim}"
        self._encoder: Optional[Tuple[str, Any, Any]] = None
        self._load_lock = threading.Lock()
        self._prefixed = _wants_e5_prefix(cfg.model)
        rev = cfg.extra.get("model_revision") or cfg.extra.get("revision")
        self.model_revision = str(rev).strip() if rev else None
        if self._prefixed:
            self.passage_mode = "e5_passage"
            self.query_mode = "e5_query"
        else:
            self.passage_mode = "raw"
            self.query_mode = "raw"

    def _load(self):
        if self._encoder is not None:
            return self._encoder
        with self._load_lock:
            if self._encoder is None:
                self._encoder = self._open_encoder()
            return self._encoder

    def _open_encoder(self) -> Tuple[str, Any, Any]:
        try:
            from mlx_embeddings.utils import load as mlx_load

            model, tokenizer = mlx_load(self._cfg.model)
            return ("mlx_embeddings", model, tokenizer)
        except ImportError as exc:
            raise EmbeddingUnavailable(
                f"MLX embedding model unavailable: {exc}"
            ) from exc
        except Exception as exc:
            raise EmbeddingUnavailable(
                f"MLX embedding model unavailable: {exc}"
            ) from exc

    def embed_batch_for(
        self, texts: Sequence[str], kind: str = "passage"
    ) -> List[List[float]]:
        if not self._prefixed:
            return self.embed_batch(texts)
        prefix = E5_PREFIXES.get(str(kind or "passage").lower(), E5_PREFIXES["passage"])
        return self.embed_batch([f"{prefix}{text}" for text in texts])

    def _embed_raw(self, texts: Sequence[str]) -> List[List[float]]:
        backend, model, tokenizer = self._load()
        try:
            import mlx.core as mx

            out: List[List[float]] = []
            for text in texts:
                ids = list(tokenizer.encode(text))[:MLX_MAX_TOKENS]
                result = model(mx.array([ids]))
                out.append([float(x) for x in _pool(mx, result)])
            return out
        except EmbeddingUnavailable:
            raise
        except Exception as exc:
            raise EmbeddingUnavailable(f"MLX embedding failed: {exc}") from exc

    def metadata(self) -> Dict[str, Any]:
        info = super().metadata()
        info["instruction_prefixes"] = self._prefixed
        info["max_tokens"] = MLX_MAX_TOKENS
        return info

    def health(self) -> Dict[str, Any]:
        try:
            self._load()
            return {"status": "ok", "detail": f"MLX model {self._cfg.model} loaded"}
        except (
            EmbeddingUnavailable,
            ImportError,
            OSError,
            RuntimeError,
            ValueError,
            TypeError,
            AttributeError,
        ) as exc:
            return {"status": "unavailable", "detail": str(exc)}


def _pool(mx: Any, result: Any) -> List[Any]:
    for attribute in ("text_embeds", "pooler_output"):
        pooled = getattr(result, attribute, None)
        if pooled is not None:
            row = pooled[0] if pooled.ndim > 1 else pooled
            return _as_float_list(row.tolist())
    hidden = getattr(result, "last_hidden_state", None)
    if hidden is None:
        hidden = result[0] if isinstance(result, (tuple, list)) else result
    row = mx.mean(hidden, axis=1)[0] if hidden.ndim == 3 else hidden[0]
    return _as_float_list(row.tolist())


class OllamaEmbeddingProvider(_NetworkEmbeddingProvider):
    provider = "ollama"

    def __init__(self, cfg: _RemoteConfig):
        super().__init__(cfg)
        self._base = (cfg.base_url or "http://127.0.0.1:11434").rstrip("/")
        if not cfg.dim:
            self.dim = _guess_dim(cfg.model, DEFAULT_EMBEDDING_DIM)
        self.model_id = f"ollama:{cfg.model}:{self.dim}"
        rev = cfg.extra.get("model_revision") or cfg.extra.get("revision")
        self.model_revision = str(rev).strip() if rev else None

    def _embed_raw(self, texts: Sequence[str]) -> List[List[float]]:
        out: List[List[float]] = []
        try:
            import httpx

            with httpx.Client(timeout=self._cfg.timeout) as client:
                resp = client.post(
                    f"{self._base}/api/embed",
                    json={"model": self._cfg.model, "input": list(texts)},
                )
                if resp.status_code == 404:
                    for text in texts:
                        r = client.post(
                            f"{self._base}/api/embeddings",
                            json={"model": self._cfg.model, "prompt": text},
                        )
                        r.raise_for_status()
                        out.append(r.json().get("embedding") or [])
                    return out
                resp.raise_for_status()
                data = resp.json()
                return data.get("embeddings") or [data.get("embedding") or []]
        except Exception as exc:
            raise EmbeddingUnavailable(f"Ollama embedding failed: {exc}") from exc

    def health(self) -> Dict[str, Any]:
        try:
            import httpx
        except ImportError as exc:
            return {"status": "unavailable", "detail": f"Ollama unreachable: {exc}"}
        _http_error: type[Exception] = getattr(httpx, "HTTPError", OSError)
        try:
            with httpx.Client(timeout=min(self._cfg.timeout, 5.0)) as client:
                r = client.get(f"{self._base}/api/tags")
                r.raise_for_status()
            return {"status": "ok", "detail": f"Ollama reachable at {self._base}"}
        except (_http_error, OSError, ValueError, TypeError, RuntimeError) as exc:
            return {"status": "unavailable", "detail": f"Ollama unreachable: {exc}"}


class OpenAICompatibleEmbeddingProvider(_NetworkEmbeddingProvider):
    provider = "openai"

    def __init__(self, cfg: _RemoteConfig):
        super().__init__(cfg)
        self._base = (cfg.base_url or "https://api.openai.com/v1").rstrip("/")
        if not cfg.dim:
            self.dim = _guess_dim(cfg.model, DEFAULT_EMBEDDING_DIM)
        self.model_id = f"openai:{cfg.model}:{self.dim}"
        rev = cfg.extra.get("model_revision") or cfg.extra.get("revision")
        self.model_revision = str(rev).strip() if rev else None

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._cfg.api_key:
            headers["Authorization"] = f"Bearer {self._cfg.api_key}"
        return headers

    def _embed_raw(self, texts: Sequence[str]) -> List[List[float]]:
        try:
            import httpx

            with httpx.Client(timeout=self._cfg.timeout) as client:
                r = client.post(
                    f"{self._base}/embeddings",
                    headers=self._headers(),
                    json={"model": self._cfg.model, "input": list(texts)},
                )
                r.raise_for_status()
                rows = sorted(r.json().get("data", []), key=lambda d: d.get("index", 0))
                return [row.get("embedding") or [] for row in rows]
        except Exception as exc:
            raise EmbeddingUnavailable(
                f"OpenAI-compatible embedding failed: {exc}"
            ) from exc

    def health(self) -> Dict[str, Any]:
        try:
            self._embed_raw(["ping"])
            return {"status": "ok", "detail": f"{self._base} reachable"}
        except (
            EmbeddingUnavailable,
            ImportError,
            OSError,
            RuntimeError,
            ValueError,
            TypeError,
            AttributeError,
        ) as exc:
            return {"status": "unavailable", "detail": str(exc)}


class CustomEmbeddingProvider(_NetworkEmbeddingProvider):
    provider = "custom"

    def __init__(self, cfg: _RemoteConfig):
        super().__init__(cfg)
        self._target_ref = str(
            cfg.extra.get("target")
            or os.getenv("LATTICEAI_EMBEDDING_CUSTOM_TARGET", "")
        )
        self.model_id = (
            f"custom:{cfg.model or self._target_ref or 'callable'}:{self.dim}"
        )
        rev = cfg.extra.get("model_revision") or cfg.extra.get("revision")
        self.model_revision = str(rev).strip() if rev else None
        self._fn: Optional[Callable[..., Any]] = None

    def _load(self):
        if self._fn is not None:
            return self._fn
        ref = self._target_ref
        if not ref:
            raise EmbeddingUnavailable(
                "custom embedding target not configured (LATTICEAI_EMBEDDING_CUSTOM_TARGET)"
            )
        module_name, _, attr = ref.replace(":", ".").rpartition(".")
        if not module_name:
            raise EmbeddingUnavailable(f"invalid custom embedding target: {ref}")
        try:
            module = importlib.import_module(module_name)
            self._fn = getattr(module, attr)
            return self._fn
        except Exception as exc:
            raise EmbeddingUnavailable(
                f"custom embedding target unavailable: {exc}"
            ) from exc

    def _embed_raw(self, texts: Sequence[str]) -> List[List[float]]:
        fn = self._load()
        try:
            return list(fn(list(texts)))
        except Exception as exc:
            raise EmbeddingUnavailable(f"custom embedding failed: {exc}") from exc

    def health(self) -> Dict[str, Any]:
        try:
            self._load()
            return {
                "status": "ok",
                "detail": f"custom target {self._target_ref} loaded",
            }
        except (
            EmbeddingUnavailable,
            ImportError,
            OSError,
            RuntimeError,
            ValueError,
            TypeError,
            AttributeError,
        ) as exc:
            return {"status": "unavailable", "detail": str(exc)}


PROVIDER_TYPES = ("hash", "huggingface", "mlx", "ollama", "openai", "custom")


def build_embedding_provider(
    provider: str,
    *,
    model: str = "",
    base_url: str = "",
    api_key: str = "",
    dim: int = 0,
    timeout: float = 30.0,
    extra: Optional[Dict[str, Any]] = None,
) -> EmbeddingProvider:
    kind = str(provider or "hash").strip().lower()
    if kind in {"", "hash", "local", "fallback"}:
        return HashEmbeddingProvider(dim=int(dim or DEFAULT_EMBEDDING_DIM))
    cfg = _RemoteConfig(
        model=model,
        base_url=base_url,
        api_key=api_key,
        dim=int(dim or 0),
        timeout=float(timeout or 30.0),
        extra=dict(extra or {}),
    )
    if kind in {"huggingface", "hf", "sentence-transformers", "sentence_transformers"}:
        return HuggingFaceEmbeddingProvider(cfg)
    if kind == "mlx":
        return MLXEmbeddingProvider(cfg)
    if kind == "ollama":
        return OllamaEmbeddingProvider(cfg)
    if kind in {"openai", "openai-compatible", "openai_compatible"}:
        return OpenAICompatibleEmbeddingProvider(cfg)
    if kind == "custom":
        return CustomEmbeddingProvider(cfg)
    raise ValueError(
        f"unknown embedding provider: {provider!r} (expected one of {PROVIDER_TYPES})"
    )


@dataclass
class ResolvedEmbedder:
    provider: EmbeddingProvider
    requested: str
    active: str
    fell_back: bool
    health: Dict[str, Any]
    detail: str = ""
    detected: Optional[Any] = None

    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "requested_provider": self.requested,
            "active_provider": self.active,
            "fell_back": self.fell_back,
            "health": self.health,
            "detail": self.detail,
            "recommended_model": RECOMMENDED_EMBEDDING_MODEL,
            "recommended_profile": RECOMMENDED_EMBEDDING_PROFILE,
            **self.provider.metadata(),
        }
        if self.detected is not None and hasattr(self.detected, "as_dict"):
            payload["detected"] = self.detected.as_dict()
        return payload


def _fallback_hash(dim: int, extra: Optional[Dict[str, Any]]) -> HashEmbeddingProvider:
    if isinstance(extra, dict) and "fallback_dim" in extra:
        return HashEmbeddingProvider(
            dim=int(extra.get("fallback_dim") or DEFAULT_EMBEDDING_DIM)
        )
    return HashEmbeddingProvider(dim=int(dim or DEFAULT_EMBEDDING_DIM))


def resolve_embedder(
    provider: str = "",
    *,
    model: str = "",
    base_url: str = "",
    api_key: str = "",
    dim: int = 0,
    timeout: float = 30.0,
    extra: Optional[Dict[str, Any]] = None,
    probe: bool = True,
) -> ResolvedEmbedder:
    requested = str(provider or "hash").strip().lower() or "hash"
    if requested == AUTO_PROVIDER:
        env_map = (extra or {}).get("env") if isinstance(extra, dict) else None
        probe_ollama = (
            bool((extra or {}).get("probe_ollama", False))
            if isinstance(extra, dict)
            else False
        )
        detection = detect_embedder(
            configured_provider=AUTO_PROVIDER,
            configured_model=model,
            env=env_map,
            probe_ollama=probe_ollama,
        )
        resolved_prov, resolved_model, resolved_dim = resolve_auto_provider(
            AUTO_PROVIDER, model, dim, detection
        )
        if resolved_prov in {"hash", "local", "fallback", ""}:
            hash_prov = HashEmbeddingProvider(
                dim=int(resolved_dim or DEFAULT_EMBEDDING_DIM)
            )
            return ResolvedEmbedder(
                hash_prov,
                AUTO_PROVIDER,
                "hash",
                False,
                hash_prov.health(),
                "deterministic local fallback",
                detected=detection,
            )
        extra_with_env = dict(extra or {})
        extra_with_env.setdefault("fallback_dim", int(dim or 0))
        if env_map is not None:
            extra_with_env.setdefault("env", env_map)
        result = resolve_embedder(
            resolved_prov,
            model=resolved_model,
            base_url=base_url,
            api_key=api_key,
            dim=resolved_dim,
            timeout=timeout,
            extra=extra_with_env,
            probe=probe,
        )
        result.requested = AUTO_PROVIDER
        result.detected = detection
        return result

    if requested in {"hash", "local", "fallback", ""}:
        hash_prov = HashEmbeddingProvider(dim=int(dim or DEFAULT_EMBEDDING_DIM))
        return ResolvedEmbedder(
            hash_prov,
            "hash",
            "hash",
            False,
            hash_prov.health(),
            "deterministic local fallback",
        )

    try:
        prov = build_embedding_provider(
            requested,
            model=model,
            base_url=base_url,
            api_key=api_key,
            dim=dim,
            timeout=timeout,
            extra=extra,
        )
    except (
        ValueError,
        TypeError,
        RuntimeError,
        OSError,
        KeyError,
        AttributeError,
    ) as exc:
        fallback = _fallback_hash(dim, extra)
        return ResolvedEmbedder(
            fallback,
            requested,
            "hash",
            True,
            {"status": "unavailable", "detail": str(exc)},
            f"could not construct {requested}; using hash fallback",
        )

    if probe:
        try:
            health = prov.health()
        except (
            EmbeddingUnavailable,
            ImportError,
            OSError,
            RuntimeError,
            ValueError,
            TypeError,
            AttributeError,
        ) as exc:
            health = {"status": "unavailable", "detail": str(exc)}
    else:
        health = {"status": "unknown", "detail": "not probed"}
    if probe and health.get("status") != "ok":
        fallback = _fallback_hash(dim, extra)
        return ResolvedEmbedder(
            fallback,
            requested,
            "hash",
            True,
            health,
            f"{requested} unavailable ({health.get('detail', '')}); using hash fallback",
        )
    return ResolvedEmbedder(prov, requested, prov.provider, False, health, "")
