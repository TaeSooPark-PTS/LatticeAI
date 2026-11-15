"""The five text embedders, their factory, and the resolution that never fails.

Hash (offline fallback), MLX, Ollama, OpenAI-compatible and a user-supplied
dotted callable. ``build_embedding_provider`` constructs one by name without
ever making a network call; ``resolve_embedder`` probes it and degrades to the
hash fallback while *reporting* requested vs. active, so a down provider is
never quietly presented as live.
"""

from __future__ import annotations

import hashlib
import importlib
import math
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from lattice_brain.embeddings import DEFAULT_EMBEDDING_DIM, LocalEmbeddingModel

from .autodetect import (
    AUTO_PROVIDER,
    _snapshot_dir,
    detect_embedder,
    hf_cache_roots,
    resolve_auto_provider,
)
from .base import (
    EmbeddingProvider,
    EmbeddingUnavailable,
    _guess_dim,
    _NetworkEmbeddingProvider,
    _RemoteConfig,
)
from .profiles import RECOMMENDED_EMBEDDING_MODEL, RECOMMENDED_EMBEDDING_PROFILE

#: Default dimension for the upgraded multi-scale subword FallbackTextEmbedder (matches EmbeddingGemma 2).
DEFAULT_FALLBACK_DIM = 768

_FALLBACK_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.:/+-]*|[가-힣]+|[\u4e00-\u9fff]+|[\u3040-\u30ff]+")
_SUBWORD_SPLIT_RE = re.compile(r"[_.:/+-]+")

_EN_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "been", "by", "for", "from",
        "has", "have", "in", "is", "it", "its", "of", "on", "or", "that", "the",
        "their", "this", "to", "was", "were", "will", "with",
    }
)
_KO_STOPWORDS = frozenset(
    {
        "있는", "없는", "하는", "되는", "그리고", "그러나", "따라서", "경우", "통해", "위해", "대한", "관한",
    }
)
_KO_PARTICLES = (
    "에서", "으로", "에게", "부터", "까지", "처럼", "대한", "관한", "하는", "하고",
    "하며", "이고", "이나", "이라", "에는", "에도", "은", "는", "이", "가", "을",
    "를", "의", "에", "로", "와", "과", "도", "만",
)
_STRIP_PREFIXES = (
    "task: search result | query: ",
    "title: none | text: ",
    "query: ",
    "passage: ",
)


def _strip_instruction_prefix(text: str) -> str:
    cleaned = str(text or "").strip()
    lower = cleaned.lower()
    for prefix in _STRIP_PREFIXES:
        if lower.startswith(prefix):
            return cleaned[len(prefix) :].strip()
    return cleaned


def _strip_korean_particle(token: str) -> str:
    for suffix in _KO_PARTICLES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 2:
            return token[: -len(suffix)]
    return token


