from __future__ import annotations

import importlib.util
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from latticeai.core.embedding_providers.autodetect import _snapshot_dir, hf_cache_roots

DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
RERANKER_ENV = "LATTICEAI_RERANKER"
RERANKER_MODEL_ENV = "LATTICEAI_RERANKER_MODEL"
RERANKER_MAX_LENGTH = 512
RERANK_MAX_DOCUMENTS = 64
RERANK_DOCUMENT_MAX_CHARS = 2_000
RERANKER_PROVIDER = "cross-encoder"

_OFF = {"0", "false", "no", "off", "disabled", "none"}

Loader = Callable[[str, int], Any]


class RerankerUnavailable(RuntimeError):
    pass


def _sentence_transformers_cross_encoder(target: str, max_length: int) -> Any:
    from sentence_transformers import CrossEncoder

    return CrossEncoder(target, max_length=max_length)


@dataclass
class CrossEncoderReranker:
    model_name: str
    target: str
    loader: Loader = _sentence_transformers_cross_encoder
    max_length: int = RERANKER_MAX_LENGTH
    _model: Any = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    @property
    def model_id(self) -> str:
        return f"{RERANKER_PROVIDER}:{self.model_name}"

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                try:
                    self._model = self.loader(self.target, self.max_length)
                except Exception as exc:
                    raise RerankerUnavailable(
                        f"cross-encoder {self.model_name} could not load: {exc}"
                    ) from exc
            return self._model

    def score(self, query: str, documents: Sequence[str]) -> List[float]:
        if not documents:
            return []
        model = self._load()
        pairs: List[Tuple[str, str]] = [
            (query, str(document or "")[:RERANK_DOCUMENT_MAX_CHARS])
            for document in documents
        ]
        try:
            raw = model.predict(pairs, show_progress_bar=False)
        except Exception as exc:
            raise RerankerUnavailable(f"cross-encoder scoring failed: {exc}") from exc
        values = raw.tolist() if hasattr(raw, "tolist") else list(raw)
        scores = [float(value) for value in values]
        if len(scores) != len(pairs):
            raise RerankerUnavailable(
                f"cross-encoder returned {len(scores)} scores for {len(pairs)} pairs"
            )
        return scores


@dataclass
class RerankerResolution:
    status: str
    model: str
    detail: str
    reranker: Optional[CrossEncoderReranker] = None

    @property
    def ready(self) -> bool:
        return self.reranker is not None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "model": self.model,
            "model_id": self.reranker.model_id if self.reranker else None,
            "provider": RERANKER_PROVIDER,
            "detail": self.detail,
        }


def _runtime_present() -> bool:
    return importlib.util.find_spec("sentence_transformers") is not None


def resolve_reranker(
    env: Optional[Mapping[str, str]] = None,
    *,
    runtime_present: Callable[[], bool] = _runtime_present,
    loader: Optional[Loader] = None,
) -> RerankerResolution:
    values: Dict[str, str] = dict(env if env is not None else os.environ)
    model = values.get(RERANKER_MODEL_ENV, "").strip() or DEFAULT_RERANKER_MODEL
    if values.get(RERANKER_ENV, "auto").strip().lower() in _OFF:
        return RerankerResolution(
            "disabled", model, f"{RERANKER_ENV} turned the reranker off"
        )
    if not runtime_present():
        return RerankerResolution(
            "unavailable",
            model,
            "sentence-transformers is not installed (pip install 'ltcai[rag]'); "
            "search keeps the RRF order",
        )
    local = Path(model).expanduser()
    snapshot: Optional[Path] = (
        local if local.is_dir() else _snapshot_dir(model, hf_cache_roots(values))
    )
    if snapshot is None:
        return RerankerResolution(
            "unavailable",
            model,
            f"{model} is not in the local model cache; weights are never fetched "
            "at query time, so search keeps the RRF order",
        )
    reranker = CrossEncoderReranker(
        model_name=model,
        target=str(snapshot),
        loader=loader or _sentence_transformers_cross_encoder,
    )
    return RerankerResolution(
        "ready", model, f"{model} loads from {snapshot} on first use", reranker
    )


__all__ = [
    "DEFAULT_RERANKER_MODEL",
    "RERANKER_ENV",
    "RERANKER_MAX_LENGTH",
    "RERANKER_MODEL_ENV",
    "RERANKER_PROVIDER",
    "RERANK_DOCUMENT_MAX_CHARS",
    "RERANK_MAX_DOCUMENTS",
    "CrossEncoderReranker",
    "RerankerResolution",
    "RerankerUnavailable",
    "resolve_reranker",
]
