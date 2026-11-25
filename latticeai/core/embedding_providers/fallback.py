"""Deterministic offline fallback text embedders.

Provides :class:`FallbackTextEmbedder` (multi-scale subword n-grams + CJK/Korean
stemming projection) and :class:`HashEmbeddingProvider` (strictly parity-locked to
``lattice-local-hash-v1`` in ``lattice_brain.embeddings.LocalEmbeddingModel``).
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Dict, List, Sequence, Tuple

from lattice_brain.embeddings import DEFAULT_EMBEDDING_DIM, LocalEmbeddingModel

from .base import EmbeddingProvider

#: Default dimension for the upgraded multi-scale subword FallbackTextEmbedder (matches EmbeddingGemma 2).
DEFAULT_FALLBACK_DIM = 768

_FALLBACK_TOKEN_RE = re.compile(
    r"[a-z0-9][a-z0-9_.:/+-]*|[가-힣]+|[\u4e00-\u9fff]+|[\u3040-\u30ff]+"
)
_SUBWORD_SPLIT_RE = re.compile(r"[_.:/+-]+")

_EN_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "this",
        "to",
        "was",
        "were",
        "will",
        "with",
    }
)
_KO_STOPWORDS = frozenset(
    {
        "있는",
        "없는",
        "하는",
        "되는",
        "그리고",
        "그러나",
        "따라서",
        "경우",
        "통해",
        "위해",
        "대한",
        "관한",
    }
)
_KO_PARTICLES = (
    "에서",
    "으로",
    "에게",
    "부터",
    "까지",
    "처럼",
    "대한",
    "관한",
    "하는",
    "하고",
    "하며",
    "이고",
    "이나",
    "이라",
    "에는",
    "에도",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "의",
    "에",
    "로",
    "와",
    "과",
    "도",
    "만",
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


def _project_weighted_features(
    features: Sequence[Tuple[str, float]], dim: int
) -> List[float]:
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
        self.model_revision = "v2"
        self.passage_mode = "raw"
        self.query_mode = "raw"

    def embed(self, text: str) -> List[float]:
        features = _extract_fallback_features(text)
        return _project_weighted_features(features, self.dim)

    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]

    def health(self) -> Dict[str, Any]:
        return {
            "status": "ok",
            "detail": "deterministic multi-scale subword local fallback",
        }


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
        self.model_revision = "v1"
        self._model = LocalEmbeddingModel(dim=resolved_dim)

    def embed(self, text: str) -> List[float]:
        # Preserve exact bit-for-bit parity with `LocalEmbeddingModel` (`lattice-local-hash-v1`).
        return self._model.embed(text)

    def health(self) -> Dict[str, Any]:
        return {"status": "ok", "detail": "deterministic local fallback"}


__all__ = [
    "DEFAULT_FALLBACK_DIM",
    "FallbackTextEmbedder",
    "HashEmbeddingProvider",
]