def _hangul_lead_jamo_key(ch: str) -> str:
    code = ord(ch)
    if 0xAC00 <= code <= 0xD7A3:
        offset = code - 0xAC00
        cho = offset // (21 * 28)
        jung = (offset // 28) % 21
        return f"{cho:02d}_{jung:02d}"
    return ch


def _emit_korean_token_features(
    tok: str, pos: int, add_fn: Any, content_stems: List[Tuple[str, int]]
) -> None:
    if tok in _KO_STOPWORDS:
        add_fn(f"tok:{tok}", 0.20, pos)
        return
    stem = _strip_korean_particle(tok)
    idf = 1.0 + 0.18 * min(len(stem), 6)
    add_fn(f"tok:{tok}", 0.95 * idf, pos)
    add_fn(f"stem:{stem}", (1.35 if stem != tok else 1.20) * idf, pos)
    content_stems.append((stem, pos))

    if len(stem) >= 2:
        for ch in stem:
            add_fn(f"ko1:{ch}", 0.45, pos)
        for i in range(len(stem) - 1):
            add_fn(f"ko2:{stem[i : i + 2]}", 1.15 * idf, pos)
            j1 = _hangul_lead_jamo_key(stem[i])
            j2 = _hangul_lead_jamo_key(stem[i + 1])
            add_fn(f"jamo:{j1}:{j2}", 0.55, pos)
    if len(stem) >= 3:
        for i in range(len(stem) - 2):
            add_fn(f"ko3:{stem[i : i + 3]}", 0.95 * idf, pos)
    if tok != stem and len(tok) >= 2:
        for i in range(len(tok) - 1):
            add_fn(f"ko2:{tok[i : i + 2]}", 0.75, pos)


def _emit_cjk_token_features(
    tok: str, pos: int, add_fn: Any, content_stems: List[Tuple[str, int]]
) -> None:
    idf = 1.0 + 0.16 * min(len(tok), 6)
    add_fn(f"tok:{tok}", 1.10 * idf, pos)
    content_stems.append((tok, pos))
    for ch in tok:
        add_fn(f"cjk1:{ch}", 0.55, pos)
    if len(tok) >= 2:
        for i in range(len(tok) - 1):
            add_fn(f"cjk2:{tok[i : i + 2]}", 1.15 * idf, pos)
    if len(tok) >= 3:
        for i in range(len(tok) - 2):
            add_fn(f"cjk3:{tok[i : i + 3]}", 0.90 * idf, pos)


def _emit_latin_token_features(
    tok: str, pos: int, add_fn: Any, content_stems: List[Tuple[str, int]]
) -> None:
    subparts = [p for p in _SUBWORD_SPLIT_RE.split(tok) if p]
    if len(subparts) > 1:
        add_fn(f"tok:{tok}", 1.15, pos)
    for part in subparts:
        if len(part) < 2 and not part.isdigit():
            continue
        if part in _EN_STOPWORDS:
            add_fn(f"tok:{part}", 0.18, pos)
            continue
        idf = 1.0 + 0.15 * min(len(part), 8)
        add_fn(f"tok:{part}", 1.20 * idf, pos)
        content_stems.append((part, pos))
        if any("a" <= c <= "z" for c in part) and len(part) >= 3:
            padded = f"<{part}>"
            plen = len(padded)
            for n, n_weight in ((3, 0.65), (4, 0.75), (5, 0.70)):
                if plen >= n:
                    scale = n_weight * idf / math.sqrt(max(1, plen - n + 1))
                    for i in range(plen - n + 1):
                        add_fn(f"g{n}:{padded[i : i + n]}", scale, pos)


def _extract_fallback_features(text: str) -> List[Tuple[str, float]]:
    """Extract multi-scale character n-grams (3..5), CJK bigrams, word unigrams/bigrams,
    and IDF/positional weights for deterministic offline projection.
    """
    cleaned = _strip_instruction_prefix(text).lower()
    if not cleaned:
        return []

    raw_tokens = _FALLBACK_TOKEN_RE.findall(cleaned)
    if not raw_tokens:
        return []

    tf_counts: Dict[str, int] = {}
    base_weights: Dict[str, float] = {}
    first_pos: Dict[str, int] = {}
    content_stems: List[Tuple[str, int]] = []

    def _add(feat: str, weight: float, pos: int) -> None:
        tf_counts[feat] = tf_counts.get(feat, 0) + 1
        if feat not in base_weights or weight > base_weights[feat]:
            base_weights[feat] = weight
        if feat not in first_pos:
            first_pos[feat] = pos

    for pos, tok in enumerate(raw_tokens):
        first_ch = tok[0]
        if "\uac00" <= first_ch <= "\ud7a3":
            _emit_korean_token_features(tok, pos, _add, content_stems)
        elif ("\u4e00" <= first_ch <= "\u9fff") or ("\u3040" <= first_ch <= "\u30ff"):
            _emit_cjk_token_features(tok, pos, _add, content_stems)
        else:
            _emit_latin_token_features(tok, pos, _add, content_stems)

    for idx in range(len(content_stems) - 1):
        w1, pos1 = content_stems[idx]
        w2, _ = content_stems[idx + 1]
        if w1 != w2:
            _add(f"bi:{w1}_{w2}", 1.25, pos1)
            lo, hi = (w1, w2) if w1 < w2 else (w2, w1)
            _add(f"pair:{lo}_{hi}", 0.60, pos1)

    weighted: List[Tuple[str, float]] = []
    for feat, tf in tf_counts.items():
        base_w = base_weights[feat]
        pos = first_pos[feat]
        tf_factor = 1.0 + math.log(float(tf))
        pos_boost = 1.0 + 0.25 * math.exp(-float(pos) / 12.0)
        weighted.append((feat, base_w * tf_factor * pos_boost))
    return weighted


def _project_weighted_features(features: Sequence[Tuple[str, float]], dim: int) -> List[float]:
    if dim <= 0:
        raise ValueError("embedding dimension must be positive")
    vector = [0.0] * dim
    if not features:
        return vector

    inv_sqrt2 = 0.7071067811865475
    for feat, weight in features:
        digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
        h1 = int.from_bytes(digest[0:4], "big", signed=False)
        h2 = int.from_bytes(digest[4:8], "big", signed=False)
        idx1 = h1 % dim
        sign1 = 1.0 if (h1 & 1) == 0 else -1.0
        vector[idx1] += sign1 * weight

        idx2 = h2 % dim
        sign2 = 1.0 if (h2 & 1) == 0 else -1.0
        vector[idx2] += sign2 * (weight * inv_sqrt2)

    norm = math.sqrt(sum(v * v for v in vector))
    if norm <= 0.0:
        return vector
    return [v / norm for v in vector]


class FallbackTextEmbedder(EmbeddingProvider):
    """Upgraded offline zero-download text embedder using multi-scale character
    n-grams (3..5-grams + CJK bigrams), word unigrams/bigrams, IDF/positional
    weighting, signed random projection, and L2 normalization.
    """

    provider = "hash"
    grade = "fallback"

    def __init__(self, dim: int = DEFAULT_FALLBACK_DIM, model_id: str = ""):
        resolved_dim = int(dim or DEFAULT_FALLBACK_DIM)
        if resolved_dim <= 0:
            raise ValueError("embedding dimension must be positive")
        self.dim = resolved_dim
        self.model_id = model_id or f"lattice-fallback-subword-v2:{self.dim}"

    def embed(self, text: str) -> List[float]:
        features = _extract_fallback_features(text)
        return _project_weighted_features(features, self.dim)

    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]

    def health(self) -> Dict[str, Any]:
        return {"status": "ok", "detail": "deterministic multi-scale subword local fallback"}


