"""Local deterministic embeddings used by the standalone Brain Core package."""

from __future__ import annotations

import hashlib
import math
import os
import re
import struct
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


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
        return float(sum(a * b for a, b in zip(left, right)))

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


# --- Large candidate #2 slice: multimodal (vision) stubs ---
# Schema already defines IMAGE / IMAGE_TEXT / CONTAINS_IMAGE.
# These stubs allow ingestion + retrieval paths to carry image signals without
# requiring heavy deps at core. Real impl can swap in local vision (e.g. via
# ollama vision or onnx CLIP) behind the same interface.
@dataclass(frozen=True)
class VisionStub:
    """Offline vision describe + embed stubs for multimodal Brain.

    describe: returns a short textual caption derived from metadata/filename.
    embed: produces a vector from image meta (size+format) + optional caption hash.
    Later: replace with real embedding model that accepts image bytes/path.
    """
    dim: int = DEFAULT_EMBEDDING_DIM

    def describe(self, path: str | None = None, meta: Optional[Dict[str, Any]] = None) -> str:
        meta = meta or {}
        w = meta.get("width") or meta.get("w") or "?"
        h = meta.get("height") or meta.get("h") or "?"
        fmt = meta.get("format") or meta.get("ext") or "img"
        name = (path or "").split("/")[-1] or "image"
        # deterministic caption stub (no external call)
        return f"Image {name} ({fmt} {w}x{h})"

    def embed_image(self, path: str | None = None, meta: Optional[Dict[str, Any]] = None, caption: str = "") -> List[float]:
        meta = meta or {}
        basis = f"{path or ''}|{meta.get('width',0)}x{meta.get('height',0)}|{meta.get('format','')}|{caption[:120]}"
        # reuse text embedder for determinism (image content hash would be better with real vision)
        model = LocalEmbeddingModel(dim=self.dim)
        return model.embed(basis)


def get_vision_embedder(dim: int | None = None) -> VisionStub:
    return VisionStub(dim=dim or DEFAULT_EMBEDDING_DIM)


__all__ = ["DEFAULT_EMBEDDING_DIM", "EMBEDDING_MODEL_ID", "LocalEmbeddingModel", "embedding_model_id", "VisionStub", "get_vision_embedder"]
