"""Find the real embedder that is already on this machine.

The default text embedder is :class:`~.text.HashEmbeddingProvider` — feature
hashing into 384 dimensions. It is deterministic, offline and always available,
and it is **not semantic**: two sentences that mean the same thing in different
words score near zero against each other. Every recall complaint about "왜 못
찾지" that is not a keyword problem is this.

A real provider has always been configurable (``LATTICEAI_EMBEDDING_PROVIDER``),
but nothing ever *looked* for one, so a machine with a perfectly good embedding
model already in its Hugging Face cache still hashed. This module is that look:

1. an explicit configuration always wins and is never second-guessed;
2. otherwise, if a known embedding model is already **downloaded**, name it —
   filesystem only, no network, no download;
3. otherwise, if an Ollama server is reachable and has an embedding model
   pulled, name that;
4. otherwise, nothing was found, and the hash fallback stays.

Detection **reports**; adoption is a separate decision the caller makes (see
:func:`resolve_auto_provider`). ``LATTICEAI_EMBEDDING_PROVIDER=auto`` adopts
what was found; anything else leaves the resolution exactly as it was and the
finding travels to ``GET /api/embeddings/status`` as ``detected``, so the setup
surface can offer the switch instead of taking it.

## Why adoption is not automatic

Rust files every vector under ``(embedding_model, embedding_dim)`` and searches
only rows whose identity matches the embedder it holds — today the hash model.
Switching the *worker's* provider therefore does not corrupt anything (the two
identities never mix), but until the read path can embed a query through the
same provider, provider vectors are written and never read. Adopting silently
would buy inference cost and no recall. The switch is real, tested and one env
var away; it is not a default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: ``LATTICEAI_EMBEDDING_PROVIDER=auto`` — adopt whatever detection found.
AUTO_PROVIDER = "auto"
#: Set to ``0``/``false`` to skip detection entirely (and its Ollama probe).
AUTODETECT_ENV = "LATTICEAI_EMBEDDING_AUTODETECT"
#: Where the Ollama probe looks when nothing else says.
OLLAMA_BASE_ENV = "LATTICEAI_EMBEDDING_BASE_URL"
DEFAULT_OLLAMA_BASE = "http://127.0.0.1:11434"

#: Local MLX embedding models this build knows how to drive, best first.
#: ``dim`` is the model's true output width; ``prefix`` marks the E5 family,
#: whose quality depends on the ``query:`` / ``passage:`` instruction.
LOCAL_MLX_MODELS: Tuple[Tuple[str, int, bool], ...] = (
    ("mlx-community/multilingual-e5-small-mlx", 384, True),
    ("mlx-community/multilingual-e5-base-mlx", 768, True),
    ("mlx-community/multilingual-e5-large-mlx", 1024, True),
    ("mlx-community/snowflake-arctic-embed-l-v2.0-8bit", 1024, False),
    ("mlx-community/embeddinggemma-300m-4bit", 768, False),
    ("mlx-community/bge-m3", 1024, False),
)

#: Ollama model names that are embedders rather than chat models.
OLLAMA_EMBEDDING_MODELS: Tuple[Tuple[str, int], ...] = (
    ("bge-m3", 1024),
    ("mxbai-embed-large", 1024),
    ("nomic-embed-text", 768),
    ("all-minilm", 384),
)


@dataclass
class Detection:
    """What was found, and how. Every field is safe to show a user."""

    #: ``"mlx"`` | ``"ollama"`` | ``""`` when nothing was found.
    provider: str = ""
    model: str = ""
    dim: int = 0
    #: ``configured`` | ``local_model`` | ``ollama`` | ``none``.
    source: str = "none"
    detail: str = ""
    #: Every candidate that was looked for and not found, for the UI to offer.
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
    """Whether to look at all. On unless explicitly switched off."""
    raw = (env or dict(os.environ)).get(AUTODETECT_ENV, "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def hf_cache_roots(env: Optional[Dict[str, str]] = None) -> List[Path]:
    """Every directory a Hugging Face snapshot could be under, in order."""
    values = env or dict(os.environ)
    roots: List[Path] = []
    for key in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        raw = values.get(key, "").strip()
        if raw:
            roots.append(Path(raw))
    home = values.get("HF_HOME", "").strip()
    if home:
        roots.append(Path(home) / "hub")
    roots.append(Path(values.get("HOME", "~")).expanduser() / ".cache/huggingface/hub")
    seen: List[Path] = []
    for root in roots:
        if root not in seen:
            seen.append(root)
    return seen


def _snapshot_dir(repo_id: str, roots: List[Path]) -> Optional[Path]:
    """The snapshot directory holding this repo's weights, if it is on disk.

    A cache entry exists as soon as *anything* has been fetched, so presence of
    the directory is not enough: a snapshot with a ``config.json`` and at least
    one weight file is what "already downloaded" has to mean, or the detector
    names a model that cannot load.
    """
    flattened = "models--" + repo_id.replace("/", "--")
    for root in roots:
        snapshots = root / flattened / "snapshots"
        if not snapshots.is_dir():
            continue
        for snapshot in sorted(snapshots.iterdir()):
            if not snapshot.is_dir():
                continue
            if not (snapshot / "config.json").exists():
                continue
            weights = any(
                (snapshot / name).exists()
                for name in ("model.safetensors", "weights.safetensors")
            ) or any(snapshot.glob("*.safetensors"))
            if weights:
                return snapshot
    return None


def detect_local_mlx(env: Optional[Dict[str, str]] = None) -> Detection:
    """The best already-downloaded MLX embedding model, or an empty finding."""
    roots = hf_cache_roots(env)
    candidates: List[Dict[str, Any]] = []
    for repo_id, dim, prefixed in LOCAL_MLX_MODELS:
        snapshot = _snapshot_dir(repo_id, roots)
        candidates.append(
            {
                "provider": "mlx",
                "model": repo_id,
                "dim": dim,
                "downloaded": snapshot is not None,
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
    """An Ollama server with an embedding model pulled, or an empty finding.

    One localhost GET with a short timeout. Any failure — no server, no httpx,
    a slow answer — is "not found", never an error: detection must not be able
    to delay or fail a boot.
    """
    values = env or dict(os.environ)
    base = (base_url or values.get(OLLAMA_BASE_ENV, "") or DEFAULT_OLLAMA_BASE).rstrip("/")
    try:
        import httpx

        with httpx.Client(timeout=timeout) as client:
            response = client.get(f"{base}/api/tags")
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
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
    """What this machine could embed with, without downloading anything.

    A configured provider is reported back as ``source="configured"`` and no
    probing happens: the operator already answered the question.
    """
    values = env or dict(os.environ)
    configured = str(configured_provider or "").strip().lower()
    if configured and configured not in {"hash", "local", "fallback", AUTO_PROVIDER}:
        return Detection(
            provider=configured,
            model=configured_model,
            source="configured",
            detail=f"{configured} is configured explicitly",
        )
    if not autodetect_enabled(values):
        return Detection(source="none", detail=f"{AUTODETECT_ENV} is off")

    local = detect_local_mlx(values)
    if local.found:
        return local
    if probe_ollama:
        ollama = detect_ollama(env=values)
        if ollama.found:
            ollama.candidates = local.candidates
            return ollama
    return local


def resolve_auto_provider(
    configured_provider: str,
    configured_model: str,
    configured_dim: int,
    detection: Detection,
) -> Tuple[str, str, int]:
    """The ``(provider, model, dim)`` the embedder should actually be built with.

    Only ``provider == "auto"`` changes anything, and only when detection found
    something; ``auto`` with nothing found resolves to ``hash``, which is the
    honest answer rather than a construction that will fail its probe.
    Explicit configuration is returned untouched.
    """
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
    "LOCAL_MLX_MODELS",
    "OLLAMA_EMBEDDING_MODELS",
    "Detection",
    "autodetect_enabled",
    "detect_embedder",
    "detect_local_mlx",
    "detect_ollama",
    "hf_cache_roots",
    "resolve_auto_provider",
]