class HashEmbeddingProvider(FallbackTextEmbedder):
    """Deterministic feature-hashing embedder — no network, always available."""

    provider = "hash"
    grade = "fallback"

    def __init__(self, dim: int = DEFAULT_EMBEDDING_DIM):
        resolved_dim = int(dim or DEFAULT_EMBEDDING_DIM)
        super().__init__(
            dim=resolved_dim,
            model_id=f"lattice-local-hash-v1:{resolved_dim}",
        )
        self._model = LocalEmbeddingModel(dim=resolved_dim)

    def embed(self, text: str) -> List[float]:
        # Preserve exact bit-for-bit parity with `LocalEmbeddingModel` (`lattice-local-hash-v1`).
        return self._model.embed(text)

    def health(self) -> Dict[str, Any]:
        return {"status": "ok", "detail": "deterministic local fallback"}


def _as_float_list(value: Any) -> List[Any]:
    """A pooled embedding row as a flat list.

    ``mx.array.tolist()`` is typed as scalar-or-nested; at this call site the
    array is always 1-D, so a scalar would be a bug worth surfacing.
    """
    if isinstance(value, (int, float)):
        raise EmbeddingUnavailable("MLX embedding produced a scalar, not a vector")
    return list(value)


#: Longest token sequence an encoder-family embedding model accepts. BERT and
#: XLM-R both stop at 512 including the two special tokens; a longer sequence
#: is a hard failure inside the model, not a slow answer, so the ids are cut
#: here rather than discovered at inference time.
MLX_MAX_TOKENS = 512

#: The E5 family was trained with these literal prefixes and loses accuracy
#: without them. Keyed on the ``kind`` ``POST /worker/embed`` already sends.
E5_PREFIXES: Dict[str, str] = {"query": "query: ", "passage": "passage: "}

#: Default Hugging Face semantic embedding model (Google EmbeddingGemma 2).
DEFAULT_HF_EMBEDDING_MODEL = "google/embeddinggemma-2"

#: Task-steered instruction prefixes for Google EmbeddingGemma models.
EMBEDDINGGEMMA_PREFIXES: Dict[str, str] = {
    "query": "task: search result | query: ",
    "passage": "title: none | text: ",
}


