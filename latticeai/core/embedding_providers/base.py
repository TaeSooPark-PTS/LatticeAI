from __future__ import annotations

import importlib
import math
import struct
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from lattice_brain.embeddings import DEFAULT_EMBEDDING_DIM

DEFAULT_INDEX_VERSION = 1
NORMALIZATION_L2 = "l2"
DEFAULT_EMBEDDING_MODE = "raw"


@dataclass(frozen=True)
class EmbeddingIndexContract:
    provider: str
    model_id: str
    model_revision: Optional[str] = None
    dimension: int = DEFAULT_EMBEDDING_DIM
    normalization: str = NORMALIZATION_L2
    passage_mode: str = DEFAULT_EMBEDDING_MODE
    query_mode: str = DEFAULT_EMBEDDING_MODE
    index_version: int = DEFAULT_INDEX_VERSION
    grade: str = "production"
    semantic: bool = True

    def is_hash_fallback(self) -> bool:
        return (
            self.provider == "hash"
            or self.grade == "fallback"
            or not self.semantic
            or self.model_id.startswith("lattice-local-hash")
            or self.model_id.startswith("lattice-fallback-")
        )

    def is_compatible_with(self, other: "EmbeddingIndexContract") -> bool:
        if self.model_id != other.model_id or self.dimension != other.dimension:
            return False
        if (
            self.normalization != other.normalization
            or self.index_version != other.index_version
        ):
            return False
        if (
            self.model_revision
            and other.model_revision
            and self.model_revision != other.model_revision
        ):
            return False
        return True

    def as_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "dimension": self.dimension,
            "normalization": self.normalization,
            "passage_mode": self.passage_mode,
            "query_mode": self.query_mode,
            "index_version": self.index_version,
            "grade": self.grade,
            "semantic": self.semantic,
        }


class EmbeddingUnavailable(RuntimeError):
    pass


