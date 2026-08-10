"""Local deterministic embeddings used by the standalone Brain Core package."""

from __future__ import annotations

import hashlib
import math
import os
import re
import struct
from dataclasses import dataclass
from typing import Iterable, List

DEFAULT_EMBEDDING_DIM = int(os.getenv("LATTICEAI_VECTOR_DIM", "384"))
EMBEDDING_MODEL_ID = f"lattice-local-hash-v1:{DEFAULT_EMBEDDING_DIM}"


def embedding_model_id(dim: int) -> str:
    return f"lattice-local-hash-v1:{int(dim)}"


def _tokenize(text: str) -> List[str]:
    raw = str(text or "").lower()
    tokens = re.findall(r"[a-z0-9][a-z0-9_.:/+-]{1,}|[가-힣]{2,}", raw)
    features: List[str] = []
    for token in tokens:
        features.append(f"tok:{token}")
        if len(token) >= 5 and re.search(r"[a-z]", token):
            for i in range(0, len(token) - 2):
                features.append(f"tri:{token[i:i+3]}")
        if re.search(r"[가-힣]", token) and len(token) >= 3:
            for i in range(0, len(token) - 1):
                features.append(f"ko:{token[i:i+2]}")
    return features


def _hash_to_index(feature: str, dim: int) -> tuple[int, float]:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big", signed=False)
    sign = 1.0 if (value & 1) == 0 else -1.0
    return value % dim, sign


@dataclass(frozen=True)
class LocalEmbeddingModel:
    """Deterministic local embedder.

    This is intentionally not presented as a production semantic model. It is
    a real, offline cosine signal for local-first operation and tests; setup
    wizard provisioning can replace it with a user-consented model/provider.
    """

    dim: int = DEFAULT_EMBEDDING_DIM
    model_id: str = EMBEDDING_MODEL_ID

    def __post_init__(self) -> None:
        if self.model_id == EMBEDDING_MODEL_ID and self.dim != DEFAULT_EMBEDDING_DIM:
            object.__setattr__(self, "model_id", embedding_model_id(self.dim))

    def embed(self, text: str) -> List[float]:
        vector = [0.0] * self.dim
        features = _tokenize(text)
        if not features:
            return vector
        for feature in features:
            index, sign = _hash_to_index(feature, self.dim)
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm <= 0:
            return vector
        return [value / norm for value in vector]

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

    def encode(self, vector: Iterable[float]) -> bytes:
        values = list(vector)
        return struct.pack(f"<{len(values)}f", *values)

    def decode(self, payload: bytes, dim: int | None = None) -> List[float]:
        if not payload:
            return []
        count = int(dim or self.dim)
        expected = count * 4
        if len(payload) != expected:
            count = len(payload) // 4
        return list(struct.unpack(f"<{count}f", payload[: count * 4]))


# Removed in v11.1.0: ``VisionStub`` / ``get_vision_embedder``.
#
# They produced a "caption" (``Image pic.png (PNG 12x8)``) and an "image
# embedding" (a hash of the filename and pixel dimensions) with no model
# involved, and ``discovery_index`` stored both. Once in the graph neither was
# distinguishable from something a vision model had actually said, which is the
# one property a caption must have. The honest seam is now
# :class:`lattice_brain.multimodal.MultimodalPorts`: a caption exists only when
# a vision-language model produced it, an image vector only when a vision model
# produced it, and their absence is recorded as an absence.

__all__ = ["DEFAULT_EMBEDDING_DIM", "EMBEDDING_MODEL_ID", "LocalEmbeddingModel", "embedding_model_id"]