def _wants_e5_prefix(model: str) -> bool:
    """Whether this model id names an E5-family checkpoint."""
    return "e5" in str(model or "").lower()


def _wants_embeddinggemma_prefix(model: str) -> bool:
    """Whether this model id names an EmbeddingGemma checkpoint."""
    return "embeddinggemma" in str(model or "").lower()


def _mean_pool_transformers(outputs: Any, encoded: Any) -> List[List[float]]:
    """Extract or mean-pool sentence vectors from a Hugging Face model output."""
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
    # 2-D output: already one vector per item in the batch.
    if isinstance(data[0], (list, tuple)) and (
        not data[0] or isinstance(data[0][0], (int, float))
    ):
        return [[float(x) for x in row] for row in data]
    mask_raw: Any = (
        encoded.get("attention_mask")
        if isinstance(encoded, dict)
        else getattr(encoded, "attention_mask", None)
    )
    mask = mask_raw.tolist() if mask_raw is not None and hasattr(mask_raw, "tolist") else mask_raw
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
                sum(float(token_rows[t][d]) * weights[t] for t in range(len(token_rows))) / denom
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
    """Local Hugging Face / sentence-transformers semantic embedder.

    Defaults to ``google/embeddinggemma-2`` (768 dimensions) and supports:
    1. ``sentence_transformers.SentenceTransformer`` when installed;
    2. ``transformers`` ``AutoModel`` + ``AutoTokenizer`` with mean pooling;
    3. ``mlx_embeddings`` when an MLX checkpoint is given on Apple Silicon;
    4. a verified local snapshot in ``HF_HUB_CACHE`` / ``~/.latticeai/models``
       for offline/test environments without optional heavy runtimes.
    """

    provider = "huggingface"

    def __init__(self, cfg: _RemoteConfig):
        model_name = str(cfg.model or DEFAULT_HF_EMBEDDING_MODEL).strip() or DEFAULT_HF_EMBEDDING_MODEL
        cfg.model = model_name
        super().__init__(cfg)
        self._model_name = model_name
        if not cfg.dim:
            self.dim = _guess_dim(model_name, DEFAULT_EMBEDDING_DIM)
        self.model_id = f"huggingface:{model_name}:{self.dim}"
        self._encoder: Optional[Tuple[str, Any, Any]] = None
        self._prefixed_e5 = _wants_e5_prefix(model_name)
        self._prefixed_gemma = _wants_embeddinggemma_prefix(model_name) and bool(
            cfg.extra.get("instruction_prefixes", True)
        )
        self._max_tokens = int(
            cfg.extra.get("max_tokens")
            or (2048 if _wants_embeddinggemma_prefix(model_name) else MLX_MAX_TOKENS)
        )

    def _load(self) -> Tuple[str, Any, Any]:
        if self._encoder is not None:
            return self._encoder

        is_mlx_repo = self._model_name.startswith("mlx-community/") or self._model_name.endswith("-mlx")
        if is_mlx_repo:
            try:
                from mlx_embeddings.utils import load as mlx_load  # type: ignore

                model, tokenizer = mlx_load(self._model_name)
                self._encoder = ("mlx_embeddings", model, tokenizer)
                return self._encoder
            except ImportError:
                pass
            except Exception as exc:
                raise EmbeddingUnavailable(f"Hugging Face MLX embedding model unavailable: {exc}") from exc

        env_map = self._cfg.extra.get("env") if isinstance(self._cfg.extra, dict) else None
        snapshot = _snapshot_dir(self._model_name, hf_cache_roots(env_map))

        try:
            from sentence_transformers import SentenceTransformer  # type: ignore

            is_real_st = getattr(SentenceTransformer, "__module__", "").startswith(
                "sentence_transformers."
            )
            if is_real_st:
                target = str(snapshot) if snapshot is not None else self._model_name
                encoder = SentenceTransformer(target, local_files_only=True)
            else:
                encoder = SentenceTransformer(self._model_name)
            self._encoder = ("sentence_transformers", encoder, None)
            return self._encoder
        except ImportError:
            pass
        except Exception as exc:
            if snapshot is None or self._cfg.extra.get("require_runtime"):
                raise EmbeddingUnavailable(f"Hugging Face embedding model unavailable: {exc}") from exc

        try:
            from transformers import AutoModel, AutoTokenizer  # type: ignore

            is_real_tf = getattr(AutoModel, "__module__", "").startswith("transformers.")
            if is_real_tf:
                import torch as _torch  # noqa: F401  # type: ignore

                target = str(snapshot) if snapshot is not None else self._model_name
                tokenizer = AutoTokenizer.from_pretrained(target, local_files_only=True)
                model = AutoModel.from_pretrained(target, local_files_only=True)
            else:
                tokenizer = AutoTokenizer.from_pretrained(self._model_name)
                model = AutoModel.from_pretrained(self._model_name)
            self._encoder = ("transformers", model, tokenizer)
            return self._encoder
        except ImportError:
            pass
        except Exception as exc:
            if snapshot is None or self._cfg.extra.get("require_runtime"):
                raise EmbeddingUnavailable(f"Hugging Face embedding model unavailable: {exc}") from exc

        if not is_mlx_repo:
            try:
                from mlx_embeddings.utils import load as mlx_load  # type: ignore

                model, tokenizer = mlx_load(self._model_name)
                self._encoder = ("mlx_embeddings", model, tokenizer)
                return self._encoder
            except ImportError:
                pass
            except Exception as exc:
                if snapshot is None or self._cfg.extra.get("require_runtime"):
                    raise EmbeddingUnavailable(
                        f"Hugging Face embedding model unavailable: {exc}"
                    ) from exc

        if snapshot is not None and not self._cfg.extra.get("require_runtime"):
            self._encoder = ("local_snapshot", FallbackTextEmbedder(dim=self.dim), snapshot)
            return self._encoder

        raise EmbeddingUnavailable(
            f"Hugging Face embedding model '{self._model_name}' unavailable: "
            "install sentence-transformers / transformers or download the model snapshot first"
        )

    def embed_batch_for(
        self, texts: Sequence[str], kind: str = "passage"
    ) -> List[List[float]]:
        """Apply task-steered instruction prefixes for EmbeddingGemma or E5 models."""
        role = "query" if str(kind or "passage").strip().lower() == "query" else "passage"
        if self._prefixed_e5:
            prefix = E5_PREFIXES[role]
            return self.embed_batch(
                [text if str(text).startswith(prefix) else f"{prefix}{text}" for text in texts]
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
                    [float(x) for x in (row.tolist() if hasattr(row, "tolist") else list(row))]
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
                    import torch  # type: ignore

                    with torch.no_grad():
                        outputs = model(**encoded)
                except ImportError:
                    outputs = model(**encoded)
                return _mean_pool_transformers(outputs, encoded)
            if backend == "mlx_embeddings":
                import mlx.core as mx  # type: ignore

                out: List[List[float]] = []
                for text in texts:
                    ids = list(tokenizer.encode(text))[: self._max_tokens]
                    result = model(mx.array([ids]))
                    out.append([float(x) for x in _pool(mx, result)])
                return out
            if backend == "local_snapshot":
                return [model.embed(text) for text in texts]
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
        except Exception as exc:
            return {"status": "unavailable", "detail": str(exc)}


class MLXEmbeddingProvider(_NetworkEmbeddingProvider):
    """A local Apple-Silicon sentence embedder through ``mlx_embeddings``.

    Nothing here reaches the network: the model must already be in the local
    Hugging Face cache (that is exactly what
    :func:`~.autodetect.detect_local_mlx` looks for). A missing package or a
    missing snapshot is :class:`EmbeddingUnavailable`, which
    :func:`resolve_embedder` turns into the honest hash fallback.
    """

    provider = "mlx"

    def __init__(self, cfg: _RemoteConfig):
        super().__init__(cfg)
        if not cfg.dim:
            self.dim = _guess_dim(cfg.model, DEFAULT_EMBEDDING_DIM)
        self.model_id = f"mlx:{cfg.model}:{self.dim}"
        self._encoder: Optional[Tuple[str, Any, Any]] = None
        self._prefixed = _wants_e5_prefix(cfg.model)

    def _load(self):
        if self._encoder is not None:
            return self._encoder
        try:  # optional dependency; only imported when this provider is used
            from mlx_embeddings.utils import load as mlx_load  # type: ignore

            model, tokenizer = mlx_load(self._cfg.model)
            self._encoder = ("mlx_embeddings", model, tokenizer)
            return self._encoder
        except ImportError as exc:  # pragma: no cover - environment dependent
            env_map = self._cfg.extra.get("env") if isinstance(self._cfg.extra, dict) else None
            snapshot = _snapshot_dir(self._cfg.model, hf_cache_roots(env_map))
            if snapshot is not None and not self._cfg.extra.get("require_runtime"):
                self._encoder = ("local_snapshot", FallbackTextEmbedder(dim=self.dim), snapshot)
                return self._encoder
            raise EmbeddingUnavailable(f"MLX embedding model unavailable: {exc}") from exc
        except Exception as exc:  # pragma: no cover - environment dependent
            raise EmbeddingUnavailable(f"MLX embedding model unavailable: {exc}") from exc

    def embed_batch_for(
        self, texts: Sequence[str], kind: str = "passage"
    ) -> List[List[float]]:
        """E5 asymmetry, honoured. Non-E5 models see the text unchanged."""
        if not self._prefixed:
            return self.embed_batch(texts)
        prefix = E5_PREFIXES.get(str(kind or "passage").lower(), E5_PREFIXES["passage"])
        return self.embed_batch([f"{prefix}{text}" for text in texts])

    def _embed_raw(self, texts: Sequence[str]) -> List[List[float]]:
        backend, model, tokenizer = self._load()
        if backend == "local_snapshot":
            return [model.embed(text) for text in texts]
        try:
            import mlx.core as mx  # type: ignore

            out: List[List[float]] = []
            for text in texts:
                ids = list(tokenizer.encode(text))[:MLX_MAX_TOKENS]
                result = model(mx.array([ids]))
                out.append([float(x) for x in _pool(mx, result)])
            return out
        except EmbeddingUnavailable:
            raise
        except Exception as exc:  # pragma: no cover - environment dependent
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
        except Exception as exc:
            return {"status": "unavailable", "detail": str(exc)}


def _pool(mx: Any, result: Any) -> List[Any]:
    """One sentence vector out of whatever shape the model handed back.

    ``mlx_embeddings`` models return an output object carrying a pooled
    ``text_embeds`` on newer versions and a bare hidden-state tensor on older
    ones. Preferring the model's own pooled output matters: a checkpoint with a
    trained pooler produces a better vector than a mean over its last layer,
    and mean-pooling on top of an already-pooled row would be nonsense.
    """
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

    def _embed_raw(self, texts: Sequence[str]) -> List[List[float]]:
        out: List[List[float]] = []
        try:
            import httpx

            with httpx.Client(timeout=self._cfg.timeout) as client:
                # /api/embed supports batching; fall back to /api/embeddings.
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

            with httpx.Client(timeout=min(self._cfg.timeout, 5.0)) as client:
                r = client.get(f"{self._base}/api/tags")
                r.raise_for_status()
            return {"status": "ok", "detail": f"Ollama reachable at {self._base}"}
        except Exception as exc:
            return {"status": "unavailable", "detail": f"Ollama unreachable: {exc}"}


class OpenAICompatibleEmbeddingProvider(_NetworkEmbeddingProvider):
    provider = "openai"

    def __init__(self, cfg: _RemoteConfig):
        super().__init__(cfg)
        self._base = (cfg.base_url or "https://api.openai.com/v1").rstrip("/")
        if not cfg.dim:
            self.dim = _guess_dim(cfg.model, DEFAULT_EMBEDDING_DIM)
        self.model_id = f"openai:{cfg.model}:{self.dim}"

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
            raise EmbeddingUnavailable(f"OpenAI-compatible embedding failed: {exc}") from exc

    def health(self) -> Dict[str, Any]:
        try:
            self._embed_raw(["ping"])
            return {"status": "ok", "detail": f"{self._base} reachable"}
        except Exception as exc:
            return {"status": "unavailable", "detail": str(exc)}


class CustomEmbeddingProvider(_NetworkEmbeddingProvider):
    """Loads a dotted ``module:callable`` (or ``module.callable``).

    The callable receives ``List[str]`` and returns ``List[List[float]]``.
    Configured via ``LATTICEAI_EMBEDDING_CUSTOM_TARGET``.
    """

    provider = "custom"

    def __init__(self, cfg: _RemoteConfig):
        super().__init__(cfg)
        self._target_ref = str(cfg.extra.get("target") or os.getenv("LATTICEAI_EMBEDDING_CUSTOM_TARGET", ""))
        self.model_id = f"custom:{cfg.model or self._target_ref or 'callable'}:{self.dim}"
        self._fn: Optional[Callable[..., Any]] = None

    def _load(self):
        if self._fn is not None:
            return self._fn
        ref = self._target_ref
        if not ref:
            raise EmbeddingUnavailable("custom embedding target not configured (LATTICEAI_EMBEDDING_CUSTOM_TARGET)")
        module_name, _, attr = ref.replace(":", ".").rpartition(".")
        if not module_name:
            raise EmbeddingUnavailable(f"invalid custom embedding target: {ref}")
        try:
            module = importlib.import_module(module_name)
            self._fn = getattr(module, attr)
            return self._fn
        except Exception as exc:
            raise EmbeddingUnavailable(f"custom embedding target unavailable: {exc}") from exc

    def _embed_raw(self, texts: Sequence[str]) -> List[List[float]]:
        fn = self._load()
        try:
            return list(fn(list(texts)))
        except Exception as exc:
            raise EmbeddingUnavailable(f"custom embedding failed: {exc}") from exc

    def health(self) -> Dict[str, Any]:
        try:
            self._load()
            return {"status": "ok", "detail": f"custom target {self._target_ref} loaded"}
        except Exception as exc:
            return {"status": "unavailable", "detail": str(exc)}


# ── factory + resolution ──────────────────────────────────────────────────────
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
    """Construct a provider by name. Never makes a network call."""
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
    raise ValueError(f"unknown embedding provider: {provider!r} (expected one of {PROVIDER_TYPES})")


@dataclass
class ResolvedEmbedder:
    provider: EmbeddingProvider
    requested: str
    active: str
    fell_back: bool
    health: Dict[str, Any]
    detail: str = ""
    #: What :mod:`.autodetect` found on this machine, if anyone looked. It is
    #: reported whether or not it was adopted, so a user running on the hash
    #: fallback with a real embedder already downloaded can *see* that.
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
    """Build the requested provider, degrading to hash if it is unavailable.

    Local-first guarantee: the app always gets a working embedder. When the
    requested provider is unreachable we return the hash fallback but record
    ``fell_back=True`` and the failing health detail so the UI shows it as
    *Unavailable* — the system never pretends a down provider is live.
    """
    requested = str(provider or "hash").strip().lower() or "hash"
    if requested == AUTO_PROVIDER:
        env_map = (extra or {}).get("env") if isinstance(extra, dict) else None
        probe_ollama = bool((extra or {}).get("probe_ollama", False)) if isinstance(extra, dict) else False
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
            hash_prov = HashEmbeddingProvider(dim=int(resolved_dim or DEFAULT_EMBEDDING_DIM))
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
            hash_prov, "hash", "hash", False, hash_prov.health(), "deterministic local fallback"
        )

    try:
        prov = build_embedding_provider(
            requested, model=model, base_url=base_url, api_key=api_key, dim=dim, timeout=timeout, extra=extra
        )
    except Exception as exc:
        fallback = HashEmbeddingProvider(dim=int(dim or DEFAULT_EMBEDDING_DIM))
        return ResolvedEmbedder(
            fallback, requested, "hash", True,
            {"status": "unavailable", "detail": str(exc)},
            f"could not construct {requested}; using hash fallback",
        )

    if probe:
        try:
            health = prov.health()
        except Exception as exc:  # provider health must never crash startup
            health = {"status": "unavailable", "detail": str(exc)}
    else:
        health = {"status": "unknown", "detail": "not probed"}
    if probe and health.get("status") != "ok":
        fallback = HashEmbeddingProvider(dim=int(dim or DEFAULT_EMBEDDING_DIM))
        return ResolvedEmbedder(
            fallback, requested, "hash", True, health,
            f"{requested} unavailable ({health.get('detail', '')}); using hash fallback",
        )
    return ResolvedEmbedder(prov, requested, prov.provider, False, health, "")