_KNOWN_DIMS = {
    "bge-m3": 1024,
    "nomic-embed-text": 768,
    "mxbai-embed-large": 1024,
    "all-minilm": 384,
    "all-minilm-l6-v2": 384,
    "bge-small-en": 384,
    "bge-base-en": 768,
    "bge-large-en": 1024,
    "gte-small": 384,
    "gte-base": 768,
    "gte-large": 1024,
    "e5-small": 384,
    "e5-base": 768,
    "e5-large": 1024,
    "multilingual-e5-small": 384,
    "multilingual-e5-small-mlx": 384,
    "multilingual-e5-base": 768,
    "multilingual-e5-base-mlx": 768,
    "multilingual-e5-large": 1024,
    "multilingual-e5-large-mlx": 1024,
    "snowflake-arctic-embed-l-v2.0-8bit": 1024,
    "embeddinggemma-2": 768,
    "google/embeddinggemma-2": 768,
    "embeddinggemma-2-4bit": 768,
    "embeddinggemma-2-8bit": 768,
    "embeddinggemma-300m": 768,
    "google/embeddinggemma-300m": 768,
    "embeddinggemma-300m-4bit": 768,
    "embeddinggemma-300m-8bit": 768,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


def _guess_dim(model: str, default: int) -> int:
    raw = str(model or "").strip().lower().split(":")[0]
    if raw in _KNOWN_DIMS:
        return _KNOWN_DIMS[raw]
    key = raw.split("/")[-1].strip()
    return _KNOWN_DIMS.get(key, default)


def _l2_normalize(vector: Sequence[float]) -> List[float]:
    norm = math.sqrt(sum(float(v) * float(v) for v in vector))
    if norm <= 0:
        return [float(v) for v in vector]
    return [float(v) / norm for v in vector]


class EmbeddingProvider:
    model_id: str = ""
    dim: int = DEFAULT_EMBEDDING_DIM
    provider: str = "hash"
    grade: str = "production"
    model_revision: Optional[str] = None
    passage_mode: str = DEFAULT_EMBEDDING_MODE
    query_mode: str = DEFAULT_EMBEDDING_MODE
    index_version: int = DEFAULT_INDEX_VERSION

    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        raise NotImplementedError

    def embed_batch_for(
        self, texts: Sequence[str], kind: str = "passage"
    ) -> List[List[float]]:
        return self.embed_batch(texts)

    def _model_id_with_dim(self, dim: int) -> str:
        head, sep, tail = self.model_id.rpartition(":")
        if sep and tail.isdigit():
            return f"{head}:{dim}"
        return self.model_id

    def embed(self, text: str) -> List[float]:
        result = self.embed_batch([text])
        return result[0] if result else [0.0] * self.dim

    def encode(self, vector: Iterable[float]) -> bytes:
        values = [float(v) for v in vector]
        return struct.pack(f"<{len(values)}f", *values)

    def decode(self, payload: bytes, dim: Optional[int] = None) -> List[float]:
        if not payload:
            return []
        count = int(dim or self.dim)
        if len(payload) != count * 4:
            count = len(payload) // 4
        return list(struct.unpack(f"<{count}f", payload[: count * 4]))

    def similarity(self, left: Iterable[float], right: Iterable[float]) -> float:
        left_v, right_v = list(left), list(right)
        if len(left_v) != len(right_v):
            raise ValueError(
                f"embedding dimension mismatch: {len(left_v)} vs {len(right_v)}; "
                "the vector index was built with a different model"
            )
        return float(sum(a * b for a, b in zip(left_v, right_v, strict=True)))

    def health(self) -> Dict[str, Any]:
        return {"status": "ok", "detail": "ready"}

    def metadata(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model_id,
            "model_id": self.model_id,
            "dim": self.dim,
            "grade": self.grade,
        }

    def index_contract(self) -> EmbeddingIndexContract:
        provider_name = str(getattr(self, "provider", "hash") or "hash")
        grade = str(getattr(self, "grade", "production") or "production")
        model_id = str(getattr(self, "model_id", "") or "")
        is_semantic = (
            grade != "fallback"
            and provider_name != "hash"
            and not model_id.startswith("lattice-local-hash")
            and not model_id.startswith("lattice-fallback-")
        )
        return EmbeddingIndexContract(
            provider=provider_name,
            model_id=model_id,
            model_revision=getattr(self, "model_revision", None),
            dimension=int(
                getattr(self, "dim", DEFAULT_EMBEDDING_DIM) or DEFAULT_EMBEDDING_DIM
            ),
            normalization=NORMALIZATION_L2,
            passage_mode=str(
                getattr(self, "passage_mode", DEFAULT_EMBEDDING_MODE)
                or DEFAULT_EMBEDDING_MODE
            ),
            query_mode=str(
                getattr(self, "query_mode", DEFAULT_EMBEDDING_MODE)
                or DEFAULT_EMBEDDING_MODE
            ),
            index_version=int(
                getattr(self, "index_version", DEFAULT_INDEX_VERSION)
                or DEFAULT_INDEX_VERSION
            ),
            grade=grade,
            semantic=is_semantic,
        )


@dataclass
class _RemoteConfig:
    model: str
    base_url: str = ""
    api_key: str = ""
    dim: int = DEFAULT_EMBEDDING_DIM
    timeout: float = 30.0
    extra: Dict[str, Any] = field(default_factory=dict)


class _NetworkEmbeddingProvider(EmbeddingProvider):
    def __init__(self, cfg: _RemoteConfig):
        self._cfg = cfg
        self.dim = int(cfg.dim or DEFAULT_EMBEDDING_DIM)

    def _embed_raw(self, texts: Sequence[str]) -> List[List[float]]:
        raise NotImplementedError

    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        clean = [str(t or "")[:50_000] for t in texts]
        if not clean:
            return []
        vectors = self._embed_raw(clean)
        out: List[List[float]] = []
        for vec in vectors:
            vec = [float(x) for x in (vec or [])]
            if vec:
                self.dim = len(vec)
                self.model_id = self._model_id_with_dim(self.dim)
            out.append(_l2_normalize(vec) if vec else [0.0] * self.dim)
        return out


def _load_dotted(ref: str, env_name: str, label: str) -> Callable[..., Any]:
    if not ref:
        raise EmbeddingUnavailable(f"{label} target not configured ({env_name})")
    module_name, _, attr = ref.replace(":", ".").rpartition(".")
    if not module_name:
        raise EmbeddingUnavailable(f"invalid {label} target: {ref}")
    try:
        module = importlib.import_module(module_name)
        return getattr(module, attr)
    except Exception as exc:
        raise EmbeddingUnavailable(f"{label} target unavailable: {exc}") from exc
