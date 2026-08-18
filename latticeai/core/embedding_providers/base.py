"""The contract every embedder implements, and the machinery they share.

``EmbeddingProvider`` is the interface: set ``model_id`` and ``dim``, implement
``embed_batch``, and inherit the float32 codec plus the dot-product similarity
that equals cosine because every ``embed`` returns an L2-normalized vector.
``_NetworkEmbeddingProvider`` adds what any provider that calls a model or a
server needs — input clamping, normalization, and locking the index identity to
the width the model actually returned.

Nothing here reaches a network or a model; the concrete providers in
:mod:`.text` and :mod:`.vision` do.
"""

from __future__ import annotations

import importlib
import math
import struct
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from lattice_brain.embeddings import DEFAULT_EMBEDDING_DIM


class EmbeddingUnavailable(RuntimeError):
    """Raised when a configured provider cannot produce an embedding.

    Callers in the hot path (``vector_search``) translate this into a clear
    503/"provider unavailable" rather than a misleading empty result.
    """


# Best-known output dimensionality for common embedding models, so the index
# identity is stable before the first (possibly remote) call. A configured
# ``dim`` always wins; an unknown model falls back to a one-time live probe.
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
    "embeddinggemma-300m-4bit": 768,
    "embeddinggemma-300m-8bit": 768,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


def _guess_dim(model: str, default: int) -> int:
    key = str(model or "").split("/")[-1].strip().lower()
    key = key.split(":")[0]
    return _KNOWN_DIMS.get(key, default)


def _l2_normalize(vector: Sequence[float]) -> List[float]:
    norm = math.sqrt(sum(float(v) * float(v) for v in vector))
    if norm <= 0:
        return [float(v) for v in vector]
    return [float(v) / norm for v in vector]


class EmbeddingProvider:
    """Interface every embedder implements.

    Subclasses must set ``model_id`` and ``dim`` and implement
    :meth:`embed_batch`; the rest (single embed, codec, similarity) is shared.
    """

    #: stable identity stored alongside every vector — change ⇒ re-index
    model_id: str = ""
    #: vector dimensionality
    dim: int = DEFAULT_EMBEDDING_DIM
    #: short provider kind ("hash" | "mlx" | "ollama" | "openai" | "custom")
    provider: str = "hash"
    #: "fallback" (hash) | "production" (real semantic model)
    grade: str = "production"

    # ── required ──────────────────────────────────────────────────────────
    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        raise NotImplementedError

    # ── optional: asymmetric models ───────────────────────────────────────
    def embed_batch_for(
        self, texts: Sequence[str], kind: str = "passage"
    ) -> List[List[float]]:
        """Embed for a *role*: ``"query"`` or ``"passage"``.

        Most embedders are symmetric and ignore the role — the default here
        does. The E5 family is not: it was trained with a literal ``query: ``
        or ``passage: `` in front of the text, and dropping the instruction
        costs real retrieval accuracy. ``POST /worker/embed`` already carries
        the role (``kind``), so the one provider that needs it can have it
        without every caller learning about instructions.
        """
        return self.embed_batch(texts)

    # ── derived (shared) ──────────────────────────────────────────────────
    def _model_id_with_dim(self, dim: int) -> str:
        """This provider's ``model_id`` restated at ``dim``.

        Every provider spells its identity ``<kind>:<model>:<dim>``, so the
        numeric tail is what moves when a live call reveals the model's true
        width. An id without such a tail does not encode a dimension and is
        returned unchanged — the index keys on ``model_id`` *and* ``dim``, so
        nothing becomes ambiguous.
        """
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
        # strict=True: a dimension mismatch means the two vectors came from
        # different embedding models. Truncating to the shorter one produces a
        # plausible-looking similarity that is meaningless — exactly the silent
        # wrongness this codebase keeps finding. Callers that can hit a model
        # swap already handle failure and fall back to lexical search.
        left_v, right_v = list(left), list(right)
        if len(left_v) != len(right_v):
            raise ValueError(
                f"embedding dimension mismatch: {len(left_v)} vs {len(right_v)}; "
                "the vector index was built with a different model"
            )
        return float(sum(a * b for a, b in zip(left_v, right_v, strict=True)))

    # ── observability ─────────────────────────────────────────────────────
    def health(self) -> Dict[str, Any]:
        """Return ``{status, detail}``; status ∈ ok | unavailable."""
        return {"status": "ok", "detail": "ready"}

    def metadata(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model_id,
            "model_id": self.model_id,
            "dim": self.dim,
            "grade": self.grade,
        }


@dataclass
class _RemoteConfig:
    model: str
    base_url: str = ""
    api_key: str = ""
    dim: int = DEFAULT_EMBEDDING_DIM
    timeout: float = 30.0
    extra: Dict[str, Any] = field(default_factory=dict)


class _NetworkEmbeddingProvider(EmbeddingProvider):
    """Common machinery for providers that call a model/server to embed."""

    def __init__(self, cfg: _RemoteConfig):
        self._cfg = cfg
        self.dim = int(cfg.dim or DEFAULT_EMBEDDING_DIM)

    # subclasses implement the raw call
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
                # lock the index identity to the true model dimensionality —
                # the id carries that dimension, so it moves with it or the
                # vectors end up filed under a width they do not have
                self.dim = len(vec)
                self.model_id = self._model_id_with_dim(self.dim)
            out.append(_l2_normalize(vec) if vec else [0.0] * self.dim)
        return out


def _load_dotted(ref: str, env_name: str, label: str) -> Callable[..., Any]:
    """Import ``module:callable`` (or ``module.callable``) or explain why not."""
    if not ref:
        raise EmbeddingUnavailable(f"{label} target not configured ({env_name})")
    module_name, _, attr = ref.replace(":", ".").rpartition(".")
    if not module_name:
        raise EmbeddingUnavailable(f"invalid {label} target: {ref}")
    try:
        module = importlib.import_module(module_name)
        return getattr(module, attr)  # type: ignore[no-any-return]
    except Exception as exc:
        raise EmbeddingUnavailable(f"{label} target unavailable: {exc}") from exc
